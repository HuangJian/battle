"""common/game_watch.py —— 单局子进程的**停滞看门狗**（rollout 与 eval 共用同一套口径）。

**为什么需要它**（2026-09-22 两起实测）：

  * 云机 rollout `it34`：310/328 局在 4s 内结算完，剩下几局卡到 **651s** —— 期间日志只有
    「已结算 N 局」的计数行，谁卡了、卡了多久，一个字都没有（旧口径 `timeout=off`）；
  * 单局正常是**亚秒级**（云机 220 并发 328 局 4s；8 并发 600 局 57s）⇒ 用户口径
    「单局 >5s 肯定不正常」，所以 >5s 的局**当场杀掉原地重跑**，不留着等 651s。

一条口径一组常量，任何调用点都不许再抄一份数字：
（上面两条是「单局多慢算不正常」；下面是「一条线程卡多久算机器卡住」——前者管慢局，
后者管**不可取消的阻塞**：D 状态的挂载点 IO / `Popen` 等子进程 exec 的握手，它们既不返回
也不抛，硬顶与看门狗都碰不到它们。）

  * ``SLOW_GAME_WARN_SEC`` (5.0) —— 点名线：超过它就打一行**带局身份**（`s3/d7`）的 WARN。
    默认硬顶与它同值 ⇒ 超时行自己已经点名了，此时不重复打（``warn_is_redundant``）；
    调用方把上限调高（`--remote-iter-game-timeout` / `--eval-game-timeout-sec`）时，
    这一层就是「跑完但慢」的唯一告警。
  * ``DEFAULT_GAME_TIMEOUT_SEC`` (5.0) —— **首次尝试**的硬顶（调用方没给上限时）：超时 ⇒
    kill + 原地重跑。旧口径「0 = 不限」在本机可以（卡住的是自己的终端），在云机上等于
    「一个卡住的 bun 子进程永远等下去」—— 就是上面那 651s 的成因。
  * ``RETRY_TIMEOUT_FACTOR`` (4.0) —— **重试尝试**的上限倍数（5s → 20s）。为什么重试反而
    放宽：实测单局墙钟是重尾的（本机强并发下 rollout p50 1.8s / p99 16.8s，eval p50 1.2~
    1.6s / p90 3.2~4.2s / p99 7~8s，见 `docs/nn/runtime-opt.md` §8），首次尝试 >5s 已属异常
    （用户口径）值得杀掉重来；但重试是**兜底**——若一次主机抖动把同一局连杀三次，代价是
    「整轮作废重发」或「评估少一局」（读数有偏），比多等十几秒贵得多。调用方**显式**给了
    上限（>0）时对每次尝试一视同仁：配置说了算，不做解释。
  * ``GAME_MAX_ATTEMPTS`` (3) —— 一局最多跑几次：超时 / rc≠0 都**原地重跑同一 argv**
    （种子在 argv 里 ⇒ 同一局是确定性的：重跑要么拿到同一份结果，要么再次响亮失败）。
  * ``GAME_POLL_SEC`` (0.5) —— 轮询粒度：软告警与硬顶都靠它发现（一次 `wait(∞)` 什么都看不见）。
  * ``STALL_WARN_SEC`` (120.0) —— **整轮**停滞线：这么久一局都没结算就点名（带还在飞的局身份）。
    它管的是「所有线程一起卡住、连心跳都哑了」那一档（单局看门狗在那时什么都打不出来）。
  * ``PROGRESS_LOG_SEC`` (60.0) —— 进度行的节流间隔（`progress_due()`）：**按时间**而不是
    按局数，因为这条线的成本只与墙钟有关（用户 2026-09-23：每分钟一句就够）。
  * ``SCAN_CEILING_SEC`` (60.0) —— **轮末扫盘**的墙钟上界（`verify_shards` / `collect_reports` /
    `collect_shard_manifests`）：它们在**主线程**上做全量文件 IO，卡住时连「整轮停滞」都没有
    （那行只在轮循环里打）⇒ 整轮对日志完全静默。超界 ⇒ 机器级停滞（还租约 + 立即重领）。
  * ``GAME_IO_SLACK_SEC`` (20.0) + ``game_ceiling_sec()`` —— 一局**整条链路**（全部尝试 +
    池回退 + 回收）的墙钟上界：超过它就是机器卡在**不可取消的阻塞**上（2026-10-06 云机离线轮
    「整轮停滞十几分钟」取证：一条线程永不返回 ⇒ 轮循环收不齐 ⇒ 轮内重投那套护栏一次都
    触发不了）。超界由腿侧按机器级停滞收场（清半截产出 + 整轮重投），**不**就地重跑。

为什么重试而不是「竞速副本」（用户 2026-09-22 提的两条路）：argv 不变 ⇒ out 目录不变 ⇒
声明的 shard 集（`data_fp`）逐字节不变；副本会多产一个同 (stage,seed) 的 shard 目录，直接撞上
「实产集 == 声明集」那道门。竞速在**多节点**在线路径上成立是因为那里有 hub 侧的候选表
（`trainer/queue_local.py` 的 `pick_race_target` 等）；云机离线只有一个节点，重试是同一效果的最小实现。
"""

