import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Crypto.Cipher import AES

import qwenwork_signin as qwen

SECRET = b"chrome-safe-storage"


def chrome_cookie(host, name, value, secret=SECRET):
    key = hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1003, 16)
    plain = hashlib.sha256(host.encode()).digest() + value.encode()
    pad = 16 - len(plain) % 16
    sealed = AES.new(key, AES.MODE_CBC, b" " * 16).encrypt(plain + bytes([pad]) * pad)
    return b"v10" + sealed


class ChromeCookieTests(unittest.TestCase):
    def _db(self, root, rows):
        db = root / "Cookies"
        con = sqlite3.connect(str(db))
        con.execute(
            "create table cookies (host_key text, name text, encrypted_value blob, "
            "path text, expires_utc integer, is_httponly integer)"
        )
        con.executemany("insert into cookies values (?,?,?,?,?,?)", rows)
        con.commit()
        con.close()
        return db

    def test_decrypts_session_cookie_for_qwenwork(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = self._db(root, [
                (".qwenwork.ai", "ory_hydra_session", chrome_cookie(".qwenwork.ai", "ory_hydra_session", "session-value"), "/", 0, 1),
                (".example.com", "ory_hydra_session", chrome_cookie(".example.com", "ory_hydra_session", "other"), "/", 0, 1),
            ])
            cookies = qwen._chrome_cookies(SECRET, path=db)
        self.assertEqual([c["name"] for c in cookies], ["ory_hydra_session"])
        self.assertEqual(cookies[0]["value"], "session-value")
        self.assertEqual(cookies[0]["domain"], ".qwenwork.ai")
        self.assertTrue(cookies[0]["httpOnly"])
        self.assertEqual(cookies[0]["expires"], -1)

    def test_plaintext_rows_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp), [(".qwenwork.ai", "token", b"plain-value", "/", 0, 1)])
            self.assertEqual(qwen._chrome_cookies(SECRET, path=db), [])

    def test_wrong_secret_yields_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._db(Path(tmp), [
                (".qwenwork.ai", "ory_hydra_session", chrome_cookie(".qwenwork.ai", "ory_hydra_session", "v", b"other"), "/", 0, 1),
            ])
            values = [c["value"] for c in qwen._chrome_cookies(SECRET, path=db)]
        self.assertNotIn("v", values)


class ImportSessionTests(unittest.TestCase):
    class Ctx:
        def __init__(self):
            self.added = None

        def add_cookies(self, cookies):
            self.added = cookies

    def test_without_session_cookie_skips_import(self):
        ctx = self.Ctx()
        with patch.object(qwen, "_chrome_secret", return_value=SECRET), \
                patch.object(qwen, "_chrome_cookies", return_value=[{"name": "_ga", "value": "x"}]):
            self.assertEqual(qwen._import_chrome_session(ctx), 0)
        self.assertIsNone(ctx.added)

    def test_copies_cookies_when_signed_in(self):
        ctx = self.Ctx()
        cookies = [{"name": "ory_hydra_session", "value": "s"}, {"name": "_ga", "value": "x"}]
        with patch.object(qwen, "_chrome_secret", return_value=SECRET), \
                patch.object(qwen, "_chrome_cookies", return_value=cookies):
            self.assertEqual(qwen._import_chrome_session(ctx), 2)
        self.assertEqual(ctx.added, cookies)


if __name__ == "__main__":
    unittest.main()
