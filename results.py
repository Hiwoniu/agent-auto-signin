"""Shared result envelope for platform runners."""


def outcome(status, report, code=0):
    return code, {"result": status, "report": report, "needs_attention": code != 0}
