"""eval_yield —— 「评估让位/份额（尾巴）策略」的判决面：哪些秒/哪些局留给本机、尾巴怎么收
（S5 第十二刀，2026-09-27，从 `rl/eval_local.py` 与 `rl/eval_dispatch.py` **两处一起**切）。

族的两半：

* **本机份额与让位**：`EVAL_LOCAL_SLOTS_DEFAULT`（份额缺省）/ `EVAL_LOCAL_RELEASE_GRACE`
  （宽限期）/ `reserve_local_slots`（尾段预留量公式）/ `hold_for_local`（节点是否该把队尾
  留给本机）/ `local_release_due`（宽限强制放行公式）/ `release_local_gate_if_starved`
  （无节点立刻开闸）/ `local_gate_release_plan` + `eval_local_early_epochs` +
  `early_epoch_reached` + `EVAL_LOCAL_EARLY_EPOCHS_DEFAULT`（放行档与提前点）。
* **尾巴收拢**：`eval_join_soft_sec` + `EVAL_JOIN_SOFT_SEC_DEFAULT`（应急旋钮：站等上限，
  缺省 0 = 不站等）/ `eval_tail_overran`（越窗判据）/ `inflight_grace_cap` +
  `EVAL_INFLIGHT_GRACE_SEC`（窗口到期后等在飞落账的上界）。

为什么单独成家（独立所有者 + 独立触发条件）：这些判决的触发者是**边界事件**——派发那一刻的
份额分档、PPO 收官的 join/交棒、下一轮 rollout 收官时的收拢、窗口到期后的在飞宽限——与
「怎么在本机跑一局评估」（子进程 / 看门狗 / 账本，`rl/eval_local.py`）零共享状态。留在运行器
里时，读者（派发器 `rl/eval_dispatch.py` / 边界侧 `rl/loop_eval.py` / 份额缺省
`rl/batch_runner.py`）为一条判据就要拖入整台运行器。分开后本模块是**零依赖叶子**（不 import
任何模块），两侧都能直接依赖。

搬来的名字在 `rl/eval_local.py` 留 `X as X` 门面（「名字是契约，位置不是」）：旧 import 路径
继续成立，历史调用点不必改。三个公式（`reserve_local_slots` / `local_release_due` /
`inflight_grace_cap`）原先是 `EvalDispatcher.run` 里的**内联表达式**——只有实现、没有名字；
本刀把它们提出来住进本族，判据与公式各只有一个实现点。
"""

from __future__ import annotations

EVAL_LOCAL_SLOTS_DEFAULT = 4  # 本地直跑槽位默认值（policy.evalLocalSlots 可覆写；0=禁用）
EVAL_LOCAL_RELEASE_GRACE = 300  # 距窗口截止剩这些秒时强制释放本地预留（本地失效也不空转到超时）
# 窗口到期仍在飞的局：给它们的落账宽限上界（收工不是立刻砍在飞——那些局有价值，
# 但旧实现在此 join(window + taskTimeoutSec) 会空等 4–76s/轮，故改为「在飞清空即走 +
# 本上界兜底」，2026-09-19 审计 B1）。
EVAL_INFLIGHT_GRACE_SEC = 120

# ---- eval 尾巴的收拢点与本机份额提前放行（2026-09-17 用户指令）-------------------
# 背景：in-loop eval 已藏在「下一轮 PPO」里（dispatch 排在 _serial_ppo 之前），但两处
# 仍把 eval 墙钟暴露在 PPO 之后：
#   ① `_join_eval` 在 PPO 收官后站着等尾巴（原硬编码 180s）；
#   ② 本机预留份额（policy.evalLocalSlots）的 gate 只在 `_join_eval` 置位——本机局
#      在 PPO 结束后才开跑，那段时间等于「PPO 后的第二次串行等待」。
# 处置：① **不站等任何固定秒数**——尾巴交给「下一轮 rollout 收官」这个自然边界收拢
#    （`_sweep_eval_tail`，非阻塞）：尾巴在整段采集期间自己跑完就自己落账，到边界只
#    做一次零成本观测/清账；跑不完的继续在后台（它自己的 `eval_window_sec` deadline
#    会结束它，且 summary 由该线程自己结算，不靠 join）。
#    `policy.evalJoinSoftSec` 保留为**应急旋钮**（缺省 0 = 不站等；>0 = 回到旧的
#    “PPO 后最多站等 N 秒”语义）。
# ② 本机份额按「本轮 PPO 是否占本机核心」分档放行：远端 PPO / 整轮上云 / stream
#    （PPO 已在轮内跑完）⇒ **立刻放行**；本机 PPO ⇒ 最后一个 epoch 开始即放行
#    （policy.evalLocalEarlyEpochs 控制，0 = 维持 R6 原语义：PPO 全收尾才放行）。
EVAL_JOIN_SOFT_SEC_DEFAULT = 0.0  # policy.evalJoinSoftSec 缺省值（0 = 不站等，尾巴交下一轮边界）
EVAL_LOCAL_EARLY_EPOCHS_DEFAULT = 1  # policy.evalLocalEarlyEpochs 缺省值（0=严格 R6）


def eval_join_soft_sec(policy_cfg: dict | None) -> float:
    """PPO 收官后站等上限（秒）：应急旋钮 policy.evalJoinSoftSec，**缺省 0 = 不站等**。

    正常路径不靠它：尾巴由下一轮 rollout 边界自然收拢（非阻塞）。非数值/NaN 回落默认、
    负值夹 0：配置写错不得让主链等一个荒谬的时长或直接崩。
    """
    raw = (policy_cfg or {}).get("evalJoinSoftSec", EVAL_JOIN_SOFT_SEC_DEFAULT)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return EVAL_JOIN_SOFT_SEC_DEFAULT
    if val != val:  # NaN
        return EVAL_JOIN_SOFT_SEC_DEFAULT
    return max(0.0, val)


