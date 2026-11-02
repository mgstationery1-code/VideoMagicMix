from __future__ import annotations

import math
import os
from itertools import islice
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class RenderJob:
    scheme_id: str
    selections: list[Any]


def shared_footage(job: RenderJob) -> dict[str, float]:
    """Screen time contributed by each full source, including slowed footage."""
    result: dict[str, float] = defaultdict(float)
    for selection in job.selections:
        scale = selection.target_duration / selection.source_duration
        for item in selection.files:
            result[os.path.normcase(os.path.abspath(item.path))] += item.duration * scale
    return dict(result)


def footage_overlap(left: RenderJob, right: RenderJob) -> float:
    a, b = shared_footage(left), shared_footage(right)
    return sum(min(duration, b.get(path, 0)) for path, duration in a.items()) / max(0.001, min(sum(a.values()), sum(b.values())))


def diversified_jobs(schemes: list[tuple[str, list[Any], list[list[Any]]]]):
    """Round-robin schemes/Hooks, varying all Body dimensions between outputs.

    A coprime walk visits each Cartesian-product tuple once without allocating
    the entire product. Different Hooks begin at different tuples.
    """
    streams = []
    for scheme_id, hooks, pools in schemes:
        sizes = [len(pool) for pool in pools]
        capacity = math.prod(sizes)
        if not capacity:
            continue
        stride = max(1, capacity * 61803398875 // 100000000000)
        while math.gcd(stride, capacity) != 1:
            stride += 1
        for offset, hook in enumerate(hooks):
            streams.append([scheme_id, hook, pools, capacity, stride, offset, 0])
    while streams:
        next_streams = []
        for scheme_id, hook, pools, capacity, stride, offset, step in streams:
            number = (offset + step * stride) % capacity
            selected = []
            for pool in reversed(pools):
                selected.append(pool[number % len(pool)])
                number //= len(pool)
            yield RenderJob(scheme_id, [hook, *reversed(selected)])
            if step + 1 < capacity:
                next_streams.append([scheme_id, hook, pools, capacity, stride, offset, step + 1])
        streams = next_streams


def select_batch_jobs(schemes, limit: int | None, max_overlap: float | None = None,
                      max_candidates: int = 5000, max_outputs: int = 5000):
    """Farthest-first selection minimizing the next worst pairwise overlap.

    Evaluate the *selected* batch exactly, even without an upper limit. Bound
    the candidate pool and report its coverage; never claim global optimality.
    Sparse source indexes avoid storing a quadratic similarity matrix.
    """
    total = sum(len(hooks) * math.prod(len(pool) for pool in pools)
                for _, hooks, pools in schemes)
    requested = limit if limit is not None else total
    candidates = list(islice(diversified_jobs(schemes), max_candidates))
    assets = [shared_footage(job) for job in candidates]
    durations = np.array([sum(asset.values()) for asset in assets])
    source_index = defaultdict(list)
    for index, asset in enumerate(assets):
        for path, duration in asset.items():
            source_index[path].append((index, duration))
    sources = {path: (np.array([item[0] for item in entries], dtype=int),
                      np.array([item[1] for item in entries]))
               for path, entries in source_index.items()}
    available = np.ones(len(candidates), dtype=bool)
    highest = np.zeros(len(candidates))
    cumulative = np.zeros(len(candidates))
    accepted = []
    used_schemes = defaultdict(int)
    used_hooks = defaultdict(int)
    worst = 0.0
    stopped_by_overlap = False
    while len(accepted) < min(requested, max_outputs) and available.any():
        eligible = np.flatnonzero(available)
        if max_overlap is not None:
            eligible = eligible[highest[eligible] <= max_overlap + 1e-9]
        if not len(eligible):
            stopped_by_overlap = True
            break
        best = highest[eligible].min()
        tied = eligible[highest[eligible] <= best + 1e-12]
        tied = tied[cumulative[tied] <= cumulative[tied].min() + 1e-12]
        chosen = min(tied, key=lambda i: (
            used_schemes[candidates[i].scheme_id],
            used_hooks[(candidates[i].scheme_id, candidates[i].selections[0].files[0].path)], int(i)))
        job = candidates[chosen]
        worst = max(worst, float(highest[chosen]))
        accepted.append(job)
        used_schemes[job.scheme_id] += 1
        used_hooks[(job.scheme_id, job.selections[0].files[0].path)] += 1
        available[chosen] = False
        overlap_seconds = np.zeros(len(candidates))
        for path, duration in assets[chosen].items():
            indices, seconds = sources[path]
            overlap_seconds[indices] += np.minimum(duration, seconds)
        ratios = overlap_seconds / np.maximum(0.001, np.minimum(durations[chosen], durations))
        highest = np.maximum(highest, ratios)
        cumulative += ratios
    limited = len(candidates) < total or len(accepted) == max_outputs < min(requested, total)
    reason = ("requested_count" if len(accepted) >= requested else "overlap_limit" if stopped_by_overlap
              else "search_limit" if limited else "exhausted")
    return accepted, {
        "examined": len(candidates), "search_limited": limited, "stop_reason": reason,
        "max_pair_overlap": worst if len(accepted) >= 2 else None,
        "pair_count": len(accepted) * (len(accepted) - 1) // 2,
        "upper_limit": max_overlap, "qualified_outputs": len(accepted),
        "requested_outputs": requested, "shortage": len(accepted) < requested,
        "all_candidates_examined": len(candidates) == total,
        "metric_exact_for_selected_batch": True,
        "algorithm": "优先选择与已选成片最高重复率更低的组合；所选批次两两精确计算，有界贪心搜索，不保证全局最优",
        "search_limit": max_candidates, "output_search_limit": max_outputs,
    }


def select_low_overlap_jobs(schemes, limit: int | None, max_overlap: float, max_examined: int = 50000):
    """Compatibility entry point for callers with a strict upper limit."""
    return select_batch_jobs(schemes, limit, max_overlap, max_candidates=min(max_examined, 5000))


def normalize_output_limit(value: str | int | None) -> int | None:
    """Normalize the quantity field; blank and zero mean unlimited."""
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    if not text.isdigit():
        raise ValueError("生成数量必须是大于或等于 0 的整数")
    number = int(text)
    return None if number == 0 else number


def allocate_round_robin(total_limit: int | None, estimates: list[int]) -> list[int]:
    """Allocate a global output budget fairly across ready schemes."""
    if total_limit is None:
        return [max(0, estimate) for estimate in estimates]
    allocations = [0 for _ in estimates]
    remaining = total_limit
    while remaining > 0:
        progressed = False
        for index, estimate in enumerate(estimates):
            if allocations[index] >= max(0, estimate):
                continue
            allocations[index] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            break
    return allocations
