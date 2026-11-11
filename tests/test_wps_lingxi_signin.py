import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wps_lingxi_signin as wps


def task(status="claimed", day_status="claimed", reward=100):
    return {
        "task_key": "daily_check_in",
        "type": "daily",
        "status": status,
        "reward_amount": reward,
        "extra": {
            "today_day": 1,
            "today_reward": reward,
            "check_in_days": [{"day": 1, "reward": reward, "status": day_status}],
        },
    }


class TaskCenterTests(unittest.TestCase):
    def test_claimed_state_never_posts(self):
        with patch.object(wps, "_request", return_value={"tasks": [task()]}) as request:
            code, out = wps._check("run")
        self.assertEqual((code, out["result"]), (0, "ALREADY"))
        request.assert_called_once_with("GET", wps.TASKS_PATH)

    def test_available_state_is_dry_run(self):
        with patch.object(wps, "_request", return_value={"tasks": [task("available", "available")]}) as request:
            code, out = wps._check("status")
        self.assertEqual((code, out["result"]), (0, "DRY_RUN"))
        self.assertIn("100 智点", out["report"])
        request.assert_called_once_with("GET", wps.TASKS_PATH)

    def test_claim_is_verified_without_replay(self):
        with patch.object(wps, "_request", side_effect=[
            {"tasks": [task("available", "available")]},
            {},
            {"tasks": [task()]},
        ]) as request:
            code, out = wps._check("run")
        self.assertEqual((code, out["result"]), (0, "CLAIMED"))
        self.assertEqual(request.call_args_list[1].args, ("POST", wps.CLAIM_PATH))
        self.assertEqual(request.call_args_list[1].kwargs, {"body": b""})

    def test_lost_claim_response_stays_unconfirmed(self):
        with patch.object(wps, "_request", side_effect=[
            {"tasks": [task("available", "available")]},
            wps.ApiError("lost"),
            wps.ApiError("read failed"),
        ]):
            code, out = wps._check("run")
        self.assertEqual((code, out["result"]), (1, "UNCONFIRMED"))

    def test_reads_plaintext_cookie_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            partitions = root / "Partitions"
            partitions.mkdir()
            db = partitions / "lingxi-business/Cookies"
            db.parent.mkdir()
            connection = sqlite3.connect(db)
            connection.execute(
                "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                "encrypted_value BLOB, expires_utc INTEGER)"
            )
            connection.executemany(
                "INSERT INTO cookies VALUES (?, ?, ?, ?, ?)",
                [
                    (".wps.cn", "wps_sid", "sid-value", b"", 0),
                    (".wps.cn", "kso_sid", "kso-value", b"", 0),
                ],
            )
            connection.commit()
            connection.close()
            with patch.dict(os.environ, {"WPS_LINGXI_DATA_DIR": directory}):
                cookies = wps._load_cookies()
        self.assertEqual({cookie["name"] for cookie in cookies}, {"wps_sid", "kso_sid"})

    def test_missing_cookie_is_no_auth(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"WPS_LINGXI_DATA_DIR": directory}):
            code, out = wps.run("status")
        self.assertEqual((code, out["result"]), (2, "NO_AUTH"))


if __name__ == "__main__":
    unittest.main()
