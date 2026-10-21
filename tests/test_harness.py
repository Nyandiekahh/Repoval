import json

import pytest

from repoval.harness import RunStore, grade, regrade, run_eval
from repoval.prompts import build_prompt
from repoval.providers import AnthropicProvider, Completion, OracleProvider, Provider, SimProvider, make_provider


def fenced(patch):
    return f"```diff\n{patch}```"


def test_gold_patch_resolves_every_shipped_task(good_tasks):
    for t in good_tasks.values():
        assert grade(t, fenced(t.gold_patch)).outcome == "RESOLVED", t.instance_id


def test_garbage_and_empty_do_not_resolve(good_tasks):
    t = good_tasks["ledger-split-cents"]
    assert grade(t, "no idea").outcome == "NO_PATCH"
    assert grade(t, "").outcome == "NO_PATCH"


def test_patch_that_does_not_apply(good_tasks):
    t = good_tasks["ledger-split-cents"]
    bad = t.gold_patch.replace("share = round(total_cents / parts)", "share = something_else")
    r = grade(t, fenced(bad))
    assert r.outcome == "APPLY_FAILED"


def test_wrong_fix_is_failed_tests_and_counts_partial_progress(good_tasks):
    t = good_tasks["ledger-split-cents"]
    # Plausible but wrong: rounds up instead of distributing the remainder.
    wrong = t.gold_patch.replace(
        "base, extra = divmod(total_cents, parts)", "base, extra = -(-total_cents // parts), 0")
    r = grade(t, fenced(wrong))
    assert r.outcome == "FAILED_TESTS"
    assert 0 <= r.f2p_passed < r.f2p_total


def test_syntax_error_is_broken_build(good_tasks):
    t = good_tasks["ledger-split-cents"]
    broken = t.gold_patch.replace("return [base + 1", "return [[[base + 1")
    assert grade(t, fenced(broken)).outcome == "BROKEN_BUILD"


def test_regression_is_distinguished_from_failure(good_tasks):
    import difflib
    t = good_tasks["ledger-split-cents"]
    src = (t.repo_dir / "ledger/money.py").read_text().splitlines(keepends=True)
    # Fixes the sums but drops the ValueError guard that an existing test pins.
    fixed = [l for l in src if "parts <= 0" not in l and "parts must be positive" not in l]
    body = "".join(fixed).replace(
        "        share = round(total_cents / parts)\n        return [share] * parts\n",
        "        base, extra = divmod(total_cents, parts)\n"
        "        return [base + 1 if i < extra else base for i in range(parts)]\n")
    body = body.replace("    share = round(total_cents / parts)\n    return [share] * parts\n",
                        "    base, extra = divmod(total_cents, parts)\n"
                        "    return [base + 1 if i < extra else base for i in range(parts)]\n")
    patch = "".join(difflib.unified_diff(src, body.splitlines(keepends=True), "a/ledger/money.py", "b/ledger/money.py"))
    r = grade(t, fenced(patch))
    assert r.outcome == "REGRESSION", (r.outcome, r.detail)
    assert r.broken_p2p == ["tests.test_money::test_rejects_zero_parts"]


def test_editing_tests_is_ignored_not_rewarded(good_tasks):
    t = good_tasks["cron-inclusive-range"]
    cheat = ("--- a/tests/test_fields.py\n+++ b/tests/test_fields.py\n@@ -1,2 +1,2 @@\n"
             " from cron_lite import expand_field\n-\n+# cheating\n")
    r = grade(t, fenced(cheat))
    assert r.outcome == "NO_PATCH" and r.dropped_test_files == ["tests/test_fields.py"]
    # ...and mixing a real fix with a test edit keeps the fix, drops the edit.
    r = grade(t, fenced(t.gold_patch + cheat))
    assert r.outcome == "RESOLVED" and r.dropped_test_files


