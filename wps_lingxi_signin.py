"""WPS Lingxi daily check-in using the persisted browser session, no app needed.

The Electron client keeps the authenticated ``.wps.cn`` cookies in
``Partitions/lingxi-business-v2-*/Cookies``.  The task-center API accepts those
cookies directly, so the client does not have to be running.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

from results import outcome

ORIGIN = "https://lingxi.wps.cn"
TASKS_PATH = "/api/public/v1/tasks"
CLAIM_PATH = "/api/public/v1/tasks/daily_check_in/claim"
TASK_KEY = "daily_check_in"
HOST = "lingxi.wps.cn"
APP_NAME = "WPS 灵犀"
KEYCHAIN_SERVICE = "WPS 灵犀 Safe Storage"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


class AuthError(Exception):
    pass


class ApiError(Exception):
    pass


def _data_dir():
    override = os.environ.get("WPS_LINGXI_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library/Application Support" / APP_NAME


def _keychain_password():
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", KEYCHAIN_SERVICE],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.rstrip(b"\r\n") or None


def _decrypt_value(blob, password, host):
    if blob[:3] not in (b"v10", b"v11") or not password:
        return ""
    try:
        from Crypto.Cipher import AES
    except ImportError:
        return ""
    key = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, dklen=16)
    try:
        plain = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(blob[3:])
    except ValueError:
        return ""
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        return ""
    plain = plain[:-pad]
    # Chromium prefixes the value with SHA-256(host_key).
    if len(plain) > 32:
        plain = plain[32:]
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _cookie_dbs():
    root = _data_dir()
    candidates = [root / "Cookies"]
    partitions = root / "Partitions"
    if partitions.is_dir():
        candidates.extend(sorted(partitions.glob("*/Cookies")))
    return [path for path in candidates if path.is_file()]


def _read_rows(path):
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error:
        return []
    try:
        return connection.execute(
            "SELECT host_key, name, value, encrypted_value, expires_utc FROM cookies"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()


def _load_cookies():
    password = None
    merged = {}
    now = time.time()
    for db in _cookie_dbs():
        for host_key, name, value, encrypted, expires_utc in _read_rows(db):
            if not name:
                continue
            expiry = expires_utc / 1_000_000 - 11644473600 if expires_utc else -1
            if expires_utc and expiry <= now:
                continue
            if not value and encrypted:
                if password is None:
                    password = _keychain_password()
                value = _decrypt_value(encrypted, password, host_key)
            if not value:
                continue
            merged.setdefault((host_key, name), value)
    cookies = [
        {"domain": host_key, "name": name, "value": value}
        for (host_key, name), value in merged.items()
    ]
    if not any(cookie["name"] == "wps_sid" for cookie in cookies):
        raise AuthError("未找到 WPS 灵犀登录态；请先在客户端登录一次")
    return cookies


def _domain_matches(domain, host):
    domain = (domain or "").lstrip(".")
    return not domain or host == domain or host.endswith("." + domain)


def _cookie_header(cookies, host=HOST):
    def quote(value):
        value = (value or "").strip().strip('"')
        return f'"{value}"' if re.search(r"[:|;=\s]", value) else value

    return "; ".join(
        f'{cookie["name"]}={quote(cookie["value"])}'
        for cookie in cookies
        if _domain_matches(cookie.get("domain"), host)
    )


def _request(method, path, body=None):
    cookies = _load_cookies()
    cookie = _cookie_header(cookies)
    if not cookie:
        raise AuthError("WPS 灵犀登录态不包含目标域 Cookie")
    headers = {
        "Cookie": cookie,
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "Accept": "application/json, text/plain, */*",
        "User-Agent": UA,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(ORIGIN + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = response.status
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(65536)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise AuthError("WPS 灵犀登录态已失效，请在客户端重新登录") from None
        raise ApiError(f"WPS 灵犀接口返回 HTTP {exc.code}") from None
    except (OSError, urllib.error.URLError) as exc:
        raise ApiError(f"无法连接 WPS 灵犀接口（{type(exc).__name__}）") from None
    if status != 200 or "json" not in content_type.casefold():
        raise AuthError("WPS 灵犀接口要求重新登录")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise AuthError("WPS 灵犀接口返回登录页，登录态已失效") from None
    if not isinstance(payload, dict):
        raise ApiError("WPS 灵犀接口返回格式异常")
    if payload.get("result") != "ok":
        code = payload.get("code") or payload.get("v7code")
        hint = payload.get("hint") or payload.get("message") or ""
        if code in (401, 403) or "登录" in hint:
            raise AuthError(hint or "WPS 灵犀登录态已失效，请在客户端重新登录")
        raise ApiError(hint or f"WPS 灵犀接口错误（code={code}）")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ApiError("WPS 灵犀接口缺少任务数据")
    return data


def _daily(data):
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        raise ApiError("WPS 灵犀任务列表格式异常")
    daily = next((task for task in tasks if isinstance(task, dict) and task.get("task_key") == TASK_KEY), None)
    if daily is None:
        raise ApiError("WPS 灵犀未返回每日签到任务")
    extra = daily.get("extra") if isinstance(daily.get("extra"), dict) else {}
    days = extra.get("check_in_days") if isinstance(extra.get("check_in_days"), list) else []
    today = next((day for day in days if isinstance(day, dict) and day.get("day") == extra.get("today_day")), {})
    return daily, extra, today


def _state():
    daily, extra, today = _daily(_request("GET", TASKS_PATH))
    status = daily.get("status")
    day_status = today.get("status")
    if status == "claimed" or day_status == "claimed":
        return "ALREADY", daily, extra, today
    if day_status == "available" or status in ("available", "claimable"):
        return "READY", daily, extra, today
    return "UNKNOWN", daily, extra, today


def _check(action):
    state, daily, extra, today = _state()
    if state == "ALREADY":
        return outcome("ALREADY", "WPS 灵犀今日已签到")
    if state != "READY":
        return outcome("UNCONFIRMED", "未确认 WPS 灵犀签到入口状态", 1)
    reward = today.get("reward") or extra.get("today_reward") or daily.get("reward_amount")
    label = f"（{reward} 智点）" if isinstance(reward, int) and reward > 0 else ""
    if action == "status":
        return outcome("DRY_RUN", f"WPS 灵犀今日可签到{label}，本次未领取")
    try:
        _request("POST", CLAIM_PATH, body=b"")
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError:
        pass
    # The claim response can be lost; confirm with a fresh read and never replay.
    try:
        after_state, _, _, _ = _state()
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError:
        after_state = "UNKNOWN"
    if after_state == "ALREADY":
        return outcome("CLAIMED", f"WPS 灵犀确认已签到{label}")
    return outcome("UNCONFIRMED", "已尝试 WPS 灵犀签到，未确认到账；未再次发起领取", 1)


def run(action):
    try:
        if action == "doctor":
            cookies = _load_cookies()
            if not any(cookie["name"] == "wps_sid" for cookie in cookies):
                raise AuthError("WPS 灵犀登录态缺少 wps_sid，请重新登录客户端")
            return outcome("OK", "WPS 灵犀登录态可读取（无需打开客户端）")
        return _check(action)
    except AuthError as exc:
        return outcome("NO_AUTH", str(exc), 2)
    except ApiError as exc:
        return outcome("ERROR", str(exc), 1)
    except Exception as exc:
        return outcome("ERROR", f"WPS 灵犀运行失败（{type(exc).__name__}）", 1)