from __future__ import annotations

from collections.abc import Sequence

#: 回收预算的**唯一数字**从原语层取（`kill` 之后最多等多久回收一个子进程）：这里算「一局整条
#: 链路的墙钟上界」时要用它——再抄一份就是第二个口径（见 `game_ceiling_sec`）。
from common.platform_utils import KILL_REAP_SEC

#: 点名线（秒）：单局超过它就打一行 WARN 点名（正常一局亚秒级）。
SLOW_GAME_WARN_SEC = 5.0
#: 首次尝试的硬顶兜底（秒）：调用方没给上限时用它（= 点名线，见模块 docstring）。
DEFAULT_GAME_TIMEOUT_SEC = 5.0
#: 重试尝试的上限倍数（首次 5s ⇒ 重试 20s）；调用方显式给上限时不放大。
RETRY_TIMEOUT_FACTOR = 4.0
#: 一局最多跑几次（首次 + 重试）。
GAME_MAX_ATTEMPTS = 3
#: 轮询粒度（秒）：软告警与硬顶都靠它发现。
GAME_POLL_SEC = 0.5

#: 进度行（「N/M games settled」）的节流间隔（秒）。
#:
#: 用户口径 2026-09-23：云端离线课的日志被进度行刷屏（`[run] kind=iter rollout: 30/224
#: games settled (18s)`）——**每分钟一句就够**。原来的节流是**按局数**（每 10 局一句），
#: 而这条线的成本与局数无关、只与墙钟有关：8 并发下一轮 328 局 3 分钟打完 = 33 行，
#: 而 220 并发的在线节点上是每秒数行。所以改成按**时间**节流（最后一句仍然必打，
#: 否则「跑完了」这件事会没有落点）。
PROGRESS_LOG_SEC = 60.0

#: 一局**整条链路**的墙钟上界（`game_ceiling_sec`）在「尝试次数 × (一局一次尝试 + 回收)」
#: 之外再留的**余量**（秒）：覆盖池回退、`_clean_attempt`（删半截产出，重试前最多 2 次、
#: 每次 ≤ `CLEAN_CEILING_SEC`）这些小的文件操作。
#:
#: 为什么要有这条线（2026-10-06 Kaggle 离线轮「整轮停滞十几分钟」取证）：一局的路径上有若干
#: **无法从 Python 里取消**的阻塞点（挂住的挂载点上的 `mkdir`/`open`/`write`/`rmtree`，以及
#: `Popen` 等子进程 exec 成功的那一手 `os.read`）。它们卡在 D 状态时既不返回也不抛 ⇒ 那一局的
#: 线程永远不结算 ⇒ 轮循环的 `wait()` 永远收不齐 ⇒ 轮内那套「机器级停滞」护栏一次都触发不了
#: （它的触发条件是「有 future 抛了 `UnreapableChildError`」）。现场读数：每 120s 一条「整轮停滞：
#: … 还有 55 局在飞」而一局都不结算，十几分钟不动。
#:
#: 所以一局必须有**自己的墙钟上界**：超了就按机器级停滞收场（清半截产出 + 整轮重投），
#: 而不是把整轮当人质。余量给得宽（正常一局亚秒级、最坏一次尝试也就 20s）——它的职责不是
#: 「掐慢局」（那是 `SLOW_GAME_WARN_SEC` / 硬顶），而是「不许无限等」。
GAME_IO_SLACK_SEC = 20.0

