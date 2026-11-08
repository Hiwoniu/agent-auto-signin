"""International Qoder daily campaign using the separate macOS desktop session."""
from pathlib import Path
import os
import re
import sys
import time
import urllib.request

from desktop_signin import outcome
import qoder_signin as shared

ORIGIN = "https://openapi.qoder.sh"
APP = "Qoder.app"
APP_DATA = "com.qoder.app.stable"
SERVICE = "Qoder App Safe Storage"
ACCOUNT = "Qoder App Key"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Api(shared.Api):
    def _open(self, request):
        return urllib.request.build_opener(_NoRedirect()).open(
            request, timeout=shared.REQUEST_TIMEOUT,
        )


def _load():
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / APP_DATA
        if not (root / shared.AUTH_FILE).is_file():
            raise shared.NoAuthError("请先在 Qoder 国际版桌面端完成登录")
        try:
            return shared._read_auth_store_retry(root)
        except (OSError, ValueError) as exc:
            raise shared.AuthFormatError("Qoder 国际版登录态格式无法读取") from exc
    if sys.platform != "darwin":
        raise shared.NoAuthError("Qoder 国际版自动读取登录态目前支持 macOS 和 Windows")
    root = Path.home() / "Library/Application Support" / APP_DATA
    if not (root / shared.AUTH_FILE).is_file():
        raise shared.NoAuthError("请先在 Qoder 国际版桌面端完成登录")
    runtime = next((path for base in (Path("/Applications"), Path.home() / "Applications")
                    if (path := shared._mac_runtime(base / APP)) and Path(path).is_file()), None)
    if runtime is None:
        raise shared.NoAuthError("未找到 Qoder 国际版桌面端")
    password = shared._mac_keychain_password(SERVICE, ACCOUNT)
    return shared._read_mac_session_retry(runtime, root, password)


def _daily_campaigns(payload, now):
    for row in shared._campaigns(payload):
        benefit = row.get("benefit")
        start, end = row.get("startAt"), row.get("endAt")
        ident = row.get("campaignId")
        if (row.get("actionType") != "CLAIM_BENEFIT" or not isinstance(benefit, dict)
                or benefit.get("kind") != "CREDITS" or benefit.get("amount") != 100
                or type(start) is not int or type(end) is not int
                or not start <= now < end or not 23 * 3600 <= end - start <= 25 * 3600
                or start % 86400 != 2 * 3600
                or not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", ident)):
            continue
        yield row


def _check(api, action):
    http, payload, _ = api.campaigns(retry=False)
    if http in (401, 403):
        return outcome("AUTH_REJECTED", "请检查 Qoder 国际版登录态或活动资格", 2)
    if http != 200 or not isinstance(payload, dict) or not isinstance(payload.get("campaigns"), list):
        return outcome("ERROR", f"无法查询 Qoder 国际版权益（HTTP {http}）", 1)
    rows = list(_daily_campaigns(payload, time.time()))
    if not rows:
        return outcome("INACTIVE", "当前未找到 Qoder 国际版每日 100 Credits 活动")
    if len(rows) != 1:
        return outcome("AMBIGUOUS", "发现多条每日奖励，未尝试领取", 1)
    daily = rows[0]
    if daily.get("claimStatus") == "CLAIMED":
        return outcome("ALREADY", "Qoder 国际版本轮 100 Credits 已领取")
    if daily.get("claimStatus") != "CLAIMABLE":
        return outcome("PENDING", "Qoder 国际版每日活动目前不可领取", 1)
    if action == "status":
        return outcome("DRY_RUN", "Qoder 国际版可领取 100 Credits，本次未领取")

    ident = daily["campaignId"]
    code, body, _ = api.claim(ident, retry=False)
    data = body.get("data", body) if isinstance(body, dict) else None
    if code == 200 and isinstance(data, dict) and data.get("status") == "CLAIMED":
        if data.get("replayed"):
            return outcome("ALREADY", "Qoder 国际版本轮 100 Credits 已领取")
        return outcome("CLAIMED", "Qoder 国际版已领取 100 Credits")
    # A delayed or lost claim response must not cause a second POST.
    verify_code, after, _ = api.campaigns(retry=False)
    if verify_code == 200 and any(row.get("campaignId") == ident and row.get("claimStatus") == "CLAIMED"
                                  for row in shared._campaigns(after)):
        return outcome("CLAIMED", "Qoder 国际版已确认本轮 100 Credits 已领取")
    return outcome("UNCONFIRMED", f"未能确认 Qoder 国际版领取结果（HTTP {code}）", 1)


def run(action):
    try:
        session = _load()
    except shared.NoAuthError:
        return outcome("NO_AUTH", "请确认 Qoder 国际版已登录，且系统允许读取它的钥匙串", 2)
    except shared.QoderAuthError:
        return outcome("AUTH_ERROR", "Qoder 国际版登录态格式无法读取", 2)
    if shared._token_expired(session):
        return outcome("AUTH_REJECTED", "Qoder 国际版登录态已过期，请在客户端重新登录", 2)
    if action == "doctor":
        return outcome("OK", "Qoder 国际版桌面登录态可读")
    return _check(Api(session["token"], ORIGIN), action)
