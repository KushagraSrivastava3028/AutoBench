# Authoring a task

A task is a directory. Harbor's layout is read unmodified; Autobench only adds optional keys.

```
my-task/
├── task.toml              metadata, limits, scoring
├── instruction.md         the prompt the agent receives
├── environment/
│   ├── Dockerfile         the world the agent starts in
│   ├── seed/              files copied into /app
│   └── setup.sh           optional; runs before the agent
├── tests/
│   ├── run-tests.sh       entrypoint; writes the JSON report
│   └── check.py
└── solution/solve.sh      the oracle; must score exactly 1.0
```

Validate as you go:

```bash
autobench validate tasks/my-task          # static gates, under a second
autobench validate tasks/my-task --deep   # plus oracle/null/hermetic/determinism
```

## The four rules

### 1. Grade the world, not the words

Assert on the **final container state**.

```python
# ✗ grades prose. The state-based gate rejects this.
assert "SUCCESS" in agent_stdout

# ✓ grades the world
assert (APP / "done.txt").read_text() == "ok"
```

### 2. Declare tolerances

```python
# ✗ rejects a correctly rounded 96.12. The tolerance gate rejects this.
assert value == 96.124991

# ✓
assert math.isclose(value, 96.124991, rel_tol=1e-3)
```

### 3. Don't quote the grader

If `instruction.md` shares more than 15% of its 8-grams with `tests/`, the leak gate fails. Describe the goal in your own words.

### 4. Oracle = 1.0, null = 0.0

The oracle must score exactly 1.0. A lower score means the task is unsolvable as written. The null agent must score exactly 0.0. A higher score means the starting state already satisfies part of the grader.

## Write portable paths

Reference locations through the `AUTOBENCH_*` variables, so the same task runs under both executors:

```bash
APP="${AUTOBENCH_WORKDIR:-/app}"
printf 'ok' > "$APP"/done.txt
```

```python
APP = Path(os.environ.get("AUTOBENCH_WORKDIR", "/app"))
subprocess.run([sys.executable, str(APP / "tool.py")])   # sys.executable, not "python3"
```

| Variable | In a container | Meaning |
|---|---|---|
| `AUTOBENCH_WORKDIR` | `/app` | the workspace |
| `AUTOBENCH_TESTS_DIR` | `/opt/tests` | tests (verifier only) |
| `AUTOBENCH_REPORT_PATH` | `/tmp/autobench-report.json` | where to write the report |
| `AUTOBENCH_SEED` | per trial | seed your RNGs from this |

## The score report

`run-tests.sh` writes this JSON to `$AUTOBENCH_REPORT_PATH`:

```json
{
  "items": [
    {"id": "retry_is_deterministic", "passed": true,  "weight": 0.5, "detail": "20/20"},
    {"id": "no_sleep_in_hot_path",   "passed": false, "weight": 0.5, "detail": "retry.py:88"}
  ]
}
```

The reward is recomputed from `items`, and the items take precedence over any self-reported total. The exit code is used only when no report exists. Every item ID must match an entry in `[[scoring.items]]`; a mismatch is reported on the verdict.

## Pin the base image

```bash
make pin-images   # rewrites FROM lines to @sha256 digests from your daemon
```

The image gate fails an unpinned `FROM`. Re-pinning changes the content hash, which creates a new task version. That is correct, because the environment has changed.

## Refused at publish time

These are hard failures with no override: a Docker socket mount, `privileged`, host networking, `CAP_SYS_ADMIN`/`SYS_PTRACE`/`NET_ADMIN`, bind mounts outside `/app`, and anything that looks like a credential in `environment.env`. See [security.md](security.md).

## Reference examples

- `tests/fixtures/tasks/always-pass`: the smallest valid task
- `tasks/_reference/fix-flaky-retry`: weighted scoring with an AST check
- `tests/fixtures/tasks/bad-output-grading`: what the lints reject, and why