#: **轮末扫盘**（`verify_shards` / `collect_reports` / `collect_shard_manifests`）的墙钟上界（秒）。
#:
#: 为什么同一族要单列一个数（2026-10-06 §32.4 ⑤）：这三步在**主线程**上做全量文件 IO
#: （rglob 扫 shard、逐局读 manifest/`_rl_report`、`data_fp` 还要哈希每个 shard 的内容）——挂住
#: 的挂载点上它们一样永不返回，而那里**连「整轮停滞」都没有**（那行只在轮循环里打）⇒ 整轮对日志
#: 完全静默（比游戏线程那一档更难查）。它们与「一局」的粒度不同（没有子进程、没有重试、不写盘），
#: 所以不能拿 `game_ceiling_sec`（那个含尝试次数与回收预算，会大到让挂死的扫盘也过关）。
#: 正常一轮的扫盘在亚秒级（几百个小 JSON + 一遍内容哈希）⇒ 60s 是「机器挂了」而不是「盘慢」。
SCAN_CEILING_SEC = 60.0

#: **重试路径上「清半截产出」的墙钟上界**（秒）：`_clean_attempt` = rglob + rmtree 一个 `w{i}/`。
#:
#: 为什么必须给它上界（2026-10-07 云机「假就绪」轮复盘）：那个清理在**逐局重试路径**上是裸调的，
#: 挂住的挂载点上它既不返回也不抛 ⇒ 44 条线程各自吃满整条链路的上界（整轮 32s → 355s），
#: 且**重试行被卡在它后面永远打不出来**（「重试过的局 0 个」是假象；修法：`retry_line` 前置）。
#: 正常清理是亚秒级（一个小目录）⇒ 5s 即「盘挂了」。与 `KILL_REAP_SEC = 5.0` 同族（都是「小 IO
#: 的有界等」）——注意它的**住址**是 `common/platform_utils.py`，本模块只是 import 它。
#:
#: ⚠ 超界 ⇒ **不就地重跑**：被放弃的删除者可能晚到，删掉新写者刚写的同一批路径
#: （`--out`/shard 名都没变）⇒「目录齐、obs 截断」= 静默错数据，正是 `_clean_attempt` 存在的
#: 全部理由。归入机器级停滞（`UnreapableChildError`）交整轮重投。
CLEAN_CEILING_SEC = 5.0

#: **搬回**（scratch → job 目录，一局）的墙钟上界（秒）：`common/scratch.drain_tree` 的一次拷贝。
#:
#: 为什么给它上界（2026-10-08 `plan/rollout-local-scratch.plan.md`）：搬回在主线程上（天然单线程的
#: 顺序 IO），而它写的是**网络挂载点** —— 挂住时既不返回也不抛，整轮的进度循环就跟着静默。
#: 与 `SCAN_CEILING_SEC` / `CLEAN_CEILING_SEC` 同族（都是「单线程上的有界文件 IO」），但量级不同：
#: 正常一局 2–3MB 顺序写 <1s，所以 30s 是「盘挂了」而不是「盘慢」。
#:
#: ⚠ 超界 ⇒ 作废这一局的**安装票**（评审 F4：被放弃的 copier 从此装不进去）并入 `stuck` 交整轮重投；
#: 确定性失败（`ENOSPC/EROFS/EACCES`…）由 `drain_tree` 直接抛 `ProtocolError`，**不进**那条循环（评审 F3）。
DRAIN_CEILING_SEC = 30.0

#: 「整轮停滞」的告警线（秒）：这么久**一局都没结算**就点名一次，并列出还在飞的局。
#:
#: 为什么需要它（2026-09-25 二次取证「rollout 卡死机器半天」）：进度行与心跳都挂在「有局结算」
#: 上（`progress_due` 在结算主循环里被调用）⇒ 所有线程一起卡住时它们**一起哑**。现场就是
#: 「5s 的 270/336 之后 890s 一行都没有」：机器在卡死，而日志里没有任何一条能说「谁卡住了」。
#: 取 2×`PROGRESS_LOG_SEC`：心跳本来就每分钟一句，这一档只回答「连心跳都停了」。
STALL_WARN_SEC = 120.0


