"""rl/modes.py — RL 入口模式注册表 + 启动参数合并。

DECISIONS §307 三模式基础设施（RL 入口整合 2026-09-01）：
  per-tick   逐 tick move/fire PPO（ppo.py）
  intent     意图步 semi-MDP PPO（ppo_intent.py，8 类意图，变步长 GAE γ^Δt）
  goal       goal 承诺步 PPO（ppo_goal.py，676/169 路目标格，心跳承诺期）

常量
  _MODES             合法 --mode 值的元组
  _MODE_BACKENDS     模式 → PPO 后端模块 的注册表（stream.py 的 backend 契约：
                     update / _ppo_load / load_episodes / chunk_episodes 四者都实现，
                     ppo.py 尾部 update = ppo_update 别名）
  _MODE_BACKUP_PREFIX 模式 → 归档文件前缀（backup_weights 按前缀分桶 prune）

函数
  resolve_mode()         两阶段 argparse 第一阶段：从 argv 预解析 --mode / --goal
  apply_mode_flags()     按 --mode 置位采集器/权重桶 flag（--goal 别名 → goal）
  merged_mode_args()     配置块合并（rl.<mode> → intent_rl(legacy) → rl）
"""
from __future__ import annotations

import argparse
from pathlib import Path

import ppo as ppo_mod
import ppo_goal
import ppo_intent

REPO_ROOT = Path(__file__).resolve().parent.parent

_MODES: tuple[str, ...] = ("per-tick", "intent", "goal")
_MODE_BACKENDS: dict[str, object] = {
    "per-tick": ppo_mod,
    "intent": ppo_intent,
    "goal": ppo_goal,
}
_MODE_BACKUP_PREFIX: dict[str, str] = {
    "per-tick": "rl-weights",
    "intent": "intent-rl-weights",
    "goal": "goal-rl-weights",
}
DEFAULT_EVAL_AT_INTENT = "5,10,15"


def resolve_mode(argv: list[str]) -> str:
    """从 argv 预解析 --mode / --goal（两阶段 argparse 的第一阶段）。纯函数。"""
    mode = "per-tick"
    for i, a in enumerate(argv):
        if a == "--mode" and i + 1 < len(argv):
            mode = argv[i + 1]
        elif a.startswith("--mode="):
            mode = a.split("=", 1)[1]
        elif a == "--goal":
            mode = "goal"
    return mode if mode in _MODES else mode  # invalid values surface via argparse


def apply_mode_flags(args: argparse.Namespace) -> argparse.Namespace:
    """按 --mode 置位采集器 / 权重桶 flag。--goal 别名 → mode=goal。"""
    if getattr(args, "goal", False):
        args.mode = "goal"
    args.intent_rollout = args.mode == "intent"
    args.goal_rollout = args.mode == "goal"
    args.goal = args.mode == "goal"
    return args


def merged_mode_args(
    cfg: dict, mode: str
) -> tuple[dict, dict]:
    """启动参数默认合并（单一事实来源，DECISIONS §307 D2）。纯函数。

    查找优先级（高→低）：rl.<mode> → intent_rl 遗留块（intent/goal 迁移期）→ rl。
    返回 (合并后的默认字典, {key: 来源块}) 供 trust-but-verify 日志。
    """
    rl_block = dict(cfg.get("rl", {}) or {})
    mode_key = "intent" if mode == "intent" else ("goal" if mode == "goal" else "")
    nested = dict((cfg.get("rl", {}) or {}).get(mode_key, {}) or {}) if mode_key else {}
    legacy = {}
    if mode in ("intent", "goal"):
        legacy = dict(cfg.get("intent_rl", {}) or {})
    merged = {**rl_block, **legacy, **nested}
    src = {k: ("rl" if k in rl_block else "fallback") for k in merged}
    for k in legacy:
        src[k] = "intent_rl(legacy)"
    for k in nested:
        src[k] = f"rl.{mode_key}"
    return merged, src
