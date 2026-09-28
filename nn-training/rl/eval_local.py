"""eval_local —— 干净评估的纯函数/无状态工具（2026-09-02 从 eval_dispatch.py 拆出）。

干净评估（2026-08-24，用户指令）：用各节点已缓存的同权重跑固定语料贪心局。
两股噪声都消掉：动作 argmax 无探索噪声、(stage,seed) 语料恒定 → 跨 checkpoint
配对可比（同 seed 胜负是确定事件）。本模块只含无状态工具与台账结算（尾巴策略已下沉 `rl/eval_yield.py`，S5 第十二刀）——
派发/对账状态机在 rl/eval_dispatch.py::EvalDispatcher（OO 化）。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

# 单局看门狗口径：**一律通过模块属性读**（`game_watch.X`）——import 会把值抄成第二份绑定，
# 测试 patch 了 game_watch 那份、调用点还在读旧绑定就是静默的错口径。
from common import game_watch
from common.protocol import EVAL_SCRIPT as _EVAL_SCRIPT
from common.protocol import UnreapableChildError
from platform_utils import KILL_REAP_SEC, keep_unreaped, kill_process_tree, popen_own_group
from rl.log import log

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 battle2（rl/ 上溯 3 层，修正 2026-09-03）

# ---------------------------------------------------------------------------
# 双轨评估（种子分类 + 过拟合报警 + summary 结算）已下沉 `rl/eval_track.py`
# （S5 第三刀，2026-09-27）。实现搬家、名字留门面：全仓调用点与测试一行不改。
# ---------------------------------------------------------------------------
from rl.eval_track import (
    _ANCHOR_SEED_SET as _ANCHOR_SEED_SET,
)
from rl.eval_track import (
    _ROTOR_SEED_SET as _ROTOR_SEED_SET,
)
from rl.eval_track import (
    DUAL_TRACK_ANCHOR as DUAL_TRACK_ANCHOR,
)
from rl.eval_track import (
    DUAL_TRACK_ROTOR as DUAL_TRACK_ROTOR,
)
from rl.eval_track import (
    EVAL_SEEDS as EVAL_SEEDS,
)
from rl.eval_track import (
    OVERFIT_GAP_PP as OVERFIT_GAP_PP,
)
from rl.eval_track import (
    OVERFIT_PERSIST_ROUNDS as OVERFIT_PERSIST_ROUNDS,
)
from rl.eval_track import (
    _acc as _acc,
)
from rl.eval_track import (
    _maybe_warn_overfit as _maybe_warn_overfit,
)
from rl.eval_track import (
    _ratio as _ratio,
)
from rl.eval_track import (
    _read_recent_track_wrs as _read_recent_track_wrs,
)
from rl.eval_track import (
    a_eval_seed_list as a_eval_seed_list,
)
from rl.eval_track import (
    dual_track_seeds as dual_track_seeds,
)
from rl.eval_track import (
    is_anchor_seed as is_anchor_seed,
)
from rl.eval_track import (
    is_rotor_seed as is_rotor_seed,
)
from rl.eval_track import (
    overfit_fires as overfit_fires,
)
from rl.eval_track import (
    overfit_gap_pp as overfit_gap_pp,
)
from rl.eval_track import (
    report_winrate_safe as report_winrate_safe,
)
from rl.eval_track import (
    rotor_offset as rotor_offset,
)
from rl.eval_track import (
    rotor_span as rotor_span,
)
from rl.eval_track import (
    settle_eval_summary as settle_eval_summary,
)
from rl.eval_track import (
    should_dual_track as should_dual_track,
)
from rl.eval_track import (
    split_anchor_rotor as split_anchor_rotor,
)

# ---- 「评估让位/份额（尾巴）策略」已下沉 `rl/eval_yield.py`（S5 第十二刀，2026-09-27）-----
# 实现搬家、名字留门面：旧 import 路径继续成立（「名字是契约，位置不是」）。判据与公式
# （份额预留 / 让位与放行档 / 尾巴越窗与收拢）只有一个实现点，读者是派发侧
# （`rl/eval_dispatch`）与边界侧（`rl/loop_eval`）——不是本文件的运行器。
from rl.eval_yield import (
    EVAL_INFLIGHT_GRACE_SEC as EVAL_INFLIGHT_GRACE_SEC,
)
from rl.eval_yield import (
    EVAL_JOIN_SOFT_SEC_DEFAULT as EVAL_JOIN_SOFT_SEC_DEFAULT,
)
from rl.eval_yield import (
    EVAL_LOCAL_EARLY_EPOCHS_DEFAULT as EVAL_LOCAL_EARLY_EPOCHS_DEFAULT,
)
from rl.eval_yield import (
    EVAL_LOCAL_RELEASE_GRACE as EVAL_LOCAL_RELEASE_GRACE,
)
from rl.eval_yield import (
    EVAL_LOCAL_SLOTS_DEFAULT as EVAL_LOCAL_SLOTS_DEFAULT,
)
from rl.eval_yield import (
    early_epoch_reached as early_epoch_reached,
)
from rl.eval_yield import (
    eval_join_soft_sec as eval_join_soft_sec,
)
from rl.eval_yield import (
    eval_local_early_epochs as eval_local_early_epochs,
)
from rl.eval_yield import (
    eval_tail_overran as eval_tail_overran,
)
from rl.eval_yield import (
    hold_for_local as hold_for_local,
)
from rl.eval_yield import (
    local_gate_release_plan as local_gate_release_plan,
)
from rl.eval_yield import (
    release_local_gate_if_starved as release_local_gate_if_starved,
)

EVAL_ITER_SUFFIX = "ev"  # eval iterId = {runId}.{it}ev → 与采集任务在 agent 结果缓存中键空间隔离

# it0 基线评估（2026-09-12 用户）：in-loop eval 的配对基准恒为课程 bc 权重的
# 干净评估，由主循环在 rollout 收官后派发、落账前每轮重试（baseline_summary_landed）。
# 它不是训练迭代（没有 iteration 事件）⇒ 课程结束门的趋势判据按 `iter <= 0` 把它
# 排除（gate_check.read_trend_rows），控制台/配对裁判仍用它当基线。账本双向隔离：
# 基线按 iter==0（且排除 B/C source 行）去重，A-eval 按 min_iter=1 去重。
BASELINE_EVAL_ITER = 0
EVAL_TASK_ATTEMPTS = 2  # 单局重试上限；超限放弃并计数（权重切换后未完成局自然作废）


# 逐局 eval 行 schema + 账本 I/O 已下沉 `rl/eval_rows.py`（S5 第一刀，2026-09-27）
# 实现搬家、名字留门面：全仓调用点与测试一行不改（「名字是契约，位置不是」）。
# ---------------------------------------------------------------------------
from rl.eval_rows import (
    EVAL_CENSUS_KEYS as EVAL_CENSUS_KEYS,
)
from rl.eval_rows import (
    EVAL_LOOT_KEYS as EVAL_LOOT_KEYS,
)
from rl.eval_rows import (
    EVAL_V8_KEYS as EVAL_V8_KEYS,
)
from rl.eval_rows import (
    _read_ledger_rows as _read_ledger_rows,
)
from rl.eval_rows import (
    _summary_games as _summary_games,
)
from rl.eval_rows import (
    append_eval_rows as append_eval_rows,
)
from rl.eval_rows import (
    append_eval_summaries as append_eval_summaries,
)
from rl.eval_rows import (
    eval_census_fields as eval_census_fields,
)
from rl.eval_rows import (
    eval_loot_fields as eval_loot_fields,
)
from rl.eval_rows import (
    eval_row as eval_row,
)
from rl.eval_rows import (
    eval_row_key as eval_row_key,
)
from rl.eval_rows import (
    eval_row_keys as eval_row_keys,
)
from rl.eval_rows import (
    eval_summary_key as eval_summary_key,
)
from rl.eval_rows import (
    eval_v8_fields as eval_v8_fields,
)
from rl.eval_rows import (
    merge_eval_rows as merge_eval_rows,
)
from rl.eval_rows import (
    read_eval_rows as read_eval_rows,
)
from rl.eval_rows import (
    read_eval_summary_rows as read_eval_summary_rows,
)


def run_eval_runner_capture(
    cmd: list[str],
    timeout_sec: float,
    cwd: str | None = None,
    *,
    label: str = "",
    log_fn: Any = None,
    kind: str = "eval",
    attempt: int = 1,
) -> subprocess.CompletedProcess[str]:
    """跑一次导出器并**捕获文本输出**（显式 UTF-8 + errors=replace）。

    必须显式给 encoding：win32 中文机的 locale 是 gbk（cp936），而 bun 的输出里带
    UTF-8 字节 ⇒ 默认解码在 `subprocess` 的 **reader 线程**里抛 UnicodeDecodeError，
    后果两条（2026-09-22 evalA 实测，65 次/100 局）：
      ① 父进程日志被整片 `Exception in thread ... _readerthread` traceback 刷屏；
      ② **captured stdout/stderr 直接丢成 None**（异常死在读线程，`communicate`
         不重抛）⇒ 下面那句失败 RuntimeError 只剩 rc、诊断信息全没（响亮错误变哑巴）。
    同 `rl/queue.bun_version` 的处置（那里早就写对了，这里是漏网的一个）。

    `cwd=None` 缺省 = 仓库根（本机/控制台路径，历史行为逐字节不变）；云机离线评估显式给
    TS 运行时树根（云上没有仓库，见 `run_local_eval_game` 的 `cwd` 形参）。

    **停滞看门狗**（2026-09-22，与 rollout 同一套口径 `common/game_watch.py`）：用 Popen +
    轮询代替一次 `subprocess.run(timeout=)`——后者在局卡住期间什么都看不见，只能等超时；
    现在单局超过 `SLOW_GAME_WARN_SEC`（5s，正常亚秒~几秒级）就**点名**打一行 WARN（带 `s3/d7`），
    到 `timeout_sec` 才 kill 并按 `TimeoutExpired` 上抛（语义与 `subprocess.run` 逐字一致，
    包括异常体里的 captured output——诊断不能被超时吃掉）。

    **收不了尸是另一档**（2026-09-25）：SIGKILL 之后 `KILL_REAP_SEC` 内连输出都收不回来 ⇒ 抛
    `UnreapableChildError`（机器级停滞），不是普通超时——调用方对它**不原地重跑**（旧写者可能
    还活着 ⇒ 同一目录上两个写者写同一份产出），而是清掉半截产出后整轮重投（见
    `remote/offline_eval.run_cloud_eval` 的轮循环）。分类就住在这个抛出点上：调用方拿到的
    异常类型就是它唯一能用的判据。

    软告警与硬顶同值（默认 5s = 5s）时不重复打 WARN：超时行的抛出体自己带着局身份与现场。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        # 自带进程组（POSIX）：超时时能把导出器自己带的子进程一起 SIGKILL（只杀父进程会留下孤儿）。
        **popen_own_group(),
    )
    t0 = time.time()
    warned = False
    while True:
        try:
            out, err = proc.communicate(timeout=game_watch.GAME_POLL_SEC)
            break
        except subprocess.TimeoutExpired:
            elapsed = time.time() - t0
            if (
                not warned
                and not game_watch.warn_is_redundant(timeout_sec)
                and elapsed >= game_watch.SLOW_GAME_WARN_SEC
            ):
                warned = True
                (log_fn or log)(
                    game_watch.slow_warn_line(
                        kind, label or "?", elapsed, timeout_sec, attempt, " ".join(cmd[:3])
                    )
                )
            if elapsed >= timeout_sec:
                kill_process_tree(proc)
                # ★ 这一步曾经是裸 `proc.communicate()`（**无上限**）：子进程卡在不可中断的 IO
                # 里（D 状态）时它永远读不到 EOF ⇒ 一个 slot 永远出不来、而且什么都看不见
                # （2026-09-25 云机「rollout 卡死机器半天」的同源形态，见
                # `platform_utils.reap_bounded`）。有上限之后：尾巴收不到就收不到（诊断少一点
                # 也比挂住强），本局按**机器级停滞**上抛（`UnreapableChildError`）——它和普通
                # 超时是两档，调用方按那个分类决定「原地重跑」还是「整轮重投、只补没评的局」
                # （见 `remote/offline_eval.run_cloud_eval` 的轮循环）。
                try:
                    out, err = proc.communicate(timeout=KILL_REAP_SEC)
                except subprocess.TimeoutExpired:
                    keep_unreaped(proc)  # 非阻塞地等它哪天退出再收尸（sweep_unreaped）
                    (log_fn or log)(
                        f"WARN {kind} 单局子进程杀不掉：{label or '?'} —— SIGKILL 之后"
                        f" {KILL_REAP_SEC:g}s 内连输出都收不回来（pid={proc.pid}，很可能卡在不可"
                        f"中断的 IO 里）——本局按机器级停滞上抛，不再等它"
                    )
                    raise UnreapableChildError(
                        f"{kind} 单局子进程杀不掉：{label or '?'} —— SIGKILL 之后"
                        f" {KILL_REAP_SEC:g}s 内回收不了（pid={proc.pid}）"
                    ) from None
                raise subprocess.TimeoutExpired(
                    cmd, timeout_sec, output=out, stderr=err
                ) from None
    return subprocess.CompletedProcess(cmd, int(proc.returncode or 0), out, err)


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
    stage_json: str = "",
    lives_override: int | None = None,
    player_level: int | None = None,
    # T1.2 policy 透传（EvalBench C 层 God 基线；'god' → export-eval-game 真 God-AI，
    # 权重快照不需存在但调用方仍传——本地直跑与节点同报告 schema）。
    policy: str = "nn",
    # 控制台「导出 replay」（rl/eval_replays_once.py）：非空 = 整局输入录成 .replay
    # 写入该目录（export-eval-game --replay；评估语义零变化）。
    replay_dir: str = "",
    # 导出器的执行根（缺省 = 仓库根，即本机/控制台的路径）。云机离线评估传 TS 运行时树
    # 的根（`ts_code_cache/<sha>/`）——云上没有「仓库」，`tools/sim/export-eval-game.ts`
    # 只在随包下发的 TS 树里。`tools/...` 相对路径与 bun 在 PATH 上都因此成立。
    cwd: str | None = None,
    # 慢局告警的落点（缺省 = `rl.log` 的 log）。云机离线评估传 run_loop 自己的 log，
    # 让「哪一局慢」与那段训练的日志同册（否则告警在另一条流里）。
    log_fn: Any = None,
    # 这是第几次尝试（重试由调用方负责，见 `remote/offline_eval`）——只进告警行。
    attempt: int = 1,
    # 长驻 worker 池（云端离线评估传，见 `remote/offline_eval.run_cloud_eval`）：非空时**先试池**，
    # 池没能服务这一局就回退下面的一次性 `bun`（行为与池不存在时相同）。本机/控制台路径传 None，
    # 它们走节点集群/本机自己的派发（池是节点侧执行面的事）。
    pool: Any = None,
    # R2 事件 rung（plan/new-era-stop.plan.md §6）：True ⇒ 本局走事件门
    # （均匀 K ∪ threat-ONSET + Δt≥3）；缺省 False = 老行为。池路径与一次性路径
    # 共用同一份 cmd（上条注释），故两侧天然一致；远端 bundle 陈旧由既有 code_hash
    # 门排除（与 --policy 等既有透传同待遇，不另设门）。
    decision_events: bool = False,
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
        # 单一来源（`common/protocol.EVAL_SCRIPT`）：池按脚本名建，两侧写两份就等着谁先漂
        _EVAL_SCRIPT,
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
    # M1d 双侧同规：课程自定义关 stageJson + lives/level 覆盖（plan §6/§5.2）
    if stage_json:
        cmd += ["--stage-json", stage_json]
    if lives_override is not None:
        cmd += ["--lives-override", str(lives_override)]
    if player_level is not None:
        cmd += ["--player-level", str(player_level)]
    # T1.2：非 nn 策略透传（god 局权重文件不需要，export 侧忽略 --weights）。
    if policy and policy != "nn":
        cmd += ["--policy", policy]
    # R2 事件 rung：课程 decision_events=True ⇒ 评估与训练同语义（train/deploy 不一致
    # 是 plan 点名的静默分裂；缺席 = 老行为，export 侧缺省 false）。
    if decision_events:
        cmd += ["--decision-events"]
    if replay_dir:
        cmd += ["--replay", replay_dir]
    t0 = time.time()
    lab = game_watch.game_label(stage, seed)
    # 长驻池优先（§22.7）：与一次性同行径的 argv、同样的硬顶与告警口径；池没能服务（超时/
    # ERR/worker 死掉/没槽位…）就**当场回退**下面的 `run_eval_runner_capture` —— 只慢不错。
    pooled = None
    if pool is not None:
        # `cmd[1:]`（去掉 bun 本身）：池的任务口径与 iter 腿逐字相同 —— 送进去的 argv 都以
        # **导出器脚本**开头（`[script, …args]`），池内部再去掉脚本路径把 args 写进 stdin。
        pooled = pool.try_capture(
            cmd[1:],
            timeout_sec,
            label=lab,
            attempt=attempt,
            kind="eval",
            where=str(out_dir),
        )
    if pooled is not None:
        _sec, lines = pooled
        proc: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
            cmd, 0, "\n".join(lines), ""
        )
    else:
        proc = run_eval_runner_capture(
            cmd,
            timeout_sec,
            cwd=cwd,
            label=lab,
            log_fn=log_fn,
            kind="eval",
            attempt=attempt,
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


def eval_done_keys(
    eval_jsonl: Path,
    wver16: str,
    iter_filter: int | None = None,
    *,
    min_iter: int | None = None,
) -> set[tuple[int, int]]:
    """已评估账本：eval_log.jsonl 中同 wver 的 (stage,seed) 集（断点/重启不重评）。

    `iter_filter` 非 None 时只认该 iter 的逐局行——it0 基线（`BASELINE_EVAL_ITER`）
    用，并同时排除带 `source` 字段的行（B/C evalboard 行与 EvalDispatcher 行同册
    `event:"eval"`，若 B/C 批恰好评过 bc 权重，其行不得被当成基线已评估）。
    `min_iter` 非 None 时排除 int iter < min_iter 的行——A-eval 账本传 1：把 it0
    基线行挡在 A-eval 去重之外（账本互吞只防单方向 = 另一半漏洞）；缺 `iter`
    字段的旧行与同 wver 跨 iter 复用（零梯度轮 args.out 未变 → 下轮免重评）照旧保留。
    """
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
                if not isinstance(r, dict) or r.get("event") != "eval":
                    continue
                if r.get("wver") != wver16:
                    continue
                if iter_filter is not None:
                    if r.get("iter") != iter_filter:
                        continue
                    if "source" in r:
                        continue
                if min_iter is not None:
                    it_v = r.get("iter")
                    if isinstance(it_v, int) and it_v < min_iter:
                        continue
                try:
                    out.add((int(r["stage"]), int(r["seed"])))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    return out


def baseline_summary_landed(traj_dir: Path, wver16: str) -> bool:
    """it0 基线是否已落账：eval_log.jsonl 里存在**同权重指纹**的 `iter=0` summary 行。

    主循环据此决定是否（重）派基线——落账即停，未落账（进程首派、节点瞬时全挂
    被跳过、bc 换文件）每轮重试，账本去重让重试天然只补缺口。按 wver 匹配：bc
    换文件后旧基线不算数，控制台合成行取的也是最后一条 it0 summary。
    """
    eval_jsonl = traj_dir.parent / "eval_log.jsonl"
    try:
        if eval_jsonl.exists():
            for line in eval_jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(r, dict)
                    and r.get("event") == "eval_summary"
                    and r.get("iter") == BASELINE_EVAL_ITER
                    and r.get("wver") == wver16
                ):
                    return True
    except OSError:
        pass
    return False


