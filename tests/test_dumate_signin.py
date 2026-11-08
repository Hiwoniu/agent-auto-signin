import unittest
from unittest.mock import call, patch

import dumate_signin as du


class DailyBonusTests(unittest.TestCase):
    def test_already_claimed_never_posts(self):
        with patch.object(du, "_request", return_value={"hasIssued": True, "totalPoints": 500}) as request:
            code, out = du._check(12345, "1.0.90", "run")
        self.assertEqual((code, out["result"]), (0, "ALREADY"))
        request.assert_called_once_with(12345, "1.0.90")

    def test_lost_claim_response_is_verified_without_replaying(self):
        with patch.object(du, "_request", side_effect=[
            {"hasIssued": False, "totalPoints": 500}, du.ProxyError("NETWORK"),
            {"hasIssued": True, "totalPoints": 1000},
        ]) as request:
            code, out = du._check(12345, "1.0.90", "run")
        self.assertEqual((code, out["result"]), (0, "CLAIMED"))
        self.assertIn("500 积分", out["report"])
        self.assertEqual(request.call_args_list, [
            call(12345, "1.0.90"), call(12345, "1.0.90", claim=True), call(12345, "1.0.90"),
        ])
