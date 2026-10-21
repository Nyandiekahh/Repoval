import pytest

from repoval.validator import validate_task


@pytest.mark.parametrize("tid", ["slugify-max-length", "ledger-split-cents", "cron-inclusive-range", "ttl-expiry-boundary"])
def test_shipped_tasks_are_valid(good_tasks, tid):
    rep = validate_task(good_tasks[tid], repeat=2)
    assert rep.verdict == "VALID", [i.to_dict() for i in rep.issues]


@pytest.mark.parametrize("tid,code", [
    ("bad-vacuous-f2p", "F2P_PASS_BEFORE"),
    ("bad-flaky-p2p", "FLAKY"),
    ("bad-gold-edits-tests", "GOLD_EDITS_TESTS"),
    ("bad-missing-test-id", "MISSING_TEST"),
    ("bad-incomplete-gold", "F2P_FAIL_AFTER"),
])
def test_broken_tasks_are_rejected_for_the_right_reason(bad_tasks, tid, code):
    rep = validate_task(bad_tasks[tid], repeat=3)
    assert rep.verdict == "INVALID"
    assert code in {i.code for i in rep.errors}


def test_leaky_statement_is_a_warning_not_an_error(bad_tasks):
    rep = validate_task(bad_tasks["warn-leaky-statement"], repeat=1)
    assert rep.verdict == "WARN"
    assert [i.code for i in rep.warnings] == ["LEAK_STATEMENT"]


def test_single_run_cannot_see_hash_flakiness(bad_tasks):
    """Documents a real limitation: with repeat=1 only seed 0 runs, so the flake hides."""
    rep = validate_task(bad_tasks["bad-flaky-p2p"], repeat=1)
    assert "FLAKY" not in {i.code for i in rep.issues}
