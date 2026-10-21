"""Task validation: decide whether an eval instance is trustworthy *before*
anyone spends tokens on it.

A bad instance silently poisons a benchmark: a "fail-to-pass" test that already
passes rewards doing nothing, a flaky test rewards luck, a problem statement that
quotes the fix rewards copying. Each check below exists because that failure mode
shows up in real datasets.

Issue codes
-----------
errors (task is unusable)
  GOLD_APPLY       gold patch does not apply to the base snapshot
  TEST_APPLY       test patch does not apply to the base snapshot
  GOLD_EDITS_TESTS gold patch modifies test files (would mask the grader)
  TEST_PATCH_SRC   test patch modifies non-test files (smuggled source change)
  NO_F2P           no fail-to-pass tests declared
  F2P_PASS_BEFORE  declared F2P test already passes before the fix (vacuous)
  F2P_FAIL_AFTER   declared F2P test still fails with the gold patch
  P2P_FAIL_BEFORE  declared P2P test fails before the fix (not a regression guard)
  P2P_FAIL_AFTER   gold patch breaks a P2P test
  MISSING_TEST     declared test id never ran (typo, renamed, or collection error)
  TIMEOUT          test command exceeded its time limit
  FLAKY            a declared test gave different outcomes across repeated runs
warnings (worth a human look)
  LEAK_STATEMENT   problem statement contains lines copied from the gold patch
  UNDECLARED_F2P   a test flips fail->pass but is not declared in F2P
  SLOW             a single run took longer than half the time budget
"""
from __future__ import annotations

import re
import time
from typing import List, Set

from .models import Issue, Task, TestRun, ValidationReport
from .patching import apply_patch, is_test_path, touched_files
from .sandbox import run_tests, sandbox

_LEAK_MIN_CHARS = 24
_TRIVIAL = re.compile(r"^[\s\W]*$|^(return|pass|else:|try:|except.*:|import .*|from .* import .*)$")


def validate_task(task: Task, repeat: int = 3) -> ValidationReport:
    """Run every check. ``repeat`` is how many times the *fixed* tree is executed
    (each with a different PYTHONHASHSEED) to smoke out order/hash flakiness."""
    started = time.monotonic()
    rep = ValidationReport(instance_id=task.instance_id)
    add = lambda code, sev, msg: rep.issues.append(Issue(code, sev, msg))  # noqa: E731

    # --- static checks on the patches (cheap, so they run first) -------------------
    for f in touched_files(task.gold_patch):
        if is_test_path(f):
            add("GOLD_EDITS_TESTS", "error", f"gold patch edits test file {f}")
    for f in touched_files(task.test_patch):
        if not is_test_path(f):
            add("TEST_PATCH_SRC", "error", f"test patch edits non-test file {f}")
    if not task.fail_to_pass:
        add("NO_F2P", "error", "task declares no fail_to_pass tests")
    leaked = _leaked_lines(task)
    if leaked:
        add("LEAK_STATEMENT", "warn",
            f"problem statement repeats {len(leaked)} line(s) of the gold patch, e.g. {leaked[0]!r}")

    declared_f2p, declared_p2p = set(task.fail_to_pass), set(task.pass_to_pass)

    # --- state A: base + test patch (the bug is present, the tests are new) -------
    with sandbox(task.repo_dir) as work:
        ok, err = apply_patch(work, task.test_patch)
        if not ok:
            add("TEST_APPLY", "error", f"test patch does not apply: {err}")
            return _finish(rep, started)
        before = run_tests(task, work)
    rep.before = before
    if before.timed_out:
        add("TIMEOUT", "error", f"base run exceeded {task.timeout}s")
        return _finish(rep, started)

    # --- state B: base + test patch + gold patch, repeated -----------------------
    afters: List[TestRun] = []
    for i in range(max(1, repeat)):
        with sandbox(task.repo_dir) as work:
            if not apply_patch(work, task.test_patch)[0]:
                add("TEST_APPLY", "error", "test patch failed on re-apply")
                return _finish(rep, started)
            ok, err = apply_patch(work, task.gold_patch)
            if not ok:
                add("GOLD_APPLY", "error", f"gold patch does not apply: {err}")
                return _finish(rep, started)
            run = run_tests(task, work, hash_seed=i)
        afters.append(run)
        if run.timed_out:
            add("TIMEOUT", "error", f"fixed run #{i + 1} exceeded {task.timeout}s")
            return _finish(rep, started)
    rep.after = afters

    # --- judge the declared tests against what we observed ----------------------
    seen_ids = set(before.outcomes) | {t for r in afters for t in r.outcomes}
    for tid in sorted((declared_f2p | declared_p2p) - seen_ids):
        add("MISSING_TEST", "error", f"declared test never ran: {tid}")

    first_after = afters[0]
    for tid in sorted(declared_f2p & seen_ids):
        b = before.outcomes.get(tid)
        if b == "passed":
            add("F2P_PASS_BEFORE", "error", f"{tid} passes without the fix (vacuous test)")
        if first_after.outcomes.get(tid) != "passed":
            add("F2P_FAIL_AFTER", "error",
                f"{tid} is {first_after.outcomes.get(tid)!r} even with the gold patch")
    for tid in sorted(declared_p2p & seen_ids):
        if before.outcomes.get(tid) != "passed":
            add("P2P_FAIL_BEFORE", "error", f"{tid} already fails before the fix")
        if first_after.outcomes.get(tid) != "passed":
            add("P2P_FAIL_AFTER", "error", f"gold patch breaks {tid}")

    # flake detection: any declared test whose outcome differs between repeats
    for tid in sorted((declared_f2p | declared_p2p) & seen_ids):
        outcomes: Set[str] = {r.outcomes.get(tid, "missing") for r in afters}
        if len(outcomes) > 1:
            add("FLAKY", "error",
                f"{tid} gave {sorted(outcomes)} across {len(afters)} runs with different hash seeds")

    flipped = {t for t in seen_ids
               if before.outcomes.get(t) in ("failed", "error") and first_after.outcomes.get(t) == "passed"}
    for tid in sorted(flipped - declared_f2p):
        add("UNDECLARED_F2P", "warn", f"{tid} flips fail->pass but is not listed in fail_to_pass")

    worst = max([before.duration] + [r.duration for r in afters])
    if worst > task.timeout / 2:
        add("SLOW", "warn", f"slowest run took {worst:.1f}s of a {task.timeout}s budget")
    return _finish(rep, started)


def _finish(rep: ValidationReport, started: float) -> ValidationReport:
    rep.seconds = time.monotonic() - started
    return rep


def _leaked_lines(task: Task) -> List[str]:
    """Lines *added* by the gold patch that appear verbatim in the problem text.

    Pasting a stack trace is fine; pasting the answer is not. Short or boilerplate
    lines are ignored so ``return x`` does not trigger a false alarm.
    """
    statement = re.sub(r"\s+", " ", task.problem_statement)
    hits = []
    for line in task.gold_patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            code = line[1:].strip()
            if len(code) >= _LEAK_MIN_CHARS and not _TRIVIAL.match(code):
                if re.sub(r"\s+", " ", code) in statement:
                    hits.append(code)
    return hits
