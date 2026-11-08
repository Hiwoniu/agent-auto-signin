import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import agent_signin
import signin


class UnifiedCommandTests(unittest.TestCase):
    def test_all_continues_after_one_adapter_fails(self):
        def invoke(platform, action):
            self.assertEqual(action, "status")
            if platform == "doubao":
                raise RuntimeError("private context")
            print(json.dumps({"result": "ALREADY", "report": "already claimed"}))
            return 0

        stdout = io.StringIO()
        with patch.object(agent_signin.sys, "platform", "darwin"), \
             patch.object(agent_signin, "_invoke", side_effect=invoke), redirect_stdout(stdout):
            self.assertNotEqual(agent_signin.main(["status", "all"]), 0)

        results = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([row["platform"] for row in results], list(agent_signin.PLATFORMS))
        self.assertEqual([row["result"] for row in results], ["ERROR" if platform == "doubao" else "ALREADY" for platform in agent_signin.PLATFORMS])
        self.assertNotIn("private context", stdout.getvalue())

    def test_workbuddy_status_never_calls_claim(self):
        with patch.object(signin, "post", return_value=(200, {"today_checked_in": False})) as post:
            code, result = signin.run_auto({}, "https://example.test", dry_run=True)

        self.assertEqual((code, result["result"]), (0, "DRY_RUN"))
        post.assert_called_once_with(
            "https://example.test/v2/billing/meter/checkin-activity-status", {}, retry=True,
        )
