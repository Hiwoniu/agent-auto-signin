"""macOS desktop claim flows using agent-desktop's semantic accessibility actions."""
from dataclasses import dataclass
import os
import re
import shutil
import subprocess
import json
import time
from pathlib import Path


@dataclass(frozen=True)
class Product:
    bundle: str
    app: str
    entries: tuple[tuple[str, ...], ...]
    ready: tuple[str, ...]
    done: tuple[str, ...]
    scope: tuple[str, ...] = ()


PRODUCTS = {
    "wps-lingxi": Product("com.wps.lingxi", "WPS 灵犀.app",
                         (("个人可用智点：*",), ("任务中心",)), ("立即签到",),
                         ("距离下次签到", "今日已签到"), ("每日签到",)),
    "trae-cn": Product("cn.trae.app", "Trae CN.app", (("每日签到",),),
                       ("立即签到", "签到领取", "签到"), ("今日已签到", "今日已签", "已签到", "签到成功"),
                       ("每日签到",)),

}
LOGIN = {"登录", "个人登录", "立即登录", "手机号登录", "Log In", "Log in", "Sign in", "Sign In"}


def outcome(status, report, code=0):
    return code, {"result": status, "report": report, "needs_attention": code != 0}


class DesktopError(Exception):
    def __init__(self, code):
        self.code = code


def _cli(*args):
    executable = os.environ.get("AGENT_SIGNIN_DESKTOP") or shutil.which("agent-desktop")
    if not executable:
        raise DesktopError("DEPENDENCY")
    try:
        result = subprocess.run([executable, *args], capture_output=True, text=True, timeout=22)
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise DesktopError("DESKTOP_ERROR") from None
    if not data.get("ok"):
        raise DesktopError(data.get("error", {}).get("code", "DESKTOP_ERROR"))
    return data.get("data")


def _nodes(tree):
    yield tree
    for child in tree.get("children", []):
        yield from _nodes(child)


def _labels(node):
    return {node[key].strip() for key in ("name", "description", "value")
            if isinstance(node.get(key), str) and node[key].strip()}


def _matches(tree, names):
    def matches(node):
        return any(label == name or (name.endswith("*") and label.startswith(name[:-1]))
                   for name in names for label in _labels(node))

    return [node for node in _nodes(tree)
            if matches(node) and node.get("ref_id")
            and "Click" in node.get("available_actions", [])
            and not {"disabled", "hidden", "offscreen"}.intersection(node.get("states", []))]


def _snapshot(product):
    try:
        result = _cli("snapshot", "--app", product.bundle, "--compact", "--max-depth", "22", "--timeout-ms", "7000")
    except DesktopError as exc:
        if exc.code != "AMBIGUOUS_TARGET":
            raise
        windows = _cli("list-windows", "--app", product.bundle)
        matches = [w for w in windows if w.get("visible") and w.get("accessible")]
        if any(re.search(r"登录|log\s*in|sign\s*in", w.get("title", ""), re.I) for w in matches):
            raise DesktopError("NO_AUTH") from None
        if len(matches) != 1:
            raise
        result = _cli("snapshot", "--window-id", matches[0]["id"], "--compact", "--max-depth", "22", "--timeout-ms", "7000")
    if not result.get("complete", True):
        raise DesktopError("PARTIAL_SNAPSHOT")
    return result["tree"]


