"""Statistics for eval results. Small, dependency-free, and tested against
hand-computed values.
"""
from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence, Tuple


def wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval. Unlike the normal approximation it behaves at
    p=0, p=1 and small n, which is exactly where eval suites live."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k estimator (Chen et al., 2021): 1 - C(n-c, k) / C(n, k)."""
    if k > n:
        raise ValueError(f"k={k} > n={n}")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def bootstrap_ci(values: Sequence[float], iters: int = 4000, alpha: float = 0.05,
                 seed: int = 1234) -> Tuple[float, float]:
    """Percentile bootstrap over *tasks* (the unit that is actually sampled)."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return (means[int(alpha / 2 * iters)], means[min(iters - 1, int((1 - alpha / 2) * iters))])


def mcnemar_exact(a_only: int, b_only: int) -> float:
    """Two-sided exact McNemar test on discordant pairs (= sign test, p=0.5).

    ``a_only`` = tasks A solved and B did not, ``b_only`` the reverse. Concordant
    tasks carry no information about which model is better, so they are ignored.
    """
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def summarise(results: List[dict], ks: Sequence[int] = (1,)) -> Dict:
    """Aggregate per-sample results into run-level metrics."""
    by_task: Dict[str, List[bool]] = {}
    outcomes: Dict[str, int] = {}
    for r in results:
        by_task.setdefault(r["instance_id"], []).append(r["outcome"] == "RESOLVED")
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    n_tasks = len(by_task)
    per_task = {t: sum(v) / len(v) for t, v in by_task.items()}
    min_n = min((len(v) for v in by_task.values()), default=0)
    out: Dict = {
        "tasks": n_tasks,
        "samples": len(results),
        "samples_per_task": min_n,
        "outcomes": dict(sorted(outcomes.items(), key=lambda kv: -kv[1])),
        "per_task": per_task,
    }
    flat = [x for v in by_task.values() for x in v]
    lo, hi = wilson_interval(sum(flat), len(flat))
    out["sample_resolve_rate"] = {"value": sum(flat) / len(flat) if flat else 0.0, "wilson95": [lo, hi]}
    for k in ks:
        if k > min_n:
            continue
        vals = [pass_at_k(len(v), sum(v), k) for v in by_task.values()]
        blo, bhi = bootstrap_ci(vals)
        out[f"pass@{k}"] = {"value": sum(vals) / len(vals), "bootstrap95": [blo, bhi]}
    return out
