"""统一运行各平台的每日签到。"""
import argparse
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import json
import sys


PLATFORMS = ("workbuddy", "doubao", "qoder-cn", "dumate", "qwenwork",
             "wps-lingxi", "trae-cn", "qoder-global", "workbuddy-global")


def supported_platforms(host=None):
    host = sys.platform if host is None else host
    if host == "darwin":
        return PLATFORMS
    if host == "win32":
        return ("workbuddy", "doubao", "qoder-cn", "qwenwork", "qoder-global")
    if host.startswith("linux"):
        return ("workbuddy", "qwenwork")
    return ()


def platform_supported(platform):
    return platform in supported_platforms() or (
        sys.platform.startswith("linux") and platform == "qoder-cn"
    )


def _invoke(platform, action):
    if platform == "workbuddy":
        import signin

        return signin._run({"run": "daily", "status": "preview", "doctor": "doctor"}[action])
    if platform == "qoder-cn":
        import qoder_signin

        return qoder_signin.main({"run": ["auto"], "status": ["auto", "--dry-run"],
                                 "doctor": ["doctor"]}[action])

    if platform == "doubao":
        import doubao_signin

        return doubao_signin.main(action)
    from results import outcome

    if platform == "dumate":
        import dumate_signin

        code, result = dumate_signin.run(action)
    elif platform == "wps-lingxi":
        import wps_lingxi_signin

        code, result = wps_lingxi_signin.run(action)
    elif platform == "trae-cn":
        import trae_cn_signin

        code, result = trae_cn_signin.run(action)
    elif platform == "qoder-global":
        import qoder_global_signin

        code, result = qoder_global_signin.run(action)
    elif platform == "qwenwork":
        import qwenwork_signin

        code, result = qwenwork_signin.run(action)
    elif platform == "workbuddy-global":
        roots = (Path("/Applications"), Path.home() / "Applications")
        installed = any((root / name).is_dir() for root in roots
                        for name in ("WorkBuddy Global.app", "WorkBuddy AI.app"))
        if installed:
            code, result = outcome("ACTIVITY_REQUIRED",
                                   "当天需在国际版客户端发起至少一条对话，奖励次日发放")
        else:
            code, result = outcome("NOT_INSTALLED", "未找到 WorkBuddy 国际版客户端", 2)
    else:
        code, result = outcome("UNSUPPORTED", "此平台尚未适配当前系统", 2)
    print(json.dumps(result, ensure_ascii=False))
    return code


def _run_one(platform, action):
    captured = StringIO()
    try:
        with redirect_stdout(captured):
            code = _invoke(platform, action)
        result = json.loads(captured.getvalue())
        if not isinstance(result, dict) or not isinstance(result.get("result"), str):
            raise ValueError("missing result")
    except Exception as exc:
        code, result = 2, {"result": "ERROR", "report": f"运行失败（{type(exc).__name__}）"}

    return code, {
        "platform": platform,
        "result": result["result"],
        "report": result.get("report", ""),
        "needs_attention": bool(code or result.get("needs_attention")),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="在本机查询或领取 Agent 每日签到奖励")
    parser.add_argument("action", choices=("status", "run", "doctor", "login"),
                        help="status 查状态；run 执行领取；doctor 检查环境；login 配置千问办公网页登录")
    parser.add_argument("platforms", nargs="+", choices=(*PLATFORMS, "all"))
    args = parser.parse_args(argv)
    if "all" in args.platforms and args.platforms != ["all"]:
        parser.error("all 不能和单个平台一起使用")

    if args.action == "login":
        if args.platforms != ["qwenwork"]:
            parser.error("login 仅适用于 qwenwork")
        import qwenwork_signin

        code, result = qwenwork_signin.run("login")
        print(json.dumps({"platform": "qwenwork", **result}, ensure_ascii=False))
        return code

    platforms = supported_platforms() if args.platforms == ["all"] else dict.fromkeys(args.platforms)
    if not platforms:
        parser.error("当前系统没有可运行的平台")
    codes = []
    for platform in platforms:
        if not platform_supported(platform):
            code, result = 2, {"platform": platform, "result": "UNSUPPORTED",
                               "report": "此平台的自动领取尚未适配当前系统",
                               "needs_attention": True}
        else:
            code, result = _run_one(platform, args.action)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        codes.append(code or int(result["needs_attention"]))
    return max(codes)


if __name__ == "__main__":
    raise SystemExit(main())