def attempt_timeout_sec(base_sec: float, attempt: int, explicit: bool = False) -> float:
    """第 `attempt` 次尝试的硬顶（秒）。

    `explicit=True`（调用方显式配了上限）⇒ 每次尝试都用它；否则**重试**尝试放宽
    `RETRY_TIMEOUT_FACTOR` 倍（兜底尝试宁可多等，也不因为一次主机抖动丢掉这一局）。
    """
    if explicit or attempt <= 1:
        return float(base_sec)
    return float(base_sec) * RETRY_TIMEOUT_FACTOR


def game_ceiling_sec(base_sec: float, explicit: bool = False, reap_sec: float | None = None) -> float:
    """一局（**全部尝试 + 池回退 + 回收**）的墙钟上界：超过它 = 机器卡在不可取消的 IO 上。

    = `GAME_MAX_ATTEMPTS × (2 × 该次尝试的硬顶 + 回收预算) + GAME_IO_SLACK_SEC`。

    为什么是「2 ×」：一次尝试的最坏路径是「先试长驻池（吃满本次硬顶）→ 回退一次性 spawn
    （再吃满一次硬顶）→ kill + 有界回收」；漏掉池那一份就会把**正常的池回退**判成机器级停滞。
    为什么按**最后一次**尝试的硬顶算：重试尝试的硬顶最宽（未显式配置时 ×`RETRY_TIMEOUT_FACTOR`），
    上界取最宽的那一次不会误杀；这个数只用来兜「永不返回」，不追求紧。
    """
    reap = KILL_REAP_SEC if reap_sec is None else float(reap_sec)
    widest = attempt_timeout_sec(base_sec, GAME_MAX_ATTEMPTS, explicit=explicit)
    return GAME_MAX_ATTEMPTS * (2.0 * float(widest) + reap) + GAME_IO_SLACK_SEC


def warn_is_redundant(timeout_sec: float) -> bool:
    """软告警是否与硬顶同值（同值 ⇒ 只打超时行，不再多打一行 WARN）。"""
    return float(timeout_sec) <= SLOW_GAME_WARN_SEC


def progress_due(
    done: int, total: int, now: float, last_at: float, *, every: float | None = None
) -> bool:
    """这一局结算完，该不该打进度行（rollout / eval 两条腿共用一个节流口径）。

    `done >= total`（最后一句）恒 True——收尾那一行是「这一轮结束了」的唯一落点；
    其余按 `now - last_at >= every`（缺省 `PROGRESS_LOG_SEC`）节流。调用方把返回值当
    「现在是不是该打」的判据，并在打完之后把 `last_at` 更新成这次的 `now`。
    """
    if int(done) >= int(total):
        return True
    return (float(now) - float(last_at)) >= (PROGRESS_LOG_SEC if every is None else float(every))


def game_label(stage: object, seed: object) -> str:
    """一局的身份（`s3/d7` 式）。

    诊断行必须能直接说清「是哪一局」——2026-09-22 那 651s 里最缺的就是这个（只有计数）。
    """
    return f"s{stage}/d{seed}"


def slow_warn_line(
    kind: str,
    label: str,
    elapsed: float,
    timeout_sec: float,
    attempt: int,
    where: str,
) -> str:
    """软告警行（**带局身份**；`kind` = `rollout` / `eval`）。"""
    return (
        f"WARN {kind} 单局异常慢：{label} 已 {elapsed:.1f}s"
        f"（正常 <{SLOW_GAME_WARN_SEC:g}s；硬顶 {timeout_sec:g}s；"
        f"第 {attempt}/{GAME_MAX_ATTEMPTS} 次）——现场见 {where}"
    )


def hard_cap_line(kind: str, label: str, elapsed: float, timeout_sec: float, where: str) -> str:
    """硬顶命中行（调用方据此抛可重试失败；`where` 是现场文件）。"""
    return (
        f"{kind} 单局超时（{elapsed:.1f}s > 硬顶 {timeout_sec:g}s）：{label}"
        f"（现场见 {where}）"
    )


