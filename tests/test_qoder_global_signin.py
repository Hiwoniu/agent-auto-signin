from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import qoder_global_signin as qoder

START = 1794103200  # 02:00 UTC; 10:00 UTC+8


def campaign(ident="daily", status="CLAIMABLE", **overrides):
    return {"campaignId": ident, "claimStatus": status, "actionType": "CLAIM_BENEFIT",
            "benefit": {"kind": "CREDITS", "amount": 100}, "startAt": START,
            "endAt": START + 86400 - 60, **overrides}


class InternationalRewardTests(unittest.TestCase):
    def test_only_daily_credits_are_claimed_and_a_lost_response_is_not_retried(self):
        api = Mock()
        api.campaigns.side_effect = [
            (200, {"campaigns": [campaign("monthly", endAt=START + 86400 * 30), campaign()]}, None),
            (200, {"campaigns": [campaign(status="CLAIMED")]}, None),
        ]
        api.claim.return_value = (0, None, "timeout")
        with patch.object(qoder.time, "time", return_value=START + 600):
            code, out = qoder._check(api, "run")
        self.assertEqual((code, out["result"]), (0, "CLAIMED"))
        api.claim.assert_called_once_with("daily", retry=False)

    def test_already_claimed_does_not_post(self):
        api = Mock()
        api.campaigns.return_value = (200, {"campaigns": [campaign(status="CLAIMED")]}, None)
        with patch.object(qoder.time, "time", return_value=START + 600):
            code, out = qoder._check(api, "run")
        self.assertEqual((code, out["result"]), (0, "ALREADY"))
        api.claim.assert_not_called()

    def test_windows_uses_only_international_profile(self):
        with TemporaryDirectory() as tmp:
            roaming = Path(tmp)
            for app in ("com.qoder.app.stable", "com.qodercn.app.stable"):
                root = roaming / app
                root.mkdir()
                (root / qoder.shared.AUTH_FILE).touch()
            with patch.object(qoder.sys, "platform", "win32"), \
                 patch.dict(qoder.os.environ, {"APPDATA": str(roaming)}), \
                 patch.object(qoder.shared, "_read_auth_store_retry", return_value={"token": "synthetic"}) as read:
                self.assertEqual(qoder._load()["token"], "synthetic")
            read.assert_called_once_with(roaming / "com.qoder.app.stable")
