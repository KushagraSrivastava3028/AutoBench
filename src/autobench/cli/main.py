"""``autobench`` — the command line.

Five commands carry the weight:

    validate       run the publication gates on a task
    run            launch a suite and watch it
    bench-harness  measure the parallel speedup, reproducibly
    gate           exit non-zero on a regression
    serve/worker   the two long-running processes
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from autobench import __version__
from autobench.core.config import get_settings, reset_settings_cache

# Windows consoles default to a legacy code page; without this the first box
# character or en-dash raises UnicodeEncodeError mid-render.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console()
app = typer.Typer(
    name="autobench",
    help="A containerised, parallel evaluation platform for LLM agents.",
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

PASS = "[green]PASS[/green]"
FAIL = "[red]FAIL[/red]"
WARN = "[yellow]WARN[/yellow]"
SKIP = "[dim]SKIP[/dim]"
_GATE_STYLE = {"passed": PASS, "failed": FAIL, "warning": WARN, "skipped": SKIP}


def _configure(verbose: bool = False) -> Any:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    reset_settings_cache()
    return get_settings()


@app.command()
def version() -> None:
    """Print the version and the resolved execution settings."""
    settings = _configure()
    console.print(f"[bold]autobench[/bold] {__version__}")
    console.print(
        f"executor=[cyan]{settings.executor}[/cyan] queue=[cyan]{settings.queue}[/cyan] "
        f"artifacts=[cyan]{settings.artifact_backend}[/cyan] db=[dim]{settings.database_url}[/dim]"
    )
    if settings.executor == "local":
        console.print(
            "[yellow]The local executor provides no isolation.[/yellow] It exists for "
            "development; set AUTOBENCH_EXECUTOR=docker for anything you will report."
        )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    path: Annotated[Path, typer.Argument(help="Task directory, or a directory of them.")],
    deep: Annotated[bool, typer.Option("--deep", help="Also run the calibration gates.")] = False,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    no_pin: Annotated[
        bool, typer.Option("--no-pin", help="Downgrade digest pinning to a warning.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run the publication gates. Exits non-zero if any gate fails.

    Without ``--deep`` the calibration gates report ``skipped``, not ``passed``:
    you cannot claim the oracle scores 1.0 from a static lint.
    """
    _configure(verbose)
    from autobench.taskspec.loader import discover_tasks, load_task
    from autobench.taskspec.validator import validate_task

    directories = discover_tasks(path) if not (path / "task.toml").is_file() else [path]
    if not directories:
        console.print(f"[red]no task.toml found under {path}[/red]")
        raise typer.Exit(2)

    async def go() -> list[dict[str, Any]]:
        runner = None
        if deep:
            from autobench.worker.pipeline import PipelineCalibrationRunner

            runner = PipelineCalibrationRunner()
        reports = []
        for directory in directories:
            try:
                task = load_task(directory)
            except Exception as exc:
                reports.append(
                    {"task_id": directory.name, "ok": False, "error": str(exc), "gates": []}
                )
                continue
            report = await validate_task(task, runner=runner, require_digest_pin=not no_pin)
            reports.append(report.to_json())
        return reports

    reports = asyncio.run(go())

    if json_out:
        console.print_json(json.dumps(reports))
    else:
        for report in reports:
            _print_validation(report)

    failed = [r for r in reports if not r.get("ok", False)]
    if failed:
        console.print(
            f"\n[red]{len(failed)} of {len(reports)} task(s) failed.[/red] "
            "A failing gate blocks publication."
        )
        raise typer.Exit(1)
    console.print(f"\n[green]{len(reports)} task(s) passed every gate that ran.[/green]")


