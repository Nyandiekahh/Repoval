import math

import pytest

from repoval.metrics import bootstrap_ci, mcnemar_exact, pass_at_k, summarise, wilson_interval


def test_wilson_matches_hand_computation():
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-3)
    assert hi == pytest.approx(0.7634, abs=1e-3)


def test_wilson_is_sane_at_the_edges():
    lo, hi = wilson_interval(0, 4)
    assert lo == 0.0 and 0.3 < hi < 0.55          # not the useless [0, 0] a normal approx gives
    lo, hi = wilson_interval(4, 4)
    assert hi == 1.0 and 0.45 < lo < 0.7
    assert wilson_interval(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize("n,c,k,expected", [
    (5, 2, 1, 0.4),                 # pass@1 is just the empirical rate
    (5, 2, 2, 1 - 3 / 10),          # 1 - C(3,2)/C(5,2)
    (5, 0, 3, 0.0),
    (5, 5, 3, 1.0),
    (4, 3, 2, 1.0),                 # fewer failures than k: cannot draw k failures
])
def test_pass_at_k(n, c, k, expected):
    assert pass_at_k(n, c, k) == pytest.approx(expected)


def test_pass_at_k_rejects_k_above_n():
    with pytest.raises(ValueError):
        pass_at_k(3, 1, 4)


def test_pass_at_k_is_monotone_in_k():
    vals = [pass_at_k(10, 3, k) for k in range(1, 11)]
    assert vals == sorted(vals) and vals[-1] == 1.0


def test_mcnemar_exact():
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(3, 3) == 1.0
    assert mcnemar_exact(0, 5) == pytest.approx(2 / 32)
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 1024)


def test_bootstrap_ci_brackets_the_mean_and_is_reproducible():
    vals = [1, 0, 1, 1, 0, 1, 0, 1, 1, 1]
    lo, hi = bootstrap_ci(vals)
    assert lo <= sum(vals) / len(vals) <= hi
    assert bootstrap_ci(vals) == (lo, hi)
    assert bootstrap_ci([]) == (0.0, 0.0)


def test_summarise_counts_and_skips_k_larger_than_samples():
    rows = [{"instance_id": "a", "outcome": "RESOLVED"}, {"instance_id": "a", "outcome": "NO_PATCH"},
            {"instance_id": "b", "outcome": "FAILED_TESTS"}, {"instance_id": "b", "outcome": "FAILED_TESTS"}]
    s = summarise(rows, ks=(1, 2, 5))
    assert s["pass@1"]["value"] == pytest.approx(0.25)
    assert s["pass@2"]["value"] == pytest.approx(0.5)
    assert "pass@5" not in s
    assert s["outcomes"]["FAILED_TESTS"] == 2
    assert math.isclose(s["sample_resolve_rate"]["value"], 0.25)
