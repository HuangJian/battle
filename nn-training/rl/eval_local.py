"""eval_local —— 干净评估的纯函数/无状态工具（2026-09-02 从 eval_dispatch.py 拆出）。

干净评估（2026-08-24，用户指令）：用各节点已缓存的同权重跑固定语料贪心局。
两股噪声都消掉：动作 argmax 无探索噪声、(stage,seed) 语料恒定 → 跨 checkpoint
配对可比（同 seed 胜负是确定事件）。本模块只含无状态工具与台账结算——
派发/对账状态机在 rl/eval_dispatch.py::EvalDispatcher（OO 化）。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.log import log

REPO_ROOT = Path(__file__).resolve().parent.parent

# 固定语料种子——前 2 个承载历史可比性（永不改动）；860003+ 为 goal-nn 扩展
# （arena 自评需要 20 seed/关的 trend 精度，纯增量、不影响旧口径）。
EVAL_SEEDS = tuple([860001, 860002, *range(860003, 860021)])
EVAL_ITER_SUFFIX = "ev"  # eval iterId = {runId}.{it}ev → 与采集任务在 agent 结果缓存中键空间隔离
EVAL_TASK_ATTEMPTS = 2  # 单局重试上限；超限放弃并计数（权重切换后未完成局自然作废）
EVAL_LOCAL_SLOTS_DEFAULT = 4  # 本地直跑槽位默认值（policy.evalLocalSlots 可覆写；0=禁用）
EVAL_LOCAL_RELEASE_GRACE = 300  # 距窗口截止剩这些秒时强制释放本地预留（本地失效也不空转到超时）


def hold_for_local(pending_len: int, reserved: int, gate_set: bool, past_release: bool) -> bool:
    """节点 worker 是否应暂缓取任务、把队列尾段留给本机直跑。

    R6 补丁：课程起步期每轮仅 ~12 局，派发即被远端线程抢空，gate 在 PPO 收尾才
    放行——届时队列恒空，本地永远零参与。预留 = 节点不取最后 reserved 局。
    释放条件（任一）：gate 已放行 / 距窗口截止进入宽限期 / reserved<=0。
    防挂死：仅当 pending 超出预留量时节点才被允许继续取之外的判断在此收口，
    全预留场景由宽限强制释放兜底。
    """
    if reserved <= 0 or gate_set or past_release:
        return False
    return 0 < pending_len <= reserved


def report_winrate_safe(wr: float | None) -> float | None:
    if wr is None:
        return None
    try:
        return round(float(wr), 4)
    except (TypeError, ValueError):
        return None


def run_local_eval_game(
    bun: str,
    weights_snapshot: str,
    stage: int,
    seed: int,
    out_dir: Path,
    max_ticks: int,
    difficulty: str,
    timeout_sec: float,
    wver: str,
) -> dict:
    """本机直跑一局贪心评估（与节点 agent 同一 runner / 同一报告 schema）。

    权重必须传**派发时刻的冻结快照**而非 rl_path——主循环 PPO 写回会原地覆盖
    rl_path，本地局读错版本就是对账灾难。失败抛异常交 worker 回队/放弃；
    成功返回补齐 wver/mode 回显的 manifest（mode 戳在 agent 路径由 agent 盖，
    本地路径由这里盖——validate_eval_result 的对账项）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        bun,
        "tools/sim/export-eval-game.ts",
        "--weights",
        weights_snapshot,
        "--out",
        str(out_dir),
        "--stage",
        str(stage),
        "--seed",
        str(seed),
        "--difficulty",
        difficulty,
        "--max-ticks",
        str(max_ticks),
        "--wver",
        wver,
        "--node-label",
        "local",
    ]
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        **_POPEN_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"rc={proc.returncode} ({(proc.stderr or proc.stdout or '')[-160:]})")
    # json.loads 返回 Any；_eval_report.json 契约固定为 dict。
    manifest: dict[str, Any] = json.loads(
        (Path(out_dir) / "_eval_report.json").read_text(encoding="utf-8")
    )
    manifest.setdefault("wver", wver)
    manifest["mode"] = "eval"
    manifest["elapsedSec"] = round(time.time() - t0, 1)
    return manifest


