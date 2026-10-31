# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Regression tests for real completion, monotonic progress, and timing learning."""
import unittest
import tempfile
from pathlib import Path
from processing_progress import ProcessingProgress, overall_fraction


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.progress = ProcessingProgress(lambda: self.now)

    def test_no_fake_completion_even_after_a_very_slow_job(self):
        self.progress.start(("small", "en", "Balanced"), 10)
        self.now += 300
        self.assertLess(self.progress.fraction(), 1)
        self.progress.finish()
        self.assertEqual(self.progress.fraction(), 1)

    def test_late_segment_reports_never_move_progress_back(self):
        self.progress.start("one", 10)
        self.progress.report(.8)
        self.progress.report(.2)
        self.assertEqual(self.progress.fraction(), .8)
        self.now += 12
        self.assertGreaterEqual(self.progress.fraction(), .8)

    def test_timing_estimate_uses_only_matching_configuration(self):
        key = ("small", "en", "Accurate")
        for duration in (2, 10, 30, 60):
            self.progress.learn(key, duration, 1.5+duration*.1)
        self.progress.start(key, 20)
        self.assertAlmostEqual(self.progress.expected, 3.5)
        self.progress.start(("small", "en", "Fast"), 20)
        self.assertAlmostEqual(self.progress.expected, 3.4)

    def test_cancel_resets_display_but_keeps_timing_calibration(self):
        self.progress.learn("small", 5, 10)
        self.progress.finish()
        self.progress.reset()
        self.assertEqual(self.progress.fraction(), 0)
        self.progress.start("small", 5)
        self.assertEqual(self.progress.expected, 10)

    def test_next_job_has_its_own_progress(self):
        self.progress.start("small", 10)
        self.progress.report(.9)
        self.progress.fraction()
        self.progress.start("small", 10)
        self.assertEqual(self.progress.fraction(), 0)

    def test_calibrated_progress_is_near_completion_at_expected_finish(self):
        self.progress.learn("small", 8, 2)
        self.progress.start("small", 8)
        self.now += 1
        self.assertAlmostEqual(self.progress.fraction(), .46)
        self.now += 1
        self.assertAlmostEqual(self.progress.fraction(), .92)

    def test_worker_time_includes_ui_queue_delay(self):
        self.progress.learn("small", 8, 2)
        self.progress.start("small", 8, started=self.now-1)
        self.assertAlmostEqual(self.progress.fraction(), .46)

    def test_long_pending_job_is_not_treated_as_a_short_phrase(self):
        self.assertAlmostEqual(overall_fraction([1, 9], 1, 0), .1)
        self.assertAlmostEqual(overall_fraction([1, 9], 1, .5), .55)
        self.assertLess(overall_fraction([1, 9], 2, 1), 1)

    def test_calibration_survives_restart_and_ignores_bad_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"timings.json"
            progress = ProcessingProgress(cache_path=path)
            key = ("small", "fa", "Balanced")
            progress.learn(key, 10, 3)
            progress.save()
            restored = ProcessingProgress(cache_path=path)
            self.assertAlmostEqual(restored.estimate(key, 10), 3)
            path.write_text('{"bad": [[-1, 8], [2, "invalid"], [2, NaN]]}')
            self.assertEqual(ProcessingProgress(cache_path=path).timings, {})

    def test_duration_update_does_not_move_display_backwards(self):
        self.progress.start("small", 5)
        self.now += 1
        before = self.progress.fraction()
        self.progress.set_duration(80)
        self.assertGreaterEqual(self.progress.fraction(), before)
