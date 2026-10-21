"""Command line interface: ``repoval validate | run | report | regrade | compare``."""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List

from . import __version__
from .harness import RunStore, regrade, run_eval
from .models import Task
from .providers import make_provider
from .report import compare_markdown, run_html, run_markdown, validation_markdown
from .validator import validate_task


def load_tasks(root: Path, only: List[str] = None) -> List[Task]:
    root = Path(root)
    dirs = [root] if (root / "task.json").exists() else sorted(p.parent for p in root.glob("*/task.json"))
    if not dirs:
        raise SystemExit(f"no tasks found under {root} (expected <dir>/task.json)")
    tasks = [Task.load(d) for d in dirs]
    if only:
        tasks = [t for t in tasks if any(o in t.instance_id for o in only)]
    return tasks


def cmd_validate(a) -> int:
    tasks = load_tasks(a.tasks, a.only)
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        reports = list(pool.map(lambda t: validate_task(t, repeat=a.repeat), tasks))
    for r in reports:
        mark = {"VALID": "ok  ", "WARN": "warn", "INVALID": "FAIL"}[r.verdict]
        print(f"[{mark}] {r.instance_id}  ({r.seconds:.1f}s)")
        for i in r.issues:
            print(f"        {i.severity:5} {i.code}: {i.message}")
    if a.markdown:
        Path(a.markdown).write_text(validation_markdown(reports))
    if a.json:
        Path(a.json).write_text(json.dumps([r.to_dict() for r in reports], indent=2))
    bad = sum(r.verdict == "INVALID" for r in reports)
    print(f"\n{len(reports) - bad}/{len(reports)} tasks usable")
    return 1 if bad and not a.allow_invalid else 0


def cmd_run(a) -> int:
    tasks = load_tasks(a.tasks, a.only)
    store = RunStore(a.out)
    provider = make_provider(a.provider)

    def progress(r, i, n):
        print(f"[{i}/{n}] {r.instance_id}#{r.sample}: {r.outcome} {r.detail}".rstrip(), flush=True)

    run_eval(tasks, provider, store, samples=a.samples, workers=a.workers, progress=progress)
    return _emit_report(store, a.samples)


def _emit_report(store: RunStore, samples: int) -> int:
    cfg, results = store.load_config(), store.results()
    ks = tuple(k for k in (1, 3, 5, 10) if k <= max(1, samples))
    md = run_markdown(cfg, results, ks)
    (store.root / "report.md").write_text(md)
    (store.root / "report.html").write_text(run_html(cfg, results))
    print("\n" + md)
    print(f"wrote {store.root}/report.md and report.html")
    return 0


def cmd_report(a) -> int:
    return _emit_report(RunStore(a.run), a.samples)


def cmd_regrade(a) -> int:
    tasks: Dict[str, Task] = {t.instance_id: t for t in load_tasks(a.tasks)}
    out = RunStore(a.out)
    regrade(tasks, RunStore(a.run), out)
    return _emit_report(out, a.samples)


def cmd_compare(a) -> int:
    ra, rb = RunStore(a.run_a), RunStore(a.run_b)
    print(compare_markdown(ra.load_config()["provider"], ra.results(), rb.load_config()["provider"], rb.results()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="repoval", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check that tasks are sound before using them")
    v.add_argument("tasks", type=Path)
    v.add_argument("--repeat", type=int, default=3, help="runs of the fixed tree (flake detection)")
    v.add_argument("--workers", type=int, default=4)
    v.add_argument("--only", nargs="*")
    v.add_argument("--markdown"), v.add_argument("--json")
    v.add_argument("--allow-invalid", action="store_true", help="exit 0 even if tasks are invalid")
    v.set_defaults(fn=cmd_validate)

    r = sub.add_parser("run", help="evaluate a model on tasks")
    r.add_argument("tasks", type=Path)
    r.add_argument("--provider", required=True, help="oracle | null | sim:P | cmd:CMD | replay:DIR | anthropic:MODEL")
    r.add_argument("-n", "--samples", type=int, default=1)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--only", nargs="*")
    r.add_argument("--out", type=Path, required=True)
    r.set_defaults(fn=cmd_run)

    rp = sub.add_parser("report", help="re-render the report of a finished run")
    rp.add_argument("run", type=Path)
    rp.add_argument("-n", "--samples", type=int, default=1)
    rp.set_defaults(fn=cmd_report)

    g = sub.add_parser("regrade", help="re-score recorded completions with the current grader")
    g.add_argument("run", type=Path)
    g.add_argument("tasks", type=Path)
    g.add_argument("--out", type=Path, required=True)
    g.add_argument("-n", "--samples", type=int, default=1)
    g.set_defaults(fn=cmd_regrade)

    c = sub.add_parser("compare", help="paired comparison of two runs")
    c.add_argument("run_a", type=Path)
    c.add_argument("run_b", type=Path)
    c.set_defaults(fn=cmd_compare)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