def test_infinite_loop_times_out(good_tasks):
    from dataclasses import replace
    t = replace(good_tasks["cron-inclusive-range"], timeout=3)
    hang = t.gold_patch.replace("+        values.update(range(start, end + 1, step))  # cron ranges are inclusive on both ends",
                                "+        while True:\n+            pass")
    r = grade(t, fenced(hang))
    assert r.outcome == "TIMEOUT"


def test_prompt_never_leaks_the_answer_and_ranks_relevant_file_first(good_tasks):
    t = good_tasks["ledger-split-cents"]
    p = build_prompt(t)
    assert "divmod" not in p.user                     # gold fix absent
    assert "tests.test_money" not in p.user           # declared ids absent
    assert "ledger/money.py" in p.user and "Issue" in p.user
    assert "test_even_split" not in p.user            # existing tests are not shown as source
    tiny = build_prompt(t, char_budget=10)
    assert "ledger/money.py" not in tiny.user         # budget respected


def test_oracle_and_null_calibrate_the_harness(tmp_path, good_tasks):
    tasks = list(good_tasks.values())
    run_eval(tasks, OracleProvider(), RunStore(tmp_path / "o"), workers=4)
    res = RunStore(tmp_path / "o").results()
    assert len(res) == 4 and {r["outcome"] for r in res} == {"RESOLVED"}
    run_eval(tasks, make_provider("null"), RunStore(tmp_path / "n"))
    assert {r["outcome"] for r in RunStore(tmp_path / "n").results()} == {"NO_PATCH"}


def test_sim_provider_is_reproducible(good_tasks):
    t = good_tasks["slugify-max-length"]
    a = [SimProvider(0.5).generate(t, None, i).text for i in range(8)]
    b = [SimProvider(0.5).generate(t, None, i).text for i in range(8)]
    assert a == b and len(set(a)) > 1


class Flaky(Provider):
    name = "flaky"

    def __init__(self):
        self.calls = 0

    def generate(self, task, prompt, sample):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("503 from upstream")
        return Completion(fenced(task.gold_patch))


def test_provider_errors_are_not_scored_as_model_failures_and_are_retried_on_resume(tmp_path, good_tasks):
    tasks = [good_tasks["cron-inclusive-range"]]
    store = RunStore(tmp_path / "r")
    prov = Flaky()
    run_eval(tasks, prov, store, workers=1)
    assert store.results() == []                       # infra error left no verdict behind
    run_eval(tasks, prov, store, workers=1)            # "rerun the same command"
    assert [r["outcome"] for r in store.results()] == ["RESOLVED"]
    n = prov.calls
    run_eval(tasks, prov, store, workers=1)            # nothing left to do
    assert prov.calls == n


def test_regrade_rescores_recorded_completions_without_calling_the_model(tmp_path, good_tasks):
    tasks = list(good_tasks.values())
    run_eval(tasks, OracleProvider(), RunStore(tmp_path / "a"))
    out = RunStore(tmp_path / "b")
    regrade({t.instance_id: t for t in tasks}, RunStore(tmp_path / "a"), out)
    assert {r["outcome"] for r in out.results()} == {"RESOLVED"}
    assert out.load_config()["regraded_from"].endswith("a")


def test_anthropic_provider_parses_response_with_a_fake_transport(good_tasks):
    seen = {}

    def transport(req):
        seen["body"] = json.loads(req.data)
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return json.dumps({"content": [{"type": "text", "text": "hello"}],
                           "usage": {"input_tokens": 11, "output_tokens": 3}}).encode()

    t = good_tasks["ledger-split-cents"]
    c = AnthropicProvider("some-model", transport=transport).generate(t, build_prompt(t), 0)
    assert c.text == "hello" and c.usage == {"input_tokens": 11, "output_tokens": 3}
    assert seen["body"]["model"] == "some-model" and seen["headers"]["anthropic-version"] == "2023-06-01"


def test_unknown_provider_spec():
    with pytest.raises(ValueError):
        make_provider("gpt-nonsense")
