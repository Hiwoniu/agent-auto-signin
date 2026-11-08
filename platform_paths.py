"""Per-user state and log locations for the supported host systems."""
import os
from pathlib import Path
import sys


def state_dir():
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library/Application Support/agent-auto-signin"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or home / "AppData/Local") / "agent-auto-signin"
    return Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share") / "agent-auto-signin"


def log_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library/Logs/agent-auto-signin"
    if sys.platform == "win32":
        return state_dir() / "logs"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "agent-auto-signin"