def _print_validation(report: dict[str, Any]) -> None:
    task_id = report.get("task_id", "?")
    if report.get("error"):
        console.print(Panel(f"[red]{report['error']}[/red]", title=f"[bold]{task_id}[/bold]"))
        return

    table = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    table.add_column("gate", style="bold", width=13)
    table.add_column("", width=6)
    table.add_column("detail", overflow="fold")

    for gate in report.get("gates", []):
        table.add_row(gate["gate"], _GATE_STYLE.get(gate["status"], gate["status"]), gate["detail"])
        for finding in gate.get("findings", []):
            colour = "red" if finding["severity"] == "error" else "yellow"
            table.add_row(
                "",
                "",
                f"[{colour}]{finding['file']}:{finding['line']}[/{colour}] {finding['message']}\n"
                f"  [dim]{finding['remediation']}[/dim]",
            )

    status = (
        "[green]publishable[/green]"
        if report.get("publishable")
        else ("[green]lints clean[/green]" if report.get("ok") else "[red]blocked[/red]")
    )
    console.print(
        Panel(
            table,
            title=f"[bold]{task_id}[/bold]  {status}",
            subtitle=f"[dim]{report.get('content_hash', '')[:23]}[/dim]",
        )
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command()
def run(
    suite: Annotated[str, typer.Option("--suite", "-s", help="Suite slug.")] = "core-reference",
    agent: Annotated[str, typer.Option("--agent", "-a")] = "react",
    model: Annotated[str, typer.Option("--model", "-m")] = "mock/mock-1",
    k: Annotated[int, typer.Option("--k", help="Attempts per task.")] = 1,
    concurrency: Annotated[int, typer.Option("--concurrency", "-c")] = 8,
    task: Annotated[
        str | None, typer.Option("--task", help="Run one task instead of a suite.")
    ] = None,
    label: Annotated[str | None, typer.Option("--label")] = None,
    budget: Annotated[
        float | None, typer.Option("--budget", help="Cancel above this USD spend.")
    ] = None,
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    json_out: Annotated[bool, typer.Option("--json")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Launch a run and execute it in-process, printing results as they land.

    This is the single-command path: no API, no Redis, no separate worker. For a
    real deployment use ``autobench serve`` plus ``autobench worker``.
    """
    settings = _configure(verbose)
    # Progress and first-run catalogue messages must not corrupt redirected JSON.
    with redirect_stdout(sys.stderr) if json_out else nullcontext():
        result = asyncio.run(
            _run_suite(settings, suite, agent, model, k, concurrency, task, label, budget, seed)
        )
    if json_out:
        typer.echo(json.dumps(result))
        return
    _print_run_result(result)
    if result["metrics"]["total_trials"] == 0:
        raise typer.Exit(1)


async def _run_suite(
    settings: Any,
    suite: str,
    agent: str,
    model: str,
    k: int,
    concurrency: int,
    single_task: str | None,
    label: str | None,
    budget: float | None,
    seed: int | None,
) -> dict[str, Any]:
    import secrets

    from sqlalchemy import select

    from autobench.api.services import aggregate_for_run
    from autobench.cli.bootstrap import bootstrap, ensure_suite, register_task
    from autobench.db.models import Run, RunStatus, Suite, SuiteMember, TaskVersion, Trial
    from autobench.db.session import dispose_engine, init_models, session_scope
    from autobench.taskspec.loader import load_task
    from autobench.worker.planner import TaskEntry, plan_run
    from autobench.worker.runner import TrialWorker

    await init_models(settings)

    async with session_scope(settings) as session:
        counts = await bootstrap(session, settings)
        if any(counts.values()):
            console.print(f"[dim]seeded: {counts}[/dim]")

        suite_row: Suite | None
        if single_task:
            path = Path(single_task)
            if not (path / "task.toml").is_file():
                path = settings.tasks_dir / single_task
            version = await register_task(session, load_task(path))
            suite_row = await ensure_suite(
                session, f"adhoc-{version.task_id}", f"ad-hoc: {version.task_id}", [version]
            )
        else:
            suite_row = (
                await session.execute(select(Suite).where(Suite.slug == suite))
            ).scalar_one_or_none()
            if suite_row is None:
                raise typer.BadParameter(f"no suite {suite!r}. Run `autobench seed` first.")

        from autobench.api.routers.runs import _autoregister_model
        from autobench.api.services import resolve_agent, resolve_model

        agent_row = await resolve_agent(session, agent)
        if agent_row is None:
            raise typer.BadParameter(f"no agent {agent!r}")
        model_row = await resolve_model(session, model) or await _autoregister_model(session, model)

        members = (
            (
                await session.execute(
                    select(TaskVersion)
                    .join(SuiteMember, SuiteMember.task_version_id == TaskVersion.id)
                    .where(SuiteMember.suite_id == suite_row.id)
                )
            )
            .scalars()
            .all()
        )
        if not members:
            raise typer.BadParameter(f"suite {suite_row.slug!r} has no members")

        run_seed = seed if seed is not None else secrets.randbits(48)
        run_row = Run(
            suite_id=suite_row.id,
            agent_id=agent_row.id,
            model_id=model_row.id,
            k=k,
            concurrency=concurrency,
            status=RunStatus.RUNNING.value,
            seed=run_seed,
            config={},
            label=label,
            budget_usd=budget,
            trials_total=0,
            created_by="cli",
        )
        session.add(run_row)
        await session.flush()

        entries = [
            TaskEntry(
                task_version_id=v.id,
                task_id=v.task_id,
                agent_timeout_sec=int((v.spec.get("agent") or {}).get("timeout_sec", 900)),
            )
            for v in members
        ]
        plan = plan_run(run_row.id, entries, k=k, run_seed=run_seed)
        for planned in plan.trials:
            session.add(
                Trial(
                    id=planned.id,
                    run_id=run_row.id,
                    task_version_id=planned.task_version_id,
                    attempt_index=planned.attempt_index,
                    seed=planned.seed,
                    priority=planned.priority,
                    expected_duration_ms=planned.expected_duration_ms,
                )
            )
        run_row.trials_total = plan.total
        run_id = run_row.id
        from autobench.core.ids import utcnow

        run_row.started_at = utcnow()

    console.print(
        f"[bold]{suite_row.slug}[/bold] · {agent} · {model} · k={k} → "
        f"[cyan]{plan.total}[/cyan] trials at concurrency {concurrency}"
    )

    worker = TrialWorker(settings)
    worker.settings.worker.max_containers = concurrency
    worker.budget = __import__(
        "autobench.worker.scheduler", fromlist=["ConcurrencyBudget"]
    ).ConcurrencyBudget(
        max_containers=concurrency,
        max_docker_ops=min(concurrency, settings.worker.max_docker_ops),
        max_verifiers=max(1, min(concurrency, settings.worker.max_verifiers)),
    )

    with console.status("[cyan]running trials…[/cyan]") as status:

        async def tick() -> None:
            while True:
                await asyncio.sleep(1.0)
                status.update(
                    f"[cyan]{worker.stats.completed}/{plan.total} trials · "
                    f"{worker.stats.passes} passed · {worker.stats.infra_errors} infra[/cyan]"
                )

        ticker = asyncio.create_task(tick())
        try:
            await worker.run_forever(run_id=run_id, drain=True)
        finally:
            ticker.cancel()
            await worker.shutdown()

    async with session_scope(settings) as session:
        completed_run = await session.get(Run, run_id)
        assert completed_run is not None
        run_row = completed_run
        assert run_row is not None
        if run_row.trials_done >= run_row.trials_total:
            run_row.status = RunStatus.COMPLETED.value
        aggregate = await aggregate_for_run(session, run_row, settings)
        from autobench.api.services import error_breakdown, run_matrix

        matrix = await run_matrix(session, run_row)
        errors = await error_breakdown(session, run_id)
        cost = float(run_row.cost_usd or 0)

    await dispose_engine()
    return {
        "run_id": str(run_id),
        "suite": suite_row.slug,
        "agent": agent,
        "model": model,
        "k": k,
        "concurrency": concurrency,
        "cost_usd": round(cost, 4),
        "metrics": aggregate.to_json(),
        "errors": errors,
        "rows": [
            {
                "task_id": row["task_id"],
                "passes": row["passes"],
                "attempts": row["attempts"],
                "cells": [
                    {
                        "status": c["status"],
                        "reward": c["reward"],
                        "binary_pass": c["binary_pass"],
                        "error_class": c["error_class"],
                    }
                    for c in row["cells"]
                ],
            }
            for row in matrix["rows"]
        ],
    }


def _print_run_result(result: dict[str, Any]) -> None:
    metrics = result["metrics"]

    header = Table.grid(padding=(0, 3))
    header.add_column(justify="left")
    header.add_column(justify="left")
    header.add_column(justify="left")
    header.add_column(justify="left")
    header.add_row(
        "[dim]pass@1[/dim]",
        f"[dim]pass@{metrics['k']}[/dim]",
        f"[dim]pass^{metrics['k']}[/dim]",
        "[dim]reliability gap[/dim]",
    )
    header.add_row(
        f"[bold]{metrics['pass_at_1']['point']:.1%}[/bold]",
        f"[bold]{metrics['pass_at_k']['point']:.1%}[/bold]",
        f"[bold]{metrics['pass_caret_k']['point']:.1%}[/bold]",
        f"[bold yellow]{metrics['reliability_gap'] * 100:.1f} pts[/bold yellow]",
    )
    header.add_row(
        f"±{metrics['pass_at_1']['margin']:.1%}",
        f"±{metrics['pass_at_k']['margin']:.1%}",
        f"±{metrics['pass_caret_k']['margin']:.1%}",
        "[dim]← the number that matters[/dim]",
    )
    console.print(Panel(header, title=f"[bold]{result['suite']}[/bold] · {result['model']}"))

    grid = Table(box=None, show_header=True, header_style="dim")
    grid.add_column("task", style="bold", overflow="fold")
    grid.add_column("attempts", justify="left")
    grid.add_column("", justify="right")
    for row in result["rows"]:
        cells = "".join(_cell_glyph(c) for c in row["cells"])
        grid.add_row(row["task_id"], cells, f"{row['passes']}/{row['attempts']}")
    console.print(grid)
    console.print("[dim]██ pass  ▓▓ partial  ░░ fail  ⟳ infra retry  ⋯ queued  ⊘ error[/dim]")

    if result["errors"]:
        console.print(f"\n[yellow]errors:[/yellow] {result['errors']}")
    console.print(
        f"\n{metrics['total_passes']}/{metrics['total_trials']} trials passed · "
        f"${result['cost_usd']:.4f} · mean reward {metrics['mean_reward']:.3f}"
    )
    if not metrics["reportable"]:
        console.print(
            "[yellow]k<3: indicative, not reportable.[/yellow] A single attempt per task "
            "measures one sample of a stochastic process."
        )


def _cell_glyph(cell: dict[str, Any]) -> str:
    if cell.get("error_class", "") and str(cell["error_class"]).startswith("infra_"):
        return "[magenta]⟳ [/magenta]"
    if cell["status"] in {"queued", "leased"}:
        return "[dim]⋯ [/dim]"
    if cell["status"] == "error":
        return "[magenta]⊘ [/magenta]"
    if cell.get("binary_pass"):
        return "[green]██[/green]"
    if cell.get("reward") and float(cell["reward"]) > 0:
        return "[yellow]▓▓[/yellow]"
    return "[red]░░[/red]"


# ---------------------------------------------------------------------------
# bench-harness
# ---------------------------------------------------------------------------


@app.command("bench-harness")
def bench_harness(
    suite: Annotated[Path, typer.Option("--suite", help="Directory of tasks to benchmark.")] = Path(
        "tasks"
    ),
    agent: Annotated[
        str, typer.Option("--agent", help="oracle isolates harness time from model variance.")
    ] = "oracle",
    model: Annotated[
        str | None, typer.Option("--model", help="Also measure end-to-end with a real model.")
    ] = None,
    concurrency: Annotated[str, typer.Option("--concurrency", "-c")] = "1,2,4,8",
    repeats: Annotated[int, typer.Option("--repeats", "-r")] = 3,
    limit: Annotated[int | None, typer.Option("--limit", help="Cap the task count.")] = None,
    out: Annotated[Path | None, typer.Option("--out")] = None,
    markdown: Annotated[
        bool, typer.Option("--markdown", help="Also print the README table.")
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Measure the parallel speedup, reproducibly.

    Using the **oracle** agent is the methodological key: it replays solve.sh
    deterministically, so every millisecond measured is harness overhead rather
    than model sampling noise. Run it again with --model for the end-to-end
    figure, reported separately.
    """
    settings = _configure(verbose)
    from autobench.cli.bench import render_markdown, run_bench, write_result

    levels = sorted({int(part) for part in concurrency.split(",") if part.strip()})
    if not levels:
        raise typer.BadParameter("--concurrency needs at least one level, e.g. 1,2,4,8")

    def progress(level: int, repeat: int, total: int) -> None:
        console.print(f"[dim]  N={level} repeat {repeat}/{total}…[/dim]")

    console.print(
        f"[bold]bench-harness[/bold] suite={suite} agent={agent} "
        f"concurrency={levels} repeats={repeats}"
    )
    result = asyncio.run(
        run_bench(
            settings,
            tasks_dir=suite,
            concurrencies=levels,
            repeats=repeats,
            agent=agent,
            model=model,
            limit=limit,
            progress=progress,
        )
    )

    table = Table(title="Harness benchmark", header_style="bold")
    table.add_column("N", justify="right")
    table.add_column("wall (s)", justify="right")
    table.add_column("speedup", justify="right")
    table.add_column("efficiency", justify="right")
    table.add_column("p50 (s)", justify="right")
    table.add_column("p95 (s)", justify="right")
    table.add_column("429", justify="right")
    table.add_column("infra", justify="right")
    table.add_column("bottleneck")
    for point in result.points:
        table.add_row(
            str(point.concurrency),
            f"{point.wall_clock_sec:.1f}",
            f"{point.speedup:.2f}x",
            f"{point.efficiency:.0%}",
            f"{point.p50_trial_sec:.2f}",
            f"{point.p95_trial_sec:.2f}",
            f"{point.throttle_rate:.1%}",
            str(point.infra_errors),
            point.bottleneck or "[dim]none[/dim]",
        )
    console.print(table)

    passed, reason = result.meets_acceptance()
    console.print(
        Panel(
            f"{result.headline}\n\n"
            f"Acceptance (>=40% at N=8): {PASS if passed else FAIL} — {reason}",
            title="[bold]G2[/bold]",
        )
    )

    target = out or Path("bench") / f"harness-{result.started_at[:10]}-{agent}.json"
    written = write_result(result, target)
    console.print(f"[dim]written to {written}[/dim]")
    if markdown:
        console.print(render_markdown(result))


# ---------------------------------------------------------------------------
# gate
# ---------------------------------------------------------------------------


@app.command()
def gate(
    baseline: Annotated[str, typer.Option("--baseline", help="Baseline run id or label.")],
    candidate: Annotated[str, typer.Option("--candidate", help="Candidate run id or label.")],
    max_regression: Annotated[float, typer.Option("--max-regression")] = 0.03,
    allow_inconclusive: Annotated[
        bool,
        typer.Option(
            "--allow-inconclusive/--strict", help="Pass when the interval straddles zero (default)."
        ),
    ] = True,
    json_out: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Exit non-zero when a suite's score drops beyond the threshold.

    Statistically honest by default: when the delta interval straddles zero the
    gate passes and says why. Blocking a merge on a two-task wobble trains
    people to ignore the gate, which is worse than not having one.
    """
    settings = _configure()

    async def go() -> dict[str, Any]:
        from autobench.api.services import run_outcomes
        from autobench.db.session import dispose_engine, session_scope
        from autobench.scoring.aggregate import compare_runs, gate_regression

        async with session_scope(settings) as session:
            base = await _resolve_run(session, baseline)
            cand = await _resolve_run(session, candidate)
            if base is None or cand is None:
                missing = baseline if base is None else candidate
                raise typer.BadParameter(f"no run matching {missing!r}")
            comparison = compare_runs(
                await run_outcomes(session, base.id),
                await run_outcomes(session, cand.id),
                confidence=settings.confidence_level,
                resamples=settings.bootstrap_resamples,
                seed=base.seed,
            )
        await dispose_engine()
        verdict = gate_regression(
            comparison,
            max_regression=max_regression,
            require_significance=allow_inconclusive,
        )
        return {
            "passed": verdict.passed,
            "reason": verdict.reason,
            "baseline_score": round(verdict.baseline_score, 4),
            "candidate_score": round(verdict.candidate_score, 4),
            "delta": round(verdict.delta, 4),
            "regressed_tasks": verdict.regressed_tasks,
            "comparison": comparison.to_json(),
        }

    result = asyncio.run(go())
    if json_out:
        console.print_json(json.dumps(result))
    else:
        console.print(
            Panel(
                f"{result['comparison']['headline']}\n\n"
                f"baseline {result['baseline_score']:.1%} → candidate "
                f"{result['candidate_score']:.1%} ({result['delta']:+.1%})\n"
                f"{result['reason']}",
                title=PASS if result["passed"] else FAIL,
            )
        )
        if result["regressed_tasks"]:
            console.print(f"[red]regressed:[/red] {', '.join(result['regressed_tasks'][:20])}")
    raise typer.Exit(0 if result["passed"] else 1)


async def _resolve_run(session: Any, reference: str) -> Any:
    from sqlalchemy import select

    from autobench.db.models import Run

    try:
        found = await session.get(Run, UUID(reference))
        if found is not None:
            return found
    except (ValueError, AttributeError):
        pass
    return (
        await session.execute(
            select(Run).where(Run.label == reference).order_by(Run.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# long-running processes and utilities
# ---------------------------------------------------------------------------


@app.command()
def serve(
    host: Annotated[str | None, typer.Option("--host")] = None,
    port: Annotated[int | None, typer.Option("--port")] = None,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    """Run the control plane (FastAPI + SSE)."""
    settings = _configure()
    import uvicorn

    uvicorn.run(
        "autobench.api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_level=settings.log_level.lower(),
        access_log=settings.environment == "development",
    )


@app.command()
def worker(
    drain: Annotated[bool, typer.Option("--drain", help="Exit when the queue empties.")] = False,
    concurrency: Annotated[int | None, typer.Option("--concurrency", "-c")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Run a worker: lease trials, execute them, persist results."""
    settings = _configure(verbose)
    if concurrency:
        settings.worker.max_containers = concurrency

    from autobench.worker.runner import TrialWorker

    async def go() -> None:
        instance = TrialWorker(settings)
        console.print(
            f"[bold]worker[/bold] {str(instance.id)[:8]} · executor={settings.executor} · "
            f"lanes={ {k: v['capacity'] for k, v in instance.budget.snapshot().items()} }"
        )
        try:
            stats = await instance.run_forever(drain=drain)
            console.print(f"[dim]{stats}[/dim]")
        except KeyboardInterrupt:
            console.print("\n[yellow]stopping; in-flight trials will finish[/yellow]")
        finally:
            await instance.shutdown()

    asyncio.run(go())


@app.command()
def seed(
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Create the schema and register the corpus, agents, models and suites."""
    settings = _configure(verbose)

    async def go() -> dict[str, int]:
        from autobench.cli.bootstrap import bootstrap
        from autobench.db.session import dispose_engine, init_models, session_scope

        await init_models(settings)
        async with session_scope(settings) as session:
            counts = await bootstrap(session, settings)
        await dispose_engine()
        return counts

    counts = asyncio.run(go())
    console.print(f"[green]seeded[/green] {counts}")


@app.command()
def replay(
    trial: Annotated[str, typer.Option("--trial", help="Trial id, full or short.")],
    interactive: Annotated[bool, typer.Option("--interactive", "-i")] = False,
) -> None:
    """Print the commands that reproduce a trial locally."""
    settings = _configure()

    async def go() -> dict[str, Any]:
        from sqlalchemy import select

        from autobench.db.models import TaskVersion, Trial
        from autobench.db.session import dispose_engine, session_scope
        from autobench.executor.base import SandboxSpec
        from autobench.executor.docker_provider import replay_commands
        from autobench.taskspec.loader import load_task

        async with session_scope(settings) as session:
            rows = (
                await session.execute(
                    select(Trial, TaskVersion).join(
                        TaskVersion, TaskVersion.id == Trial.task_version_id
                    )
                )
            ).all()
            match = next(
                (
                    r
                    for r in rows
                    if str(r[0].id).replace("-", "").startswith(trial.replace("-", ""))
                ),
                None,
            )
            if match is None:
                raise typer.BadParameter(f"no trial matching {trial!r}")
            trial_row, version = match
            source_path = version.source_path
            if not source_path:
                raise typer.BadParameter("The recorded task source is unavailable for replay.")
            task = load_task(source_path)
            if task.content_hash != version.content_hash:
                raise typer.BadParameter(
                    "Task source has changed; restore the recorded version before replay."
                )
            image = version.image_digest or task.image_tag(settings.image_namespace)
            commands = replay_commands(
                task,
                image,
                SandboxSpec.from_task(task, image, seed=trial_row.seed),
                trial_id=str(trial_row.id)[:8],
            )
        await dispose_engine()
        return {"task_id": version.task_id, "seed": trial_row.seed, "commands": commands}

    result = asyncio.run(go())
    console.print(
        Panel(
            "\n".join(result["commands"]),
            title=f"[bold]replay {result['task_id']}[/bold] (seed {result['seed']})",
        )
    )
    if interactive:
        console.print("[dim]Run the second command to land in a shell inside the task image.[/dim]")


@app.command("keys")
def keys(
    create: Annotated[str | None, typer.Option("--create", help="Name for a new key.")] = None,
    scopes: Annotated[str, typer.Option("--scopes")] = "read,run",
) -> None:
    """Create an API key. The plaintext is shown exactly once."""
    settings = _configure()
    if not create:
        console.print("[yellow]use --create NAME[/yellow]")
        raise typer.Exit(2)

    async def go() -> str:
        from autobench.core.security import generate_api_key
        from autobench.db.models import ApiKey
        from autobench.db.session import dispose_engine, init_models, session_scope

        await init_models(settings)
        plaintext, prefix, hashed = generate_api_key()
        async with session_scope(settings) as session:
            session.add(
                ApiKey(
                    name=create,
                    prefix=prefix,
                    key_hash=hashed,
                    scopes=[s.strip() for s in scopes.split(",") if s.strip()],
                )
            )
        await dispose_engine()
        return plaintext

    plaintext = asyncio.run(go())
    console.print(Panel(plaintext, title=f"[bold]{create}[/bold] — shown once, not stored"))


if __name__ == "__main__":  # pragma: no cover
    app()
