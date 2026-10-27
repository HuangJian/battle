"""volume_alloc.py — 分关采样分配（plan/rollout-stage-balance.plan.md §3，`VOLUME_ALLOC_RULE`）。

为什么存在：`target_transitions` 的达标线是**分关** quota（`ceil(target/关数)`），而云机腿
过去按**全局** est 反解**每关同一个**局数（`volume_waves.initial_games`）⇒ 关间 `samples/局`
差 1.75× 时，同一轮里既有浪费（长局关溢出被裁）又有缺口（短局关欠采）。本模块把「每关多少局」
拆成三个纯函数：**首批按上界反解**（故意偏小 ⇒ 过采率低）、**补差按机器宽度铺满**
（`spread_games_by_stage`：批次级固定开销与批内局数无关 ⇒ 批要按机器宽度走，不住缺口走，
2026-10-11）、以及**缺口解算**的老口径（`topup_games_by_stage`：现在只剩「对账规格」身份）。

标定（真机 `tmp/x21-psh-b` 142 轮 per-game，bootstrap 2×10⁴ 轮；配方见评审 §7）：

| 规则 | P(本轮有缺口) | E[局数/轮] | E[缺口样本] | E[浪费样本] |
|---|---|---|---|---|
| 全局 est 一批，无补差 | 91.1% | 157.8 | 1918 | 2659 |
| 上界首批 + **1** 批补差（均值） | 71.6% | 160.1 | 299 | 1761 |
| 上界首批 + ≤4 批（…, 末批偏保守）= **本模块口径** | **3.0%** | 162.0 | **2** | 2056 |

量纲（与 `volume_waves` 文件头 T9 同一条红线）：`target/quota/collected/est_*` 全是
**已结算 shard 的 `nSamples` 之和**（samples，不是 ticks）。节点侧补差的分母取**产出集**
（未裁剪：it168 实测 Σ=48 080 > kept 44 747），裁剪后的数会让估计量随配额有偏。

★ 2026-10-27：云机侧的采集调度改成**逐局流式**（`worker/stream_collect.py`：每结算一局就
判「差额最大的关」，下一局派它，不再一次反解「这一关要几局」）。本模块因此只剩
**计划/同源校验**身份：`alloc_games_by_stage`（计划侧首批局数）、`spread_games_by_stage`
（本机 trainer 的铺满档）、`widen_by_width`（自主机按 worker 数铺满）与几个纯账本函数。

本模块只有纯函数与常量（无 IO、无 torch、无随机）；IO（扫 shard 目录、跑子进程）在
`worker/iter_rollout.py` / `worker/stream_collect.py`。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from worker.volume_math import ceil_div as _ceil_div
from worker.volume_quota import target_per_stage

#: 分配规则版本（进 iteration 事件 / 云机轮报的 provenance；**不进** `corpus_identity_fp`——
#: 与 `est` 同类：都是「est 决定的局数」这类运行参数，本机链早就逐轮变而不进身份）。
VOLUME_ALLOC_RULE = "per-stage-v3"

#: 首批「上界」系数：`est_hi = ceil(est × 1.15)` ⇒ 首批局数偏少 ⇒ 过采率 10.6%。
#: 1.25 会把过采率降到 3.1%，代价是首批更小、补差批数变多——回标值（§3.4 表）。
DEFAULT_EST_HI_FACTOR = 1.15

#: 派局 est 的**下界**系数（plan/rollout-corpus-supply §3.1 A0-c）：`est_low = floor(est × 0.92)`
#: ⇒ 缺口方程 `ceil(quota/est)` 多派 ~8% 局，把「局长继续变短」的趋势误差吃掉一部分。
#: 为什么是 0.92：DoD 的 `Σgames ≤ 1.15 × 理论` 只容得下 ~13% 超派，0.92 落在里面
#: （6144/488=13 局 vs 理论 12 局 = 1.08）；0.85 那种「末批保守」档会直接打穿腿 C 的 CPU 秒账。
#: 已知残余：趋势误差 >8% 的关仍会出第二批——所以 DoD 写 `batches ≤ 2`，不写 `== 1`。
DEFAULT_EST_LO_FACTOR = 0.92

#: 补差批数上界（**不含**首批）：首批 + 最多 3 批补差 ⇒ P(缺口) 18.9% → 3.0%（§3.4 表）。
DEFAULT_TOPUP_MAX_BATCHES = 3

#: 末批（`batches_left == 1`）的 est 折扣：缺口不可恢复、浪费可恢复 ⇒ 末批故意偏保守。
DEFAULT_LAST_BATCH_LO_FACTOR = 0.85

#: ★ 候选池的**过供**倍数（2026-10-21，用户口径「一开始就铺满所有 worker」）。
#: 派出去的局数不再是「刚好够」，而是「缺口 × 本值」的一个**候选池**：队列里一直有活，
#: 结算一局就从池里取下一局（dispatcher 本来就是这个语义），逐关满额即 `halt_event` 熔断，
#: 池里**没派出去的候选从不运行**（不是"派了再丢"）。
#: 为什么不吃 est 误差：est 逐轮 ±40% 跳变，缺口方程算出的局数必然整体偏少/偏多，
#: 而「池 + 早停」的墙钟由**配额达成时刻**决定，与池多大无关（多出来的局从不跑）。
DEFAULT_POOL_MULT = 2.0

#: **无估计**（首轮冷启动：est 已从课程/CLI 面退场，盘上又没有历史）时的补差批数上界。
#: 初批只有「每关 1 局」（`alloc_games_by_stage` 的不猜档），缺口全靠满铺批填 ⇒ 常规的
#: `DEFAULT_TOPUP_MAX_BATCHES`（3，按「首批已反解到位、只需补残余」标定）不够；这里给的
#: 是一个**防跑飞**的绝对值（循环本身在 eligible 空时自然退出，见 `spread_games_by_stage`）。
#: 2026-10-12（plan/rollout-estfree-worksteal §4.1 冷启动表）。
DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN = 64


def _scale(x: int, factor: float) -> tuple[int, int]:
    """`(x × factor × 1000, 1000)`——**定点化**，避免二进制浮点把「恰好整数的界」抬一档。

    为什么不让调用方写 `math.ceil(est * 1.15)`：`20 × 1.15` 在浮点里是 22.999999999999996
    或 23.000000000000004（取决于值），`ceil` 在后者上会多算一局；而这一局是全链路的基准
    单位（局数硬顶、目录序号、配额反解都靠它）。因子精度取 3 位小数（1.15 / 0.85 都精确）。
    """
    return int(x) * round(float(factor) * 1000), 1000


def est_hi(est: int, *, factor: float = DEFAULT_EST_HI_FACTOR) -> int:
    """`est` 的上界估计 `ceil(est × factor)`；`est ≤ 0` ⇒ **`0` = 无估计**（不是错误）。

    2026-10-12（plan/rollout-estfree-worksteal §4.1）：est 已从**课程/CLI 面退场**，只剩
    「盘上历史派生量」一个身份 ⇒ 首轮无历史时它是 `0`。`0` 是**语义值**，往下游传成
    「每关 1 局」（`alloc_games_by_stage`）与「不设默认硬顶」（`default_game_caps`）；
    **绝不**在这里被静默抬成某个假估计（反探针③：无历史时又猜了一个 est）。
    """
    if int(est) <= 0:
        return 0
    if float(factor) <= 0:
        raise ValueError(f"est_hi 需要 factor > 0，得到 {factor!r}")
    num, den = _scale(int(est), factor)
    return max(1, _ceil_div(num, den))


def pull_est_low(est: int, *, factor: float = DEFAULT_EST_LO_FACTOR) -> int:
    """`est` 的**下界**估计 `floor(est × factor)`；`est ≤ 0` ⇒ **`0` = 无估计**。

    用途只有一处：**喂派局**（`plan_continuous_batch` 的 `ests`）——把局均估计往低处拉，
    让 `ceil(缺口/est)` 多派几局。**不喂硬顶**（`game_cap` 仍按原 est 算，避免双重放大）。

    定点化（与 `est_hi` 同一条教训）：`530 × 0.92` 在二进制浮点里可能落在 487.59999…，
    `floor` 会掉一档；这里先乘 1000 再整除，结果与「十进制手算」逐字一致。
    `est=1` 时结果为 1 而不是 0：`0` 在下游是「无估计」的语义值，会把派局切到 fallback——
    那是一次静默的口径漂移，不是「保守」。
    """
    if int(est) <= 0:
        return 0
    if not 0 < float(factor) <= 1:
        raise ValueError(f"pull_est_low 需要 0 < factor ≤ 1，得到 {factor!r}")
    num, den = _scale(int(est), factor)
    return max(1, num // den)


def pick_stage_est(learned: int, trailing: int) -> tuple[int, str]:
    """分关 est 的来源选择：**本轮已结算 → 历史窗口 → 无估计**。

    为什么把顺序写成函数（2026-10-21，plan/rollout-corpus-supply §1.3/§3.1）：现场是
    `trailing or learned`，而 trailing 永远有值 ⇒ **本轮自校准被短路**，est 一直跟着
    3 轮 pooled 均值走（652 vs 实际 530，+23% ⇒ 少派局 ⇒ 多补一批）。`learned` 是唯一
    不依赖历史窗口的量，必须先说话。

    返回 `(est, src)`：`src` 进 iteration 事件的 `est_src`（可复核「这一轮到底用了谁」）。
    `(0, "none")` = 首轮连历史都没有 ⇒ 调用方走「不猜」档（**不是**错误）。
    """
    if int(learned) > 0:
        return int(learned), "learned"
    if int(trailing) > 0:
        return int(trailing), "trailing"
    return 0, "none"


def alloc_games_by_stage(
    stages: Sequence[int],
    target_transitions: int,
    *,
    ests_hi: Mapping[int, int],
) -> dict[int, int]:
    """首批：`G_s = max(1, ceil(quota / est_hi_s))`（**逐关独立**，跨关不借额度）。

    `est_hi_s ≤ 0` = **无估计**（首轮冷启动）⇒ 该关 **1 局**（不猜；缺口交给满铺补差批，
    见 plan/rollout-estfree-worksteal §4.1）。**缺 key** 仍是响亮错误（那是 bug，不是无估计）。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("alloc_games_by_stage 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    out: dict[int, int] = {}
    for stage in stage_list:
        if stage not in ests_hi:
            raise ValueError(f"alloc_games_by_stage 缺 stage {stage} 的 est_hi（收到 {ests_hi!r}）")
        hi = int(ests_hi.get(stage, 0) or 0)
        out[stage] = 1 if hi <= 0 else max(1, _ceil_div(quota, hi))  # hi ≤ 0 = 无估计 ⇒ 不猜
    return out


def spread_games_by_stage(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    games_done: Mapping[int, int],
    target_transitions: int,
    game_caps: Mapping[int, int],
    width: int,
) -> dict[int, int]:
    """补差批的**满铺**配额：一批恰好 `width` 局，均分给「仍未达标且未触顶」的关。

    为什么不再用缺口解算（`topup_games_by_stage`，2026-10-11 `plan/rollout-topup-fastpath`
    §1.7 / 评审 P0-3）：补差批**每批都要付一遍批次级固定开销**（scratch 落点/探针、TS 树安装、
    权重镜像、建池冷启动、轮末扫盘与清理），这些成本**与批内局数无关** ⇒ 批越小，固定开销占比
    越接近 100%。缺口方程（`ceil(缺口/est)`）的设计目标是「不缺口」，副产品是**批必然小且越补越小**
    ——现场形状：it34 补差1（18 局）3.35s，补差2（**1 局**）48.90s；it49 同形（3.00s → 52.49s）。
    机器宽度（`width` = 首批**实际用过的** workers）已经摆在那里，不用它才是浪费。

    语义（与 `topup_games_by_stage` 的差别是**故意的**）：

    * eligible = `collected < quota` **且**未触 `game_cap`；空 ⇒ 返回 `{}`（本轮不再补差）；
    * 否则把 `width` 局**轮转均分**给 eligible（顺序 = `stages` 给定序 ⇒ 确定性，不吃随机）——
      轮转等价于大余数法（基数 `width // n`，前 `width % n` 个关各 +1），但能**顺手管住硬顶**；
    * **`game_cap` 是硬顶**（与 v3 的「不截断」不同——评审后改成尊重课程字段）：每关拿到的不超过
      它的 cap 余量，余下的局轮转给其它还有余量的 eligible 关；
      ⇒ 批小于 `width` 的**唯一**情形 = 所有 eligible 关的 cap 余量加起来 < `width`
      （即「别再烧了」真的生效时才缩短批，与「缺口小 ⇒ 批就小」是两回事）；
    * `width <= len(eligible)` ⇒ 前 `width` 个 eligible 关各 1 局（退化档：机器比关数还窄）；
    * **不吃 `est`**（不问「一局能产多少样本」，只问「机器能同时跑几局」）；
    * 超采（相对 quota）是故意的（口径：「多余样本直接丢弃」），`wasted_samples` 因此会上升——
      它是**软指标**（不进任何门禁）；硬顶则不越（上面那条）。

    为什么不缺口仍成立：PPO 本就有**逐关样本量配额**（`per_stage_quota`，截断在 GAE 之前）⇒
    多跑的局只烧墙钟，不改训练语义；而缺口由「eligible 非空就继续铺」+ `max_batches` 兜。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("spread_games_by_stage 需要至少一个 stage")
    if int(width) <= 0:
        raise ValueError(f"spread_games_by_stage 需要 width ≥ 1，得到 {width!r}")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    eligible: list[int] = []
    for stage in stage_list:
        if int(collected.get(stage, 0) or 0) >= quota:
            continue
        cap = int(game_caps.get(stage, 0) or 0)
        if cap > 0 and int(games_done.get(stage, 0) or 0) >= cap:
            continue
        eligible.append(stage)
    if not eligible:
        return {}
    n = int(width)
    if n <= len(eligible):
        return {s: 1 for s in eligible[:n]}
    #: 逐关硬顶余量（cap ≤ 0 = **无顶**）。轮转发放：谁还有余量谁拿下一局 —— 与大余数法的分配
    #: 逐关相同（对无限顶时），但硬顶触发时会自然落到其它关，而不是把这一批变小。
    #: ⚠ 「无顶」的等价物取 `n`（本批局数），**不能**取 0：循环最多发 `n` 局 ⇒ `n` 就代表
    #: 「发不完」；写成 0 会让无顶档（`game_caps={}`，例如 `explicit=0` 的调用方漏算 cap）
    #: 整个满铺静默成空 —— 一个静默短采，正是本模块要防的那类。
    slack: dict[int, int] = {}
    for s in eligible:
        cap = int(game_caps.get(s, 0) or 0)
        slack[s] = n if cap <= 0 else max(0, cap - int(games_done.get(s, 0) or 0))
    out: dict[int, int] = {s: 0 for s in eligible}
    left, i = n, 0
    while left > 0 and any(v > 0 for v in slack.values()):
        s = eligible[i % len(eligible)]
        if slack[s] > 0:
            out[s] += 1
            slack[s] -= 1
            left -= 1
        i += 1
    return {s: v for s, v in out.items() if v > 0}


def topup_games_by_stage(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    games_done: Mapping[int, int],
    target_transitions: int,
    ests: Mapping[int, int],
    game_caps: Mapping[int, int],
    batches_left: int,
    last_lo_factor: float = DEFAULT_LAST_BATCH_LO_FACTOR,
) -> dict[int, int]:
    """补差批：逐关 `max(0, ceil((quota − collected) / est))`，`game_cap` 截断。

    ⚠ **2026-10-27 起云机侧不再走它**：逐局流式调度（`worker/stream_collect.py`）不回答
    「这一关要几局」，因此也不需要 est 反解（误差 ±40% ⇒ 多派/少派，正是「一批一批补」
    的根源）。本函数保留为**对账/回归规格**身份（它的公式就是「缺口 ÷ 局均样本」这句话的
    字面量，测试与取证仍拿它对账）。

    est 必须用**本轮实测**（首波一跑就有 `collected/games`），不是 plan 打包时的旧值
    （首轮恒 0 ⇒ 只能走「不猜档」每关 1 局 ⇒ 44 批就是这么来的，2026-10-26 云机现场）。

    * 已达标的关**不进结果**（`collected ≥ quota`）；
    * 触 `game_cap`（`games_done ≥ cap`）的关**不进结果**（调用方负责把「未达标 + 触顶」
      当响亮事件——静默短采正是本模块要防的事）；
    * `batches_left <= 1` ⇒ 本批是最后一批 ⇒ est 打 `last_lo_factor` 折（偏保守：多跑
      几局换「不缺口」，代价有上界）。
    * 返回空 dict ⇒ 本轮不再补差。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("topup_games_by_stage 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    last = int(batches_left) <= 1
    out: dict[int, int] = {}
    for stage in stage_list:
        got = int(collected.get(stage, 0) or 0)
        done = int(games_done.get(stage, 0) or 0)
        if got >= quota:
            continue
        cap = int(game_caps.get(stage, 0) or 0)
        if cap > 0 and done >= cap:
            continue
        est = int(ests.get(stage, 0) or 0)
        if est <= 0:
            raise ValueError(f"topup_games_by_stage 缺 stage {stage} 的 est（收到 {ests!r}）")
        if last:
            num, den = _scale(est, last_lo_factor)
            est = max(1, num // den)  # 向下取整 = 偏保守方向
        remaining = quota - got
        n = _ceil_div(remaining, est)
        if cap > 0:
            n = min(n, max(0, cap - done))
        if n <= 0:
            continue
        out[stage] = n
    # 输出按**缺口降序**（缺口相同 ⇒ stage 升序，确定性）：调用方按这个顺序生成 argv，
    # 派进 worker 池的先后就跟着它走 —— 「差额最大的关卡先采」是用户口径（2026-10-26），
    # 按 stage 升序发会把小号关永远排在前面（现场：41 批里前 9 批一直在补 2000-2003）。
    return dict(
        sorted(out.items(), key=lambda kv: (-(quota - int(collected.get(kv[0], 0) or 0)), kv[0]))
    )


def pool_games_by_stage(
    *,
    stages: Sequence[int],
    collected: Mapping[int, int],
    games_done: Mapping[int, int],
    target_transitions: int,
    ests: Mapping[int, int],
    game_caps: Mapping[int, int],
    width: int = 0,
    mult: float = DEFAULT_POOL_MULT,
) -> dict[int, int]:
    """**候选池**（2026-10-21，用户口径）：一次派多少局的**上限**，不是"要采多少局"。

    与 `spread_games_by_stage` / `topup_games_by_stage` 的分工：

    * 那两个是「**要采多少局**」——由缺口方程决定，est 偏一点就少采一批或多采一批；
    * 本函数是「**队列里先放多少候选**」——`缺口 × mult`，多余的那部分**只在配额没达成时
      才可能被派出去**；逐关一满额就 `halt_event` 熔断，池里剩下的候选**从不运行**
      （不是"派了再丢"，是不派）。

    ⇒ 调度侧因此**不需要知道集群有几个槽位**：只要池远大于槽位（52×2=104 >> 32），
    dispatcher 从 t=0 起就把每个槽都填满，并且一直有活（队列语义本来就是"空一个补一个"）。
    `width > 0` 时再兜一次：池小于 `width` 就按缺口比例补足到 `width`（显式铺满）。

    `est ≤ 0` = 无估计 ⇒ 该关 **1 局**（不猜，与 `alloc_games_by_stage` 同口径）。
    `game_cap` 是硬顶（与 `spread_games_by_stage` 同：余量用完就不再发）。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("pool_games_by_stage 需要至少一个 stage")
    if float(mult) <= 0:
        raise ValueError(f"pool_games_by_stage 需要 mult > 0，得到 {mult!r}")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    eligible: list[int] = []
    for stage in stage_list:
        if int(collected.get(stage, 0) or 0) >= quota:
            continue
        cap = int(game_caps.get(stage, 0) or 0)
        if cap > 0 and int(games_done.get(stage, 0) or 0) >= cap:
            continue
        eligible.append(stage)
    if not eligible:
        return {}
    out: dict[int, int] = {}
    for stage in eligible:
        got = int(collected.get(stage, 0) or 0)
        done = int(games_done.get(stage, 0) or 0)
        est = int(ests.get(stage, 0) or 0)
        mult_num = round(float(mult) * 1000)
        n = 1 if est <= 0 else _ceil_div((quota - got) * mult_num, est * 1000)
        cap = int(game_caps.get(stage, 0) or 0)
        if cap > 0:
            n = min(n, max(0, cap - done))
        if n > 0:
            out[stage] = n
    # 兜底铺满：池 < 机器宽度 ⇒ 按缺口比例补到 width（只在 width 已知时）
    want = int(width)
    if want > 0:
        total = sum(out.values())
        if total < want:
            for stage in _round_robin(eligible, want - total):
                cap = int(game_caps.get(stage, 0) or 0)
                done = int(games_done.get(stage, 0) or 0)
                if cap > 0 and out.get(stage, 0) + done >= cap:
                    continue
                out[stage] = out.get(stage, 0) + 1
    return {s: v for s, v in out.items() if v > 0}


def _round_robin(stages: Sequence[int], n: int) -> list[int]:
    """`n` 个名额按 stage 给定序轮转发（确定性，与 `spread_games_by_stage` 同形状）。"""
    out: list[int] = []
    for i in range(max(0, int(n))):
        out.append(int(stages[i % len(stages)]))
    return out


def quota_met(
    *, stages: Sequence[int], collected: Mapping[int, int], target_transitions: int
) -> bool:
    """**逐关**配额是否全部达成（熔断判据；全局达标不算——短局关不能靠长局关顶）。

    语义与 `shortfall_by_stage` 逐字同源：每一关都必须 `collected ≥ quota`。
    """
    return not shortfall_by_stage(
        stages=stages, collected=collected, target_transitions=target_transitions
    )


def widen_by_width(games_by_stage: Mapping[int, int], width: int) -> dict[int, int]:
    """把逐关局数**重铺到至少 `width` 个候选**（用户口径 2026-10-22：有几个 worker 铺多少局）。

    与 `pool_games_by_stage(width=…)` 的分工：那个在**派发前**算（知道 collected/quota/est），
    这个在**已经有了一份逐关局数**（plan 里的 `games_per_stage_by_stage`）之后把它加宽——
    只需要一个数：本机 worker 数（`cpu_worker_slots()`，自主机问自己，不 ping 任何人）。

    规则：总数已 ≥ `width` ⇒ 原样返回（不折腾）；不足 ⇒ 按 stage 给定序**轮转**补足
    （确定性，与 `spread_games_by_stage` 同形状）。多加的候选不会白跑——派发侧逐关满额即停，
    池里剩下的从不运行。
    """
    out = {int(s): max(0, int(v)) for s, v in games_by_stage.items()}
    want = int(width)
    if want <= 0 or not out:
        return out
    total = sum(out.values())
    if total >= want:
        return out
    stages = sorted(out)
    for i in range(want - total):
        out[stages[i % len(stages)]] += 1
    return out


def shortfall_by_stage(
    *, stages: Sequence[int], collected: Mapping[int, int], target_transitions: int
) -> dict[int, int]:
    """逐关缺口（`max(0, quota − collected)`；已达标的关不在结果里）。**硬指标**。"""
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("shortfall_by_stage 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    out: dict[int, int] = {}
    for stage in stage_list:
        short = quota - int(collected.get(stage, 0) or 0)
        if short > 0:
            out[stage] = short
    return out


def wasted_samples(
    *, stages: Sequence[int], collected: Mapping[int, int], target_transitions: int
) -> int:
    """过采样本数（`Σ max(0, collected − quota)`）。**软指标**：有上界即可，不要求 0。"""
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("wasted_samples 需要至少一个 stage")
    quota = target_per_stage(int(target_transitions), len(stage_list))
    return sum(max(0, int(collected.get(stage, 0) or 0) - quota) for stage in stage_list)


def stage_totals(
    manifests: Sequence[object],
) -> tuple[dict[int, int], dict[int, int]]:
    """单局 shard manifest 列表 → `(collected_by_stage, games_by_stage)`（**产出集**口径）。

    只读 `stage` + `nSamples`（回退 `totalSamples`）；缺 stage / 缺样本数的行跳过
    （半个目录 / 坏 manifest 不该让整轮补差失败——它只是少一局的读数，不静默改口径）。
    """
    collected: dict[int, int] = {}
    games: dict[int, int] = {}
    for m in manifests:
        if not isinstance(m, Mapping):
            continue
        raw_stage = m.get("stage")
        if isinstance(raw_stage, bool) or not isinstance(raw_stage, int):
            continue
        n = m.get("nSamples")
        if not isinstance(n, int) or isinstance(n, bool):
            n = m.get("totalSamples")
        if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
            continue
        collected[int(raw_stage)] = collected.get(int(raw_stage), 0) + int(n)
        games[int(raw_stage)] = games.get(int(raw_stage), 0) + 1
    return collected, games


def default_game_caps(
    stages: Sequence[int],
    target_transitions: int,
    *,
    ests: Mapping[int, int],
    explicit: int = 0,
    mult: int = 4,
) -> dict[int, int]:
    """逐关局数硬顶：`explicit > 0` 时全关同值；否则 `mult × ceil(quota / est_s)`（**有估计**的关）。

    `est_s ≤ 0`（首轮冷启动、无估计）⇒ 该关**不设**默认顶（不出现在结果里 = 无顶）：
    没有估计就不猜一个顶（plan/rollout-estfree-worksteal §4.1）。此时「别再派了」由
    显式 `max_games_per_stage` 与（切片 P0-b）的 no-progress 守卫承担。

    ★ 与 `volume_quota.default_game_cap` 的分工：那个是**单一 est** 的旧口径（本机链历史上
    用全局 est 算 cap），本函数是**分关**版——低 est 的关需要更多局，用全局 est 会让它
    「配额未满就触顶」（评审 P1-5），而 DoD 又把「触 game_cap」当合格路径 ⇒ 静默借口。
    """
    stage_list = [int(s) for s in stages]
    if not stage_list:
        raise ValueError("default_game_caps 需要至少一个 stage")
    if int(mult) <= 0:
        raise ValueError(f"default_game_caps 需要 mult ≥ 1，得到 {mult!r}")
    if int(explicit) > 0:
        return {stage: int(explicit) for stage in stage_list}
    quota = target_per_stage(int(target_transitions), len(stage_list))
    out: dict[int, int] = {}
    for stage in stage_list:
        est = int(ests.get(stage, 0) or 0)
        if est <= 0:
            continue  # 无估计 ⇒ 不设默认顶（缺席 = 无顶，两个消费者都认缺席）
        out[stage] = max(1, _ceil_div(quota, est) * int(mult))
    return out


def validate_volume_block_ext(block: object, *, require_runtime: bool = False) -> dict:
    """分关 `volume` 块（计划侧 + 可选运行时 `it`/`rotate_seed`）的形状 + **同源**校验。

    同源 = 「块里的 `per_stage_quota` 与 `games_per_stage_by_stage` 必须能从 `target/stages/est`
    重新解出来」——两侧（hub 导出 / 节点补差）各自只信自己重算的那一份，杜绝「规则改了但块
    没跟上」的静默漂移。失败抛 `ValueError`（调用方转 `ProtocolError`，响亮失败）。

    `widen_width`（可选，缺省 0 = 未加宽）：自主机把首批按本机 worker 数铺满后写在块里，
    此时 `games_per_stage_by_stage` 必须等于 `widen_by_width(反解值, widen_width)`——
    「加宽」是可复算的纯函数，同源约束照旧逐关生效。
    """
    if not isinstance(block, dict):
        raise ValueError(f"volume 必须是对象，收到 {type(block).__name__}")
    required: tuple[str, ...] = (
        "target_transitions",
        "stages",
        "per_stage_quota",
        "games_per_stage_by_stage",
        "est_s_by_stage",
        "est_hi_factor",
        "topup",
    )
    if require_runtime:
        required = (*required, "it", "rotate_seed")
    missing = [k for k in required if k not in block]
    if missing:
        raise ValueError(f"volume 缺字段: {missing}")
    int_keys = (
        ("target_transitions", "per_stage_quota", "it", "rotate_seed")
        if require_runtime
        else (
            "target_transitions",
            "per_stage_quota",
        )
    )
    for k in int_keys:
        v = block[k]
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"volume.{k} 必须是正整数，收到 {v!r}")
    raw_stages = block["stages"]
    if not isinstance(raw_stages, list) or not raw_stages:
        raise ValueError(f"volume.stages 必须是非空数组，收到 {raw_stages!r}")
    stages: list[int] = []
    for s in raw_stages:
        if isinstance(s, bool) or not isinstance(s, int):
            raise ValueError(f"volume.stages 必须是整数数组，收到 {s!r}")
        stages.append(int(s))
    ests: dict[int, int] = {}
    raw_ests = block["est_s_by_stage"]
    if not isinstance(raw_ests, dict):
        raise ValueError(f"volume.est_s_by_stage 必须是对象，收到 {raw_ests!r}")
    for k, v in raw_ests.items():
        # 0 = 无估计（首轮冷启动）⇒ 该关 `G_s = 1`（与 `alloc_games_by_stage` 的不猜档同源）。
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(
                f"volume.est_s_by_stage[{k!r}] 必须是非负整数（0 = 无估计），收到 {v!r}"
            )
        ests[int(k)] = int(v)
    missing_ests = [s for s in stages if s not in ests]
    if missing_ests:
        raise ValueError(f"volume.est_s_by_stage 缺关: {missing_ests}")
    raw_g = block["games_per_stage_by_stage"]
    if not isinstance(raw_g, dict):
        raise ValueError(f"volume.games_per_stage_by_stage 必须是对象，收到 {raw_g!r}")
    games: dict[int, int] = {}
    for k, v in raw_g.items():
        if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
            raise ValueError(f"volume.games_per_stage_by_stage[{k!r}] 必须是正整数，收到 {v!r}")
        games[int(k)] = int(v)
    missing_g = [s for s in stages if s not in games]
    if missing_g:
        raise ValueError(f"volume.games_per_stage_by_stage 缺关: {missing_g}")
    extra_g = [s for s in games if s not in stages]
    if extra_g:
        raise ValueError(f"volume.games_per_stage_by_stage 多出关: {extra_g}")
    factor = block["est_hi_factor"]
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or float(factor) <= 0:
        raise ValueError(f"volume.est_hi_factor 必须是正数，收到 {factor!r}")
    want_quota = target_per_stage(int(block["target_transitions"]), len(stages))
    if want_quota != int(block["per_stage_quota"]):
        raise ValueError(
            f"volume.per_stage_quota={block['per_stage_quota']} 与 ceil(target/关数)={want_quota}"
            " 不符（分关达标线两侧必须同源）"
        )
    want_g = alloc_games_by_stage(
        stages,
        int(block["target_transitions"]),
        ests_hi={s: est_hi(ests[s], factor=float(factor)) for s in stages},
    )
    # ★ 2026-10-22（自主机首批铺满的**同源**面）：`remote/plan_run` 会用 `widen_by_width`
    #   把首批局数按本机 worker 数加宽（用户口径「有几个 worker 铺多少局」）⇒ 块里的
    #   `games_per_stage_by_stage` 从此**不再等于** est 反解值，而是它的确定性加宽。
    #   宽度本身必须随块声明（`widen_width`），这里按声明值复算一遍、逐关逐字比对：
    #   同源没被放松——「加宽」是可复算的纯函数，不是「谁都能把 games 改大」。
    widen = block.get("widen_width", 0)
    if isinstance(widen, bool) or not isinstance(widen, int) or widen < 0:
        raise ValueError(f"volume.widen_width 必须是非负整数（0 = 未加宽），收到 {widen!r}")
    if int(widen) > 0:
        want_g = widen_by_width(want_g, int(widen))
    for stage in stages:
        if games[stage] != want_g[stage]:
            raise ValueError(
                f"volume.games_per_stage_by_stage[{stage}]={games[stage]} 与"
                f" ceil(达标线/est_hi)={want_g[stage]} 不符（首批局数两侧必须同源）"
            )
    raw_topup = block["topup"]
    if not isinstance(raw_topup, dict):
        raise ValueError(f"volume.topup 必须是对象，收到 {raw_topup!r}")
    for k in ("enabled", "max_batches", "last_lo_factor", "max_games_per_stage"):
        if k not in raw_topup:
            raise ValueError(f"volume.topup 缺字段: {k!r}")
    if not isinstance(raw_topup["enabled"], bool):
        raise ValueError(f"volume.topup.enabled 必须是布尔，收到 {raw_topup['enabled']!r}")
    for k in ("max_batches", "max_games_per_stage"):
        v = raw_topup[k]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"volume.topup.{k} 必须是非负整数，收到 {v!r}")
    lo = raw_topup["last_lo_factor"]
    if isinstance(lo, bool) or not isinstance(lo, (int, float)) or not (0 < float(lo) <= 1):
        raise ValueError(f"volume.topup.last_lo_factor 必须在 (0, 1]，收到 {lo!r}")
    out = dict(block)
    out["stages"] = stages
    out["est_s_by_stage"] = ests
    out["games_per_stage_by_stage"] = games
    out["est_hi_factor"] = float(factor)
    return out


def validate_runtime_volume(block: object) -> dict:
    """运行时块校验（= 计划块校验 + `it`/`rotate_seed` 两个必填运行时键）。"""
    return validate_volume_block_ext(block, require_runtime=True)
