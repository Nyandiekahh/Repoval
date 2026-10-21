"""Prompt construction.

The model sees: the issue text, the failing tests it must satisfy, and as many
source files as fit in a character budget, ranked by lexical overlap with the
issue (a crude BM25-ish retriever; good enough for small repos, and honest about
being a baseline). It does *not* see the gold patch or the declared test ids.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from .models import Task
from .patching import is_test_path, touched_files

SYSTEM = (
    "You are a careful software engineer fixing a bug in a Python repository. "
    "Reply with a single unified diff (git format, paths relative to the repo root) "
    "inside one ```diff fenced block. Change source files only; never edit tests. "
    "Keep the change minimal and do not break existing behaviour."
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


@dataclass
class Prompt:
    instance_id: str
    system: str
    user: str

    def to_dict(self):
        return {"instance_id": self.instance_id, "system": self.system, "user": self.user}


def build_prompt(task: Task, char_budget: int = 24000) -> Prompt:
    files = _read_sources(task.repo_dir)
    ranked = _rank(files, task.problem_statement + "\n" + task.test_patch)
    parts, used = [], 0
    for path, text in ranked:
        block = f"### {path}\n```python\n{text}\n```\n"
        if used + len(block) > char_budget:
            continue  # skip, but keep trying smaller files further down the list
        parts.append(block)
        used += len(block)
    tests = "\n".join(
        l[1:] for l in task.test_patch.splitlines() if l.startswith("+") and not l.startswith("+++")
    )
    user = (
        f"## Issue\n{task.problem_statement.strip()}\n\n"
        f"## Tests that must pass after your fix\n```python\n{tests.strip()}\n```\n\n"
        f"## Repository files\n{''.join(parts)}\n"
        "Respond with the diff only."
    )
    return Prompt(task.instance_id, SYSTEM, user)


def _read_sources(repo: Path) -> List[Tuple[str, str]]:
    out = []
    for p in sorted(repo.rglob("*.py")):
        rel = p.relative_to(repo).as_posix()
        if is_test_path(rel) or "__pycache__" in rel:
            continue
        out.append((rel, p.read_text(errors="replace")))
    return out


def _rank(files: List[Tuple[str, str]], query: str) -> List[Tuple[str, str]]:
    q = Counter(w.lower() for w in _WORD.findall(query))
    n = max(len(files), 1)
    df = Counter()
    for _, text in files:
        df.update(set(w.lower() for w in _WORD.findall(text)))
    def score(item):
        _, text = item
        tf = Counter(w.lower() for w in _WORD.findall(text))
        return sum(
            (1 + math.log(tf[w])) * math.log(1 + n / df[w]) * min(c, 3)
            for w, c in q.items() if w in tf
        ) / (1 + math.log(1 + len(text) / 2000))
    return sorted(files, key=score, reverse=True)