def stall_line(kind: str, inflight: int, since_sec: float, labels: Sequence[str]) -> str:
    """整轮停滞行：**已停滞多久 + 还有几局在飞 + 是哪几局**（卡住时唯一能说话的读数）。

    `labels` 按派发顺序给（前 3 个点名，其余只计数）——「谁卡了」正是 2026-09-22 那 651s
    与 2026-09-25 那 890s 里最缺的一条信息。
    """
    who = "、".join(str(x) for x in list(labels)[:3])
    if len(labels) > 3:
        who += "…"
    return (
        f"WARN {kind} 整轮停滞：{since_sec:.0f}s 里一局都没结算（还有 {inflight} 局在飞"
        + (f"：{who}" if who else "")
        + "）——单局看门狗只管单局（5s 硬顶），卡住的往往是**子进程回收或挂载点 IO**，"
        "查那两条（common.platform_utils.reap_bounded 的那本账）"
    )


def ceiling_line(kind: str, label: str, ceiling_sec: float, where: str) -> str:
    """一局**超界**行（整条链路都没在上界内返回）：机器卡在不可取消的 IO 上。

    与 `hard_cap_line` 的分工：那一行是「这一局慢」（有上限的等，超时即 kill + 就地重跑）；
    这一行是「本线程连自己都没能返回」（不可中断的 IO，kill 也收不了场）——处置只能是
    整轮重投（见 `game_ceiling_sec`）。
    """
    return (
        f"WARN {kind} 单局超界（{ceiling_sec:g}s 内整条链路都没返回）：{label}"
        f"——大概率卡在不可中断的 IO（挂住的挂载点：mkdir/open/写盘/子进程 exec 握手）；"
        f"本局不就地重跑（同一目录上可能还有活写者），交回整轮重投（现场 {where}）"
    )


def scan_ceiling_line(kind: str, what: str, ceiling_sec: float, where: str) -> str:
    """轮末扫盘**超界**行：卡在不可取消的文件 IO 上，本轮产物结不了算。

    与 `ceiling_line` 的分工：那一行是「一局」；这一行是**轮末在主线程上**的全量扫盘
    （`verify_shards`/`collect_reports`/`collect_shard_manifests`）——那里没有轮循环，也就没有
    「整轮停滞」那行能说话，所以这一行是现场唯一的读数（见 `SCAN_CEILING_SEC`）。
    """
    return (
        f"WARN {kind} 轮末扫盘超界（{ceiling_sec:g}s 内没返回）：{what}"
        f"——大概率卡在不可中断的 IO（挂住的挂载点：rglob/读 manifest/_rl_report、data_fp 哈希）；"
        f"本轮产物结不了算，交回 worker 还租约 + 立即重领重投（不报失败、云机不停）；现场 {where}"
    )


def retry_line(
    kind: str,
    label: str,
    attempt: int,
    prev: object,
    timeout_sec: float,
) -> str:
    """重试行：说清「重跑第几次、上次为什么、这次的上限是多少」（上限变化必须可见）。

    ⚠ 它必须**先于**重试前的清理打出来（调用点纪律）：清理一旦挂住，这行就是「到底重试了没」
    的唯一读数——2026-10-07 的现场整轮都看不到它（「重试过的局 0 个」），而其实每局都在重试。
    """
    return (
        f"{kind} 单局重试 {attempt}/{GAME_MAX_ATTEMPTS}：{label}"
        f"（上次：{prev}；本次上限 {timeout_sec:g}s）"
    )


def clean_ceiling_line(
    kind: str, label: str, ceiling_sec: float, attempt: int, where: str
) -> str:
    """重试路径**清理超界**行：半截产出清不掉 ⇒ **不就地重跑**，按机器级停滞交整轮重投。

    为什么不能「照常重跑这一局」：被放弃的那条 daemon 线程还在跑 rmtree，它删的正是新一次尝试
    要写的同一批路径（`--out`/shard 名都没变）⇒ 目录齐、obs 截断的静默错数据（见
    `CLEAN_CEILING_SEC`）。超界那一刻的现场（哪一局、第几次尝试、上界值）由本行留痕。
    """
    return (
        f"WARN {kind} 半截产出清不掉（attempt {attempt}）：{label}"
        f"（{ceiling_sec:g}s 内 rmtree 没返回——挂载点 IO 还没好）"
        "——本局不就地重跑（删除者可能晚到，再起写者 = 静默错数据），"
        f"交回整轮重投（现场 {where}）"
    )


