"""build_argparser —— run_rl CLI 参数定义（瘦身，2026-09-02）。

从 run_rl.py main() 拆出（~300 行纯声明）：参数默认取自 rl-config.json
（merged_mode_args 单一事实来源），CLI 显式传参覆盖。collect-only 子进程
复用同一 parser（argv 继承），因此本模块**顶层不 import torch**（--lr 默认
用字面量 3e-4 而非 ppo.engine.LR，见注释）。
"""

from __future__ import annotations

import argparse
import os

from rl.breaker import KL_BREAK, KL_BREAK_CONSEC
from rl.modes import (
    _MODES,
    DEFAULT_EVAL_AT_INTENT,
)


def build_argparser(mode: str, rl_args: dict) -> argparse.ArgumentParser:
    """构建三模式 argparse。rl_args = merged_mode_args 合并后的配置默认值。"""

    def _d(name, fallback):
        return rl_args.get(name, fallback)

    ap = argparse.ArgumentParser()
    # ===== RL 入口整合（DECISIONS §307）：三模式后端 =====
    ap.add_argument(
        "--mode",
        default=mode,
        choices=list(_MODES),
        help="后端：per-tick（默认）/ intent（意图步）/ goal（goal 承诺步，"
        "原 run_rl_intent --goal）",
    )
    ap.add_argument(
        "--goal", action="store_true", help="兼容别名：--mode goal（原 run_rl_intent --goal）"
    )
    # ---- intent/goal 模式专属（原 run_rl_intent 参数；全模式注册不报错，未用即忽略）----
    ap.add_argument(
        "--replan", type=int, default=_d("replan", 30), help="intent replan cadence（M7① 定稿 30）"
    )
    ap.add_argument(
        "--heartbeat",
        type=int,
        default=_d("heartbeat", 240),
        help="goal 承诺期 T ticks（--mode goal 时生效）",
    )
    ap.add_argument(
        "--goal-coarse", action="store_true", help="T9a：169 路块级动作空间（logsumexp 聚合）"
    )
    ap.add_argument(
        "--warmup-iters",
        type=int,
        default=_d("warmup_iters", 1),
        help="前 N 迭代只训 value 头（intent/goal 冷启动 value 随机 → 先学回报基线）",
    )
    ap.add_argument(
        "--kickstart-kl",
        type=float,
        default=_d("kickstart_kl", 1.0),
        help="kickstarting KL 惩罚基础系数（plan #5；0=关闭）",
    )
    ap.add_argument(
        "--kickstart-decay",
        type=float,
        default=_d("kickstart_decay", 0.5),
        help="kickstarting 系数每策略迭代衰减因子",
    )
    ap.add_argument(
        "--eval-seeds",
        type=int,
        default=_d("eval_seeds", 10),
        help="m1-eval 每关种子数（intent/goal 干净评估；350 局/轮 @10）",
    )
    ap.add_argument(
        "--baseline",
        type=float,
        default=_d("baseline", 0.723),
        help="intent/goal 干净评估 Δ 的参照基线（M7② 72.3 个百分点）",
    )
    ap.add_argument(
        "--stop-loss-at",
        type=int,
        default=_d("stop_loss_at", 15 if mode in ("intent", "goal") else 0),
        help="止损迭代：>= 此迭代且 Δ<=stop-loss-delta 即停车（0=关闭）",
    )
    ap.add_argument(
        "--stop-loss-delta",
        type=float,
        default=_d("stop_loss_delta", 0.0),
        help="止损 Δ 阈值（相对 baseline）",
    )
    ap.add_argument(
        "--kl-break",
        type=float,
        default=_d("kl_break", KL_BREAK),
        help="F4 KL 熔断阈值（intent/goal 放宽到 json/0.6，避免误熔断 Bug D）",
    )
    ap.add_argument(
        "--kl-break-consec",
        type=int,
        default=_d("kl_break_consec", KL_BREAK_CONSEC),
        help="F4 KL 连续代阈值（intent/goal 专属）",
    )
    ap.add_argument(
        "--out-log",
        default=_d("out_log", ""),
        help="stdout 落盘路径（json out_log；空=仅控制台）。Tee 控制台+文件。",
    )
    ap.add_argument(
        "--err-log",
        default=_d("err_log", ""),
        help="stderr 落盘路径（json err_log；空=仅控制台）。Tee 控制台+文件。",
    )
    ap.add_argument(
        "--bc",
        default="tmp/student-weights-dagger/weights.json",
        help="BC checkpoint to warm-start from (first init only)",
    )
    ap.add_argument(
        "--out",
        default="tmp/rl-weights/weights.json",
        help="RL weights path (written every iteration; also the resume source)",
    )
    ap.add_argument("--traj", default="tmp/rl-traj", help="trajectory root dir")
    ap.add_argument(
        "--iters",
        type=int,
        default=15,
        help="iterations to run; 0 = infinite (stop via --max-hours or Ctrl-C)",
    )
    ap.add_argument(
        "--start-it",
        type=int,
        default=None,
        help="resume iteration index (default: auto — last completed iteration in "
        "training_log.jsonl + 1, so restarts continue where they stopped)",
    )
    ap.add_argument("--stages", default="0-3", help="explicit stage range (ignored in rotate mode)")
    ap.add_argument("--seeds", default="0-3", help="explicit seed range (ignored in rotate mode)")
    ap.add_argument(
        "--seed-rotate",
        type=int,
        default=_d("seed_rotate", 0),
        help="explicit 模式 seed 轮转：>0 时每迭代对 --stages 每关抽 N 个全新 "
        "seed（(rotateSeed,it) 键控、断点复现）；0 = 固定 --seeds（旧行为）",
    )
    ap.add_argument(
        "--rotate-stages",
        type=int,
        default=_d("rotate_stages", 0),
        help=">0: rotate through ALL stages this many per iteration "
        "(iteration i uses stages [(i-1)*N %% 35 ...]); seeds are drawn "
        "fresh every iteration from a (seed, iter)-derived RNG",
    )
    ap.add_argument(
        "--seeds-per-stage", type=int, default=10, help="random seeds per stage in rotate mode"
    )
    ap.add_argument(
        "--total-stages",
        type=int,
        default=_d("total_stages", 35),
        help="stage count for rotate mode (repo has 35)",
    )
    ap.add_argument(
        "--curriculum-stages",
        default="",
        help="curriculum mode: easy→hard ordered stage list (e.g. "
        "'13,1,16,8,21,4,15,31,0,29,33,...'). Non-empty enables it: each "
        "iteration samples only the active window (first N stages), N grows "
        "deterministically with it (see --curriculum-every). Recommended "
        "ordering = per-stage eval win rate desc (2026-08-25 audit).",
    )
    ap.add_argument(
        "--curriculum-start", type=int, default=4, help="curriculum initial active-stage count"
    )
    ap.add_argument(
        "--curriculum-every",
        type=int,
        default=8,
        help="curriculum: expand every N iterations (0 = never expand)",
    )
    ap.add_argument(
        "--curriculum-grow", type=int, default=4, help="curriculum: +G stages per expansion step"
    )
    ap.add_argument("--difficulty", default=_d("difficulty", "hard"))
    ap.add_argument("--max-ticks", type=int, default=_d("max_ticks", 12000))
    # goal-nn 卡 A2：玩具奖励臂覆盖（''=按 stage 解析：arena→级默认臂，真实关→v7；
    # 'toy:<arm>' 强制玩具臂用于扫参，'v7' 强制 v7）。经 queue/agent 透传到导出器。
    ap.add_argument(
        "--reward",
        default="",
        help="rollout reward override: '' (stage-derived), 'v7', or 'toy:<arm>'",
    )
    # goal-nn 卡 A3：dodge 模式覆盖（''=按 stage 解析：arena→l0，真实关→off；
    # 'off'|'l0'|'god' 强制，'god' 仅 A/B 报告用）。经 queue/agent 透传到导出器。
    ap.add_argument(
        "--dodge", default="", help="dodge override: '' (stage-derived), 'off', 'l0', or 'god'"
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=_d("workers", min(os.cpu_count() or 4, 12)),
        help="concurrent bun rollout workers (games partitioned by seed)",
    )
    ap.add_argument(
        "--local-slots",
        type=int,
        default=_d("local_slots", 0),
        help="trainer direct-thread slots (stream mode). R6 schedule: "
        "first-dispatched during collection; suspend once PPO waves "
        "begin (auto-resume if the whole cluster stalls); join eval "
        "remainder after PPO. 0 = auto (max(2, workers//4))；默认取 "
        "rl-config 的 rl.local_slots",
    )
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument(
        "--mb",
        type=int,
        default=_d("mb", 512),
        help="minibatch size — 512 halves gradient steps vs 256 "
        "(faster PPO, smaller per-iteration KL drift)",
    )
    ap.add_argument(
        "--lr",
        type=float,
        default=_d("lr", 3e-4),
        help="PPO learning rate（默认 3e-4，与 ppo/engine.py LR 同步；collect-only "
        "子进程不得 import torch，故不用模块常量）",
    )
    ap.add_argument(
        "--adv-norm",
        choices=("auto", "global", "wave", "none"),
        default=_d("adv_norm", "auto"),
        help="advantage 归一化粒度（P1-7，2026-09-02）：auto=流式 wave / 串行 "
        "global（保持现状）；global=整轮归一；wave=每 wave 归一；none=不归一 "
        "（供对照实验）。同一套超参在不同粒度下数学不同，显式记录口径",
    )
    ap.add_argument("--seed", type=int, default=_d("seed", 7))
    ap.add_argument(
        "--max-hours",
        type=float,
        default=0.0,
        help="wall-clock budget in hours; checked between iterations; 0 = unlimited",
    )
    ap.add_argument(
        "--keep-iters",
        type=int,
        default=_d("keep_iters", 3),
        help="keep only the last N trajectory dirs (disk bound); 0 = keep all",
    )
    ap.add_argument(
        "--stream",
        type=int,
        default=_d("stream", 1),
        help="1（默认，AGENTS §15.6）= 流式迭代：采集与 PPO 波次重叠，集群不在 PPO 窗口闲置；"
        "0 = 串行（采集全部完成后再统一 PPO）——仅调试/归因用",
    )
    ap.add_argument(
        "--eval-stages",
        default="",
        help="干净评估语料（goal-nn）：'' = 真实关 0..total_stages-1（旧行为）；"
        "传关卡规格如 '1000-1002' = arena 训练场自评（OOD 信号）",
    )
    ap.add_argument(
        "--eval-games-per-stage",
        type=int,
        default=2,
        help="干净评估：每关固定种子贪心局数（0=关闭）。rollout 收官后的 PPO 空窗期 "
        "分发到全部 ping.evalSupport 节点；结果追加 tmp/rl-traj/eval_log.jsonl",
    )
    ap.add_argument(
        "--eval-window-sec",
        type=int,
        default=_d("eval_window_sec", 1500),
        help="干净评估线程的墙钟预算；超时未结算的局放弃（不阻塞 PPO 与下一轮）",
    )
    ap.add_argument(
        "--eval-every",
        type=int,
        default=_d("eval_every", 1),
        help="干净评估稀疏化（吞吐 T3）：每 N 轮跑一次 eval。1 = 每轮（默认，字节一致）；N>1 = 非 eval 轮不派发不 join（集群尾段留给下一轮采集/双缓冲）。判门频率随之降为每 N 轮，判据不变（plan/goal-nn-throughput.md）。",
    )
    ap.add_argument(
        "--eval-at",
        default=_d("eval_at", DEFAULT_EVAL_AT_INTENT if mode in ("intent", "goal") else ""),
        help=(
            "干净评估绝对迭代点集（复用 run_rl_intent 的 eval_at 语义，如 "
            "'5,10,15,20'）：只在列出的迭代派发 eval；空 = 关闭该维（配合 "
            "--eval-every 或默认每轮）。与 --eval-every 可叠加（两者都满足才跑）。"
        ),
    )
    ap.add_argument(
        "--double-buffer",
        type=int,
        default=_d("double_buffer", 0),
        help="吞吐 T4：双缓冲——本轮 PPO 收尾后 spawn 后台 collect-only 子进程预采"
        "下一轮（行为快照 θ_N，子进程读快照不读 args.out，防权重写回污染）；"
        "下轮开头 join 子进程后直接走盘上 shard 聚合重放（藏掉采集墙钟）。"
        "依赖 T3（--eval-at/--eval-every 释放集群尾段）。默认 0 = 原行为字节一致。",
    )
    ap.add_argument(
        "--precollect-early",
        type=int,
        default=_d("precollect_early", 0),
        help="吞吐 T4 提前量：预采 spawn 时机从『PPO 全收尾』提前到『第"
        "(epochs-提前量) 个 epoch 完成后』（如 1 = epoch3/4 后就 spawn，PO 藏进"
        "最后 1 个 epoch）。快照 θ_{N,e3} ≈ θ_N（差最后一段梯度），语义仍 on-policy 带内；"
        "配合 --precollect-games 只预采下一轮首波语料。0 = 原行为（PPO 后 spawn）。",
    )
    ap.add_argument(
        "--precollect-games",
        type=int,
        default=_d("precollect_games", 0),
        help="吞吐 T4 限制：预采子进程只采前 N 局（下一轮首波 wave 的语料），其余"
        "局由下轮以 θ_N 现场采集（严格 on-policy）。0 = 全量 150 局预采（原行为）。",
    )
    ap.add_argument(
        "--precollect-samples",
        type=int,
        default=_d("precollect_samples", 0),
        help="吞吐 T4 样本量 halt：预采子进程累计样本达此值即停采（不截断 pairs，"
        "用 halt_event 提前退出）。0 = 不启用（用 --precollect-games 或全量）。"
        "与 --precollect-games 互斥：precollect_samples 优先。",
    )
    ap.add_argument(
        "--collect-only",
        type=int,
        default=0,
        help="内部：仅采集一轮落盘后退出（T4 双缓冲子进程模式；不 PPO/不 eval/不写权重）。",
    )
    # ===== 课程配置化（plan/rl-training-config.md §3）：唯一启动入口，无 CLI 逐参覆盖 =====
    ap.add_argument(
        "--course",
        default="",
        help="课程名（nn-training/curricula/<name>.jsonc）或路径。启动参数/关卡布局/"
        "奖励公式的单一事实来源——优先级：课程配置 > rl-config.json > argparse 默认；"
        "传入后各训练参数不再允许 CLI 逐参覆盖（评审 P1-7：改名避开 --curriculum-* 语义）",
    )
    ap.add_argument(
        "--course-file",
        default="",
        help="等价于 --course <路径>（显式文件路径形式；与 --course 互斥）",
    )
    # ===== 远程 PPO（plan/remote-ppo-architecture.md §5/§9）：PPO 在云端 GPU，rollout 在本地 =====
    # 默认 local = 现状逐字节回归基线；remote 内部强制 stream=0（每迭代结算一次 PPO）+
    # skip 模型构建（hub 免 torch，D2），且与 --stream 1 / --double-buffer 显式互斥（fail-fast）。
    ap.add_argument(
        "--ppo",
        default=_d("ppo", "local"),
        choices=("local", "remote"),
        help="PPO 执行面：local（现状，CPU/本机） / remote（云端 GPU worker，旁路 hub-server）",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="冒烟预演（配 --ppo remote）：收到冒烟回显结果（remote_worker --echo）后"
             "作废本轮并干净退出；it 不前进、不写 iteration 事件",
    )
    ap.add_argument(
        "--remote-hub-url",
        default=_d("remote_hub_url", ""),
        help="远程模式：hub-server base URL（如 http://127.0.0.1:8787）",
    )
    ap.add_argument(
        "--remote-token",
        default=_d("remote_token", ""),
        help="远程模式：hub-server Bearer token（云 worker 与训练主循环共享）",
    )
    ap.add_argument(
        "--remote-job-root",
        default=_d("remote_job_root", ""),
        help="远程模式：job 目录根（默认 <traj>/remote-jobs；与 hub-server --job-root 一致）",
    )
    ap.add_argument(
        "--remote-precollect",
        type=int,
        default=_d("remote_precollect", 0),
        help="远程模式预采（D3/Q10，默认 0=测后开）：1=PPO 等待窗口 spawn 下一轮首波"
        "预采（stale 上限 30%，超量下轮现场重采）",
    )
    ap.add_argument(
        "--echo-config",
        action="store_true",
        help="只打印生效配置 + 当轮公式与 params 指纹（AST dump），不训练——"
        "可重定向到文件，据此复现任意 iter 的完整奖励计算（评审 R1-8 / LC §4.5）",
    )
    return ap
