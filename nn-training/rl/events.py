"""events —— training_log.jsonl 事件写入（2026-09-02 从 rl/loop.py 拆出）。

run_training 主循环里所有 jsonl 事件（run_start / iteration / circuit_break /
iter_error）统一收敛到这里：字段契约与旧 rl/loop.py 内联写入逐字节一致，
单测可直接覆盖事件行 schema。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


def write_event(jsonl_path: Path, event: dict) -> None:
    """追加一条事件到 jsonl（不吞异常——与旧内联写入同语义，失败向上传播）。

    调用方（loop 的失败重试）负责兜底；观测事件失败不该静默跳过训练主链。
    """
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def log_iter_error(jsonl_path: Path, it: int, err: str) -> None:
    """迭代失败落 training_log.jsonl（iter_error 事件）。

    此前失败详情只进易失 stdout——detach 启动下不可见，it2/it3 连续跳轮时
    无任何可复盘痕迹。观测必须自带牙齿：last_completed_iter 只认 iteration
    事件，iter_error 不影响断点续跑定位。OSError 静默（失败回放路径不该再炸）。
    """
    try:
        write_event(
            jsonl_path,
            {
                "event": "iter_error",
                "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "error": str(err)[:500],
            },
        )
    except OSError:
        pass


def write_run_start(jsonl_path: Path, args, rotate_seed: int) -> None:
    """run_start 事件：落盘启动参数与课程 rotateSeed（断点续跑继承来源）。"""
    write_event(
        jsonl_path,
        {
            "event": "run_start",
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "args": {k: v for k, v in vars(args).items()},
            "rotateSeed": rotate_seed,
        },
    )


def write_iteration(jsonl_path: Path, args, it: int, report: dict, m: dict) -> None:
    """iteration 事件（字段契约与旧 rl/loop.py 内联写入逐字节一致）。

    m: {rollout_sec, ppo_sec, total_steps, chunks_n, agg, kl_cum, halted,
        dropped_games, waves, load_sec, tail_drain_sec, eval_join_sec}
    """
    agg = m["agg"]
    write_event(
        jsonl_path,
        {
            "event": "iteration",
            "iter": it,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "winRate": report["winRate"],
            "outcomes": report["outcomes"],
            "score_mean": report.get("scoreStats", {}).get("mean"),
            "score_std": report.get("scoreStats", {}).get("std"),
            "dim_means": report.get("dimMeans", {}),
            # 意图 RL 字段（per-tick 报告无此行 → None，不破兼容）。
            "intentCounts": report.get("intentCounts" if args.mode != "goal" else "actionCounts"),
            "baseIntegrity": report.get("dimMeans", {}).get("baseIntegrity"),
            "samples": report["totalSamples"],
            "ticks": report["totalTicks"],
            "rollout_sec": m["rollout_sec"],
            "ppo_sec": m["ppo_sec"],
            "steps": m["total_steps"],
            "chunks": m["chunks_n"],
            "policy": agg["policy"] if agg else None,
            "value": agg["value"] if agg else None,
            "entropy": agg["entropy"] if agg else None,
            "kl": agg["kl"] if agg else None,
            "mean_ret": agg["mean_ret"] if agg else None,
            "lr": args.lr,
            # P1-12：reward/dodge 臂版本落盘（历史实验可归因——
            # 此前奖励规格无记录，复盘无法区分 v7/toy 臂）
            "reward": getattr(args, "reward", ""),
            "dodge": getattr(args, "dodge", ""),
            "mb": args.mb,
            "epochs": args.epochs,
            # 队列模式附加字段（nodes=[] 纯本地模式不含，保字节一致基线）
            **(
                {
                    "missing": report["missing"],
                    "expectedGames": report["expectedGames"],
                    "dist": report["dist"],
                }
                if "missing" in report
                else {}
            ),
            # 纯采集（用户定义）：末局结算 − 权重分发完毕；队列模式实测透传，
            # 纯本地路径回退为 rollout 全长（无重叠即等价纯采集）。
            "pure_collect_sec": report.get("pure_collect_sec", round(m["rollout_sec"], 1)),
            # R5 遥测补牙（2026-08-25）：流式的 kl 只是末 wave 单值，对轮内
            # 累积漂移全盲——补 kl_cum/halted/dropped 与各阶段耗时拆分。
            # F4 熔断仍读 kl（每梯度步均值，跨模式可比）；轮内漂移由
            # streamKlCap 治理，kl_cum 供观测与事后分析。
            "kl_cum": m["kl_cum"],
            "halted": m["halted"],
            "dropped_games": m["dropped_games"],
            "waves": m["waves"],
            "load_sec": m["load_sec"],
            "tail_drain_sec": m["tail_drain_sec"],
            "dist_phase_sec": report.get("dist_phase_sec"),
            "eval_join_sec": m["eval_join_sec"],
        },
    )


def write_circuit_break(
    jsonl_path: Path,
    it: int,
    tripped: str,
    agg: dict,
    kl_streak: int,
    ent_streak: int,
    report: dict,
    args,
) -> None:
    """circuit_break 事件：F4 熔断落地（训练 PAUSED 前的最后观测记录）。"""
    write_event(
        jsonl_path,
        {
            "event": "circuit_break",
            "iter": it,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": tripped,
            "kl": agg["kl"],
            "kl_streak": kl_streak,
            "entropy": agg["entropy"],
            "ent_streak": ent_streak,
            "winRate": report["winRate"],
            "weights": args.out,
        },
    )
