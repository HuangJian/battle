"""bc_entropy_probe.py — BC 部署熵（on-policy）探针（B9，2026-11-05）。

**为什么不是 `bc_learnability_probe.py`**：那个探针在**教师状态分布**上算 off-policy 熵，
只能回答「同一批状态上标签分布有没有收窄」，**不是**「部署时策略有多随机」。2026-11-05
复盘把两者混读，得出了「熵没降」的结论（采样标签熵 1.3777 ≈ ln4 是**标签**熵，不是策略熵）。

本工具读一次**行为门**（200 局）的 `eval_log.jsonl`（`trainer/eval_course_once.py` 产物），
按每局**实际走子直方图** `moveHist` 算方向动作熵（nats，4 方向，上限 ln4≈1.386）：

    H = -Σ p_i ln p_i,  p_i = count(dir_i) / Σ count(dir)

- 只用 on-policy 实际动作（不是标签、不是教师状态分布）；
- 槽 0（停/无输入）不进熵，单独报 `stop_frac`（v6 无 STOP 类，stop 高说明推理在丢帧）；
- 期望带（沿用 `bc-v6-train` 的 0.6–0.75）只对 **≥200 局** 才当读数，不足只打 `sufficient: false`。

用法：
    python tools/bc_entropy_probe.py --jsonl <run>/eval_log.jsonl [--min-games 200] [--out x.json]

「压熵靠换 argmax 标签，不是加 epoch」（plan §12 决策 #1 / §12.4）：本工具只负责**量**，
不做判决。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

from common.cli import house_parser, run_main

#: 熵期望带（nats，4 方向动作；ln4 ≈ 1.386 ⇒ 0.6–0.75 = 归一化 0.43–0.54）。
ENTROPY_BAND = (0.6, 0.75)
#: 行为门局数（少于它只算抽样，不算判决）。
MIN_GAMES = 200


def _percentile(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    s = sorted(vals)
    i = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[i]


def action_entropy(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """逐局方向动作熵 + 汇总（纯函数；`moveHist` = [停, 上, 下, 左, 右]）。"""
    per_game: list[float] = []
    stops = 0.0
    moves = 0.0
    skipped = 0
    for r in rows:
        h = r.get("moveHist")
        if not isinstance(h, (list, tuple)) or len(h) < 2:
            skipped += 1
            continue
        counts = [max(0.0, float(x or 0)) for x in h]
        total = sum(counts)
        if total <= 0:
            skipped += 1
            continue
        stops += counts[0]
        moves += total
        dirs = counts[1:]
        d = sum(dirs)
        if d <= 0:
            per_game.append(0.0)  # 一整局不动：熵 0，保留（这本身就是退化证据）
            continue
        h_sum = 0.0
        for c in dirs:
            if c > 0:
                p = c / d
                h_sum -= p * math.log(p)
        per_game.append(h_sum)

    n = len(per_game)
    mean = sum(per_game) / n if n else float("nan")
    return {
        "games": n,
        "skipped": skipped,
        "entropy_nats": round(mean, 4) if n else None,
        "entropy_norm": round(mean / math.log(4), 4) if n else None,
        "p10": round(_percentile(per_game, 0.10), 4) if n else None,
        "p50": round(_percentile(per_game, 0.50), 4) if n else None,
        "p90": round(_percentile(per_game, 0.90), 4) if n else None,
        "stop_frac": round(stops / moves, 4) if moves > 0 else None,
        "in_band": bool(n and ENTROPY_BAND[0] <= mean <= ENTROPY_BAND[1]),
        "band": list(ENTROPY_BAND),
    }


def probe(jsonl_path: Path, min_games: int = MIN_GAMES) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    out = action_entropy(rows)
    out.update(
        {
            "source": str(jsonl_path),
            "min_games": int(min_games),
            "sufficient": bool(out["games"] >= int(min_games)),
            "note": (
                "on-policy（实际动作直方图）；off-policy 教师状态探针只作 A/B 相对比较"
            ),
        }
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = house_parser(description="BC 部署熵（200 局 on-policy move 直方图）")
    ap.add_argument("--jsonl", required=True, help="eval_course_once 的 eval_log.jsonl")
    ap.add_argument("--min-games", type=int, default=MIN_GAMES)
    ap.add_argument("--out", default="", help="可选：结果 JSON 落盘路径")
    args = ap.parse_args(argv)
    path = Path(args.jsonl)
    if not path.is_file():
        print(f"找不到 {path}", file=sys.stderr)
        return 2
    res = probe(path, int(args.min_games))
    text = json.dumps(res, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if not res["sufficient"]:
        print(
            f"⚠ 只有 {res['games']} 局（<{res['min_games']}）——只作哨兵，不能当判决",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    run_main(main)
