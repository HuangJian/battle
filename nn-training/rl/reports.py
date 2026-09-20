"""跨 worker 的报告聚合 —— 本地 rollout 与远端单局摘要共用。"""

from __future__ import annotations

from typing import Any


def win_of(summary: dict[str, Any]) -> int:
    """单局摘要是否 stage_clear（meta 账本 win 字段的唯一口径）。"""
    return 1 if summary.get("outcomes", {}).get("stage_clear", 0) > 0 else 0


def aggregate_rollout_collect(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """多波 volume 的 it 级 rollout 聚合（用户口径 2026-09-19）。

    起点 = 各波 `weights_dist_start_ts` 的 **min**（首波开始分发）；
    终点 = 各波 `collect_end_ts` 的 **max**（最后一波样本齐可交 PPO）。
    产出：`pure_collect_sec` + 数值锚点 + `rollout_collect_aggregated`。
    无 ts 锚点时回退各波 `pure_collect_sec` 的 max（**下界**，多波端到端只会更长）
    并标 `rollout_collect_aggregated=False`。
    """
    starts = [
        float(r["weights_dist_start_ts"])
        for r in reports
        if r.get("weights_dist_start_ts") is not None
    ]
    ends = [
        float(r["collect_end_ts"]) for r in reports if r.get("collect_end_ts") is not None
    ]
    per_wave = [
        float(r["pure_collect_sec"])
        for r in reports
        if r.get("pure_collect_sec") is not None
    ]
    out: dict[str, Any] = {}
    if starts and ends:
        t0, t1 = min(starts), max(ends)
        out["weights_dist_start_ts"] = t0
        out["collect_end_ts"] = t1
        out["pure_collect_sec"] = round(t1 - t0, 1)
        out["rollout_collect_aggregated"] = True
        out["rollout_collect_waves"] = len(starts)
        return out
    if per_wave:
        out["pure_collect_sec"] = max(per_wave)
        out["rollout_collect_aggregated"] = False
        out["rollout_collect_waves"] = len(per_wave)
    return out


def empty_collect_report() -> dict[str, Any]:
    """空采集报告（合法 shape）：games/winRate/outcomes/samples/ticks 齐全，供日志/事件读。"""
    return combine_reports([])


def adopt_volume_report(wave_combined: dict[str, Any] | None) -> dict[str, Any]:
    """连续配额收官采纳本轮采集报告（2026-09-19 bugfix）。

    `wave_combined` 是本轮全部 batch 的 combine 结果，内含本轮
    weights_dist_start_ts / collect_end_ts。**不得**再与上一轮 `_report`
    做 combine_reports：min(start) 会钉在历史波，pure_collect_sec 轮轮累加
    （x20-powered it6 实测 425s，真采集 rollout_sec 仅 ~30s）。

    本轮无采集（空 batch / 提前返回）时返回**空但合法** shape，绝不返回 `{}`——
    否则 `_log_report` / events 读 `report['games']` 会 KeyError（2026-09-19 回归）。
    """
    if wave_combined:
        return wave_combined
    return empty_collect_report()


#: 本进程 wave 时间锚点（磁盘单局 manifest 通常没有；有则覆盖进合并结果）。
_VOLUME_TIMING_KEYS = (
    "weights_dist_start_ts",
    "collect_end_ts",
    "pure_collect_sec",
    "rollout_collect_aggregated",
    "rollout_collect_waves",
    "weights_dist_start_at",
    "weights_dist_done_at",
)


def merge_volume_report(
    wave_combined: dict[str, Any] | None,
    disk_manifests: list[dict[str, Any]],
) -> dict[str, Any]:
    """连续配额收官报告 = **本轮盘上 shard**，wave 只补时间锚点。

    x20-steady it75 停机后重启 it76：quota 停机前已采满 ⇒ 新进程 batches=0，
    `combine_reports([])` 的除零保护把 winRate 填 0.0——与「打了 N 局全输」
    在账本上不可区分（§107 监控盲区）。配额账本早已以盘上 manifest 为真
    （`settled_stage_totals`），报告必须同源。

    备选与否决：只在 wave 为空时回填 —— 否，重启后本进程可能只补了缺口批，
    wave 有 games 但小于盘上全量，仍会低估 winRate/局数。
    """
    disk = combine_reports(disk_manifests) if disk_manifests else None
    if disk is None or int(disk.get("games") or 0) <= 0:
        return adopt_volume_report(wave_combined)
    wave = wave_combined or {}
    merged = dict(disk)
    for k in _VOLUME_TIMING_KEYS:
        if wave.get(k) is not None:
            merged[k] = wave[k]
    return adopt_volume_report(merged)


def combine_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """跨 worker 精确重聚合（scoreList/dimLists 原始值列表）。

    本地 rollout 与远端单局摘要同构（远端 manifest 即单局 _rl_report.json 内容，
    另带 wver/node/elapsedSec 溯源字段，不影响聚合），两条采样路径共用本函数。
    M8 意图 RL：额外聚合 intentCounts（意图动作分布）与 totalKills（存在时）。
    rollout 耗时（2026-09-19）：多波时 min(weights_dist_start_ts)→max(collect_end_ts)。
    空 dict / 缺键报告跳过（轮初 `_report={}` 不得炸 combine）。
    """
    combined: dict[str, Any] = {
        "games": 0,
        "winRate": 0.0,
        "outcomes": {},
        "totalSamples": 0,
        "totalTicks": 0,
        "scoreList": [],
        "dimLists": {},
    }
    wins = 0
    intentCounts: list[int] | None = None
    totalKills = 0
    for r in reports:
        if not r:
            continue
        combined["games"] += int(r.get("games") or 0)
        combined["totalSamples"] += int(r.get("totalSamples") or 0)
        combined["totalTicks"] += int(r.get("totalTicks") or 0)
        totalKills += r.get("totalKills") or 0
        for o, c in r.get("outcomes", {}).items():
            combined["outcomes"][o] = combined["outcomes"].get(o, 0) + c
            if o == "stage_clear":
                wins += c
        combined["scoreList"].extend(r.get("scoreList") or [])
        for k, vs in (r.get("dimLists") or {}).items():
            combined["dimLists"].setdefault(k, []).extend(vs)
        ic = r.get("intentCounts")
        if ic:
            if intentCounts is None:
                intentCounts = [0] * len(ic)
            for i in range(min(len(intentCounts), len(ic))):
                intentCounts[i] += ic[i]
    combined["winRate"] = round(wins / combined["games"], 4) if combined["games"] else 0.0
    # T7.2 goal rollout 无 intentCounts 但报告 totalKills（run_rl_intent 日志行消费）——
    # 无条件聚合（per-tick RL 报告缺省 0，只增字段不破兼容）。
    combined["totalKills"] = totalKills
    if intentCounts is not None:
        combined["intentCounts"] = intentCounts
    sl = combined["scoreList"]
    if sl:
        n = len(sl)
        mean = sum(sl) / n
        var = sum((x - mean) ** 2 for x in sl) / max(1, n - 1)
        combined["scoreStats"] = {
            "mean": round(mean, 4),
            "std": round(var**0.5, 4),
            "min": round(min(sl), 4),
            "max": round(max(sl), 4),
        }
    combined["dimMeans"] = {
        k: round(sum(v) / len(v), 4) for k, v in combined["dimLists"].items() if v
    }
    combined.update(aggregate_rollout_collect([r for r in reports if r]))
    return combined
