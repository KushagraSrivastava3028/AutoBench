# fix-flaky-retry

Author notes:

* The bug is a wall-clock deadline standing in for an attempt counter. Under
  load the deadline expires early and the function is retried fewer times.
* The `no_sleep_in_hot_path` item is an AST check, not a grep, so a comment
  mentioning `time.sleep` does not fail the task.
* Watch the oracle timing gate: pytest startup dominates, so `agent.timeout_sec`
  has to leave room.
