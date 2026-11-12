from autobench.scoring.aggregate import TaskOutcome, aggregate_run


def test_repeated_attempts_do_not_claim_independent_task_evidence():
    result = aggregate_run([TaskOutcome("easy", 100, 100), TaskOutcome("hard", 100, 0)], 5)
    assert result.pass_at_1.point == 0.5
    assert result.pass_at_1.low <= result.pass_at_1.point <= result.pass_at_1.high
    assert result.pass_at_1.high - result.pass_at_1.low > 0.4
    assert result.pass_at_1.method.startswith("bootstrap")


def test_incomplete_attempts_cannot_be_reported_as_requested_k():
    result = aggregate_run([TaskOutcome("incomplete", 2, 2), TaskOutcome("complete", 3, 0)], 3)
    assert result.pass_at_k.point == 0.0
    assert result.pass_caret_k.point == 0.0
    assert not result.reportable


def test_infrastructure_only_run_preserves_its_invalidity_evidence():
    result = aggregate_run([TaskOutcome("broken", 0, 0, infra_errors=5)], 5)
    assert result.infra_errors == 5
    assert result.total_trials == 0
    assert not result.reportable
