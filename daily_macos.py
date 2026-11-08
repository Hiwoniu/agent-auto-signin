"""Register or remove the per-user macOS launchd job."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

LABEL = "com.xiangming.agent-auto-signin"
QODER_LABEL = f"{LABEL}.qoder"
ROOT = Path(__file__).resolve().parent


def launch_plist(python, desktop, hour, minute, logs):
    return {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(ROOT / "scheduled_run.py"), "daily",
                             "--hour", str(hour), "--minute", str(minute)],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"AGENT_SIGNIN_DESKTOP": str(desktop),
                                 "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StartInterval": 60,
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(logs / "daily.jsonl"),
        "StandardErrorPath": str(logs / "daily.err"),
    }


def qoder_plist(python, logs):
    # An elapsed-time wakeup leaves Beijing-time eligibility to scheduled_run.py.
    return {
        "Label": QODER_LABEL,
        "ProgramArguments": [str(python), str(ROOT / "scheduled_run.py"), "qoder"],
        "WorkingDirectory": str(ROOT),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StartInterval": 60,
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(logs / "qoder.jsonl"),
        "StandardErrorPath": str(logs / "qoder.err"),
    }


def _launchctl(*args, check=True, capture_output=False):
    return subprocess.run(["/bin/launchctl", *map(str, args)], check=check, capture_output=capture_output)


def _install(domain, agents, config):
    label = config["Label"]
    service = f"{domain}/{label}"
    plist = agents / f"{label}.plist"
    with tempfile.NamedTemporaryFile(dir=agents, prefix=f".{label}.", delete=False) as handle:
        tmp = Path(handle.name)
        plistlib.dump(config, handle)
    try:
        if _launchctl("print", service, check=False, capture_output=True).returncode == 0:
            _launchctl("bootout", service)
        tmp.replace(plist)
    finally:
        tmp.unlink(missing_ok=True)
    _launchctl("bootstrap", domain, plist)
    _launchctl("print", service, capture_output=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="管理当前 macOS 用户的每日积分任务")
    parser.add_argument("action", choices=("install", "status", "uninstall"))
    parser.add_argument("--hour", type=int, choices=range(24), default=9, metavar="0-23")
    parser.add_argument("--minute", type=int, choices=range(60), default=30, metavar="0-59")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("此命令只支持 macOS")

    domain = f"gui/{os.getuid()}"
    agents = Path.home() / "Library/LaunchAgents"
    logs = Path.home() / "Library/Logs/agent-auto-signin"
    if args.action == "status":
        return max(_launchctl("print", f"{domain}/{label}", check=False).returncode
                   for label in (LABEL, QODER_LABEL))
    if args.action == "uninstall":
        for label in (LABEL, QODER_LABEL):
            _launchctl("bootout", f"{domain}/{label}", check=False, capture_output=True)
            (agents / f"{label}.plist").unlink(missing_ok=True)
        return 0

    desktop = os.environ.get("AGENT_SIGNIN_DESKTOP") or shutil.which("agent-desktop")
    if not desktop or not Path(desktop).is_file():
        parser.error("未找到 agent-desktop")
    python = Path(os.path.abspath(sys.executable))
    agents.mkdir(parents=True, exist_ok=True)
    logs.mkdir(mode=0o700, parents=True, exist_ok=True)

    _install(domain, agents, qoder_plist(python, logs))
    _install(domain, agents, launch_plist(python, Path(desktop).absolute(), args.hour, args.minute, logs))
    print(f"Qoder 两版：每日 10:05（UTC+8）起；日志：{logs / 'qoder.jsonl'}")
    print(f"其余平台：每日 {args.hour:02d}:{args.minute:02d}（北京时间 UTC+8）；日志：{logs / 'daily.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
