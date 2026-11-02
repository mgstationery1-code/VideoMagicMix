"""Bounded, source-aware rotation. Quality targets never silently reduce quantity.

Only the recent viewing window is measured, not every pair in the whole batch.
All timings below are retimed screen seconds; Hook is deliberately excluded.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import os
import random

import numpy as np

from .planning import RenderJob, shared_footage

MAX_BATCH = 5000
PROPOSALS = 8
EPSILON = 1e-9


def body_footage(job):
    return shared_footage(RenderJob(job.scheme_id, job.selections[1:]))


def overlap_seconds(a, b):
    return sum(min(seconds, b.get(path, 0)) for path, seconds in a.items())


def body_overlap(a, b):
    return overlap_seconds(a, b) / max(.001, min(sum(a.values()), sum(b.values())))


def visual_identity(job):
    # Scheme names, narration and subtitles cannot disguise identical footage.
    return tuple(tuple((os.path.normcase(os.path.abspath(f.path)), float(s.target_duration))
                       for f in s.files) for s in job.selections)


def summarize_rotation(jobs, window=3, target=.5, row_keys=None, scanned_paths=None):
    """Exact recent-pair audit, also used for the successfully rendered subset."""
    assets = [body_footage(job) for job in jobs]
    pairs, violations, total, worst, worst_pair = 0, 0, 0., None, None
    details = []
    for i, current in enumerate(assets):
        for j in range(max(0, i - window), i):
            ratio = body_overlap(current, assets[j])
            pairs += 1
            total += ratio
            exceeds = target is not None and ratio > target + EPSILON
            violations += int(exceeds)
            if worst is None or ratio > worst:
                worst, worst_pair = ratio, [j + 1, i + 1]
            contributions = []
            keys = (row_keys or {}).get(jobs[i].scheme_id, [])
            for group, selection in enumerate(jobs[i].selections[1:]):
                group_assets = shared_footage(RenderJob('', [selection]))
                seconds = overlap_seconds(group_assets, assets[j])
                contributions.append({'row_key': keys[group] if group < len(keys) else f'Body{group+1}',
                                      'shared_seconds': seconds})
            details.append({'left': j+1, 'right': i+1, 'ratio': ratio, 'exceeds': exceeds,
                            'shared_seconds': overlap_seconds(current, assets[j]),
                            'denominator_seconds': min(sum(current.values()), sum(assets[j].values())),
                            'contributions': contributions})
    identities = defaultdict(list)
    usage = {}
    hook_usage = Counter()
    for index, job in enumerate(jobs):
        identities[visual_identity(job)].append(index+1)
        hook_usage[job.selections[0].files[0].path] += 1
        keys = (row_keys or {}).get(job.scheme_id, [])
        for group, selection in enumerate(job.selections[1:]):
            row = keys[group] if group < len(keys) else f'Body{group+1}'
            for path, seconds in shared_footage(RenderJob('', [selection])).items():
                key = (job.scheme_id, row, path)
                item = usage.setdefault(key, {'scheme_id': job.scheme_id, 'row_key': row,
                                              'path': path, 'count': 0, 'seconds': 0.})
                item['count'] += 1
                item['seconds'] += seconds
    # Include scanned but unused files rather than hiding them from diagnostics.
    for scheme, groups in (scanned_paths or {}).items():
        for row, paths in groups.items():
            for raw in paths:
                path = os.path.normcase(os.path.abspath(raw))
                usage.setdefault((scheme, row, path), {'scheme_id': scheme, 'row_key': row,
                                                     'path': path, 'count': 0, 'seconds': 0.})
    duplicates = [indices for indices in identities.values() if len(indices) > 1]
    requires_acceptance = bool(violations or duplicates)
    details.sort(key=lambda p: -p['ratio'])
    return {'strategy': 'balanced_rotation', 'metric': 'recent_body_source_reuse',
            'recent_window': window, 'upper_limit': target, 'max_pair_overlap': worst,
            'average_pair_overlap': total / pairs if pairs else None, 'pair_count': pairs,
            'passed_pairs': pairs - violations, 'violating_pairs': violations,
            'target_met': not violations, 'requires_acceptance': requires_acceptance,
            'worst_pair': worst_pair, 'pair_details': details[:100], 'details_truncated': len(details) > 100,
            'duplicate_groups': duplicates, 'duplicate_count': sum(len(g)-1 for g in duplicates),
            'source_usage': list(usage.values()), 'hook_usage': dict(hook_usage),
            'metric_exact_for_selected_batch': True,
            'algorithm': '均衡轮换；仅精确检查最近窗口内的 Body 素材复用，不保证全批次或视觉相似度'}


class CandidatePool:
    """Retiming and sparse source identities are cached once per preflight."""
    def __init__(self, selections):
        self.selections = selections
        self.assets = [shared_footage(RenderJob('', [s])) for s in selections]
        self.paths = sorted({p for a in self.assets for p in a})
        self.matrix = np.array([[a.get(p, 0) for p in self.paths] for a in self.assets])
        self.used = np.zeros(len(selections))
        self.speed_cost = np.array([abs(math.log(s.source_duration / s.target_duration)) for s in selections])

    def choose(self, recent, exposure, partial, denominators, rng, proposal):
        # Common seconds by source, not candidate index; long shared shots cost more.
        pair_scores = np.array([np.minimum(self.matrix, [a.get(p, 0) for p in self.paths]).sum(axis=1)
                                for a in recent])
        if len(recent):
            projected = (pair_scores + np.asarray(partial)[:, None]) / np.asarray(denominators)[:, None]
            penalty = projected.max(axis=0) + .25 * projected.mean(axis=0)
        else:
            penalty = np.zeros(len(self.selections))
        observed = np.array([exposure[p] for p in self.paths])
        # Squared-exposure increment favors unused sources, accounting for duration.
        fairness = (2 * self.matrix * observed + self.matrix**2).sum(axis=1)
        fairness /= max(1., float(fairness.max()))
        score = penalty + .08 * fairness + .015 * self.used / max(1., float(self.used.max()))
        # Multiple bounded proposals escape synchronized cycles without changing rules.
        score += np.array([rng.random() for _ in self.selections]) * (0.015 if proposal else 0.000001)
        order = np.lexsort((self.speed_cost, score))
        chosen = int(order[0])
        return chosen, pair_scores[:, chosen] if len(recent) else []


def select_rotation_jobs(schemes, limit, max_overlap=.5, recent_window=3, *, row_keys=None, scanned_paths=None):
    """O(outputs * proposals * sum(pool sizes) * window), no Cartesian search."""
    if not 1 <= recent_window <= 10:
        raise ValueError('关注最近条数必须是 1–10 的整数')
    if limit is not None and (limit < 1 or limit > MAX_BATCH):
        raise ValueError(f'每批生成数量请填写 1–{MAX_BATCH}，更多视频请分批生成')
    valid = [(s, h, p) for s, h, p in schemes if h and p and all(p)]
    capacity = sum(len(h) * math.prod(map(len, p)) for _, h, p in valid)
    desired = capacity if limit is None else limit
    count = min(desired, capacity, MAX_BATCH)
    prepared = [(s, h, [CandidatePool(pool) for pool in p]) for s, h, p in valid]
    scheme_capacities = {s: len(h) * math.prod(map(len, p)) for s, h, p in valid}
    rng = random.Random(20261101)
    exposure, used_hooks, used_schemes = defaultdict(float), Counter(), Counter()
    jobs, recent_assets, identities = [], [], set()
    evaluated = 0
    for output in range(count):
        scheme, hooks, pools = min((entry for entry in prepared if used_schemes[entry[0]] < scheme_capacities[entry[0]]),
                                  key=lambda entry: used_schemes[entry[0]])
        hook = min(hooks, key=lambda h: (used_hooks[(scheme, h.files[0].path)], rng.random()))
        target_duration = sum(pool.selections[0].target_duration for pool in pools)
        denominators = [max(.001, min(target_duration, sum(a.values()))) for a in recent_assets]
        proposals = []
        for attempt in range(PROPOSALS):
            indices = [0] * len(pools)
            partial = np.zeros(len(recent_assets))
            order = list(range(len(pools)))
            rng.shuffle(order)
            if attempt == 0:
                order.sort(key=lambda g: -pools[g].selections[0].target_duration)
            for g in order:
                indices[g], shared = pools[g].choose(recent_assets, exposure, partial, denominators, rng, attempt)
                partial += np.asarray(shared)
                evaluated += len(pools[g].selections)
            job = RenderJob(scheme, [hook, *(p.selections[i] for p, i in zip(pools, indices))])
            assets = body_footage(job)
            ratios = [body_overlap(assets, a) for a in recent_assets]
            identity = visual_identity(job)
            fairness = sum(2 * exposure[p] * seconds + seconds**2 for p, seconds in assets.items())
            score = (sum(r > max_overlap + EPSILON for r in ratios) if max_overlap is not None else 0,
                     identity in identities,
                     max(ratios, default=0), sum(ratios), fairness)
            proposals.append((score, job, indices, assets, identity))
        best = min(proposals, key=lambda item: item[0])
        if best[0][1]:
            # A bounded one-group repair avoids repeatedly selecting a local optimum.
            _, base_job, base_indices, _, _ = best
            repairs = 0
            for g in rng.sample(list(range(len(pools))), len(pools)):
                for alternative in rng.sample(range(len(pools[g].selections)), min(32, len(pools[g].selections))):
                    indices = list(base_indices)
                    indices[g] = alternative
                    job = RenderJob(scheme, [hook, *(p.selections[i] for p, i in zip(pools, indices))])
                    identity = visual_identity(job)
                    assets = body_footage(job)
                    ratios = [body_overlap(assets, a) for a in recent_assets]
                    score = (sum(r > max_overlap + EPSILON for r in ratios) if max_overlap is not None else 0,
                             identity in identities,
                             max(ratios, default=0), sum(ratios),
                             sum(2 * exposure[p] * seconds + seconds**2 for p, seconds in assets.items()))
                    proposals.append((score, job, indices, assets, identity))
                    repairs += 1
                    if repairs >= 64:
                        break
                if repairs >= 64:
                    break
        _, job, indices, assets, identity = min(proposals, key=lambda item: item[0])
        jobs.append(job)
        identities.add(identity)
        used_schemes[scheme] += 1
        used_hooks[(scheme, hook.files[0].path)] += 1
        for pool, index in zip(pools, indices):
            pool.used[index] += 1
        for path, seconds in assets.items():
            exposure[path] += seconds
        recent_assets = (recent_assets + [assets])[-recent_window:]
    report = summarize_rotation(jobs, recent_window, max_overlap, row_keys, scanned_paths)
    report['groups'] = []
    for scheme, _, pools in prepared:
        keys = (row_keys or {}).get(scheme, [])
        for g, pool in enumerate(pools):
            row = keys[g] if g < len(keys) else f'Body{g+1}'
            common = pool.matrix.min(axis=0)
            report['groups'].append({'scheme_id': scheme, 'row_key': row,
                                     'candidate_count': len(pool.selections),
                                     'target_duration': pool.selections[0].target_duration,
                                     'unavoidable_seconds': float(common.sum()),
                                     'common_sources': [p for p, s in zip(pool.paths, common) if s > EPSILON],
                                     'eligible_source_count': len(pool.paths)})
    report.update(requested_outputs=desired, planned_outputs=len(jobs), theoretical_outputs=capacity,
                  shortage=len(jobs) < desired, batch_limited=desired > MAX_BATCH and capacity > MAX_BATCH,
                  candidate_evaluations=evaluated, proposals_per_output=PROPOSALS,
                  max_batch=MAX_BATCH)
    return jobs, report
