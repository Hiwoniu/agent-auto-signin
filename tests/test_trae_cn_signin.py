import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

import trae_cn_signin as trae


def encrypted_blob(info, wrapped=None):
    wrapped = wrapped or b"w" * 32
    digest = hashlib.sha512(wrapped).digest()
    block = bytearray(digest + trae._MASK)
    block[:64] = hashlib.sha512(bytes(block)).digest()
    key, iv = bytes(block[:16]), bytes(block[16:32])
    payload = json.dumps(info).encode("utf-8")
    plain = hashlib.sha512(payload).digest() + payload
    ciphertext = AES.new(key, AES.MODE_CBC, iv).encrypt(pad(plain, 16))
    return base64.b64encode(trae.HEADER + wrapped + ciphertext).decode("ascii")


class CheckinTests(unittest.TestCase):
    def test_checked_in_never_posts(self):
        payload = {"code": 0, "enable": True, "checked_in": True, "credits": 100}
        with patch.object(trae, "_request", return_value=payload) as request:
            code, out = trae._check("run")
        self.assertEqual((code, out["result"]), (0, "ALREADY"))
        request.assert_called_once_with(trae.STATUS_PATH, {"req_source": 1})

    def test_available_state_is_dry_run(self):
        payload = {"code": 0, "enable": True, "checked_in": False, "credits": 100}
        with patch.object(trae, "_request", return_value=payload):
            code, out = trae._check("status")
        self.assertEqual((code, out["result"]), (0, "DRY_RUN"))
        self.assertIn("100 Credits", out["report"])

    def test_claim_is_verified_without_replay(self):
        before = {"code": 0, "enable": True, "checked_in": False, "credits": 100}
        after = {"code": 0, "enable": True, "checked_in": True, "credits": 100}
        with patch.object(trae, "_request", side_effect=[before, {}, after]) as request:
            code, out = trae._check("run")
        self.assertEqual((code, out["result"]), (0, "CLAIMED"))
        self.assertEqual(request.call_args_list[1].args, (trae.CLAIM_PATH, {"req_source": 1}))

    def test_disabled_account_is_inactive(self):
        payload = {"code": 0, "enable": False, "checked_in": False}
        with patch.object(trae, "_request", return_value=payload):
            code, out = trae._check("status")
        self.assertEqual((code, out["result"]), (0, "INACTIVE"))

    def test_decrypts_auth_blob(self):
        info = {"token": "token-value", "userId": "1", "host": "https://api.trae.cn"}
        self.assertEqual(trae._decrypt_blob(encrypted_blob(info)), info)

    def test_rejects_foreign_auth_blob(self):
        with self.assertRaises(trae.AuthError):
            trae._decrypt_blob(base64.b64encode(b"not-a-trae-blob").decode("ascii"))


class DeviceIdTests(unittest.TestCase):
    def _storage(self, root, payload):
        (root / "aha").mkdir()
        (root / "aha" / "TinyStorage").write_text(
            json.dumps({"tiny_storage_data": payload}), encoding="utf-8",
        )

    def test_reads_encrypted_device_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._storage(root, {"aha.device.device_id": encrypted_blob({"device_id_str": "1571676408017584"})})
            with patch.dict(os.environ, {"TRAE_CN_DATA_DIR": tmp}), patch.object(trae, "_DEVICE_ID", None):
                self.assertEqual(trae._device_id(), "1571676408017584")

    def test_missing_storage_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"TRAE_CN_DATA_DIR": tmp}), \
                patch.object(trae, "_DEVICE_ID", None):
            self.assertEqual(trae._device_id(), "")

    def test_claim_request_sends_device_header(self):
        calls = []

        class Response:
            status = 200
            headers = {"Content-Type": "application/json"}

            def read(self, size=-1):
                return b'{"code": 0, "message": "success"}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            calls.append(request)
            return Response()

        info = {"token": "token-value", "userId": "1", "host": "https://api.trae.cn"}
        with patch.object(trae, "_load_session", return_value=info), \
                patch.object(trae, "_device_id", return_value="device-1"), \
                patch.object(trae.urllib.request, "urlopen", fake_urlopen):
            trae._request(trae.CLAIM_PATH, {"req_source": 1})
        self.assertEqual(calls[0].get_header("X-device-id"), "device-1")


if __name__ == "__main__":
    unittest.main()
