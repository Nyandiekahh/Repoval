# repoval

Validate repo-level eval tasks, then use them to measure how well an LLM fixes bugs.

I built this around one question that sounds boring and turns out to be most of the job:
**how do you know an eval number means anything?** A SWE-bench-style score is only as
trustworthy as (a) the tasks behind it and (b) the harness that grades patches. `repoval`
has a tool for each, plus the statistics to report the result honestly.

- Pure Python standard library. No dependencies to audit, nothing to pin.
- 50 tests, plus a calibration check (below); a GitHub Actions workflow runs both on Python 3.10 and 3.12.

```
pip install -e ".[dev]"
repoval validate examples/tasks                       # are the tasks sound?
repoval run examples/tasks --provider oracle --out runs/oracle
repoval run examples/tasks --provider sim:0.5 -n 6 --out runs/sim
repoval compare runs/oracle runs/sim
```

## The two halves

### 1. `repoval validate`: audit tasks before spending tokens on them

A task is a repo snapshot with a bug, a `gold.patch` (the real fix), a `test.patch` (tests
that pin it), and lists of tests that must go fail→pass (`fail_to_pass`) or stay green
(`pass_to_pass`). Each way that can be quietly wrong has a check:

| code | what it catches |
|---|---|
| `F2P_PASS_BEFORE` | a "fail-to-pass" test that passes without the fix, so doing nothing scores |
| `F2P_FAIL_AFTER` / `P2P_FAIL_AFTER` | the gold patch doesn't actually satisfy the tests |
| `FLAKY` | a declared test whose result changes across runs (each run uses a different `PYTHONHASHSEED`) |
| `MISSING_TEST` | a declared test id that never ran (typo, rename, collection error) |
| `GOLD_EDITS_TESTS` / `TEST_PATCH_SRC` | a fix that rewrites the grader, or a test patch that smuggles in source changes |
| `LEAK_STATEMENT` (warn) | the issue text pastes lines of the gold patch |
| `UNDECLARED_F2P` (warn) | a test flips fail→pass but nobody listed it |
| `GOLD_APPLY` / `TEST_APPLY` / `TIMEOUT` / `SLOW` | the plumbing |

`examples/tasks_invalid/` has one deliberately broken task per check. Real output:

```
[FAIL] bad-flaky-p2p
        error FLAKY: tests.test_ordering::test_report_ordering_matches_author_machine gave ['failed', 'passed'] across 3 runs with different hash seeds
[FAIL] bad-incomplete-gold
        error F2P_FAIL_AFTER: tests.test_fields::test_star_includes_upper_bound is 'failed' even with the gold patch
[FAIL] bad-vacuous-f2p
        error F2P_PASS_BEFORE: tests.test_extra::test_lowercases passes without the fix (vacuous test)
[warn] warn-leaky-statement
        warn  LEAK_STATEMENT: problem statement repeats 1 line(s) of the gold patch, ...
```

The validator earned its keep while I was building this: my first version of the
`slugify-max-length` task failed it, because two of the tests I'd called fail-to-pass
already passed on the buggy code. They were characterisation tests; they moved to
`pass_to_pass`. (The history of that fix is the reason `tools/build_examples.py` has a comment
about it.)

### 2. `repoval run`: evaluate a model

```
prompt  →  provider  →  extract diff  →  drop test-file hunks  →  apply  →  sandboxed pytest  →  outcome
```

Every attempt ends in exactly one outcome, so a change in the headline number can be
explained rather than shrugged at:

`RESOLVED` · `FAILED_TESTS` · `REGRESSION` · `BROKEN_BUILD` · `APPLY_FAILED` · `NO_PATCH` · `TIMEOUT` · `PROVIDER_ERROR`

Providers (one method each, so adding one is ~20 lines):

| spec | use |
|---|---|
| `oracle` | returns the gold patch. A correct harness scores **100%** |
| `null` | returns nothing. A correct harness scores **0%** |
| `sim:P` | succeeds with probability P, otherwise fails in realistic ways (truncated diff, prose only, useless-but-valid patch, editing tests). Seeded and reproducible |
| `cmd:./model.sh` | pipes the prompt to any command |
| `anthropic:MODEL` | Messages API over `urllib` (retries 429/5xx with jittered backoff) |
| `replay:RUN_DIR` | replays recorded completions |