def drain_ceiling_line(kind: str, label: str, ceiling_sec: float, where: str) -> str:
    """搬回**超界**行：本地盘 → job 目录的一局拷贝在上界内没返回。

    与 `scan_ceiling_line` / `ceiling_line` 的分工：那两行管「跑局/扫盘」，这一行管**结算之后的搬回**
    （见 `DRAIN_CEILING_SEC`）。处置：安装票作废（被放弃的 copier 从此装不进去）+ 本局并入 `stuck`
    交整轮重投 —— 与机器级停滞同口径，不就地重跑。
    """
    return (
        f"WARN {kind} 搬回超界（{ceiling_sec:g}s 内没返回）：{label}"
        "——本地盘 → job 目录的拷贝卡住了（网络盘 IO 还没好）；本局不装进去"
        "（半截拷贝不许进 job 目录：`scan_shard_dirs` 会把它当产出），交整轮重投"
        f"；现场 {where}"
    )


def drain_fail_line(kind: str, label: str, n_done: int, n_failed: int, where: str) -> str:
    """搬回失败的**一局**行（非绑口；轮账里的 `搬回=` 汇总是它的汇总）。"""
    return (
        f"WARN {kind} 搬回失败：{label}（本地盘上的产出还在，本局不产出）"
        f"——累计 {n_done} 成 / {n_failed} 败；现场 {where}"
    )


def game_time_summary(kind: str, items: list[tuple[float, str]], *, retried: int = 0) -> str:
    """一轮结束时的**单局耗时分布**（每轮都打：5s 这条线要靠真数据校准，不靠猜）。

    `items` = 逐局 `(墙钟秒, 局身份)`（**成功那次尝试**的墙钟；重试次数用 `retried` 另计）。
    最慢 3 局**点名**——不然「p99=17s」这种读数没法查是哪几局。
    """
    secs = sorted(s for s, _ in items)
    n = len(secs)
    if n == 0:
        return f"{kind} 单局耗时：本轮没有跑成的局（重试过的局 {retried} 个）"
    slowest = sorted(items, key=lambda x: x[0], reverse=True)[:3]
    over = sum(1 for s in secs if s >= SLOW_GAME_WARN_SEC)
    return (
        f"{kind} 单局耗时（{n} 局）：p50={_pct(secs, 0.5):.2f}s p90={_pct(secs, 0.9):.2f}s "
        f"p99={_pct(secs, 0.99):.2f}s max={secs[-1]:.2f}s｜≥{SLOW_GAME_WARN_SEC:g}s 有 {over} 局"
        f"｜重试过的局 {retried} 个｜最慢：" + "、".join(f"{lab}={s:.2f}s" for s, lab in slowest)
    )


def _pct(sorted_secs: list[float], q: float) -> float:
    """有序列表的分位数（最近秩，无插值；列表已排序，调用点负责）。"""
    if not sorted_secs:
        return 0.0
    idx = round(q * (len(sorted_secs) - 1))
    return sorted_secs[min(max(idx, 0), len(sorted_secs) - 1)]


__all__ = [
    "CLEAN_CEILING_SEC",
    "DEFAULT_GAME_TIMEOUT_SEC",
    "DRAIN_CEILING_SEC",
    "GAME_IO_SLACK_SEC",
    "GAME_MAX_ATTEMPTS",
    "GAME_POLL_SEC",
    "RETRY_TIMEOUT_FACTOR",
    "SCAN_CEILING_SEC",
    "SLOW_GAME_WARN_SEC",
    "STALL_WARN_SEC",
    "attempt_timeout_sec",
    "ceiling_line",
    "clean_ceiling_line",
    "drain_ceiling_line",
    "drain_fail_line",
    "game_ceiling_sec",
    "game_label",
    "game_time_summary",
    "hard_cap_line",
    "retry_line",
    "scan_ceiling_line",
    "slow_warn_line",
    "stall_line",
    "warn_is_redundant",
]
