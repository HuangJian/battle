"""m1-eval 干净评估管线（intent/goal 模式专用，自 run_rl_intent.py 原样迁入 —
RL 入口整合，plan/RL-Entry-Consolidation.md P3）。

与 rl/eval_dispatch.py（per-tick 模式）双轨并存（DECISIONS §307 D3）：两者的
语料/判门协议不同——本模块走 `tools/sim/m1-eval.ts --policy intent-exec|goal`
固定语料（35 关 × eval_seeds）整批贪心局 + error 局整批重跑；eval_dispatch 走
逐局派发 + eval_log.jsonl wver 键对账。入口统一在 run_rl.py，按 --mode 分派。

职责：
  - parse_m1_eval_report：m1-eval 输出解析（纯函数，可单测）
  - run_clean_eval：整批评估 + error 局自动重跑（≤3 次）
  - dispatch_eval_bg_m1：后台线程派发 → eval_summary 写回 training_log.jsonl
  - read_eval_summary：回读该迭代最新 eval_summary（断点/线程竞态下对账）
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

from rl.log import log
from rl.queue import REPO_ROOT  # noqa: F401 — 与 eval_dispatch 同源约定

# Windows：spawn 子进程（bun 评估）时用 CREATE_NO_WINDOW，避免黑控制台窗口抢焦点。
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW  # noqa: E402


def parse_m1_eval_report(text: str) -> dict:
    """从 m1-eval 输出（stdout+stderr 合并文本）提取结果。**纯函数，可单测**。

    返回：
      winRate — 横幅 `[m1-eval] WIN RATE xx.x%` 打印在 stderr（m1-eval.ts L350
        process.stderr.write），JSON 报告在 stdout——必须合并查（it20/25 教训：
        只解析 stdout → 干净评估恒 null → 止损失效）；
      total / cleared / error — 顶层 JSON report 的 `"total": N, "outcomes": {...}`。
        逐关 perStage 数组里的 "total" 后不跟 "outcomes"，不会被误匹配。
    """
    win = None
    for line in text.splitlines():
        # 大小写不敏感（横幅曾只匹配 'winRate' 漏掉 'WIN RATE'——§27 教训）。
        m = re.search(r"win[ -]?rate[=:\s]+([\d.]+)\s*%", line, re.IGNORECASE)
        if m:
            win = float(m.group(1)) / 100
            break
    total = 0
    cleared = 0
    error = 0
    m = re.search(r'"total": (\d+),\s*"outcomes": \{([^}]*)\}', text)
    if m:
        total = int(m.group(1))
        oc = m.group(2)
        m2 = re.search(r'"stage_clear": (\d+)', oc)
        cleared = int(m2.group(1)) if m2 else 0
        m3 = re.search(r'"error": (\d+)', oc)
        error = int(m3.group(1)) if m3 else 0
    return {"winRate": win, "total": total, "cleared": cleared, "error": error}


# 干净评估最大重跑次数：error 局 > 0 就整批重跑（节点瞬态失败常见——it33 实测
# 87% error、同权重重跑即干净）。超限后接受带 error 标记的结果，不再无限重试。
CLEAN_EVAL_MAX_RETRY = 3


def run_clean_eval(bun: str, rl_path: str, args, _runner=None) -> dict:
    """m1-eval intent-exec/goal 固定语料贪心评估（35 关 × eval_seeds/关，**派发远端 agents**）。

    seeds 固定为 1..N 与 M7② 基线同语料 → 配对可比（P1-1k3 / §245 协议）。
    --dist-nodes 把 350 局派到全部 agent（40+ 槽，~4–6min），**本机不再独占**。

    2026-08-27 §30 修订（两次教训）：① 取消『eval 本地跑』——远端算力必须被利用
    （用户：纯 eval 任务也应分派 agents）；② 触发时机从『队列清空』改为 stream 的
    on_ppo_started（PPO 启动 = 全量结算到账 + 节点空闲）——『队列清空』会撞尾局
    tail_drain → eval 350 局与 rollout 残余并行抢槽 → 大批 503 → winRate 掉到
    4.9%/6.9% → 假阳性止损（it25 实测）。PPO 本地跑、eval 远端跑、互不抢。
    ③ **结果校验 + 自动重跑**（用户指令 2026-08-28）：跑完解析 outcomes.error，
    非零即整批重跑（最多 CLEAN_EVAL_MAX_RETRY 次），杜绝假阳性胜率污染止损判定
    （it33 首跑 87% error → 同权重重跑即干净 75% 量级的实证）。
    `_runner` 供测试注入 fake runner（替换 subprocess 执行）。
    """
    seeds = args.eval_seeds
    cmd = [bun, "tools/sim/m1-eval.ts",
           "--stages", "all", "--seeds", f"1-{seeds}",
           "--difficulty", args.difficulty,
           "--policy", "goal" if args.goal else "intent-exec",
           "--goal-weights" if args.goal else "--intent-weights", rl_path,
           "--dist-nodes", "nn-training/rl-config.json",
           "--workers", str(max(2, min(8, args.workers)))]
    attempts = 0
    while True:
        attempts += 1
        log(f"clean eval (distributed) attempt {attempts}/{CLEAN_EVAL_MAX_RETRY}: {' '.join(cmd)}")
        if _runner is not None:
            res = _runner(cmd)
        else:
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                                  timeout=3600, **_POPEN_NO_WINDOW)
            if proc.returncode != 0:
                raise RuntimeError(
                    f"m1-eval rc={proc.returncode}: {(proc.stderr or proc.stdout)[-400:]}")
            res = parse_m1_eval_report(proc.stdout + "\n" + proc.stderr)
        err = res.get("error") or 0
        if err > 0 and attempts < CLEAN_EVAL_MAX_RETRY:
            log(f"WARN clean eval attempt {attempts} had {err} error games "
                f"(total={res.get('total')}) — rerunning whole batch")
            continue
        res["retries"] = attempts - 1
        res["games"] = 35 * seeds  # 兼容 dispatch_eval_bg_m1 的 eval_summary 字段
        if err > 0:
            log(f"WARN clean eval accepted with {err} error games after {attempts} "
                f"attempts (max {CLEAN_EVAL_MAX_RETRY}) — result carries error mark")
        return res


def dispatch_eval_bg_m1(bun: str, rl_path: str, args, it: int,
                        jsonl_path: Path, baseline: float) -> threading.Thread:
    """干净评估后台线程（流式 on_ppo_started / 串行 rollout 收官后触发）：跑
    m1-eval → 结果写回 training_log.jsonl 的 eval_summary 事件 + 供止损判定。
    返回线程句柄，主循环在 jsonl 写回前 join（与 eval_dispatch.dispatch_eval_bg
    同语义）。"""
    def _body() -> None:
        try:
            er = run_clean_eval(bun, rl_path, args)
            ev = er.get("winRate")
            delta = (ev - baseline) if ev is not None else None
            rec = {
                "event": "eval_summary", "iter": it,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "winRate": round(ev, 4) if ev is not None else None,
                "games": er["games"], "baseline": baseline,
                "delta": round(delta, 4) if delta is not None else None,
            }
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            if ev is not None:
                log(f"eval it{it}: clean winRate={ev:.1%} ({er['games']} games) "
                    f"Δ vs baseline={delta:+.1%}")
            else:
                # 评估盲（横幅未命中）必须显式告警，而不是靠 f-string 对 None 抛
                # ValueError 伪装成「评估失败」（it10 实测教训：kill agent 打断评估
                # → m1-eval rc=0 无横幅 → win=None → None.__format__ 崩溃）。
                log(f"eval it{it}: WARN clean winRate=null (banner missed) — "
                    f"games={er['games']} baseline={baseline}")
        except Exception as e:  # noqa: BLE001 — 评估旁路失败不中断训练
            log(f"WARN clean eval it{it} failed (ignored): {e}")
    t = threading.Thread(target=_body, daemon=True, name=f"eval-m1-it{it}")
    t.start()
    return t


def read_eval_summary(jsonl_path: Path, it: int) -> dict | None:
    """回读该迭代最新 eval_summary（评估线程写入）——断点/线程竞态下仍可对上。"""
    out = None
    try:
        if jsonl_path.exists():
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("event") == "eval_summary" and r.get("iter") == it:
                    out = r
    except OSError:
        pass
    return out
