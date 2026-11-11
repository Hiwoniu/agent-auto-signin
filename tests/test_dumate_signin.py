import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Crypto.Cipher import AES

import dumate_signin as du


def encrypted_store(key, cookies):
    nonce = b"0123456789ab"
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(json.dumps(cookies).encode("utf-8"))
    return base64.b64encode(nonce + tag + ciphertext).decode("ascii")


class DailyBonusTests(unittest.TestCase):
    def test_already_claimed_never_posts(self):
        with patch.object(du, "_state", return_value={"hasIssued": True, "totalPoints": 500}), \
             patch.object(du, "_request") as request:
            code, out = du._check("run")
        self.assertEqual((code, out["result"]), (0, "ALREADY"))
        request.assert_not_called()

    def test_lost_claim_response_is_verified_without_replaying(self):
        with patch.object(du, "_state", side_effect=[
            {"hasIssued": False, "totalPoints": 500},
            {"hasIssued": True, "totalPoints": 1000},
        ]), patch.object(du, "_request", side_effect=du.ApiError("lost")) as request:
            code, out = du._check("run")
        self.assertEqual((code, out["result"]), (0, "CLAIMED"))
        self.assertIn("500 积分", out["report"])
        request.assert_called_once_with(claim=True)

    def test_cookie_header_and_csrf(self):
        cookies = [
            {"name": "bce-user-info", "value": '"abc"', "domain": ".bce.baidu.com"},
            {"name": "BDUSS", "value": "x", "domain": ".baidu.com"},
            {"name": "other", "value": "y", "domain": ".example.com"},
        ]
        self.assertEqual(du._csrf_token(cookies), "abc")
        header = du._cookie_header(cookies)
        self.assertIn("bce-user-info=abc", header)
        self.assertIn("BDUSS=x", header)
        self.assertNotIn("other", header)

    def test_decrypts_persisted_cookies(self):
        key = os.urandom(32)
        cookies = [{"name": "bce-user-info", "value": "abc", "domain": ".bce.baidu.com"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "auth.json").write_text(json.dumps({"cookies": encrypted_store(key, cookies)}))
            (root / ".cookie-key").write_bytes(key)
            with patch.dict(os.environ, {"DUMATE_DATA_DIR": directory}):
                self.assertEqual(du._load_cookies(), cookies)

    def test_missing_auth_is_no_auth(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"DUMATE_DATA_DIR": directory}):
            code, out = du.run("status")
        self.assertEqual((code, out["result"]), (2, "NO_AUTH"))


if __name__ == "__main__":
    unittest.main()
