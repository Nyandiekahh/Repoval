"""The eval loop: prompt -> model -> patch -> sandboxed tests -> verdict.

Design notes
* Completions are persisted *before* grading (``completions.jsonl``). Grading is a
  pure function of (task, text), so a run can be re-graded after a harness fix
  without paying for the model again (``repoval regrade``).
* Runs are resumable: results are appended per sample, and an interrupted run
  skips whatever is already on disk.
* Outcomes are a closed taxonomy, so a drop in the headline number can be
  traced to *why* (bad diffs vs. wrong fixes vs. regressions).
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .models import Task
from .patching import apply_patch, drop_test_files, extract_patch, touched_files
from .prompts import build_prompt
from .providers import Completion, Provider
from .sandbox import run_tests, sandbox

OUTCOMES = {
    "RESOLVED": "all fail-to-pass tests pass and nothing regressed",
    "FAILED_TESTS": "patch applied, but a target test still fails",
    "REGRESSION": "target tests pass, but a previously-passing test broke",
    "BROKEN_BUILD": "patch applied but tests could not even be collected (syntax/import error)",
    "APPLY_FAILED": "diff was extracted but did not apply to the repo",
    "NO_PATCH": "no diff could be found in the response",
    "TIMEOUT": "test run exceeded the time limit",
    "PROVIDER_ERROR": "the model call itself failed (infra, not a model failure)",
}


@dataclass
class Result:
    instance_id: str
    sample: int
    outcome: str
    detail: str = ""
    f2p_passed: int = 0
    f2p_total: int = 0
    broken_p2p: List[str] = field(default_factory=list)
    dropped_test_files: List[str] = field(default_factory=list)
    patch_files: List[str] = field(default_factory=list)
    latency: float = 0.0
    usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def grade(task: Task, completion_text: str, sample: int = 0) -> Result:
    """Pure grading function: (task, model text) -> Result. No model calls."""
    res = Result(task.instance_id, sample, "NO_PATCH", f2p_total=len(task.fail_to_pass))
    patch = extract_patch(completion_text)
    if patch is None:
        res.detail = "no unified diff found in response"
        return res
    patch, dropped = drop_test_files(patch)
    res.dropped_test_files = dropped
    if not patch.strip():
        res.detail = "diff only touched test files, which are ignored"
        return res
    res.patch_files = touched_files(patch)

    with sandbox(task.repo_dir) as work:
        ok, err = apply_patch(work, patch)
        if not ok:
            res.outcome, res.detail = "APPLY_FAILED", err[:300]
            return res
        ok, err = apply_patch(work, task.test_patch)
        if not ok:  # the model changed lines our tests need; its fault, but say why
            res.outcome, res.detail = "APPLY_FAILED", f"test patch conflicts with model patch: {err[:200]}"
            return res
        run = run_tests(task, work)

    if run.timed_out:
        res.outcome, res.detail = "TIMEOUT", f"exceeded {task.timeout}s"
        return res
    f2p_ok = [t for t in task.fail_to_pass if run.outcomes.get(t) == "passed"]
    res.f2p_passed = len(f2p_ok)
    res.broken_p2p = [t for t in task.pass_to_pass if run.outcomes.get(t) != "passed"]
    missing = [t for t in task.fail_to_pass if t not in run.outcomes]
    if missing and len(missing) == len(task.fail_to_pass):
        res.outcome = "BROKEN_BUILD"
        res.detail = run.stdout_tail.strip().splitlines()[-1][:200] if run.stdout_tail.strip() else "no tests ran"
    elif len(f2p_ok) < len(task.fail_to_pass):
        res.outcome = "FAILED_TESTS"
        res.detail = f"{len(task.fail_to_pass) - len(f2p_ok)} target test(s) still failing"
    elif res.broken_p2p:
        res.outcome, res.detail = "REGRESSION", f"broke {len(res.broken_p2p)} existing test(s)"
    else:
        res.outcome = "RESOLVED"
    return res


class RunStore:
    """Append-only on-disk record of a run (thread-safe)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _append(self, name: str, rec: dict):
        with self._lock, open(self.root / name, "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _read(self, name: str) -> List[dict]:
        p = self.root / name
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []

    def save_config(self, cfg: dict):
        (self.root / "run.json").write_text(json.dumps(cfg, indent=2))

    def load_config(self) -> dict:
        return json.loads((self.root / "run.json").read_text())

    def add_completion(self, iid, sample, text, c: Completion):
        self._append("completions.jsonl", {"instance_id": iid, "sample": sample, "text": text,
                                           "latency": c.latency, "usage": c.usage})

    def add_result(self, r: Result):
        self._append("results.jsonl", r.to_dict())

    def completions(self) -> List[dict]:
        return self._read("completions.jsonl")

    def results(self) -> List[dict]:
        return self._read("results.jsonl")


def run_eval(tasks: Sequence[Task], provider: Provider, store: RunStore, samples: int = 1,
             workers: int = 4, progress: Optional[Callable[[Result, int, int], None]] = None) -> List[dict]:
    done = {(r["instance_id"], r["sample"]) for r in store.results()}
    cached = {(c["instance_id"], c["sample"]): c for c in store.completions()}
    jobs = [(t, s) for t in tasks for s in range(samples) if (t.instance_id, s) not in done]
    total = len(jobs)
    store.save_config({
        "provider": provider.name, "samples": samples, "tasks": [t.instance_id for t in tasks],
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })

    def work(task: Task, sample: int) -> Result:
        key = (task.instance_id, sample)
        if key in cached:                      # resumed run: do not pay for the model twice
            text, lat, usage = cached[key]["text"], cached[key].get("latency", 0.0), cached[key].get("usage", {})
        else:
            try:
                comp = provider.generate(task, build_prompt(task), sample)
            except Exception as e:             # noqa: BLE001 - provider failures must not kill the run
                r = Result(task.instance_id, sample, "PROVIDER_ERROR", detail=f"{type(e).__name__}: {e}"[:300],
                           f2p_total=len(task.fail_to_pass))
                return r
            store.add_completion(task.instance_id, sample, comp.text, comp)
            text, lat, usage = comp.text, comp.latency, comp.usage
        r = grade(task, text, sample)
        r.latency, r.usage = lat, usage
        return r

    finished = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = [pool.submit(work, t, s) for t, s in jobs]
        for fut in as_completed(futs):
            r = fut.result()
            # PROVIDER_ERROR is deliberately not persisted: a resume should retry it.
            if r.outcome != "PROVIDER_ERROR":
                store.add_result(r)
            finished += 1
            if progress:
                progress(r, finished, total)
    return store.results()


def regrade(tasks: Dict[str, Task], store: RunStore, out: RunStore) -> List[dict]:
    """Re-score recorded completions with the *current* grader."""
    out.save_config({**store.load_config(), "regraded_from": str(store.root)})
    for c in store.completions():
        task = tasks.get(c["instance_id"])
        if task is None:
            continue
        r = grade(task, c["text"], c["sample"])
        r.latency, r.usage = c.get("latency", 0.0), c.get("usage", {})
        out.add_completion(c["instance_id"], c["sample"], c["text"], Completion(c["text"]))
        out.add_result(r)
    return out.results()
