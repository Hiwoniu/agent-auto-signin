"""豆包电脑版每日签到（macOS / Windows）。

活动入口、客户端 Cookie 格式参考 tututashu/doubao-auto-checkin（MIT）。
"""
from contextlib import closing
import base64
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import sys
from time import monotonic, time
from urllib.parse import urlsplit

ACTIVITY_URL = "https://www.doubao.com/pc/agent-explore"
CLIENT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 SamanthaDoubao/2.33.3"
)
CLIENT_DATA = Path.home() / "Library/Application Support/Doubao"
KEYCHAIN_SERVICES = ("Doubao Safe Storage", "Doubao Browser Safe Storage")
SESSION_NAMES = {"sessionid", "sessionid_ss", "sid_guard", "sid_tt"}
ALREADY_TEXT = {"已签到", "今日已签到", "今日已领", "明日再来"}
SUCCESS_TEXT = {"签到成功"}


class SetupError(Exception):
    pass


def _keychain_passwords():
    for service in KEYCHAIN_SERVICES:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-w", "-s", service],
                capture_output=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.rstrip(b"\r\n"):
            yield result.stdout.rstrip(b"\r\n")


def _decrypt_cookie(blob, key):
    from Crypto.Cipher import AES

    if not blob or blob[:3] not in (b"v10", b"v11") or (len(blob) - 3) % 16 or len(blob) < 51:
        return ""
    plaintext = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(blob[3:])
    pad = plaintext[-1]
    if not 1 <= pad <= 16 or plaintext[-pad:] != bytes([pad]) * pad:
        return ""
    try:
        return plaintext[32:-pad].decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _read_rows(db):
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3)) as con:
        return con.execute(
            "SELECT host_key, name, value, encrypted_value, path, is_secure, "
            "is_httponly, expires_utc FROM cookies "
            "WHERE host_key = 'doubao.com' OR host_key LIKE '%.doubao.com'"
        ).fetchall()


def _windows_roots():
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
    try:
        candidates = sorted(local.glob("*doubao*"))
    except OSError:
        return
    for directory in candidates:
        # The standalone Doubao Work profile is distinct; do not silently switch accounts.
        if "work" in directory.name.casefold():
            continue
        for root in (directory, directory / "User Data"):
            if (root / "Local State").is_file():
                yield root
                break


def _windows_key(root):
    from qoder_signin import _dpapi_unprotect

    data = json.loads((root / "Local State").read_text(encoding="utf-8"))
    encrypted = base64.b64decode(data["os_crypt"]["encrypted_key"], validate=True)
    if not encrypted.startswith(b"DPAPI"):
        raise SetupError("豆包 Windows 密钥不是当前支持的 DPAPI 格式")
    return _dpapi_unprotect(encrypted[5:])


def _decrypt_cookie_windows(blob, key, host):
    from Crypto.Cipher import AES

    if len(blob) < 31 or blob[:3] not in (b"v10", b"v11"):
        return ""
    try:
        value = AES.new(key, AES.MODE_GCM, nonce=blob[3:15]).decrypt_and_verify(blob[15:-16], blob[-16:])
        host_hash = hashlib.sha256(host.encode("utf-8")).digest()
        if value.startswith(host_hash):
            value = value[len(host_hash):]
        text = value.decode("utf-8")
        return text if all(character.isprintable() for character in text) else ""
    except (ValueError, UnicodeDecodeError):
        return ""


def _load_windows(profile):
    from qoder_signin import QoderAuthError

    for root in _windows_roots():
        db = next((path for path in (root / profile / "Network/Cookies", root / profile / "Cookies")
                   if path.is_file()), None)
        if db is None:
            continue
        try:
            rows = _read_rows(db)
            key = _windows_key(root)
        except (OSError, sqlite3.Error, ValueError, KeyError, QoderAuthError, SetupError):
            continue
        cookies = _cookies_for_key(rows, key, _decrypt_cookie_windows)
        if any(cookie["name"] in SESSION_NAMES for cookie in cookies):
            return cookies
    raise SetupError("无法读取豆包 Windows 登录态；请先登录，若 Cookie 库被占用，退出客户端后重试。v20 暂未支持")


def load_cookies():
    profile = os.environ.get("DOUBAO_PROFILE", "Default")
    if Path(profile).name != profile or profile in ("", ".", ".."):
        raise SetupError("DOUBAO_PROFILE 必须是客户端配置目录名")
    if sys.platform == "win32":
        return _load_windows(profile)
    if sys.platform != "darwin":
        raise SetupError("豆包适配目前支持 macOS 和 Windows")
    root = CLIENT_DATA / profile
    db = next((p for p in (root / "Cookies", root / "Network/Cookies") if p.is_file()), None)
    if db is None:
        raise SetupError("没有找到豆包电脑版的 Cookie 库；请安装并登录，或检查 DOUBAO_PROFILE")
    try:
        rows = _read_rows(db)
    except sqlite3.Error:
        raise SetupError("无法读取豆包 Cookie 库；请退出豆包客户端后重试") from None

    has_key = False
    for password in _keychain_passwords():
        has_key = True
        key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, dklen=16)
        cookies = _cookies_for_key(rows, key)
        if any(cookie["name"] in SESSION_NAMES for cookie in cookies):
            return cookies
    if not has_key:
        raise SetupError("无法读取豆包钥匙串；请确认已在豆包电脑版登录并允许本机钥匙串读取")
    raise SetupError("没有找到有效的豆包登录态；请在豆包电脑版重新登录")


