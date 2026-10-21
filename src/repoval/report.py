"""Render run summaries as Markdown, JSON or a single self-contained HTML page."""
from __future__ import annotations

import html
from typing import Dict, List

from .harness import OUTCOMES
from .metrics import mcnemar_exact, summarise
from .models import ValidationReport


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def run_markdown(config: dict, results: List[dict], ks=(1, 3)) -> str:
    s = summarise(results, ks)
    lines = [f"# Eval report: `{config.get('provider', '?')}`", ""]
    lines.append(f"{s['tasks']} tasks x {s['samples_per_task']} sample(s) = {s['samples']} graded attempts.")
    lines.append("")
    lines += ["| metric | value | 95% interval |", "|---|---|---|"]
    r = s["sample_resolve_rate"]
    lines.append(f"| resolve rate (per attempt) | {_pct(r['value'])} | {_pct(r['wilson95'][0])} - {_pct(r['wilson95'][1])} (Wilson) |")
    for k in ks:
        m = s.get(f"pass@{k}")
        if m:
            lines.append(f"| pass@{k} | {_pct(m['value'])} | {_pct(m['bootstrap95'][0])} - {_pct(m['bootstrap95'][1])} (bootstrap over tasks) |")
    lines += ["", "## Where attempts ended up", "", "| outcome | count | meaning |", "|---|---|---|"]
    for name, n in s["outcomes"].items():
        lines.append(f"| {name} | {n} | {OUTCOMES.get(name, '')} |")
    lines += ["", "## Per task", "", "| task | resolved |", "|---|---|"]
    for t, v in sorted(s["per_task"].items()):
        lines.append(f"| {t} | {_pct(v)} |")
    cheats = [x for x in results if x.get("dropped_test_files")]
    if cheats:
        lines += ["", f"Note: {len(cheats)} attempt(s) tried to edit test files; those hunks were discarded."]
    return "\n".join(lines) + "\n"


def compare_markdown(name_a: str, res_a: List[dict], name_b: str, res_b: List[dict]) -> str:
    sa, sb = summarise(res_a), summarise(res_b)
    common = sorted(set(sa["per_task"]) & set(sb["per_task"]))
    a_only = [t for t in common if sa["per_task"][t] > sb["per_task"][t]]
    b_only = [t for t in common if sb["per_task"][t] > sa["per_task"][t]]
    p = mcnemar_exact(len(a_only), len(b_only))
    ra, rb = sa["sample_resolve_rate"]["value"], sb["sample_resolve_rate"]["value"]
    verdict = ("no statistically meaningful difference" if p >= 0.05
               else f"{name_a if ra > rb else name_b} is better (p={p:.3f})")
    lines = [f"# {name_a} vs {name_b}", "",
             f"On {len(common)} shared tasks: {name_a} {_pct(ra)}, {name_b} {_pct(rb)}.", "",
             f"- only {name_a} solves: {len(a_only)} {a_only}",
             f"- only {name_b} solves: {len(b_only)} {b_only}",
             f"- exact McNemar p-value: {p:.3f}", "", f"**{verdict}.** "
             + ("With so few discordant tasks, absence of significance is not evidence of equality."
                if len(a_only) + len(b_only) < 10 else "")]
    return "\n".join(lines).strip() + "\n"


def validation_markdown(reports: List[ValidationReport]) -> str:
    lines = ["# Task validation", "", "| task | verdict | findings |", "|---|---|---|"]
    for r in reports:
        found = "<br>".join(f"`{i.code}` {i.message}" for i in r.issues) or "-"
        lines.append(f"| {r.instance_id} | **{r.verdict}** | {found} |")
    good = sum(r.verdict != "INVALID" for r in reports)
    lines += ["", f"{good}/{len(reports)} tasks usable."]
    return "\n".join(lines) + "\n"


def run_html(config: dict, results: List[dict]) -> str:
    s = summarise(results, (1,))
    rate = s["sample_resolve_rate"]
    colour = {"RESOLVED": "#2e7d32", "FAILED_TESTS": "#c62828", "REGRESSION": "#ef6c00",
              "BROKEN_BUILD": "#6a1b9a", "APPLY_FAILED": "#455a64", "NO_PATCH": "#78909c",
              "TIMEOUT": "#795548", "PROVIDER_ERROR": "#9e9e9e"}
    total = max(1, s["samples"])
    bars = "".join(
        f'<div class="seg" style="flex:{n};background:{colour.get(o, "#999")}" title="{o}: {n}"></div>'
        for o, n in s["outcomes"].items())
    legend = "".join(
        f'<li><i style="background:{colour.get(o, "#999")}"></i>{html.escape(o)} <b>{n}</b>'
        f' <span>{html.escape(OUTCOMES.get(o, ""))}</span></li>' for o, n in s["outcomes"].items())
    rows = "".join(
        f"<tr><td>{html.escape(r['instance_id'])}</td><td>{r['sample']}</td>"
        f"<td style='color:{colour.get(r['outcome'], '#000')}'>{r['outcome']}</td>"
        f"<td>{r['f2p_passed']}/{r['f2p_total']}</td><td>{html.escape(r.get('detail', ''))}</td></tr>"
        for r in sorted(results, key=lambda r: (r["instance_id"], r["sample"])))
    return f"""<!doctype html><meta charset=utf-8><title>repoval report</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#1b1b1b}}
.bar{{display:flex;height:22px;border-radius:4px;overflow:hidden;margin:1rem 0}}.seg{{min-width:2px}}
li{{list-style:none;margin:.2rem 0}}li i{{display:inline-block;width:.8rem;height:.8rem;border-radius:2px;margin-right:.4rem}}
li span{{color:#666;font-size:.85em}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:.3rem .5rem;text-align:left}}
.big{{font-size:2.4rem;font-weight:600}}</style>
<h1>{html.escape(config.get('provider', '?'))}</h1>
<div class=big>{_pct(rate['value'])}</div>
<div>resolve rate over {s['samples']} attempts on {s['tasks']} tasks (95% Wilson {_pct(rate['wilson95'][0])} - {_pct(rate['wilson95'][1])})</div>
<div class=bar>{bars}</div><ul>{legend}</ul>
<h2>Attempts</h2><table><tr><th>task</th><th>#</th><th>outcome</th><th>F2P</th><th>detail</th></tr>{rows}</table>"""
