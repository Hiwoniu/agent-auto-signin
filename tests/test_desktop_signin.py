import unittest
from unittest.mock import patch

import desktop_signin as desk


def button(name, ref):
    return {"role": "button", "name": name, "ref_id": ref, "available_actions": ["Click"]}


def article(name, *children):
    return {"role": "article", "name": name, "children": children}


class DesktopClaimTests(unittest.TestCase):
    def test_ambiguous_delivery_is_verified_without_retry(self):
        anchor = {"name": "每日签到与任务奖励"}
        ready = {"children": [anchor, button("立即签到", "@daily")]}
        done = {"children": [anchor, {"name": "距离下次签到 12:00:00"}]}
        with patch.object(desk, "_installed", return_value=True), \
             patch.object(desk, "_snapshot", side_effect=[ready, done]), \
             patch.object(desk, "_cli", side_effect=[
                 {"accessibility": {"state": "granted"}}, desk.DesktopError("TIMEOUT"),
             ]) as cli:
            code, result = desk.run("wps-lingxi", "run")
        self.assertEqual((code, result["result"]), (0, "CLAIMED"))
        self.assertEqual([call.args for call in cli.call_args_list], [
            ("permissions",), ("click", "@daily"),
        ])

    def test_trae_uses_daily_card_instead_of_duplicate_menu_button(self):
        tree = {"children": [
            article("每日领 100 积分", button("签到", "@daily")),
            {"role": "dialog", "children": [
                {"name": "每日领 100 积分"}, button("签到", "@menu"),
                button("管理账户", "@account"),
            ]},
        ]}
        self.assertEqual(desk._view(tree, desk.PRODUCTS["trae-cn"]), ("READY", "@daily"))
