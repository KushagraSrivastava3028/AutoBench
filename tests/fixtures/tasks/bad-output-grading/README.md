# bad-output-grading (negative fixture)

Expected: the `state-based` gate and the `tolerance` gate both **fail**.

* `assert "SUCCESS" in agent_stdout` grades what the agent said.
* `assert measured == 96.124991` is the CORE-Bench failure: a correctly
  rounded `96.12` would be marked wrong.
