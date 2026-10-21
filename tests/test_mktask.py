"""End to end: author a brand-new task from before/after trees, then validate and grade it."""
import textwrap

from repoval.harness import grade
from repoval.mktask import make_task
from repoval.models import Task
from repoval.validator import validate_task


def _write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(text).lstrip("\n"))


def test_round_trip(tmp_path):
    before, after = tmp_path / "before", tmp_path / "after"
    _write(before, "mathx/__init__.py", "from .core import clamp\n")
    _write(before, "mathx/core.py", """
        def clamp(x, lo, hi):
            return max(lo, min(x, hi - 1))
        """)
    _write(before, "tests/__init__.py", "")
    _write(before, "tests/test_core.py", """
        from mathx import clamp


        def test_inside():
            assert clamp(5, 0, 10) == 5
        """)
    import shutil
    shutil.copytree(before, after)
    _write(after, "mathx/core.py", """
        def clamp(x, lo, hi):
            return max(lo, min(x, hi))
        """)
    with open(after / "tests/test_core.py", "a") as f:
        f.write("\n\ndef test_upper_bound_is_inclusive():\n    assert clamp(99, 0, 10) == 10\n")

    out = tmp_path / "tasks" / "clamp"
    make_task(before, after, out, "clamp-upper", "clamp() never returns hi.",
              ["tests.test_core::test_upper_bound_is_inclusive"], ["tests.test_core::test_inside"],
              "../../before")
    task = Task.load(out)
    assert "tests/" not in task.gold_patch and "mathx/core.py" in task.gold_patch
    assert "tests/test_core.py" in task.test_patch
    assert validate_task(task, repeat=2).verdict == "VALID"
    assert grade(task, f"```diff\n{task.gold_patch}```").outcome == "RESOLVED"
