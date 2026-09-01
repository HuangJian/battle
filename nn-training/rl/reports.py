"""跨 worker 的报告聚合 —— 本地 rollout 与远端单局摘要共用。"""

from __future__ import annotations


def win_of(summary: dict) -> int:
    """单局摘要是否 stage_clear（meta 账本 win 字段的唯一口径）。"""
    return 1 if summary.get("outcomes", {}).get("stage_clear", 0) > 0 else 0


def combine_reports(reports: list[dict]) -> dict:
    """跨 worker 精确重聚合（scoreList/dimLists 原始值列表）。

    本地 rollout 与远端单局摘要同构（远端 manifest 即单局 _rl_report.json 内容，
    另带 wver/node/elapsedSec 溯源字段，不影响聚合），两条采样路径共用本函数。
    M8 意图 RL：额外聚合 intentCounts（意图动作分布）与 totalKills（存在时）。
    """
    combined = {
        "games": 0,
        "winRate": 0.0,
        "outcomes": {},
        "totalSamples": 0,
        "totalTicks": 0,
        "scoreList": [],
        "dimLists": {},
    }
    wins = 0
    intentCounts = None
    totalKills = 0
    for r in reports:
        combined["games"] += r["games"]
        combined["totalSamples"] += r["totalSamples"]
        combined["totalTicks"] += r["totalTicks"]
        totalKills += r.get("totalKills", 0)
        for o, c in r.get("outcomes", {}).items():
            combined["outcomes"][o] = combined["outcomes"].get(o, 0) + c
            if o == "stage_clear":
                wins += c
        combined["scoreList"].extend(r.get("scoreList", []))
        for k, vs in r.get("dimLists", {}).items():
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
    return combined
