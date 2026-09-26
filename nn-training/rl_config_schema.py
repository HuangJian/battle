"""rl_config_schema —— rl-config.json 的键白名单校验（plan/rl-config-cleanup.plan.md §3.4）。

**只告警不拒**：未知键 / 已退役键在启动日志里点出来，但绝不 block 开训——把「配置里多了
一个手写键」变成「训练起不来」，代价远大于收益（用户裁决 O3，2026-09-25）。

为什么要有它：rl-config 的键只在运行时被 `cfg.get(...)` 顺手读，**拼错/退役的键会静默沉睡**
（`intent_rl` 就这样沉睡到 2026-09-26）。白名单把「这个键还有人读吗」变成一条可执行的断言。

白名单数据住 `rl_config.schema.json`；**本模块是它今天唯一的消费者**。

⚠ 2026-09-26 评审更正：本 docstring 与 schema 的 `_doc` 曾写「与 dashboard `stack/smoke.ts`
共用同一份——一处增删，两侧同时生效」，**那是错的**：`dashboard/src/**` 对该 JSON 零引用，
`stack/smoke.ts::rlConfigSmoke` 仍只查 port/token/nodes。控制台侧接线**尚未做** ⇒
plan §3.4/E8 的「塞假键 ⇒ 冒烟面板也点出来」**未达成**（`docs/nn/rl-config.md §1.7` 已如实登记）。
改 dashboard 时顺手接上，那时再恢复「共用一份」的说法。

`courses` 块与 `nodes` 表是自由形状（每课/每节点自定），只查顶层与 `policy`/`rl` 两段的键名。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

#: 白名单数据的家（本模块同目录）。
SCHEMA_PATH = Path(__file__).resolve().parent / "rl_config.schema.json"


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """读白名单数据；读不到/坏形状 → 空 dict（**校验自身的故障不得变成训练的故障**）。"""
    try:
        data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def retired_keys() -> dict[str, str]:
    """已退役键 → 原因（`policy.upgradeBranch` 这类点号路径）。"""
    retired = load_schema().get("retired")
    return dict(retired) if isinstance(retired, dict) else {}


def allowed_keys(section: str) -> frozenset[str]:
    """某段（`policy`/`rl`）允许的键名集合；未知段 → 空集合。"""
    sections = load_schema().get("sections")
    keys = sections.get(section) if isinstance(sections, dict) else None
    return frozenset(keys) if isinstance(keys, list) else frozenset()


def check_rl_config(cfg: dict[str, Any]) -> list[str]:
    """返回告警行（空列表 = 干净）。**纯函数，永不抛**。

    三类命中：
      · 顶层键不在白名单（`version/policy/nodes/rl/courses`）⇒ 未知顶层段；
      · `policy.*` / `rl.*` 的键不在白名单 ⇒ 未知键（多半是拼错）；
      · 命中 `retired` ⇒ 已退役键（带原因，比「未知」更具体）。
    自由形状段（`nodes`/`courses`/`rl.remote_hubs`/`rl.intent`/`rl.goal`）不下钻。
    """
    schema = load_schema()
    if not schema or not isinstance(cfg, dict) or not cfg:
        return []
    top = schema.get("top")
    top_set = set(top) if isinstance(top, list) else set()
    freeform = schema.get("freeform")
    freeform_set = set(freeform) if isinstance(freeform, list) else set()
    retired = retired_keys()
    advisories: list[str] = []

    for key in cfg:
        dotted = str(key)
        if dotted in retired:
            advisories.append(f"{dotted}（已退役）：{retired[dotted]}")
        elif top_set and dotted not in top_set:
            advisories.append(f"{dotted}：未知顶层段（白名单 {'/'.join(sorted(top_set))}）")

    for section in ("policy", "rl"):
        block = cfg.get(section)
        if not isinstance(block, dict):
            continue
        allowed = allowed_keys(section)
        for key in block:
            dotted = f"{section}.{key}"
            if dotted in freeform_set:
                continue  # 自由形状（键名由课程/节点/模式自定义）
            if dotted in retired:
                advisories.append(f"{dotted}（已退役）：{retired[dotted]}")
            elif allowed and key not in allowed:
                advisories.append(f"{dotted}：未知键（白名单没有它——拼错？或该写进 curricula/*.jsonc）")
    return advisories
