"""DuMate daily bonus through its running desktop session proxy."""
import http.client
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import time

from desktop_signin import outcome

APP_NAME = "DuMate.app"
LOG = Path.home() / "Library/Application Support/qianfan-desktop-app/log/electron.log"
ORIGIN = "https://console.bce.baidu.com"
API = "/api/dumate/points/"
PORT_RE = re.compile(rb"Transparent proxy started on 127\.0\.0\.1:([0-9]{1,5})(?![0-9])")


class ProxyError(Exception):
    pass


def _app():
    return next((root / APP_NAME for root in (Path("/Applications"), Path.home() / "Applications")
                 if (root / APP_NAME).is_dir()), None)


def _command(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _proxy_port(app):
    executable = str(app / "Contents/MacOS/DuMate")
    pids = [p for p in _command("/usr/bin/pgrep", "-x", "DuMate").split() if p.isdecimal()
            and _command("/bin/ps", "-p", p, "-o", "comm=").strip() == executable]
    if not pids:
        return None
    names = _command("/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(pids),
                     "-iTCP", "-sTCP:LISTEN", "-Fn")
    owned = set(re.findall(r"(?m)^n127\.0\.0\.1:([0-9]{1,5})$", names))
    try:
        with LOG.open("rb") as handle:
            handle.seek(max(0, LOG.stat().st_size - 4 * 1024 * 1024))
            ports = PORT_RE.findall(handle.read())
    except OSError:
        return None
    return next((int(p) for p in reversed(ports) if p.decode() in owned), None)


def _request(port, version, *, claim=False):
    path = ORIGIN + API + ("loginBonus" if claim else "loginBonusInfo")
    headers = {
        "Host": "console.bce.baidu.com",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Dumate-Client-Type": "desktop",
        "dumate-version": version,
    }
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
    try:
        connection.request("POST" if claim else "GET", path, body=b"{}" if claim else None, headers=headers)
        response = connection.getresponse()
        if response.status in (301, 302, 401, 403):
            raise ProxyError("NO_AUTH")
        if response.status != 200:
            raise ProxyError("HTTP_ERROR")
        payload = json.loads(response.read(65536))
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ProxyError("NETWORK") from exc
    finally:
        connection.close()
    if not isinstance(payload, dict):
        raise ProxyError("UNKNOWN")
    if payload.get("success") is not True or payload.get("code") != 0:
        raise ProxyError("NOT_AVAILABLE")
    return payload.get("result")


def _state(port, version):
    state = _request(port, version)
    if not isinstance(state, dict) or type(state.get("hasIssued")) is not bool:
        raise ProxyError("UNKNOWN")
    return state


def _check(port, version, action):
    try:
        before = _state(port, version)
    except ProxyError as exc:
        if str(exc) == "NO_AUTH":
            return outcome("NO_AUTH", "百度搭子登录已失效，请在桌面端重新登录", 2)
        return outcome(str(exc), "百度搭子未返回可确认的签到状态", 1)
    if before["hasIssued"]:
        return outcome("ALREADY", "百度搭子今天已签到")
    if action == "status":
        return outcome("DRY_RUN", "百度搭子今日可签到，本次未领取")

    # Verify against a fresh read even when the claim response is lost. Never replay.
    try:
        _request(port, version, claim=True)
    except ProxyError:
        pass
    try:
        after = _state(port, version)
    except ProxyError:
        after = {}
    if after.get("hasIssued") is True:
        old, new = before.get("totalPoints"), after.get("totalPoints")
        amount = f" {new - old} 积分" if type(old) is int and type(new) is int and new > old else ""
        return outcome("CLAIMED", f"百度搭子确认已签到{amount}")
    return outcome("UNCONFIRMED", "已尝试百度搭子签到，未确认到账；未再次发起领取", 1)


def run(action):
    app = _app() if sys.platform == "darwin" else None
    if app is None:
        return outcome("NOT_INSTALLED", "未找到百度搭子 macOS 客户端", 2)
    try:
        with (app / "Contents/Info.plist").open("rb") as handle:
            version = plistlib.load(handle)["CFBundleShortVersionString"]
        if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){1,4}", version):
            raise ValueError
    except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
        return outcome("UNKNOWN", "无法识别百度搭子客户端版本", 2)

    port = _proxy_port(app)
    if action == "doctor":
        if port:
            return outcome("OK", "百度搭子已安装，客户端本地接口可用")
        return outcome("NO_SESSION", "请打开已登录的百度搭子客户端", 2)
    if port is None:
        _command("/usr/bin/open", "-g", "-a", str(app))
        for _ in range(8):
            time.sleep(1)
            port = _proxy_port(app)
            if port:
                break
    if port is None:
        return outcome("NO_SESSION", "百度搭子本地接口未就绪；请打开客户端并确认已登录", 2)
    return _check(port, version, action)
