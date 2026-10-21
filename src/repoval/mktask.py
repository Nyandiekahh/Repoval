"""Build a task directory from a *before* tree and an *after* tree.

The author edits a copy of the repo (fixing the bug and adding tests), and this
splits the difference into ``gold.patch`` (source) and ``test.patch`` (tests).
Patches are generated with difflib rather than ``git diff --no-index`` so paths
come out clean and the output does not depend on the host's git config.
"""
from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path
from typing import Dict, List

from .patching import is_test_path

_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}


def _tree(root: Path) -> Dict[str, List[str]]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts) & _SKIP_DIRS) and p.suffix != ".pyc":
            out[rel.as_posix()] = p.read_text().splitlines(keepends=True)
    return out


def diff_trees(before: Path, after: Path) -> Dict[str, str]:
    """Return ``{path: git-style diff}`` for every added/changed file."""
    a, b = _tree(before), _tree(after)
    diffs: Dict[str, str] = {}
    for path in sorted(set(a) | set(b)):
        old, new = a.get(path), b.get(path)
        if old == new:
            continue
        if new is None:
            raise ValueError(f"{path}: deleting files is not supported in tasks")
        head = [f"diff --git a/{path} b/{path}\n"]
        if old is None:
            head.append("new file mode 100644\n")
        body = list(difflib.unified_diff(
            old or [], new, "/dev/null" if old is None else f"a/{path}", f"b/{path}", n=3))
        # difflib omits the newline-less final line marker; require clean files instead
        if any(not l.endswith("\n") for l in new[-1:]):
            raise ValueError(f"{path}: file must end with a newline")
        diffs[path] = "".join(head + body)
    return diffs


def make_task(before: Path, after: Path, out: Path, instance_id: str, statement: str,
              fail_to_pass: List[str], pass_to_pass: List[str], repo_rel: str,
              meta: dict = None) -> None:
    diffs = diff_trees(before, after)
    gold = "".join(d for p, d in diffs.items() if not is_test_path(p))
    test = "".join(d for p, d in diffs.items() if is_test_path(p))
    out.mkdir(parents=True, exist_ok=True)
    (out / "gold.patch").write_text(gold)
    (out / "test.patch").write_text(test)
    (out / "task.json").write_text(json.dumps({
        "instance_id": instance_id, "repo": repo_rel, "problem_statement": statement.strip() + "\n",
        "fail_to_pass": fail_to_pass, "pass_to_pass": pass_to_pass, "meta": meta or {},
    }, indent=2) + "\n")
