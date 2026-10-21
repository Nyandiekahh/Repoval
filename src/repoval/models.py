"""Plain data containers shared by the validator and the harness.

Everything here is JSON-serialisable on purpose: runs are written to disk and
re-graded later, so the on-disk format *is* the API.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_TEST_CMD = "{python} -m pytest -p no:cacheprovider -q --junitxml={junit}"


@dataclass
class Task:
    """One SWE-bench-style instance: a repo snapshot, a bug, tests that pin it."""

    instance_id: str
    repo_dir: Path                      # snapshot of the repo *before* the fix
    problem_statement: str
    gold_patch: str                     # the reference fix (never touches tests)
    test_patch: str                     # adds/changes the tests that pin the fix
    fail_to_pass: List[str]             # must fail before, pass after
    pass_to_pass: List[str]             # must pass both before and after
    test_cmd: str = DEFAULT_TEST_CMD
    timeout: int = 120
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, task_dir: Path) -> "Task":
        """Load ``<task_dir>/task.json`` plus its sibling patch files."""
        task_dir = Path(task_dir)
        spec = json.loads((task_dir / "task.json").read_text())
        repo_dir = (task_dir / spec["repo"]).resolve()
        if not repo_dir.is_dir():
            raise FileNotFoundError(f"{task_dir}: repo '{spec['repo']}' not found")
        return cls(
            instance_id=spec["instance_id"],
            repo_dir=repo_dir,
            problem_statement=spec["problem_statement"],
            gold_patch=(task_dir / "gold.patch").read_text(),
            test_patch=(task_dir / "test.patch").read_text(),
            fail_to_pass=list(spec["fail_to_pass"]),
            pass_to_pass=list(spec.get("pass_to_pass", [])),
            test_cmd=spec.get("test_cmd", DEFAULT_TEST_CMD),
            timeout=int(spec.get("timeout", 120)),
            meta=spec.get("meta", {}),
        )


@dataclass
class TestRun:
    """Outcome of one test-suite execution inside a sandbox."""

    outcomes: Dict[str, str]            # test id -> passed|failed|error|skipped
    returncode: int
    duration: float
    timed_out: bool = False
    stdout_tail: str = ""

    @property
    def passed(self) -> set:
        return {t for t, o in self.outcomes.items() if o == "passed"}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Issue:
    """A single finding from the validator."""

    code: str
    severity: str                       # "error" invalidates the task; "warn" does not
    message: str

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)


@dataclass
class ValidationReport:
    instance_id: str
    issues: List[Issue] = field(default_factory=list)
    before: Optional[TestRun] = None
    after: Optional[List[TestRun]] = None
    seconds: float = 0.0

    @property
    def errors(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [i for i in self.issues if i.severity == "warn"]

    @property
    def verdict(self) -> str:
        if self.errors:
            return "INVALID"
        return "WARN" if self.warnings else "VALID"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "verdict": self.verdict,
            "issues": [i.to_dict() for i in self.issues],
            "seconds": round(self.seconds, 3),
        }
