"""greedy rollout 采集链路的**接缝**测试（plan/greedy-outcome-quota rev4）。

只覆盖「采集 → 预筛 → 打包前裁剪 → 落账」这条链：所有 shard 都是临时目录里的假
manifest，所有训练对象都是最小替身。**不起训练、不跑 PPO、不连 hub。**

单测已经逐块钉过纯函数（`tests/worker/test_greedy_quota.py`）与采集停止/降级
（`tests/worker/test_rollout_volume.py`）；本文件补的是它们之间的**顺序契约**：

1. 发布预筛 → drop → protected trim → mem 过滤 → 留痕合并，一步都不能换序；
2. `rollout_end` / `iteration` 两个事件只在激活时写 `greedy_quota_stats`（老账本逐字节不变）；
3. `step_rollout` 的 node 拒绝门：三档全 0 是合法关闭，非零才拒；跨轮槽位每轮清零。
"""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_remote_job import _trim_shards_to_quota
from trainer.loop_round_steps import RoundSteps
from worker.events import write_iteration, write_rollout_end
from worker.greedy_quota import (
    greedy_progress,
    is_greedy_manifest,
    load_manifests,
    merge_greedy_stats,
    parse_greedy_quota,
    select_greedy_shards,
)
from worker.loop_round import COLLECT_NODE


def _shard(
    root: Path, stage: int, seed: int, outcome: str, n: int, *, greedy: bool
) -> Path:
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    mm: dict = {"stage": stage, "seed": seed, "nSamples": n, "outcome": outcome}
    if greedy:
        mm["labelMode"] = "greedy"
    (d / "manifest.json").write_text(json.dumps(mm), encoding="utf-8")
    return d


# --------------------------------------------------- 1. 发布链路的顺序契约


def test_publish_chain_prefilter_protected_trim_and_mem(tmp_path: Path) -> None:
    """预筛先丢多余 greedy → protected 交 trim → mem 只认最终保留集 → 留痕不丢。

    生产里这四步散在 `_remote_ppo_publish` 的相邻 20 行，任何一步挪位都会静默
    改变「payload / data_fp / 云机装载集」三者是否自洽（§7.4）。这里用**生产函数**
    按同一顺序组合，钉住结果集。
    """
    root = tmp_path / "dist"
    dirs: list[Path] = []
    for stage in (2000, 2001):
        for i in range(5):  # 每关 5 局 greedy win，逐关 win 目标 250 ⇒ 只保 3
            dirs.append(_shard(root, stage, i, "stage_clear", 100, greedy=True))
        for i in range(5, 7):  # 每关 2 局 greedy timeout（供给 200）
            dirs.append(_shard(root, stage, i, "timeout", 100, greedy=True))
        for i in range(7, 9):  # 每关 2 局普通采样，预筛一律不动
            dirs.append(_shard(root, stage, i, "stage_clear", 100, greedy=False))

    plan = parse_greedy_quota(
        {"win": 0.5, "loss": 0.0, "timeout": 0.5, "oversample_cap": 2.0, "soft": True},
        target_transitions=1000,
        stages=[2000, 2001],
    )
    assert plan is not None

    # 零落盘路径：mem 优先于盘上 manifest，所以内存账必须是**同形**的（否则
    # 丢掉 labelMode/outcome 会让整批 greedy 认不出来）。
    mem = {
        d.name: json.loads((d / "manifest.json").read_text(encoding="utf-8")) for d in dirs
    }
    manifests = load_manifests(dirs, mem)
    assert all(
        is_greedy_manifest(manifests[d.name])
        == (manifests[d.name].get("labelMode") == "greedy")
        for d in dirs
    ), "mem 与盘上 manifest 必须同形"

    sel = select_greedy_shards([p.name for p in dirs], manifests, plan, it=1)
    ordinary = {p.name for p in dirs if not is_greedy_manifest(manifests[p.name])}
    assert (sel.keep | sel.drop).isdisjoint(ordinary), "非 greedy shard 一律不动"
    assert all(is_greedy_manifest(manifests[n]) for n in sel.drop)
    assert sel.stats["dropped"] == 4, "每关丢 2 局多余 win"

    # 预筛丢的局必须先从打包集移除，才能交给 trim / mem 过滤。
    after_prefilter = [p for p in dirs if p.name not in sel.drop]
    collect_stats = {
        "rule": 2,
        "degraded": False,
        "cap_hit": False,
        "waves": 2,
        "targets": {"win": 500, "loss": 0, "timeout": 500},
    }
    stats = merge_greedy_stats(collect_stats, sel.stats)
    assert stats["degraded"] is False and stats["waves"] == 2, "采集留痕不得被覆盖"
    assert stats["prefilter"]["kept"] == len(sel.keep)

    # quota 故意小于每关 protected 总量 ⇒ 只有 protected 硬保留能拦住 seed 序前缀反杀。
    kept, trimmed, _samples = _trim_shards_to_quota(
        after_prefilter, 300, mem=mem, protected=set(sel.keep) or None
    )
    assert set(sel.keep) <= {p.name for p in kept}, "protected 不许被裁"
    assert trimmed > 0, "普通局应当被配额裁掉一部分"

    # 最终 mem 只认保留集（发布/验收对账、payload 都读它）。
    final_mem = {p.name: mem[p.name] for p in kept if p.name in mem}
    assert set(final_mem) == {p.name for p in kept}
    assert all(n not in final_mem for n in sel.drop), "预筛丢掉的局不能进 payload 内存账"

    bins, total = greedy_progress(
        [p.name for p in kept], {p.name: manifests[p.name] for p in kept}
    )
    assert bins["win"] > 0 and bins["timeout"] > 0 and bins["loss"] == 0
    assert bins["win"] + bins["timeout"] == total, "greedy 总账只由三档构成"


