"""Visit the QwenWork personal web app to refresh its login bonus."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from desktop_signin import outcome
from platform_paths import state_dir

URL = "https://qwenwork.ai/app/"


def _profile():
    return Path(os.environ.get("AGENT_SIGNIN_QWEN_PROFILE", str(state_dir() / "qwenwork-browser"))).expanduser()


def _check(page):
    url = urlsplit(page.url)
    if url.scheme != "https" or url.hostname not in {"qwenwork.ai", "www.qwenwork.ai"}:
        return "NO_AUTH"
    if not url.path.startswith("/app"):
        return "NO_AUTH"
    # The signed-out demo also shows a New task button. Check its Sign in control first.
    for name in ("Sign in", "Log in", "登录"):
        if any(control.is_visible() for control in page.get_by_role(
            "button", name=name, exact=True,
        ).all()):
            return "NO_AUTH"
    body = page.locator("body").inner_text(timeout=5000)
    lower = body.casefold()
    if any(marker in lower for marker in (
        "sign in to qwenwork", "log in to qwenwork", "continue with google", "登录千问", "扫码登录",
    )):
        return "NO_AUTH"
    if any(marker in lower for marker in ("daily login bonus received", "每日登录奖励已到账")):
        return "ALREADY"
    if any(marker in lower for marker in (
        "my ai team", "new task", "new conversation", "我的 ai 团队", "新建任务",
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
            )
            try:
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
                        page.get_by_text("New Task", exact=True)
                    ).first.wait_for(timeout=4500)
                except Error:
                    pass
                state = _check(page)
                if state == "NO_AUTH":
                    return outcome(state, "千问办公需要登录，请进入个人工作区", 2)
                if state == "ALREADY":
                    return outcome(state, "页面已确认每日登录奖励")
                if state == "VISITED":
                    return outcome(state, "已访问登录后的工作区；每日奖励由平台发放，未核对到账")
                return outcome(state, "已打开千问办公，但无法确认登录态或奖励到账", 1)
            finally:
                ctx.close()
    except (Error, EOFError):
        return outcome("BROWSER", "无法打开千问办公浏览器；请检查网络或关闭项目已打开的登录窗口", 2)
