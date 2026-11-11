"""DuMate daily bonus using the persisted desktop login, without opening the app.

The desktop client stores its authenticated browser cookies in ``auth.json`` as
AES-256-GCM ciphertext (base64 of nonce || tag || ciphertext) and keeps the
32-byte key in ``.cookie-key`` next to it.  Replaying the same console request
with those cookies is enough; the local transparent proxy is not needed.
"""
import base64
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

from results import outcome

ORIGIN = "https://console.bce.baidu.com"
INFO_PATH = "/api/dumate/points/loginBonusInfo"
CLAIM_PATH = "/api/dumate/points/loginBonus"
APP_DATA = "qianfan-desktop-app"
VERSION_FALLBACK = "1.0.90"
HOST = "console.bce.baidu.com"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class AuthError(Exception):
    pass


class ApiError(Exception):
    pass


def _data_dir():
    override = os.environ.get("DUMATE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support" / APP_DATA


def _decrypt(encrypted, key):
    from Crypto.Cipher import AES

    raw = base64.b64decode(encrypted, validate=True)
    if len(raw) < 29:
        raise AuthError("百度搭子 Cookie 密文不完整")
    try:
        plain = AES.new(key, AES.MODE_GCM, nonce=raw[:12]).decrypt_and_verify(raw[28:], raw[12:28])
    except ValueError:
        raise AuthError("无法解密百度搭子 Cookie，请重新登录客户端") from None
    try:
        cookies = json.loads(plain)
    except ValueError:
        raise AuthError("百度搭子 Cookie 格式无法识别") from None
    if not isinstance(cookies, list) or not cookies:
        raise AuthError("百度搭子 Cookie 列表为空")
    return cookies


def _load_cookies():
    root = _data_dir()
    auth_file = root / "auth.json"
    key_file = root / ".cookie-key"
    if not auth_file.is_file() or not key_file.is_file():
        raise AuthError("未找到百度搭子登录态；请先在客户端登录一次")
    try:
        store = json.loads(auth_file.read_text(encoding="utf-8"))
        key = key_file.read_bytes()
    except (OSError, ValueError) as exc:
        raise AuthError(f"无法读取百度搭子登录态（{type(exc).__name__}）") from None
    if len(key) != 32:
        raise AuthError("百度搭子 Cookie 密钥长度异常")
    encrypted = store.get("cookies")
    if not encrypted:
        profiles = store.get("accountProfiles")
        if isinstance(profiles, list):
            active = store.get("activeProfileId")
            chosen = next((p for p in profiles if p.get("profileId") == active), None)
            if chosen is None and profiles:
                chosen = profiles[0]
            if isinstance(chosen, dict):
                encrypted = chosen.get("encryptedCookies")
    if not isinstance(encrypted, str) or not encrypted:
        raise AuthError("百度搭子登录态缺少 Cookie 字段")
    return _decrypt(encrypted, key)


def _domain_matches(domain, host):
    domain = (domain or "").lstrip(".")
    return not domain or host == domain or host.endswith("." + domain)


def _quote(value):
    value = (value or "").strip().strip('"').strip("'")
    return f'"{value}"' if re.search(r"[:|;=\s]", value) else value


def _cookie_header(cookies, host=HOST):
    return "; ".join(
        f'{cookie.get("name")}={_quote(cookie.get("value"))}'
        for cookie in cookies
        if cookie.get("name") and _domain_matches(cookie.get("domain"), host)
    )


def _csrf_token(cookies, host=HOST):
    for cookie in cookies:
        if cookie.get("name") == "bce-user-info" and _domain_matches(cookie.get("domain"), host):
            return (cookie.get("value") or "").strip().strip('"').strip("'")
    return ""


def _client_version():
    for app_dir in (Path("/Applications"), Path.home() / "Applications"):
        plist = app_dir / "DuMate.app/Contents/Info.plist"
        try:
            import plistlib

            with plist.open("rb") as handle:
                version = plistlib.load(handle).get("CFBundleShortVersionString")
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if isinstance(version, str) and re.fullmatch(r"\d+(?:\.\d+){1,4}", version):
            return version
    return VERSION_FALLBACK


def _request(claim=False):
    cookies = _load_cookies()
    cookie = _cookie_header(cookies)
    csrf = _csrf_token(cookies)
    if not csrf:
        raise AuthError("百度搭子登录态缺少 bce-user-info，请重新登录客户端")
    headers = {
        "Cookie": cookie,
        "csrftoken": csrf,
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "X-Dumate-Client-Type": "desktop",
        "dumate-version": _client_version(),
        "User-Agent": UA,
    }
    if claim:
        headers["Content-Type"] = "application/json"
        request = urllib.request.Request(ORIGIN + CLAIM_PATH, data=b"{}", method="POST", headers=headers)
    else:
        request = urllib.request.Request(ORIGIN + INFO_PATH, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            body = response.read(65536)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError("百度搭子登录态已失效，请在客户端重新登录") from None
        raise ApiError(f"百度搭子接口返回 HTTP {exc.code}") from None
    except (OSError, urllib.error.URLError) as exc:
        raise ApiError(f"无法连接百度搭子接口（{type(exc).__name__}）") from None
    if status != 200 or "json" not in content_type.casefold():
        raise AuthError("百度搭子接口要求重新登录")
    try:
        payload = json.loads(body)
    except ValueError:
        raise AuthError("百度搭子接口返回登录页，登录态已失效") from None
    if not isinstance(payload, dict):
        raise ApiError("百度搭子接口返回格式异常")
    if payload.get("success") is not True or payload.get("code") != 0:
        code = payload.get("code")
        if code in (401, 403):
            raise AuthError("百度搭子登录态已失效，请在客户端重新登录")
        raise ApiError(payload.get("message") or f"百度搭子接口错误（code={code}）")
    return payload.get("result")


def _state():
    state = _request()
    if not isinstance(state, dict) or type(state.get("hasIssued")) is not bool:
        raise ApiError("百度搭子未返回可确认的签到状态")
    return state


def _check(action):
    before = _state()
    if before["hasIssued"]:
        return outcome("ALREADY", "百度搭子今天已签到")
    if action == "status":
        return outcome("DRY_RUN", "百度搭子今日可签到，本次未领取")
    # A lost claim response is verified with a fresh read; never replay the POST.
    try:
        _request(claim=True)
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError:
        pass
    try:
        after = _state()
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError:
        after = {}
    if after.get("hasIssued") is True:
        old, new = before.get("totalPoints"), after.get("totalPoints")
        amount = f" {new - old} 积分" if type(old) is int and type(new) is int and new > old else ""
        return outcome("CLAIMED", f"百度搭子确认已签到{amount}")
    return outcome("UNCONFIRMED", "已尝试百度搭子签到，未确认到账；未再次发起领取", 1)


def run(action):
    if sys.platform not in ("darwin", "win32", "linux"):
        return outcome("UNSUPPORTED", "百度搭子自动领取尚未适配当前系统", 2)
    try:
        if action == "doctor":
            cookies = _load_cookies()
            if not _csrf_token(cookies):
                raise AuthError("百度搭子登录态缺少 bce-user-info，请重新登录客户端")
            return outcome("OK", "百度搭子登录态可读取（无需打开客户端）")
        return _check(action)
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError as exc:
        return outcome("ERROR", str(exc), 1)
    except Exception as exc:
        return outcome("ERROR", f"百度搭子运行失败（{type(exc).__name__}）", 1)