# ------------------------------------------------------------- 2. 事件留痕


def _args() -> SimpleNamespace:
    return SimpleNamespace(mode="per-tick", lr=1e-4, mb=2, epochs=1, reward="", dodge="")


M: dict = {
    "rollout_sec": 1.0,
    "ppo_sec": 2.0,
    "total_steps": 3,
    "chunks_n": 1,
    "agg": None,
    "kl_cum": None,
    "halted": False,
    "dropped_games": None,
    "waves": None,
    "load_sec": None,
    "tail_drain_sec": None,
    "eval_join_sec": None,
}


def test_rollout_end_writes_greedy_stats_only_when_active(tmp_path: Path) -> None:
    """采集收官事件带采集侧读数；未激活时一个键都不多（老账本逐字节不变）。"""
    p = tmp_path / "training_log.jsonl"
    stats = {"rule": 2, "degraded": True, "cap_hit": True, "waves": 3}
    row = write_rollout_end(
        p, _args(), 1, {"winRate": 0.5}, 1.0, greedy_quota_stats=stats
    )
    assert row["greedy_quota_stats"] == stats
    plain = write_rollout_end(p, _args(), 2, {"winRate": 0.5}, 1.0)
    assert {"greedy_quota_stats"}.isdisjoint(plain)


def test_iteration_writes_greedy_stats_only_when_active(tmp_path: Path) -> None:
    """iteration 事件（PPO 之后落账）也要能读到 degraded/cap_hit ⇒ 合并而非覆盖。"""
    p = tmp_path / "training_log.jsonl"
    merged = merge_greedy_stats(
        {"degraded": True, "cap_hit": True, "waves": 3},
        {"kept": 30, "dropped": 63},
    )
    row = write_iteration(
        p, _args(), 1, {"winRate": 0.5}, {**M, "greedy_quota_stats": merged}
    )
    assert row["greedy_quota_stats"]["degraded"] is True
    assert row["greedy_quota_stats"]["cap_hit"] is True
    assert row["greedy_quota_stats"]["prefilter"]["kept"] == 30
    plain = write_iteration(p, _args(), 2, {"winRate": 0.5}, dict(M))
    assert {"greedy_quota_stats"}.isdisjoint(plain)


# ------------------------------------------------------------ 3. node 门 / 清零


class _Gate:
    def rollout(self, _fp: object) -> nullcontext[None]:
        return nullcontext()


def _node_loop(gq: dict) -> SimpleNamespace:
    return SimpleNamespace(
        args=SimpleNamespace(greedy_quota=gq),
        _remote_iter=lambda it, pairs: None,
        # 上一轮残留：node 分支不跑采集，但发布相位仍会读这三个槽位。
        _greedy_plan={"stale": True},
        _greedy_stats={"stale": True},
        _greedy_report={"stale": True},
    )


def _node_ctx() -> SimpleNamespace:
    return SimpleNamespace(it=1, collect_mode=COLLECT_NODE, pairs=[], dist_cfg=None)


def _patch_node_round(monkeypatch: pytest.MonkeyPatch) -> None:
    import trainer.loop_round_steps as rsteps
    import worker.cmd as wcmd

    monkeypatch.setattr(rsteps, "eval_gate", lambda: _Gate())
    monkeypatch.setattr(wcmd, "course_fp_for_args", lambda args: "fp")


def test_node_path_accepts_active_greedy_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-11-10：greedy 采集已全节点化 ⇒ node 分支不再按 quota 拒绝。

    回归：初版 `step_rollout` 用 `COLLECT_NODE + quota_active` 抛
    「只支持本机采集」，导致节点腿永远采不到 greedy 语料。
    """
    _patch_node_round(monkeypatch)
    loop = _node_loop({"win": 0.1, "timeout": 0.075})
    assert RoundSteps.step_rollout(cast(Any, loop), cast(Any, _node_ctx())) is None
    assert loop._greedy_plan is None
    assert loop._greedy_stats is None
    assert loop._greedy_report is None


def test_node_gate_allows_zero_quota_and_clears_slots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """三档全 0 = 合法关闭：node 腿照跑；且上轮 greedy 槽位每轮清零。"""
    _patch_node_round(monkeypatch)
    loop = _node_loop({"win": 0, "loss": 0, "timeout": 0})
    assert RoundSteps.step_rollout(cast(Any, loop), cast(Any, _node_ctx())) is None
    assert loop._greedy_plan is None
    assert loop._greedy_stats is None
    assert loop._greedy_report is None