def _view(tree, product):
    labels = set().union(*(_labels(node) for node in _nodes(tree)))
    anchored = not product.scope or any(
        mark.casefold() in label.casefold() for mark in product.scope for label in labels
    )
    if product.bundle == "cn.trae.app":
        def daily(label):
            return bool(re.fullmatch(r"每日领(?:取)?\s*\d+\s*积分", label))

        articles = [node for node in _nodes(tree) if node.get("role") == "article"
                    and any(daily(label) for label in _labels(node))]
        menus = [node for node in _nodes(tree) if node.get("role") == "dialog"
                 and (descendant_labels := set().union(*(_labels(c) for c in _nodes(node))))
                 and "管理账户" in descendant_labels and any(daily(label) for label in descendant_labels)]
        scopes = articles or menus
        if len(scopes) > 1:
            return "AMBIGUOUS", None
        if scopes:
            tree = scopes[0]
            labels = set().union(*(_labels(node) for node in _nodes(tree)))
        anchored = bool(scopes)
    if anchored:
        for label in labels:
            if label in product.done or (
                "距离下次签到" in product.done and label.startswith("距离下次签到")
            ):
                return "ALREADY", None
        ready = _matches(tree, product.ready)
        if len(ready) == 1:
            return "READY", ready[0]["ref_id"]
        if len(ready) > 1:
            return "AMBIGUOUS", None
    if _matches(tree, LOGIN):
        return "NO_AUTH", None
    return "UNKNOWN", None


def _open_panel(product, tree):
    for names in product.entries:
        state, _ = _view(tree, product)
        if state != "UNKNOWN":
            return tree
        targets = _matches(tree, names)
        if not targets and product.bundle == "cn.trae.app":
            # TRAE labels the account button with its avatar name, account name and plan.
            targets = [node for node in _nodes(tree) if node.get("role") == "button"
                       and node.get("ref_id") and "Click" in node.get("available_actions", [])
                       and "disabled" not in node.get("states", [])
                       and any(re.fullmatch(r"(.+?)\s+\1\s+(?:免费|专业版|旗舰版|Free|Pro)", label)
                               for label in _labels(node))]
        if len(targets) == 1:
            target = targets[0]
            if "expanded" in target.get("states", []):
                continue
            _cli("click", target["ref_id"])
            tree = _snapshot(product)
        elif len(targets) > 1:
            raise DesktopError("AMBIGUOUS_TARGET")
    return tree


def _installed(product):
    return any((root / product.app).is_dir() for root in (Path("/Applications"), Path.home() / "Applications"))


def run(platform, action):
    product = PRODUCTS[platform]
    if not _installed(product):
        return outcome("NOT_INSTALLED", f"未找到 {product.app}", 2)
    try:
        permissions = _cli("permissions")
        if permissions.get("accessibility", {}).get("state") != "granted":
            return outcome("PERMISSION", "需要给运行 agent-desktop 的进程授予 macOS 辅助功能权限", 2)
        if action == "doctor":
            return outcome("OK", "客户端已安装，agent-desktop 辅助功能权限可用")
        try:
            tree = _snapshot(product)
        except DesktopError as exc:
            if exc.code not in {"APP_NOT_FOUND", "WINDOW_NOT_FOUND"}:
                raise
            _cli("launch", product.bundle, "--activate", "--timeout", "15000")
            tree = _snapshot(product)
        tree = _open_panel(product, tree)
        status, ref = _view(tree, product)
        if status == "ALREADY":
            return outcome(status, "客户端显示本轮已领取")
        if status == "NO_AUTH":
            return outcome(status, "请在对应客户端完成一次个人账号登录", 2)
        if status != "READY":
            return outcome(status, "未能确认签到入口；请打开客户端的每日签到或权益面板后再试", 1)
        if action == "status":
            return outcome("DRY_RUN", "已找到签到按钮，本次未点击")

        # AXPress may have reached the app even if it reports an error. Never replay it.
        try:
            _cli("click", ref)
        except DesktopError:
            pass
        for delay in (0, .4, 1.0, 2.0):
            time.sleep(delay)
            status, _ = _view(_snapshot(product), product)
            if status == "ALREADY":
                return outcome("CLAIMED", "客户端确认本轮已领取")
        return outcome("UNCONFIRMED", "已尝试点击，客户端尚未确认到账；未再次点击", 1)
    except DesktopError as exc:
        if exc.code == "NO_AUTH":
            return outcome(exc.code, "请在对应客户端完成一次个人账号登录", 2)
        return outcome(exc.code, "未能读取或操作客户端；请检查窗口及辅助功能权限", 1)
