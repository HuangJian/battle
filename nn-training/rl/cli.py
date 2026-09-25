"""build_argparser —— run_rl CLI 参数定义（瘦身，2026-09-02）。

从 run_rl.py main() 拆出（~300 行纯声明）：参数默认取自 rl-config.json
（merged_mode_args 单一事实来源），CLI 显式传参覆盖。collect-only 子进程
复用同一 parser（argv 继承），因此本模块**顶层不 import torch**（--lr 默认
用字面量 3e-4 而非 ppo.engine.LR，见注释）。
"""

from __future__ import annotations

import argparse

from rl.breaker import (
    ENT_BREAK,
    ENT_BREAK_CONSEC,
    ENT_BREAK_MAX_WINRATE,
    KL_BREAK,
    KL_BREAK_CONSEC,
)
from rl.modes import (
    _MODES,
    DEFAULT_EVAL_AT_INTENT,
)


def build_argparser(mode: str, rl_args: dict) -> argparse.ArgumentParser:
    """构建三模式 argparse。rl_args = merged_mode_args 合并后的配置默认值。"""

    def _d(name, fallback):
        return rl_args.get(name, fallback)

    # 节点侧单局硬顶兜底值：**引用看门狗里的那个常量**（不在 help 里抄第二份数字——
    # 抄了就会漂，而 help 说的必须就是代码做的事）。函数内 import：本模块顶层不拉重物。
    from platform_utils import effective_cores
    from remote import game_watch

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
    # F4 ENT 熔断（2026-09-06 课程可配，DECISIONS §339）：热启动课程（BC 蒸馏权重）
    # 天生低熵，旧硬编码 0.60/8/0.5 会误判为崩塌——课程可下调 ent_break（如 0.25）
    # 收紧保护，或抬高 ent_break_consec 放宽。配合相对崩塌语义（breaker.py）。
    ap.add_argument(
        "--ent-break",
        type=float,
        default=_d("ent_break", ENT_BREAK),
        help="F4 ENT 熔断阈值：熵 <= 此值且 winRate < ent-break-max-winrate 才计连击",
    )
    ap.add_argument(
        "--ent-break-consec",
        type=int,
        default=_d("ent_break_consec", ENT_BREAK_CONSEC),
        help="F4 ENT 连续轮阈值",
    )
    ap.add_argument(
        "--ent-break-max-winrate",
        type=float,
        default=_d("ent_break_max_winrate", ENT_BREAK_MAX_WINRATE),
        help="F4 ENT 护栏：winRate >= 此值视为已收敛，不因低熵停车",
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
        "--exit-on-done",
        action="store_true",
        help="收官（ALL DONE）后直接退出进程（默认停车不断进程：本地停采 + 云停机 + "
        "等待重启；前台脚本/自动化等待退出码时用本旗）。",
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
        "--target-transitions",
        type=int,
        default=_d("target_transitions", 0),
        help="按样本量动态采集（plan/dynamic-rollout-volume）：本轮目标 transitions"
        "（已结算 shard 的 nSamples 之和，分关达标线 = ceil(/关数)）；"
        "0 = 关闭，走 --seed-rotate 固定局数旧语义",
    )
    ap.add_argument(
        # 2026-09-15 T9：旧名 --est-ticks-per-game 是 10× 量纲错（ticks 填进 samples
        # 分母），改名后无兼容别名——旧名会在 argparse 层直接报错（响亮优于静默）。
        "--est-samples-per-game",
        type=int,
        default=_d("est_samples_per_game", 0),
        help="局均 samples 估计（--target-transitions > 0 时必填，单位与 nSamples 一致"
        "= 局均 ticks / K）：首轮反解局数用，之后由 jsonl 的 trailing samples 均值覆盖",
    )
    ap.add_argument(
        "--max-games-per-stage",
        type=int,
        default=_d("max_games_per_stage", 0),
        help="单关单轮局数硬顶（0 = 默认规则 初波 G0 × 4）；触顶 = 停采 + 响亮日志",
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
        # 核数走 effective_cores()（容器配额/亲和掩码 > 宿主机裸数）：os.cpu_count() 在容器里报
        # 宿主机的 224，而 cgroup 可能只给 96（2026-09-25 云机卡死那条账）。
        default=_d("workers", min(effective_cores(), 12)),
        help="concurrent bun rollout workers (games partitioned by seed)",
    )
    ap.add_argument(
        "--local-slots",
        type=int,
        default=_d("local_slots", None),
        help="trainer direct-thread slots (stream mode). R6 schedule: "
        "first-dispatched during collection; suspend once PPO waves "
        "begin (auto-resume if the whole cluster stalls); join eval "
        "remainder after PPO. 0 = 关闭本机直跑（全交给远端节点；远端集体失联仍自动"
        "兜底接管）；缺省/未配置 = auto（stream 走 max(2, workers//4)，queue 走 "
        "workers 封顶）。默认取 rl-config 的 rl.local_slots（每轮热读，2026-09-06 起）",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="run_rl 单实例锁（.run_rl.lock）强制接管——运维重启时旧进程"
        "未被 --kill-previous 杀掉的兜底",
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
        "--rotate-seed",
        type=int,
        default=None,
        help="**调试专用**（训练配对以课程文件为准：`paired_rotate_seed`，两条腿各写同一把 V）"
        "——显式 rotateSeed 覆盖：两条腿传同一值 ⇒ (rotateSeed,it) 种子流逐轮一致、"
        "可配对比较；缺省 None = 旧行为（续跑继承账本 / 全新时刻抖动）。"
        "训练启动请走控制台开课（plan/accident.plan.md §1/§2），本旗标只是后门；"
        "后门用法 = 课程文件里**删键**（显式写 null 会覆盖它）",
    )
    ap.add_argument(
        "--normalize-ret",
        type=int,
        default=_d("normalize_ret", 0),
        help="R5：ret 跨 batch 归一（value 头拟合 O(1) 目标）；0 = 历史行为 "
        "（默认）。课程模式由课程 normalize_ret 驱动（课程是单一事实来源，"
        "无 CLI 逐参覆盖）。",
    )
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
    # ===== 单一 PPO 路径（2026-09-21，plan/accident.plan.md §3）：`--ppo` 旗标**已删除** =====
    # PPO 恒为「打包 → 发布到 hub 队列 → 等 worker 认领 → 三重校验落位」；循环自己没有计算
    # 能力 ⇒「PPO 跑在哪」这个概念在训练侧不存在（想本机算，操作员在控制台起本机 worker，
    # 与云机走**同一认领协议**）。local/remote 分支、launcher env、bare 缺省一并删除。
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="冒烟预演：收到冒烟回显结果（remote_worker --echo）后"
        "作废本轮并干净退出；it 不前进、不写 iteration 事件",
    )
    ap.add_argument(
        "--gate-halt-mode",
        default=_d("gate_halt_mode", "halt"),
        choices=("halt", "notify"),
        help="门禁触发时对云端 PPO worker 的动作："
        "halt = 下发停机达令（默认，历史行为）；"
        "notify = 只记录 gate_verdict + 控制台横幅提示，**不停机**。"
        "运行时可由控制台顶部开关热切（写 <traj>/gate-halt-mode.txt，每轮判定读一次）",
    )
    ap.add_argument(
        "--remote-hub-url",
        default=_d("remote_hub_url", ""),
        help="远程模式：hub-server base URL（如 http://127.0.0.1:8787）",
    )
    ap.add_argument(
        "--remote-transport",
        default=_d("remote_transport", "auto"),
        choices=("auto", "pull", "push", "hubpush"),
        help="远程 PPO 传输裁决：auto=登记在册的 gpu_push 节点 > hub，"
        "rl.hub_push（缺省开）且 hub 可达时改走 hub 中介推送；"
        "pull=强制走 hub（等 worker 自己来领）；push=强制直推 gpu_push 节点（无节点则响亮失败）；"
        "hubpush=发布到 hub、由 hub 按登记表推给空闲 GPU worker（需 hub-url+token）",
    )
    ap.add_argument(
        "--remote-token",
        default=_d("remote_token", ""),
        help="远程模式：hub-server Bearer token（云 worker 与训练主循环共享）",
    )
    # M1（2026-09-17）：隧道选项**只是记录口径**——真正拉起 cloudflared 的是控制台。
    # 但 iteration 事件的 wire.protocol/edge_ip 必须记**当时真正生效**的值
    # （plan §1.4：开关取值要进指标，否则事后无法按选项分组统计）。
    # 取值优先级：本参数 > rl-config `courses.<stem>.cf_*` > rl.* > 空（不记）。
    # 空 = 不记（旧行为）：`--remote-cf-protocol` 是 `type=str` 且有 choices，
    # 字符串才会被写进账本；用 "" 表示「本次不记」而非 "auto"。
    ap.add_argument(
        "--remote-cf-protocol",
        default=_d("cf_protocol", ""),
        choices=("", "http2", "quic", "auto"),
        help="记录口径（M1）：本轮 cloudflared 实际使用的隧道协议，写进 iteration 事件的"
        " wire.protocol；空串 = 不记（旧行为）。控制台启动时已回写 rl-config，本参数只"
        "用于手工启动/覆盖",
    )
    ap.add_argument(
        "--remote-cf-edge-ip",
        default=_d("cf_edge_ip", ""),
        choices=("", "4", "6", "auto"),
        help="记录口径（M1）：本轮 cloudflared 实际使用的边缘 IP 版本，写进 wire.edge_ip；"
        "空串 = 不记",
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
        # ⚠ `%%` 不是笔误：argparse 的 `_expand_help` 对 help 再做一次 `% params`，
        # 裸 `%` 会让**整个 `--help` 崩**（ValueError: unsupported format character）
        # ——2026-09-21 实测（本行曾是全仓唯一裸 `%`），由 `tests/test_cli_help.py` 守住。
        help="远程模式预采（D3/Q10，默认 0=测后开）：1=PPO 等待窗口 spawn 下一轮首波"
        "预采（stale 上限 30%%，超量下轮现场重采）",
    )
    ap.add_argument(
        "--remote-slim",
        type=int,
        default=_d("slim", 1),
        choices=(0, 1),
        help="远程 PPO 协议瘦身（M2）：1=opt/ref 走内容寻址 blob（**默认**，上行字节"
        " ~4.43MB → ~1.2MB）；0=逐字节回到旧行为（内联 base64 + payload 内冗余文件）。"
        "回退开关，取值进 iteration 事件的 wire.slim（A/B 归因用）",
    )
    # M3（2026-09-17，plan/remote-wire-remediation §5.2）：rollout 上云开关。
    # local = 历史行为（hub 采样本机产 shard，整轮口径逐字节不变）；
    # node = 本轮由节点自己跑 rollout（kind=iter job），hub 不再本地采样。
    # run  = 整段（kind=run job）：节点领走 it..end_it 自己跑完（离线训练模式）。
    # 取值优先级：本参数 > rl-config `courses.<stem>.rollout_src` > rl.* > local。
    ap.add_argument(
        "--rollout-src",
        # ★ 默认**不**取 `rl.*`（2026-09-25，plan/online-offline-role-routing §2.5）：
        # `_d("rollout_src", "auto")` 会把 `rl-config` 顶层的 `rl.rollout_src` 读成 argparse
        # 默认值 ⇒ `_rollout_source` 看到非 auto 就直接早返回，**课程级
        # `courses.<课>.rollout_src` 被整个忽略**（控制台 UI 显示 run、实际跑 local）。
        # 写死 "auto" 只是让裁决回到课程级；`rl.rollout_src` 仍在 `_rollout_source` 的
        # 兜底链里（课程级为空 ⇒ 照旧生效）⇒ 顶层配置的语义一字未变。
        default="auto",
        choices=("auto", "local", "node", "run"),
        help="M3 rollout 上云：'local'=本机采样（默认行为）；'node'=本轮整轮上云"
        "（节点 bun 跑 exporter 产 shard + 跑 PPO，kind=iter job）；'run'=**整段**上云"
        "（一次 kind=run job 领走 it..end_it，节点自主跑完；需要 --run-iters 说明段长，"
        "离线训练模式的机器侧写法）；'auto'=按 rl-config（rl.rollout_src /"
        " courses.<课>.rollout_src）解析，缺省 local；取值进 iteration"
        " 事件的 wire.rollout_src（A/B 归因用）",
    )
    # 半离线（2026-09-17）：一次 `kind=run` job 覆盖 N 轮，节点收到（课程 + 初始权重 +
    # 代码 + 计划）后自主跑完，hub 失联也不影响（逐轮权重/指标落在节点工作目录，
    # Kaggle /kaggle/working / Colab Drive）。0 = 关（逐轮上云/本机，历史行为）。
    ap.add_argument(
        "--run-iters",
        type=int,
        default=_d("run_iters", 0),
        help="半离线整段：一次领走 N 轮（kind=run job；<0 = 跑到课程末尾）——节点自主跑完"
        "并逐轮落产物（K/D 官方目录，可打包下载）；0 = 关",
    )
    ap.add_argument(
        "--export-bundle",
        default="",
        help="全离线：把 --run-iters 那一段打成可上传 Kaggle/Colab 的任务包（zip）后退出；"
        "本轮不训练。云机侧：remote.bundle import 后 remote.run_loop 自主跑完",
    )
    ap.add_argument(
        "--run-wait-sec",
        type=float,
        default=_d("run_wait_sec", 0.0),
        help="半离线段的等待上限（秒；整段墙钟量级）。0 = rl.run_wait_sec > 缺省 8h",
    )
    ap.add_argument(
        "--remote-iter-game-timeout",
        type=float,
        default=_d("remote_iter_game_timeout", 0.0),
        help="M3 rollout 上云：节点侧单局墙钟上限（秒）。0 = 用节点兜底硬顶"
        f"（{game_watch.DEFAULT_GAME_TIMEOUT_SEC:g}s，用户口径：单局 >{game_watch.SLOW_GAME_WARN_SEC:g}s"
        "肯定不正常）；>0 时完全按它。超时的局被 kill 并**原地重跑**同一 argv"
        f"（最多 {game_watch.GAME_MAX_ATTEMPTS} 次，未显式配置时重试上限放宽"
        f" ×{game_watch.RETRY_TIMEOUT_FACTOR:g}），仍失败才算整轮失败",
    )
    ap.add_argument(
        "--remote-iter-workers",
        type=int,
        default=_d("remote_iter_workers", 0),
        help="M3 rollout 上云：节点侧 rollout 并发（0 = 用 args.workers，即课程 quotas.workers；"
        "节点侧另有上限钳制）",
    )
    ap.add_argument(
        "--gate-remediate-stop-after",
        type=int,
        default=_d("gate_remediate_stop_after", 4),
        help="I2（2026-09-13）：提示类门（plateau）REMEDIATE 连续这么多次后**停腿**"
        "（0 = 关，保留旧行为）。c6-pickup3 6 次 / c6-bonus 10 次 cloud halt 的教训："
        "平台期每 5 轮必然复现 REMEDIATE，反复确认的「边际收益枯竭」就是停腿信号",
    )
    ap.add_argument(
        "--echo-config",
        action="store_true",
        help="只打印生效配置 + 当轮公式与 params 指纹（AST dump），不训练——"
        "可重定向到文件，据此复现任意 iter 的完整奖励计算（评审 R1-8 / LC §4.5）",
    )
    return ap
