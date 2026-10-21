"""Everything about unified diffs: extracting them from chat prose, splitting
them per file, filtering test files, and applying them with ``git apply``.

LLMs are creative about how they hand back a patch. We accept fenced blocks
(```diff / ```patch / bare ```), raw ``diff --git`` output, and bare
``--- a/ +++ b/`` hunks. We do *not* try to repair semantically broken diffs:
if ``git apply`` rejects it, that is a legitimate model failure.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

_FENCE = re.compile(r"```[ \t]*(?:diff|patch|udiff)?[ \t]*\n(.*?)```", re.S)
_TEST_PATH = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$"
)


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def extract_patch(text: str) -> Optional[str]:
    """Pull the most plausible unified diff out of a model response."""
    if not text or not text.strip():
        return None
    candidates: List[str] = [m.group(1) for m in _FENCE.finditer(text)]
    candidates.append(text)  # the whole response may itself be a raw diff
    best: Optional[str] = None
    for cand in candidates:
        patch = _slice_diff(cand)
        if patch and (best is None or len(patch) > len(best)):
            best = patch
    return best


def _slice_diff(text: str) -> Optional[str]:
    lines = text.replace("\r\n", "\n").split("\n")
    start = None
    for i, line in enumerate(lines):
        if line.startswith("diff --git ") or (
            line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")
        ):
            start = i
            break
    if start is None:
        return None
    kept: List[str] = []
    in_hunk_remaining = [0, 0]  # old/new lines still owed by the current hunk
    for line in lines[start:]:
        if in_hunk_remaining[0] > 0 or in_hunk_remaining[1] > 0:
            # Inside a hunk the header counts tell us exactly how many lines to
            # keep, so prose that happens to start with '-' or '+' cannot leak in.
            if line.startswith("\\"):
                kept.append(line)
                continue
            tag = line[:1]
            if tag == "+":
                in_hunk_remaining[1] -= 1
            elif tag == "-":
                in_hunk_remaining[0] -= 1
            else:  # context line (' ') or an empty line some models strip to ''
                in_hunk_remaining[0] -= 1
                in_hunk_remaining[1] -= 1
            kept.append(line if tag in "+- " and line else " ")
            continue
        m = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
        if m:
            in_hunk_remaining = [int(m.group(1) or 1), int(m.group(2) or 1)]
            kept.append(line)
        elif line.startswith(("diff --git ", "--- ", "+++ ", "index ", "new file mode",
                              "deleted file mode", "old mode", "new mode",
                              "similarity index", "rename from", "rename to")):
            kept.append(line)
        elif not line.strip() and not kept:
            continue
        else:
            break  # prose after the diff
    patch = "\n".join(kept).rstrip("\n") + "\n"
    return patch if "@@" in patch else None


def split_by_file(patch: str) -> List[Tuple[str, str]]:
    """Return ``[(path, file_diff)]``; path is the post-image path (``b/`` side).

    Walks the patch with the same hunk bookkeeping as :func:`_slice_diff`, so a
    removed line that happens to read ``--- foo`` is never mistaken for a header,
    and git-style and plain unified diffs can be mixed in one response.
    """
    lines = patch.splitlines()
    chunks: List[List[str]] = []
    owed = [0, 0]                       # old/new hunk lines still to consume
    for i, line in enumerate(lines):
        if owed[0] > 0 or owed[1] > 0:
            tag = line[:1]
            if tag == "+":
                owed[1] -= 1
            elif tag == "-":
                owed[0] -= 1
            elif tag != "\\":
                owed[0] -= 1
                owed[1] -= 1
            chunks[-1].append(line)
            continue
        has_plus = bool(chunks) and any(l.startswith("+++ ") for l in chunks[-1])
        new_file = line.startswith("diff --git ") or (
            line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")
            and (not chunks or has_plus))
        if new_file or not chunks:
            chunks.append([])
        chunks[-1].append(line)
        m = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
        if m:
            owed = [int(m.group(1) or 1), int(m.group(2) or 1)]
    out: List[Tuple[str, str]] = []
    for chunk in chunks:
        path = _chunk_path(chunk)
        if path:
            out.append((path, "\n".join(chunk) + "\n"))
    return out


def _chunk_path(chunk: List[str]) -> Optional[str]:
    for line in chunk:
        if line.startswith("+++ "):
            p = line[4:].split("\t")[0].strip()
            if p == "/dev/null":
                continue
            return p[2:] if p.startswith("b/") else p
    for line in chunk:
        if line.startswith("--- "):
            p = line[4:].split("\t")[0].strip()
            if p != "/dev/null":
                return p[2:] if p.startswith("a/") else p
    for line in chunk:
        m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
        if m:
            return m.group(2)
    return None


def touched_files(patch: str) -> List[str]:
    return [p for p, _ in split_by_file(patch)]


def drop_test_files(patch: str) -> Tuple[str, List[str]]:
    """Remove per-file diffs that edit tests. Returns ``(clean_patch, dropped_paths)``.

    Models must fix the bug in source, not by rewriting the tests that judge them.
    """
    kept: List[str] = []
    dropped: List[str] = []
    for path, body in split_by_file(patch):
        if is_test_path(path):
            dropped.append(path)
        else:
            kept.append(body)
    return "".join(kept), dropped


def apply_patch(workdir: Path, patch: str, check_only: bool = False) -> Tuple[bool, str]:
    """Apply with ``git apply`` (works outside a git repo). Returns ``(ok, stderr)``."""
    if not patch.strip():
        return False, "empty patch"
    cmd = ["git", "apply", "--whitespace=nowarn", "--recount"]
    if check_only:
        cmd.append("--check")
    proc = subprocess.run(
        cmd, cwd=workdir, input=patch, text=True, capture_output=True, timeout=60
    )
    return proc.returncode == 0, proc.stderr.strip()
