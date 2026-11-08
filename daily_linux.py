"""Install a per-user systemd timer on Linux."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from platform_paths import log_dir

ROOT = Path(__file__).resolve().parent
NAME = "agent-auto-signin"
SERVICE = f"{NAME}.service"
TIMER = f"{NAME}.timer"


def _arg(value):
    value = str(value)
    if any(character in value for character in "\n\r\0"):
        raise ValueError("路径中不能包含换行或 NUL")
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{value}"'


def units(python, hour, minute):
    command = " ".join(map(_arg, (
        python, ROOT / "scheduled_run.py", "all", "--write-logs", "--hour", hour, "--minute", minute,
    )))
    return {
        SERVICE: f"[Unit]\nDescription=Agent daily credits\n\n[Service]\nType=oneshot\nExecStart={command}\n",
        TIMER: """[Unit]
Description=Check Beijing-time Agent schedules

[Timer]
OnActiveSec=1min
OnUnitInactiveSec=1min
AccuracySec=1s

[Install]
WantedBy=timers.target
""",
    }


def _write(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _systemctl(*args, check=True):
    return subprocess.run(["systemctl", "--user", *args], check=check).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="安装或检查当前 Linux 用户的北京时间定时任务")
    parser.add_argument("action", choices=("install", "status", "uninstall"))
    parser.add_argument("--hour", type=int, choices=range(24), default=9, metavar="0-23")
    parser.add_argument("--minute", type=int, choices=range(60), default=30, metavar="0-59")
    args = parser.parse_args(argv)
    if not sys.platform.startswith("linux"):
        parser.error("此命令需要 Linux 和 systemd 用户会话")

    unit_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user"
    if args.action == "status":
        return _systemctl("status", TIMER, "--no-pager", check=False)
    if args.action == "uninstall":
        if (unit_dir / TIMER).exists():
            _systemctl("disable", "--now", TIMER)
        for name in (SERVICE, TIMER):
            (unit_dir / name).unlink(missing_ok=True)
        return _systemctl("daemon-reload")

    unit_dir.mkdir(parents=True, exist_ok=True)
    for name, content in units(Path(os.path.abspath(sys.executable)), args.hour, args.minute).items():
        _write(unit_dir / name, content)
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", TIMER)
    _systemctl("restart", TIMER)
    _systemctl("is-active", TIMER)
    print(f"每日 {args.hour:02d}:{args.minute:02d}（北京时间）；日志：{log_dir()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"systemd 用户任务操作失败：{exc}", file=sys.stderr)
        raise SystemExit(2)
