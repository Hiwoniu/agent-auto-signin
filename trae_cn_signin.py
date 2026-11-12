"""TRAE CN daily check-in using the persisted desktop token, no app needed.

The client stores the authenticated account blob in
``User/globalStorage/storage.json`` under ``iCubeAuthInfo://...``.  The blob is
AES-CBC encrypted with a random key wrapped by a fixed mask (the format used by
the client's ``byteCrypto`` module).  Decrypting it offline yields the access
token accepted by ``api.trae.cn`` as ``Cloud-IDE-JWT``.

The claim endpoint also requires the client device id that the desktop app sends
as ``x-device-id``.  It lives in ``aha/TinyStorage`` under
``aha.device.device_id``, encrypted with the same scheme; without the header the
API answers ``9004 The submitted order parameters are incorrect``.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from results import outcome

ORIGIN = "https://api.trae.cn"
STATUS_PATH = "/trae/api/v2/ug/checkin_credits/status"
CLAIM_PATH = "/trae/api/v2/ug/checkin_credits/claim"
APP_NAME = "Trae CN"
HOST = "api.trae.cn"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)
HEADER = bytes([116, 99, 5, 16, 0, 0])
_DEVICE_ID = None
_IHE = bytes([
    82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251,
    124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203,
    84, 123, 148, 50, 166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78,
    8, 46, 161, 102, 40, 217, 36, 178, 118, 91, 162, 73, 109, 139, 209, 37,
])
_RHE = bytes([
    31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95,
    96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239,
    160, 224, 59, 77, 174, 42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97,
    23, 43, 4, 126, 186, 119, 214, 38, 225, 105, 20, 99, 85, 33, 12, 125,
])
_MASK = bytes(left ^ right for left, right in zip(_IHE, _RHE))


class AuthError(Exception):
    pass


class ApiError(Exception):
    pass


def _data_dir():
    override = os.environ.get("TRAE_CN_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support" / APP_NAME


def _decrypt(value, label="登录态"):
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import unpad

    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise AuthError(f"TRAE {label}不是有效的 base64") from None
    if len(raw) < 38 or raw[:6] != HEADER:
        raise AuthError(f"TRAE {label}格式不受支持，请重新登录客户端")
    wrapped = raw[6:38]
    digest = hashlib.sha512(wrapped).digest()
    block = bytearray(digest + _MASK)
    block[:64] = hashlib.sha512(bytes(block)).digest()
    key, iv = bytes(block[:16]), bytes(block[16:32])
    try:
        plain = unpad(AES.new(key, AES.MODE_CBC, iv).decrypt(raw[38:]), 16)
    except ValueError:
        raise AuthError(f"无法解密 TRAE {label}，请重新登录客户端") from None
    if len(plain) < 65 or hashlib.sha512(plain[64:]).digest() != plain[:64]:
        raise AuthError(f"TRAE {label}校验失败，请重新登录客户端")
    try:
        info = json.loads(plain[64:])
    except ValueError:
        raise AuthError(f"TRAE {label}内容无法解析") from None
    if not isinstance(info, dict):
        raise AuthError(f"TRAE {label}内容格式异常")
    return info


def _decrypt_blob(value):
    return _decrypt(value)


def _device_id():
    """Return the desktop client device id used by the claim endpoint."""
    global _DEVICE_ID
    if _DEVICE_ID is not None:
        return _DEVICE_ID
    _DEVICE_ID = ""
    try:
        store = json.loads((_data_dir() / "aha/TinyStorage").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _DEVICE_ID
    data = store.get("tiny_storage_data") if isinstance(store, dict) else None
    value = data.get("aha.device.device_id") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        return _DEVICE_ID
    try:
        record = _decrypt(value, "设备标识")
    except AuthError:
        return _DEVICE_ID
    device = record.get("device_id_str")
    if isinstance(device, str) and device:
        _DEVICE_ID = device
    return _DEVICE_ID


def _load_session():
    storage = _data_dir() / "User/globalStorage/storage.json"
    if not storage.is_file():
        raise AuthError("未找到 TRAE 登录态；请先在客户端登录一次")
    try:
        store = json.loads(storage.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthError(f"无法读取 TRAE 登录态（{type(exc).__name__}）") from None
    if not isinstance(store, dict):
        raise AuthError("TRAE 登录态文件格式异常")
    candidates = []
    for key, value in store.items():
        if not isinstance(key, str) or not key.startswith("iCubeAuthInfo://") or not isinstance(value, str):
            continue
        try:
            info = _decrypt_blob(value)
        except AuthError:
            continue
        if isinstance(info.get("token"), str) and info["token"]:
            candidates.append(info)
    if not candidates:
        raise AuthError("TRAE 登录态中没有可用令牌，请重新登录客户端")
    return next((info for info in candidates if str(info.get("host", "")).endswith("trae.cn")), candidates[0])


def _expired(info):
    value = info.get("expiredAt")
    if not isinstance(value, str):
        return False
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= datetime.now(timezone.utc)


def _request(path, body):
    info = _load_session()
    if _expired(info):
        raise AuthError("TRAE 登录态已过期，请在客户端重新登录一次")
    headers = {
        "Authorization": f"Cloud-IDE-JWT {info['token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-User-Region": (info.get("userRegion") or {}).get("region", "CN")
        if isinstance(info.get("userRegion"), dict) else "CN",
        "User-Agent": UA,
    }
    device = _device_id()
    if device:
        headers["x-device-id"] = device
    request = urllib.request.Request(
        ORIGIN + path, data=json.dumps(body).encode("utf-8"), method="POST", headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(65536)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError("TRAE 登录态已失效，请在客户端重新登录") from None
        raise ApiError(f"TRAE 接口返回 HTTP {exc.code}") from None
    except (OSError, urllib.error.URLError) as exc:
        raise ApiError(f"无法连接 TRAE 接口（{type(exc).__name__}）") from None
    if status != 200 or "json" not in content_type.casefold():
        raise AuthError("TRAE 接口要求重新登录")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise AuthError("TRAE 接口返回登录页，登录态已失效") from None
    if not isinstance(payload, dict):
        raise ApiError("TRAE 接口返回格式异常")
    code = payload.get("code")
    if code == 1001:
        raise AuthError("TRAE 登录态未被接口接受，请在客户端重新登录")
    if isinstance(code, int) and code != 0:
        raise ApiError(payload.get("message") or f"TRAE 接口错误（code={code}）")
    return payload


def _status():
    payload = _request(STATUS_PATH, {"req_source": 1})
    if type(payload.get("enable")) is not bool or type(payload.get("checked_in")) is not bool:
        raise ApiError("TRAE 未返回可确认的签到状态")
    return payload


def _check(action):
    before = _status()
    if not before["enable"]:
        return outcome("INACTIVE", "TRAE 当前账号未开通每日签到活动")
    if before["checked_in"]:
        return outcome("ALREADY", "TRAE 今日已签到")
    reward = before.get("credits") or before.get("extra_credits")
    label = f"（{reward} Credits）" if isinstance(reward, int) and reward > 0 else ""
    if action == "status":
        return outcome("DRY_RUN", f"TRAE 今日可签到{label}，本次未领取")
    detail = ""
    try:
        _request(CLAIM_PATH, {"req_source": 1})
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError as exc:
        detail = f"（{exc}）"
    # Confirm with a fresh status read; never replay the claim.
    try:
        after = _status()
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError:
        after = {}
    if after.get("checked_in") is True:
        return outcome("CLAIMED", f"TRAE 确认已签到{label}")
    return outcome("UNCONFIRMED", f"已尝试 TRAE 签到{detail}，未确认到账；未再次发起领取", 1)


def run(action):
    try:
        if action == "doctor":
            info = _load_session()
            if _expired(info):
                raise AuthError("TRAE 登录态已过期，请在客户端重新登录一次")
            return outcome("OK", "TRAE 登录态可读取（无需打开客户端）")
        return _check(action)
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError as exc:
        return outcome("ERROR", str(exc), 1)
    except Exception as exc:
        return outcome("ERROR", f"TRAE 运行失败（{type(exc).__name__}）", 1)
