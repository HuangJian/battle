"""terminal_stats —— 每 iter 终局指标聚合（plan §4.7 / M1b 扩展）。

只读 manifest.json，不读 metrics.npy、不按 tick 遍历，O(#games)。
产出写 `<traj>/iteration-terminal.jsonl`，与 `training_log.jsonl` 同级，
不随 _rotate_cleanup 丢失。

每轮 append 一行，event="iteration_terminal"，包含：
  it, time, games, kills, powerUps, ticks, outcomes, win_rate, by_stage
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any


def _median(values: list[float]) -> float:
    """中位数（纯 Python，不依赖 numpy）。"""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _p95(values: list[float]) -> float:
    """95 分位（纯 Python）。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(math.ceil(0.95 * len(s)) - 1)))
    return float(s[idx])


def terminal_stats(
    iter_dir: str,
    *,
    it: int = 0,
    identity: dict[str, Any] | None = None,
    expected_games: int | None = None,
) -> dict[str, Any]:
    """对 `iter_dir` 下全部 manifest.json 聚合终局指标。

    聚合规则：
    - 递归扫 it{N}/**/manifest.json，跳过路径含 local-eval 的；
    - 按 (stage, seed) 去重，保留 nSamples 最大者；
    - 产出 kills/powerUps/ticks 的聚合统计；
    - 无可用 manifest → 返回 {"it": it, "games": 0, "error": "no_manifests"}。
    """
    manifests: dict[tuple[int, int], dict[str, Any]] = {}
    iter_path = Path(iter_dir)
    if not iter_path.is_dir():
        return {"it": it, "games": 0, "error": "no_manifests"}

    for p in sorted(iter_path.rglob("manifest.json")):
        pp = str(p)
        if "local-eval" in pp or "\\local-eval\\" in pp:
            continue
        try:
            mm = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        st = mm.get("stage")
        sd = mm.get("seed")
        if not isinstance(st, int) or not isinstance(sd, int):
            continue
        key = (st, sd)
        prev = manifests.get(key)
        if prev is None or (mm.get("nSamples") or 0) > (prev.get("nSamples") or 0):
            manifests[key] = mm

    if not manifests:
        return {"it": it, "games": 0, "error": "no_manifests"}

    games = len(manifests)
    kills_list: list[float] = []
    pu_list: list[float] = []
    ticks_list: list[float] = []
    outcomes: dict[str, int] = {}
    by_stage: dict[int, dict[str, Any]] = {}

    for mm in manifests.values():
        st = mm["stage"]
        k = float(mm.get("kills") or 0)
        pu = float(mm.get("powerUpsCollected") or 0)
        tk = float(mm.get("ticks") or 0)
        oc = str(mm.get("outcome", "unknown"))
        kills_list.append(k)
        pu_list.append(pu)
        ticks_list.append(tk)
        outcomes[oc] = outcomes.get(oc, 0) + 1

        stage_rec = by_stage.setdefault(
            st,
            {"games": 0, "kills_total": 0.0, "pu_total": 0.0, "ticks_total": 0.0},
        )
        stage_rec["games"] += 1
        stage_rec["kills_total"] += k
        stage_rec["pu_total"] += pu
        stage_rec["ticks_total"] += tk

    kills_total = sum(kills_list)
    pu_total = sum(pu_list)
    win_rate = round(outcomes.get("stage_clear", 0) / games, 4) if games else 0.0

    # by_stage: 计算均值
    by_stage_out: dict[str, Any] = {}
    for st, rec in by_stage.items():
        g = rec["games"]
        by_stage_out[str(st)] = {
            "games": g,
            "kills_total": round(rec["kills_total"], 2),
            "pu_total": round(rec["pu_total"], 2),
            "ticks_mean": round(rec["ticks_total"] / g, 1) if g else 0.0,
        }

    out: dict[str, Any] = {
        "event": "iteration_terminal",
        "it": it,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "games": games,
        "kills": {
            "total": round(kills_total, 2),
            "mean": round(kills_total / games, 2) if games else 0.0,
            "p50": round(_median(kills_list), 2),
            "p95": round(_p95(kills_list), 2),
            "max": round(max(kills_list), 2) if kills_list else 0.0,
        },
        "powerUps": {
            "total": round(pu_total, 2),
            "mean": round(pu_total / games, 2) if games else 0.0,
            "max": round(max(pu_list), 2) if pu_list else 0.0,
        },
        "ticks": {
            "mean": round(sum(ticks_list) / games, 1) if games else 0.0,
            "p50": round(_median(ticks_list), 1),
        },
        "outcomes": outcomes,
        "win_rate": win_rate,
        "by_stage": by_stage_out,
    }
    if expected_games is not None:
        out["expected_games"] = expected_games
    if identity:
        out["reward_identity"] = identity
    return out


def write_terminal_stats(
    traj_root: str,
    *,
    it: int,
    iter_dir: str,
    identity: dict[str, Any] | None = None,
    expected_games: int | None = None,
) -> None:
    """聚合终局指标并追加到 `<traj>/iteration-terminal.jsonl`。

    不报错（warn-only），所有异常静默捕获。
    """
    try:
        rec = terminal_stats(iter_dir, it=it, identity=identity, expected_games=expected_games)
        if rec.get("games", 0) == 0 and "error" in rec:
            return  # 无可用 manifest，跳过写盘
        path = Path(traj_root) / "iteration-terminal.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 统计失败不打断训练