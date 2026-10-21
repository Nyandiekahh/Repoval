"""Throw-away working copies of a repo and a careful subprocess runner.

Isolation here is *hygiene*, not a security boundary: we copy the tree so runs
cannot dirty each other, scrub the environment so results do not depend on the
host's shell, and kill the whole process group on timeout so a runaway test
cannot outlive its verdict. To evaluate untrusted model output at scale, run
the whole tool inside a container (see README, "Threat model").
"""
from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, Optional

from .models import Task, TestRun

_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", ".pytest_cache", ".git", ".venv", "*.egg-info", ".mypy_cache"
)
_PASSTHROUGH_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")


@contextmanager
def sandbox(repo_dir: Path) -> Iterator[Path]:
    """Yield a fresh private copy of ``repo_dir``; delete it afterwards."""
    root = Path(tempfile.mkdtemp(prefix="repoval-"))
    work = root / "repo"
    try:
        shutil.copytree(repo_dir, work, ignore=_IGNORE)
        yield work
    finally:
        shutil.rmtree(root, ignore_errors=True)


def clean_env(hash_seed: int = 0) -> Dict[str, str]:
    env = {k: os.environ[k] for k in _PASSTHROUGH_ENV if k in os.environ}
    env.update(
        PYTHONHASHSEED=str(hash_seed),   # varied by the flake detector
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUNBUFFERED="1",
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        TZ="UTC",
        NO_COLOR="1",
    )
    return env


def run_tests(task: Task, work: Path, hash_seed: int = 0, timeout: Optional[int] = None) -> TestRun:
    """Run the task's test command in ``work`` and parse the JUnit report."""
    junit = work.parent / f"junit-{hash_seed}.xml"
    cmd = task.test_cmd.format(python=shlex.quote(sys.executable), junit=shlex.quote(str(junit)))
    limit = timeout or task.timeout
    start = time.monotonic()
    proc = subprocess.Popen(
        shlex.split(cmd), cwd=work, env=clean_env(hash_seed), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
    )
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, _ = proc.communicate()
    duration = time.monotonic() - start
    outcomes = parse_junit(junit) if junit.exists() else {}
    return TestRun(
        outcomes=outcomes, returncode=proc.returncode, duration=duration,
        timed_out=timed_out, stdout_tail=(out or "")[-2000:],
    )


def parse_junit(path: Path) -> Dict[str, str]:
    """Map ``module::test`` ids to passed/failed/error/skipped.

    Ids are built as ``<classname>::<name>`` straight from the JUnit XML, which
    is stable across pytest versions and independent of terminal formatting.
    """
    outcomes: Dict[str, str] = {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return outcomes  # a crashed run (e.g. collection died mid-write)
    for case in root.iter("testcase"):
        tid = f"{case.get('classname', '')}::{case.get('name', '')}"
        if case.find("failure") is not None:
            outcomes[tid] = "failed"
        elif case.find("error") is not None:
            outcomes[tid] = "error"
        elif case.find("skipped") is not None:
            outcomes[tid] = "skipped"
        else:
            outcomes[tid] = "passed"
    return outcomes
