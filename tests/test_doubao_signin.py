import importlib.util
import unittest
from unittest.mock import patch

import doubao_signin as doubao


@unittest.skipUnless(importlib.util.find_spec("playwright"), "install requirements-doubao.txt for browser tests")
class ActivityPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        cls.addClassCleanup(playwright.stop)
        cls.browser = playwright.chromium.launch(channel="chromium")
        cls.addClassCleanup(cls.browser.close)

    def open(self, html, url=doubao.ACTIVITY_URL):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.route("**/*", lambda route: route.fulfill(body=html, content_type="text/html; charset=utf-8"))
        page.goto(url)
        return page

    def test_status_does_not_click_claim(self):
        page = self.open('<button onclick="this.remove()">立即签到</button>')
        self.assertEqual(doubao.check_activity(page, dry_run=True)[1]["result"], "DRY_RUN")
        self.assertTrue(page.get_by_text("立即签到").is_visible())

    def test_claim_requires_confirmed_state_and_rerun_skips(self):
        page = self.open("""<button onclick="this.replaceWith('签到成功')">立即签到</button>""")
        self.assertEqual(doubao.check_activity(page, dry_run=False)[1]["result"], "CLAIMED")
        self.assertEqual(doubao.check_activity(page, dry_run=False)[1]["result"], "ALREADY")

    def test_disappearing_button_is_not_proof_of_claim(self):
        page = self.open('<p>每日签到</p><button onclick="this.remove()">立即签到</button>')
        with patch.object(doubao, "monotonic", side_effect=[0, 0, 0, 13]):
            code, result = doubao.check_activity(page, dry_run=False)
        self.assertNotEqual(code, 0)
        self.assertEqual(result["result"], "UNCONFIRMED")

    def test_daily_card_checkmark_is_already_claimed(self):
        page = self.open("""
            <div class="card-test"><div>每日签到</div><div class="rewarded-test">✓</div></div>
            <div class="card-test"><button onclick="this.remove()">立即签到</button></div>
        """)
        self.assertEqual(doubao.check_activity(page, dry_run=False)[1]["result"], "ALREADY")
        self.assertTrue(page.get_by_text("立即签到").is_visible())

    def test_other_task_checkmark_does_not_count_for_daily_card(self):
        page = self.open("""
            <div class="card-test"><div>每日签到</div><button>立即签到</button></div>
            <div class="card-test"><div>选择工作领域</div><div class="rewarded-test">✓</div></div>
        """)
        self.assertEqual(doubao.check_activity(page, dry_run=True)[1]["result"], "DRY_RUN")

    def test_two_matching_buttons_fail_without_click(self):
        page = self.open('<button onclick="this.remove()">立即签到</button>' * 2)
        self.assertEqual(doubao.check_activity(page, dry_run=False)[1]["result"], "AMBIGUOUS")
        self.assertEqual(page.get_by_text("立即签到").count(), 2)


@unittest.skipUnless(importlib.util.find_spec("Crypto"), "install pycryptodome for cookie tests")
class DesktopSessionTests(unittest.TestCase):
    def test_reads_one_profile_and_only_current_doubao_cookies(self):
        import hashlib
        from pathlib import Path
        import sqlite3
        from tempfile import TemporaryDirectory

        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad

        password = b"synthetic"
        key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
        blob = b"v10" + AES.new(key, AES.MODE_CBC, b" " * 16).encrypt(
            pad(bytes(32) + b"synthetic-session", 16)
        )
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "Default"
            profile.mkdir()
            with sqlite3.connect(profile / "Cookies") as con:
                con.execute("""CREATE TABLE cookies (host_key, name, value, encrypted_value,
                                path, is_secure, is_httponly, expires_utc)""")
                con.executemany("INSERT INTO cookies VALUES (?, ?, '', ?, '/', 1, 1, ?)", (
                    (".doubao.com", "sessionid", blob, 0),
                    (".doubao.com", "expired", blob, 11644473601000000),
                    ("not-doubao.com", "unrelated", blob, 0),
                ))
            with patch.object(doubao, "CLIENT_DATA", root), \
                 patch.object(doubao.sys, "platform", "darwin"), \
                 patch.object(doubao, "_keychain_passwords", return_value=iter((b"wrong-key", password))), \
                 patch.dict(doubao.os.environ, {"DOUBAO_PROFILE": "Default"}):
                cookies = doubao.load_cookies()

        self.assertEqual([(c["name"], c["value"]) for c in cookies], [("sessionid", "synthetic-session")])

    def test_windows_cookie_requires_valid_gcm_tag_and_strips_domain_hash(self):
        import hashlib
        from Crypto.Cipher import AES

        key, nonce, host = b"k" * 32, b"n" * 12, ".doubao.com"
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(hashlib.sha256(host.encode()).digest() + b"session")
        blob = b"v10" + nonce + ciphertext + tag
        self.assertEqual(doubao._decrypt_cookie_windows(blob, key, host), "session")
        invalid = blob[:-1] + bytes([blob[-1] ^ 1])
        self.assertEqual(doubao._decrypt_cookie_windows(invalid, key, host), "")