def eval_done_keys(eval_jsonl: Path, wver16: str) -> set[tuple[int, int]]:
    """已评估账本：eval_log.jsonl 中同 wver 的 (stage,seed) 集（断点/重启不重评）。"""
    out: set[tuple[int, int]] = set()
    try:
        if eval_jsonl.exists():
            for line in eval_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(r, dict) and r.get("event") == "eval" and r.get("wver") == wver16:
                    try:
                        out.add((int(r["stage"]), int(r["seed"])))
                    except (KeyError, TypeError, ValueError):
                        continue
    except OSError:
        pass
    return out


def settle_eval_summary(
    eval_jsonl: Path,
    key16: str,
    it: int,
    pairs: list[tuple[int, int]],
    total: int,
    seen: set[tuple[int, int]],
    wins: list[int],
    cleared_total: list[int],
    outcomes: dict[str, int],
    node_games: dict[str, int],
    jsonl_lock: threading.Lock,
    t_eval_start: float,
    rollout_winrate: float | None,
) -> None:
    """评估窗口结束后的对账与 summary 落账（原 dispatch_eval_round 尾部，纯函数化）。

    断点续跑口径：summary 必须聚合台账中该 (iter,wver) 的全部逐局行——只统计本次
    补跑会低估分母（it29 实测教训：补跑 20 局写出 2/20）。ledger 无行时退回本次
    现场计数（wins/cleared_total/outcomes/node_games 由 record 闭包累积）。
    """
    dropped = total - len(seen)
    led_wins = 0
    led_clears = 0
    led_outcomes: dict[str, int] = {}
    led_nodes: dict[str, int] = {}
    try:
        with open(eval_jsonl, encoding="utf-8") as jf:
            for ln in jf:
                try:
                    r = json.loads(ln)
                except Exception:
                    continue
                if r.get("event") != "eval" or r.get("wver") != key16 or r.get("iter") != it:
                    continue
                led_wins += 1 if r.get("win") else 0
                # 全歼率（方案 A 口径）：旧行（cleared 缺省）视为未全歼——新 schema
                # 上线前的行只有本地局有 cleared，此处保守记 0，聚合以新行口径为准。
                led_clears += 1 if r.get("cleared") else 0
                oc = str(r.get("outcome") or "?")
                led_outcomes[oc] = led_outcomes.get(oc, 0) + 1
                ndm = str(r.get("node") or "?")
                led_nodes[ndm] = led_nodes.get(ndm, 0) + 1
    except FileNotFoundError:
        pass
    if led_outcomes:
        n = sum(led_outcomes.values())
        wins_v = led_wins
        clears_v = led_clears
        outcomes = led_outcomes
        node_games = led_nodes
    else:
        n = len(seen)
        wins_v = wins[0]
        clears_v = cleared_total[0]
    dropped = max(dropped, len(pairs) - n)
    clean_wr = (wins_v / n) if n else None
    clear_rate = (clears_v / n) if n else None
    summary = {
        "event": "eval_summary",
        "iter": it,
        "wver": key16,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sec": round(time.time() - t_eval_start, 1),
        "games": n,
        "wins": wins_v,
        "winRate": round(clean_wr, 4) if clean_wr is not None else None,
        # 全歼率（敌人全灭即算，不受 BONUS TIME 截断影响）——S3/S4a 门判定读它。
        "clears": clears_v,
        "clearRate": round(clear_rate, 4) if clear_rate is not None else None,
        "outcomes": outcomes,
        "dropped": dropped,
        "rolloutWinRate": report_winrate_safe(rollout_winrate),
        # 每节点实际结算的评估局数（勿与并发槽位混淆——首版曾误写 nd["c"]）
        "nodes": dict(sorted(node_games.items())),
    }
    with jsonl_lock, open(eval_jsonl, "a", encoding="utf-8") as jf:
        jf.write(json.dumps(summary) + "\n")
    if n:
        done_msg = (
            f"[eval] it{it} DONE wver={key16[:12]}… clean winRate="
            f"{clean_wr:.1%} ({wins_v}/{n}, dropped={dropped})"
            + (
                f" clearRate={clear_rate:.1%} ({clears_v}/{n})"
                if clear_rate is not None
                else ""
            )
            + (
                f" vs rollout(sampled)={rollout_winrate:.1%}"
                if rollout_winrate is not None
                else ""
            )
            + f" outcomes={json.dumps(outcomes)}"
        )
        log(done_msg)
    else:
        log(f"[eval] it{it}: no game settled within window — nothing recorded")
