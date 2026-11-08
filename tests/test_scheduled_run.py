from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import scheduled_run as schedule


class BeijingScheduleTests(unittest.TestCase):
    def test_09_30_and_10_05_do_not_move_with_mac_daylight_saving(self):
        for day, offset, daily_hour, qoder_hour in (
            ("2026-01-10", "-05:00", 20, 21),
            ("2026-07-10", "-04:00", 21, 22),
        ):
            daily = datetime.fromisoformat(f"{day}T{daily_hour}:30:00{offset}")
            qoder = datetime.fromisoformat(f"{day}T{qoder_hour}:05:00{offset}")
            early_daily = daily.replace(minute=29)
            early_qoder = qoder.replace(minute=4)
            with self.subTest(day=day):
                self.assertIsNone(schedule.daily_cycle_at(early_daily, 9, 30))
                self.assertEqual(schedule.daily_cycle_at(daily, 9, 30), day[:-2] + "11")
                self.assertIsNone(schedule.cycle_at(early_qoder))
                self.assertEqual(schedule.cycle_at(qoder), day[:-2] + "11")

    def test_qoder_retries_only_the_unfinished_edition(self):
        now = datetime.fromisoformat("2026-11-08T10:05:00+08:00")
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()), \
             patch.object(schedule, "supported_platforms", return_value=schedule.PLATFORMS):
            path = Path(tmp) / "state.json"
            runner = Mock(side_effect=[
                (0, {"result": "ALREADY"}),
                (1, {"result": "ERROR"}),
                (0, {"result": "CLAIMED"}),
            ])
            self.assertEqual(schedule.run("qoder", now, path, runner), 1)
            self.assertEqual(schedule.run("qoder", now.replace(minute=35), path, runner), 0)
            self.assertEqual(schedule.run("qoder", now.replace(hour=11), path, runner), 0)
        self.assertEqual([c.args for c in runner.call_args_list], [
            ("qoder-cn", "run"), ("qoder-global", "run"), ("qoder-global", "run"),
        ])

    def test_other_platforms_run_once_and_do_not_include_qoder(self):
        now = datetime.fromisoformat("2026-11-08T09:30:00+08:00")
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()), \
             patch.object(schedule, "supported_platforms", return_value=schedule.PLATFORMS):
            path = Path(tmp) / "state.json"
            runner = Mock(return_value=(0, {"result": "ALREADY"}))
            schedule.run("daily", now, path, runner)
            schedule.run("daily", now.replace(hour=11), path, runner)
        self.assertEqual([c.args for c in runner.call_args_list],
                         [(name, "run") for name in schedule.DAILY_PLATFORMS])
        self.assertTrue(set(schedule.QODER_PLATFORMS).isdisjoint(schedule.DAILY_PLATFORMS))

    def test_linux_runs_only_its_supported_adapters(self):
        now = datetime.fromisoformat("2026-11-08T10:10:00+08:00")
        with TemporaryDirectory() as tmp, redirect_stdout(StringIO()), \
             patch.object(schedule, "supported_platforms", return_value=("workbuddy", "qwenwork")):
            path = Path(tmp) / "state.json"
            runner = Mock(return_value=(0, {"result": "ALREADY"}))
            schedule.run("qoder", now, path, runner)
            runner.assert_not_called()
            schedule.run("daily", now, path, runner)
        self.assertEqual([call.args for call in runner.call_args_list],
                         [("workbuddy", "run"), ("qwenwork", "run")])
