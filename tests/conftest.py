from pathlib import Path

import pytest

from repoval.cli import load_tasks

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture(scope="session")
def good_tasks():
    return {t.instance_id: t for t in load_tasks(EXAMPLES / "tasks")}


@pytest.fixture(scope="session")
def bad_tasks():
    return {t.instance_id: t for t in load_tasks(EXAMPLES / "tasks_invalid")}
