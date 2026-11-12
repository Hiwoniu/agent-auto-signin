"""Visit the QwenWork personal web app to refresh its login bonus."""
import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from results import outcome
from platform_paths import state_dir

URL = "https://qwenwork.ai/app/"
# Aliyun ESA serves a slider page to plain headless browsers; a real Chrome UA plus
# hiding the automation flag keeps the workspace reachable without a manual drag.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
STEALTH_SCRIPT = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"

CHROME_COOKIES = Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies"
SESSION_COOKIE = "ory_hydra_session"


def _profile():
    return Path(os.environ.get("AGENT_SIGNIN_QWEN_PROFILE", str(state_dir() / "qwenwork-browser"))).expanduser()


def _chrome_secret():
    """Read Chrome's cookie key from the login keychain, or None when unavailable."""
    try:
        done = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"],
            capture_output=True, timeout=8, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def _chrome_cookies(secret, path=None):
    """Decrypt the qwenwork cookies Chrome already holds for this Mac user."""
    from Crypto.Cipher import AES

    db = Path(path or os.environ.get("AGENT_SIGNIN_CHROME_COOKIES", str(CHROME_COOKIES))).expanduser()
    if not secret or not db.is_file():
        return []
    key = hashlib.pbkdf2_hmac("sha1", secret, b"saltysalt", 1003, 16)
    try:
        source = sqlite3.connect(f"file:{db}?immutable=1", uri=True)
        rows = source.execute(
            "select host_key, name, encrypted_value, path, expires_utc, is_httponly "
            "from cookies where host_key like '%qwenwork.ai'"
        ).fetchall()
        source.close()
    except sqlite3.Error:
        return []
    cookies = []
    for host, name, value, cookie_path, expires, http_only in rows:
        if not value or value[:3] not in (b"v10", b"v11"):
            continue
        try:
            plain = AES.new(key, AES.MODE_CBC, b" " * 16).decrypt(value[3:])
            plain = plain[:-plain[-1]]
        except ValueError:
            continue
        # Chrome 127+ binds the cookie to its host with a SHA-256 prefix.
        if plain[:32] == hashlib.sha256(host.encode()).digest():
            plain = plain[32:]
        try:
            text = plain.decode("utf-8")
        except UnicodeDecodeError:
            continue
        cookies.append({
            "name": name,
            "value": text,
            "domain": host if host.startswith(".") else "." + host,
            "path": cookie_path or "/",
            "expires": expires / 1_000_000 - 11644473600 if expires else -1,
            "httpOnly": bool(http_only),
            "secure": True,
            "sameSite": "Lax",
        })
    return cookies


def _import_chrome_session(ctx):
    """Copy Chrome's signed-in qwenwork session into the project browser. Returns count."""
    cookies = _chrome_cookies(_chrome_secret())
    if not any(cookie["name"] == SESSION_COOKIE for cookie in cookies):
        return 0
    ctx.add_cookies(cookies)
    return len(cookies)


def _check(page):
    url = urlsplit(page.url)
    if url.scheme != "https" or url.hostname not in {"qwenwork.ai", "www.qwenwork.ai"}:
        return "NO_AUTH"
    if not url.path.startswith("/app"):
        return "NO_AUTH"
    # Aliyun ESA shows a slider page instead of the workspace for automated visits.
    body = page.locator("body").inner_text(timeout=5000)
    if any(marker in body for marker in (
        "请完成以下操作，验证您是真人", "按住滑块", "Request ID：",
    )) or "滑动验证" in page.title():
        return "CAPTCHA"
    # The signed-out demo also shows a New task button. Check its Sign in control first.
    for name in ("Sign in", "Log in", "登录"):
        if any(control.is_visible() for control in page.get_by_role(
            "button", name=name, exact=True,
        ).all()):
            return "NO_AUTH"
    lower = body.casefold()
    if any(marker in lower for marker in (
        "sign in to qwenwork", "log in to qwenwork", "continue with google", "登录千问", "扫码登录",
    )):
        return "NO_AUTH"
    if any(marker in lower for marker in ("daily login bonus received", "每日登录奖励已到账")):
        return "ALREADY"
    if any(marker in lower for marker in (
        "my ai team", "new task", "new conversation", "我的 ai 团队", "新建任务",
        "新任务", "定时任务", "技能·扩展", "im 频道", "积分余额",
    )):
        return "VISITED"
    return "UNCONFIRMED"


def run(action):
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError:
        return outcome("DEPENDENCY", "千问办公需要 Playwright 和 Chromium", 2)

    profile = _profile()
    if action not in {"login", "doctor"} and not (profile / "Local State").exists():
        return outcome("NO_AUTH", "请在项目的千问办公浏览器登录一次：agent_signin.py login qwenwork", 2)
    try:
        with sync_playwright() as playwright:
            if action == "doctor":
                browser = playwright.chromium.launch(channel="chromium", timeout=12000)
                browser.close()
                if not (profile / "Local State").exists():
                    return outcome("NO_AUTH", "Chromium 可启动；尚未设置项目的千问办公浏览器登录态", 2)
                return outcome("OK", "Chromium 可启动，千问办公浏览器配置存在")

            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            ctx = playwright.chromium.launch_persistent_context(
                str(profile), headless=action != "login", channel="chromium", timeout=15000,
                user_agent=UA, locale="zh-CN", args=LAUNCH_ARGS,
            )
            try:
                ctx.add_init_script(STEALTH_SCRIPT)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                if action == "login":
                    sign_in = page.get_by_role("button", name="Sign in", exact=True).or_(
                        page.get_by_role("button", name="登录", exact=True)
                    )
                    try:
                        sign_in.first.wait_for(state="visible", timeout=3500)
                        if sign_in.count() == 1:
                            sign_in.click(timeout=4000)
                    except Error:
                        pass
                    print("请在浏览器确认已进入千问办公个人工作区；如已登录，直接回终端按 Enter。", flush=True)
                    input()
                try:
                    page.get_by_text("新建任务", exact=True).or_(
                        page.get_by_text("新任务", exact=True)
                    ).or_(
                        page.get_by_text("New Task", exact=True)
                    ).first.wait_for(timeout=4500)
                except Error:
                    pass
                state = _check(page)
                imported = 0
                if action != "login" and state not in {"VISITED", "ALREADY"}:
                    page.wait_for_timeout(2500)
                    state = _check(page)
                if action != "login" and state in {"NO_AUTH", "UNCONFIRMED"}:
                    imported = _import_chrome_session(ctx)
                    if imported:
                        page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2500)
                        state = _check(page)
                if state == "CAPTCHA":
                    return outcome(state, "千问办公停在阿里云滑块验证页；需在有界面浏览器过一次验证", 1)
                if state == "NO_AUTH":
                    hint = "千问办公需要登录，请进入个人工作区"
                    if imported:
                        hint = "Chrome 里的千问会话已过期，请在客户端或浏览器重新登录一次"
                    return outcome(state, hint, 2)
                if state == "ALREADY":
                    return outcome(state, "页面已确认每日登录奖励")
                if state == "VISITED":
                    note = f"（复用 Chrome 登录态，导入 {imported} 个 Cookie）" if imported else ""
                    return outcome(state, f"已访问登录后的工作区{note}；每日奖励由平台发放，未核对到账")
                return outcome(state, "已打开千问办公，但无法确认登录态或奖励到账", 1)
            finally:
                ctx.close()
    except (Error, EOFError):
        return outcome("BROWSER", "无法打开千问办公浏览器；请检查网络或关闭项目已打开的登录窗口", 2)
