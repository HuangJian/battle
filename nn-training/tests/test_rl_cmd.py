"""rl/cmd.py — 本地 rollout 命令模板的 flag 口径锁死。

回归背景（2026-09-12 c6-gae 核验）：`args_rollout_overrides()` 的键是下划线
（`lives_override`/`player_level`），`build_rollout_cmd` 曾用 `f\"--{k}\"` 原样
拼出 `--lives_override`，而 `export-rl-rollout.ts` 只认连字符
`--lives-override`/`--player-level`（未知 flag 静默忽略）——本地直跑全程以
hard 缺省（3命1星）跑，远端以课程覆盖（1命0星）跑，rollout 胜率虚高 ~17pp。
本文件把导出器接受的连字符口径钉死：下划线变体一律视为 bug。
"""
from __future__ import annotations

import types

from rl.cmd import build_rollout_cmd


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "goal_rollout": False,
        "intent_rollout": False,
        "max_ticks": 2400,
        "difficulty": "hard",
        "dodge": "",
        "lives_override": None,
        "player_level": None,
        "course_obj": None,
        "course_path": "",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _cmd(**kw) -> list[str]:
    return build_rollout_cmd(
        "bun", _args(**kw),
        weights="tmp/w.json", out_dir="tmp/out", stage=2000, seed=7,
        wver="abc", node_label="local",
    )


def test_overrides_use_hyphen_flags() -> None:
    cmd = _cmd(lives_override=1, player_level=0)
    assert "--lives-override" in cmd
    assert "--player-level" in cmd
    assert cmd[cmd.index("--lives-override") + 1] == "1"
    assert cmd[cmd.index("--player-level") + 1] == "0"


def test_no_underscore_flag_variants() -> None:
    """下划线 flag 会被导出器静默丢弃——出现即 bug（c6-gae 根因）。"""
    cmd = _cmd(lives_override=1, player_level=0)
    assert "--lives_override" not in cmd
    assert "--player_level" not in cmd


def test_no_overrides_no_flags() -> None:
    cmd = _cmd()
    assert "--lives-override" not in cmd
    assert "--player-level" not in cmd
    assert "--lives_override" not in cmd
    assert "--player_level" not in cmd