def _cookies_for_key(rows, key, decrypt=None):
    cookies = []
    now = time()
    for host, name, value, blob, path, secure, http_only, expires in rows:
        expiry = expires / 1_000_000 - 11644473600 if expires else -1
        if expires and expiry <= now:
            continue
        value = (decrypt(blob, key, host) if decrypt else _decrypt_cookie(blob, key)) if blob else value
        if not value:
            continue
        cookies.append({
            "name": name, "value": value, "domain": host, "path": path or "/",
            "secure": bool(secure), "httpOnly": bool(http_only), "expires": expiry,
        })
    return cookies


def _result(status, report, code=0):
    return code, {"result": status, "report": report, "needs_attention": code != 0}


def _on_activity(page):
    url = urlsplit(page.url)
    return url.scheme == "https" and url.hostname == "www.doubao.com" and url.path.rstrip("/") == "/pc/agent-explore"


def _visible_actions(scope):
    # 页面可能用非 button 元素渲染 CTA。只找精确签到文案，避开其它「立即领取」活动。
    targets = scope.get_by_text("立即签到", exact=True).all()
    return [target for target in targets if target.is_visible() and target.is_enabled()]


def _state(page):
    if not _on_activity(page):
        return "UNKNOWN", None
    text = page.locator("body").inner_text(timeout=3000)
    if "仅支持在豆包电脑版" in text:
        return "CLIENT_ONLY", None
    if "扫码登录" in text or "登录后参与" in text:
        return "NO_AUTH", None
    lines = {line.strip() for line in text.splitlines()}
    cards = page.locator('div[class^="card-"], div[class*=" card-"]').filter(
        has=page.get_by_text("每日签到", exact=True),
    )
    if cards.count() > 1:
        return "AMBIGUOUS", None
    if cards.count() == 1:
        card = cards.first
        # 完成态只有勾图标、没有文字。仅检查每日签到卡片的直属状态元素。
        marks = card.locator(':scope > [class^="rewarded-"], :scope > [class*=" rewarded-"]')
        if any(mark.is_visible() for mark in marks.all()):
            return "ALREADY", None
        targets = _visible_actions(card)
    else:
        targets = _visible_actions(page)
    if len(targets) == 1:
        return "READY", targets[0]
    if len(targets) > 1:
        return "AMBIGUOUS", None
    if lines & SUCCESS_TEXT:
        return "SUCCESS", None
    if lines & ALREADY_TEXT:
        return "ALREADY", None
    return "UNKNOWN", None


def _wait_state(page, seconds, after_click=False):
    deadline = monotonic() + seconds
    while True:
        state, target = _state(page)
        if state in {"SUCCESS", "ALREADY", "NO_AUTH", "CLIENT_ONLY", "AMBIGUOUS"}:
            return state, target
        if not after_click and state == "READY":
            return state, target
        if monotonic() >= deadline:
            return "UNKNOWN", None
        page.wait_for_timeout(250)


def check_activity(page, dry_run):
    state, target = _wait_state(page, 12)
    if state in {"ALREADY", "SUCCESS"}:
        return _result("ALREADY", "豆包今天已签到")
    if state == "NO_AUTH":
        return _result(state, "豆包登录态失效，请重新登录桌面端", 2)
    if state == "CLIENT_ONLY":
        return _result(state, "豆包活动页未接受当前客户端通道", 1)
    if state != "READY":
        return _result(state, "没有找到唯一可操作的豆包签到按钮，请查看电脑版活动页", 1)
    if dry_run:
        return _result("DRY_RUN", "豆包签到按钮可用，本次未点击")

    target.click(timeout=5000)
    state, _ = _wait_state(page, 12, after_click=True)
    if state in {"SUCCESS", "ALREADY"}:
        return _result("CLAIMED", "豆包活动页已确认今日签到完成")
    return _result("UNCONFIRMED", "已尝试签到，但活动页没有明确确认；请到桌面端查看", 1)


def _client_ua():
    if sys.platform == "win32":
        return CLIENT_UA.replace("Macintosh; Intel Mac OS X 10_15_7", "Windows NT 10.0; Win64; x64")
    for app_dir in (Path("/Applications"), Path.home() / "Applications"):
        try:
            with (app_dir / "Doubao.app/Contents/Info.plist").open("rb") as handle:
                version = plistlib.load(handle).get("CFBundleShortVersionString", "")
        except (OSError, ValueError, plistlib.InvalidFileException):
            continue
        if isinstance(version, str) and re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
            return CLIENT_UA.rsplit("/", 1)[0] + "/" + version
    return CLIENT_UA


def run(action):
    from playwright.sync_api import Error, sync_playwright

    cookies = load_cookies()
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True, channel="chromium", timeout=15000)
        except Error:
            return _result("BROWSER", "无法启动 Chromium，请运行 python -m playwright install chromium", 2)
        try:
            if action == "doctor":
                return _result("OK", "豆包桌面登录态可读取，Chromium 可启动")
            context = browser.new_context(user_agent=_client_ua(), locale="zh-CN")
            context.add_cookies(cookies)
            page = context.new_page()
            response = page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=25000)
            if response is None or not response.ok:
                return _result("NETWORK", "无法打开豆包活动页", 1)
            return check_activity(page, dry_run=action == "status")
        except Error:
            return _result("BROWSER", "豆包活动页打开或交互失败，请在桌面端检查活动", 1)
        finally:
            browser.close()


def main(action):
    try:
        code, result = run(action)
    except SetupError as exc:
        code, result = _result("NO_AUTH", str(exc), 2)
    except ImportError:
        code, result = _result("DEPENDENCY", "请先安装 requirements-doubao.txt 中的依赖", 2)
    print(json.dumps(result, ensure_ascii=False))
    return code