CI runs `oracle` and `null` against the example tasks. If a refactor ever breaks grading,
that is the cheapest possible alarm.

## Decisions worth explaining

- **Completions are saved before they are graded.** Grading is a pure function
  `grade(task, text)`. When I fix a grader bug, `repoval regrade` re-scores the recorded
  outputs for free instead of re-querying the model, and results from before/after the fix
  stay comparable.
- **Runs resume.** Results are appended per sample. Re-running the same command skips what
  is done. `PROVIDER_ERROR` is deliberately *not* persisted, so a 503 from an API is retried
  rather than recorded as "the model failed".
- **Test edits are ignored, not punished.** A model's hunks against test files are dropped
  and logged (`dropped_test_files`), so a response that fixes the bug *and* edits a test is
  still graded on the fix, while editing a test can never be the thing that makes it pass.
- **Test ids come from JUnit XML**, not from scraping terminal output, so they don't change
  with pytest's verbosity or version.
- **pass@k uses the unbiased estimator** (Chen et al. 2021), not "fraction of tasks with at
  least one hit among k". Intervals: Wilson for per-attempt rates (it behaves at 0%/100% and
  small n), percentile bootstrap *over tasks* for pass@k, because tasks are the unit that
  was sampled.
- **`compare` uses an exact McNemar test** on per-task discordant results. Two models
  scoring 62% vs 58% on 50 tasks is usually noise, and the tool says so rather than
  declaring a winner.
- **The patch extractor trusts hunk headers.** `@@ -a,b +c,d @@` says exactly how many lines
  belong to the hunk, so trailing commentary (even a line starting with `- `) can't leak into
  the diff, and a removed line that reads `--- foo` can't be mistaken for a file header.
- **Prompts never show the gold patch or declared test ids**, only the issue, the new tests'
  source, and repo files ranked by TF-IDF overlap with the issue under a character budget.
  A test asserts this.

## Layout

```
src/repoval/
  validator.py   task checks              sandbox.py    copy-on-run workdirs, process-group timeouts
  harness.py     grade() + resumable run  patching.py   diff extraction / splitting / apply
  providers.py   oracle, sim, cmd, API    metrics.py    Wilson, pass@k, bootstrap, McNemar
  prompts.py     context building         report.py     markdown / HTML
  mktask.py      before/after trees → task directory
examples/        4 valid tasks, 6 broken ones, 4 tiny repos
tools/build_examples.py   how the examples were produced
tests/           50 tests, including end-to-end authoring of a new task
```

Authoring your own task: edit a copy of a repo (fix the bug, add tests), then
`mktask.make_task(before, after, out_dir, ...)` splits the diff into `gold.patch` and
`test.patch` by path. Then `repoval validate` it. Validation is meant to be part of
authoring, not an afterthought.

## Limitations (stated plainly)

- **Not a security boundary.** The sandbox is a temp-dir copy with a scrubbed environment
  and a killable process group. Running untrusted model output at scale needs a container
  or VM around the whole tool. Network access is not blocked.
- **Python/pytest only.** The abstractions (task, test run, outcome) are language-agnostic;
  `sandbox.run_tests` and the JUnit parser are the parts to swap.
- **Flake detection is probabilistic.** It varies the hash seed and repeats runs; it won't
  catch time- or network-dependent flakes that happen to be stable over 3 runs. One of the
  tests documents that with `repeat=1`.
- **Small example repos.** They keep the whole suite at about 20 seconds; they are
  not a benchmark. Pointing `repoval` at real repos needs pinned environments per task
  (SWE-bench-style images), which is the natural next step.
- **Retrieval is a baseline.** TF-IDF file ranking is fine for toy repos and weak for big ones.
- The `anthropic` provider is exercised against a fake transport in tests, not a live API.

## Ideas I'd do next

Per-task docker images; a `--lint-statement` mode that flags under-specified issues (tests
asserting behavior the issue never states); mutation-testing the F2P tests to see whether
wrong-but-plausible patches could pass; and stratified reporting by `meta.difficulty`.
# Repoval