def eval_tail_overran(t_start: float, window_sec: float, now: float) -> bool:
    """尾巴是否已跑过**它自己的** `eval_window_sec` 窗口（越过 = 异常，日志要打 WARN）。

    边界收拢不站等，但仍需要知道「这个尾巴是不是已经不正常了」：它自带的窗口是唯一的
    时间基准（不是新魔数），过期后线程会在自己的收尾路径里结算 summary 并退出。
    """
    if window_sec <= 0:
        return False
    return (float(now) - float(t_start)) > float(window_sec)


def eval_local_early_epochs(policy_cfg: dict | None) -> int:
    """本机 PPO 路径提前放行本机份额的 epoch 数（policy.evalLocalEarlyEpochs；0=不放行）。"""
    raw = (policy_cfg or {}).get("evalLocalEarlyEpochs", EVAL_LOCAL_EARLY_EPOCHS_DEFAULT)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return EVAL_LOCAL_EARLY_EPOCHS_DEFAULT
    return max(0, val)


def local_gate_release_plan(
    *,
    ppo_remote: bool,
    node_rollout: bool,
    stream_round: bool,
    early_epochs: int,
) -> str:
    """本机 eval 份额的放行档（纯函数）：'immediate' | 'last_epoch' | 'on_join'。

    - immediate：本轮本机**不跑 PPO**（远端 PPO / 整轮上云 rollout），或 stream 轮里
      PPO 已在轮内跑完 ⇒ 本机核心此刻空闲，预留尾段立即开跑（同时把节点从
      hold_for_local 的预留里放出来）。
    - last_epoch：本机 PPO ⇒ 最后一个 epoch 开始即放行（early_epochs>0）；
    - on_join：early_epochs==0 = 维持 R6 原语义（PPO 全部收尾、_join_eval 置位才放行）。
    """
    if node_rollout or ppo_remote or stream_round:
        return "immediate"
    return "last_epoch" if early_epochs > 0 else "on_join"


def early_epoch_reached(ep_done: int, epochs: int, early: int) -> bool:
    """第 ep_done 个 epoch（1 基，与 ppo_update 的 on_epoch_done 同口径）完成时，
    是否已到提前放行点。early=1、epochs=4 ⇒ 第 3 个 epoch 完成即放行（最后一个 epoch
    与本机 eval 份额并行）；与吞吐 T4 预采用的同一判据（`ep_done >= epochs - early`）。"""
    if early <= 0:
        return False
    return int(ep_done) >= max(1, int(epochs) - int(early))


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


def release_local_gate_if_starved(local_gate, nodes_ok: list) -> bool:
    """无远端节点时立刻开闸本机 eval。

    2026-09-15 x3-power it30：engine_epoch 全员 mismatch → nodes_ok=[] →
    local_worker 空等 gate 到 deadline（600s 零局），drain 超时后才写 run_complete
    ——控制台「训练已完成」横幅被拖到 10 分钟后，且终轮 eval 缺失。
    """
    if local_gate is None or nodes_ok:
        return False
    local_gate.set()
    return True


# ---- 从 `EvalDispatcher.run()` 的内联判决点提出的三个公式（S5 第十二刀，2026-09-27）---------
# 判决点原先散在 873 行派发器的闭包里（尾段预留量 / 宽限强制释放点 / 在飞落账宽限）：只有
# 实现、没有名字。提出来的三个都是**原式逐项等价**——不是重新设计（本机行为零变化），只是让
# 判据与公式各只有一个实现点。


def reserve_local_slots(
    total: int, local_slots: int, snapshot_ready: bool, gate_wired: bool
) -> int:
    """尾段预留量：节点不取最后这么多局（留给本机直跑）；0 = 节点可取走全部。

    原式（`EvalDispatcher.run`）：`min(local_slots, total) if (snapshot_path is not None and
    local_gate is not None and local_slots > 0) else 0` —— 冻结权重快照缺席（本机跑不了局）、
    gate 未接线（本机不参与）或份额为 0 时恒 0；预留量不超过待评总数。
    """
    if not (snapshot_ready and gate_wired and local_slots > 0):
        return 0
    return min(local_slots, total)


def local_release_due(now: float, deadline: float) -> bool:
    """距窗口截止已进入宽限期（`EVAL_LOCAL_RELEASE_GRACE`）⇒ 强制释放本机预留。

    原式（`EvalDispatcher` 的节点 worker）：`time.time() >= deadline - EVAL_LOCAL_RELEASE_GRACE`
    —— 这是 `hold_for_local` 的 `past_release` 实参的唯一来源；本机预留失效（本机一直没开跑）
    也不许把整轮空转到超时：宽限期一进，节点继续取任务。
    """
    return float(now) >= float(deadline) - EVAL_LOCAL_RELEASE_GRACE


def inflight_grace_cap(task_timeout: float) -> float:
    """窗口到期后等「在飞局落账」的宽限上界（秒）：`min(taskTimeoutSec, EVAL_INFLIGHT_GRACE_SEC)`。

    原式（`EvalDispatcher.run` 的收口段）：`float(min(task_timeout, EVAL_INFLIGHT_GRACE_SEC))`
    —— 在飞清空即走，本上界只是兜底（旧实现在此 join(window + taskTimeoutSec) 会空等
    4–76s/轮，2026-09-19 审计 B1）。
    """
    return float(min(task_timeout, EVAL_INFLIGHT_GRACE_SEC))
