"""Production rotation invariants, using explicit source identities and durations."""
import math
import time
import unittest
from collections import Counter
from unittest.mock import patch

from backend.mixer import MediaInfo, Selection
from backend.planning import RenderJob
from backend.rotation import (PROPOSALS, body_footage, body_overlap,
                              select_rotation_jobs, summarize_rotation, visual_identity)


def option(names, target=10):
    files = [MediaInfo(name, seconds) for name, seconds in names]
    duration = sum(f.duration for f in files)
    return Selection(files, duration, target, target / duration, 1.4, True)


def one(name, duration=10):
    return option([(name, duration)], duration)


class RotationTests(unittest.TestCase):
    def test_recent_pairs_are_exact_and_hook_does_not_disguise_identical_body(self):
        jobs = [RenderJob('s', [one(f'h{i}', 100), one('body')]) for i in range(30)]
        report = summarize_rotation(jobs, 3, .5)
        self.assertEqual(report['pair_count'], 84)
        self.assertEqual(report['violating_pairs'], 84)
        self.assertEqual(report['max_pair_overlap'], 1)
        self.assertEqual(report['pair_details'][0]['denominator_seconds'], 10)
        self.assertTrue(report['requires_acceptance'])

    def test_no_cartesian_enumeration_and_no_quantity_reduction_for_target(self):
        schemes = [('s', [one(f'h{i}', 3) for i in range(30)], [[one('fixed')]])]
        with patch('backend.planning.diversified_jobs', side_effect=AssertionError('No Cartesian scan')):
            jobs, report = select_rotation_jobs(schemes, 30, .01)
        self.assertEqual(len(jobs), 30)
        self.assertFalse(report['shortage'])
        self.assertEqual(report['violating_pairs'], 84)
        self.assertEqual(report['groups'][0]['unavoidable_seconds'], 10)

    def test_rotates_sources_and_balances_hooks(self):
        schemes = [('s', [one(f'h{i}', 3) for i in range(3)],
                    [[one(f'g{g}-{i}', 10) for i in range(6)] for g in range(3)])]
        jobs, report = select_rotation_jobs(schemes, 30, .5)
        self.assertEqual(len(jobs), 30)
        self.assertEqual(report['pair_count'], 84)
        self.assertEqual(report['violating_pairs'], 0)
        self.assertEqual(report['duplicate_count'], 0)
        self.assertEqual(sorted(report['hook_usage'].values()), [10, 10, 10])
        for group in range(3):
            counts = Counter(job.selections[group+1].files[0].path for job in jobs)
            self.assertEqual(len(counts), 6)
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 2)
        again, _ = select_rotation_jobs(schemes, 30, .5)
        self.assertEqual([visual_identity(j) for j in jobs], [visual_identity(j) for j in again])

    def test_user_logged_capacity_is_not_truncated_to_three(self):
        counts = [4,1,3,4,6,4,1,4,1,2,7,4]
        durations = [2.56,2.72,2.76,2.28,3.46,8.18,3.10,3.62,2.88,2.94,2.94,3]
        pools = [[one(f'b{g}-{i}', t) for i in range(n)] for g, (n,t) in enumerate(zip(counts, durations))]
        schemes = [('s', [one(f'h{i}', 3) for i in range(3)], pools)]
        started = time.monotonic()
        jobs, report = select_rotation_jobs(schemes, 50, .5)
        self.assertEqual(report['theoretical_outputs'], 774144)
        self.assertEqual(len(jobs), 50)
        self.assertEqual(report['candidate_evaluations'], 50 * sum(counts) * PROPOSALS)
        expected = [body_overlap(body_footage(jobs[i]), body_footage(jobs[j]))
                    for i in range(len(jobs)) for j in range(max(0, i-3), i)]
        self.assertAlmostEqual(report['max_pair_overlap'], max(expected))
        self.assertEqual(report['pair_count'], 144)
        self.assertLess(time.monotonic() - started, 15)

    def test_shared_sources_are_counted_even_when_candidates_differ(self):
        pool = [option([('common', 8), (f'tail{i}', 2)]) for i in range(4)]
        jobs, report = select_rotation_jobs([('s', [one('hook', 3)], [pool])], 4, .5)
        self.assertEqual(report['groups'][0]['unavoidable_seconds'], 8)
        self.assertGreaterEqual(report['max_pair_overlap'], .8)
        self.assertTrue(report['requires_acceptance'])

    def test_long_common_shot_is_avoided_when_alternative_exists(self):
        pool = [option([('common',8),('a',2)]), option([('common',8),('b',2)]), one('different')]
        jobs, _ = select_rotation_jobs([('s', [one(f'h{i}',3) for i in range(4)], [pool])], 8, .5, 1)
        for a, b in zip(jobs, jobs[1:]):
            self.assertLessEqual(body_overlap(body_footage(a), body_footage(b)), .5)

    def test_retiming_and_order_ignore_hook(self):
        a = RenderJob('s', [one('hook'), option([('a',6),('b',6)],10)])
        b = RenderJob('s', [one('differenthook',30), option([('b',6),('a',6)],10)])
        self.assertAlmostEqual(body_overlap(body_footage(a), body_footage(b)), 1)
        c = RenderJob('s', [one('hook'), option([('a',2),('c',8)],10)])
        self.assertAlmostEqual(body_overlap(body_footage(a), body_footage(c)), .2)

    def test_multiple_schemes_and_skipped_rows_are_reported_correctly(self):
        schemes = [(s, [one(f'{s}-h',3)], [[one(f'{s}-b{i}') for i in range(3)]]) for s in ['s1','s2']]
        jobs, report = select_rotation_jobs(schemes, 6, .5, row_keys={'s1':['Body5'],'s2':['Body20']})
        self.assertEqual(Counter(j.scheme_id for j in jobs), {'s1':3,'s2':3})
        self.assertEqual({g['row_key'] for g in report['groups']}, {'Body5','Body20'})
        self.assertEqual({s['row_key'] for s in report['source_usage']}, {'Body5','Body20'})

    def test_duplicate_visuals_are_reported_even_across_schemes(self):
        job = RenderJob('s1', [one('h'), one('b')])
        other = RenderJob('s2', job.selections)
        report = summarize_rotation([job, other], 3, None)
        self.assertEqual(report['duplicate_groups'], [[1,2]])
        self.assertTrue(report['requires_acceptance'])

    def test_small_scheme_exhausts_before_large_scheme_without_forced_duplicates(self):
        schemes = [('small', [one('h1')], [[one('a')]]),
                   ('large', [one('h2')], [[one(f'b{i}') for i in range(8)]])]
        jobs, report = select_rotation_jobs(schemes, 8)
        self.assertEqual(Counter(j.scheme_id for j in jobs), {'small':1, 'large':7})
        self.assertEqual(report['duplicate_count'], 0)

    def test_one_output_and_zero_capacity(self):
        jobs, report = select_rotation_jobs([('s',[one('h')],[[one('b')]])],1)
        self.assertIsNone(report['max_pair_overlap'])
        self.assertEqual(report['pair_count'], 0)
        self.assertFalse(report['requires_acceptance'])
        jobs, report = select_rotation_jobs([],30)
        self.assertEqual(jobs, [])
        self.assertTrue(report['shortage'])

    def test_requested_quantity_and_range_validation(self):
        schemes = [('s', [one('h')], [[one('b')]])]
        jobs, report = select_rotation_jobs(schemes, None)
        self.assertEqual(len(jobs), 1)
        jobs, report = select_rotation_jobs(schemes, 30)
        self.assertEqual(len(jobs), 1)
        self.assertTrue(report['shortage'])
        for window in [0,11]:
            with self.assertRaises(ValueError): select_rotation_jobs(schemes,1,recent_window=window)
        with self.assertRaises(ValueError): select_rotation_jobs(schemes,5001)


if __name__ == '__main__':
    unittest.main()
