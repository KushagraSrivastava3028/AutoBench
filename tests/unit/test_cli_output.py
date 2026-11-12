"""Machine-readable CLI output stays separate from execution diagnostics."""

import json

from typer.testing import CliRunner


def test_run_json_keeps_first_run_and_progress_messages_on_stderr(monkeypatch):
    from autobench.cli import main

    expected = {"run_id": "test-run", "metrics": {"total_trials": 1}}

    async def run_suite(*args):
        main.console.print("seeded: agents and reference tasks")
        main.console.print("core-reference: 1 trial at concurrency 1")
        return expected

    monkeypatch.setattr(main, "_configure", lambda verbose: object())
    monkeypatch.setattr(main, "_run_suite", run_suite)
    result = CliRunner().invoke(main.app, ["run", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == expected
    assert "seeded:" in result.stderr
    assert "1 trial" in result.stderr
