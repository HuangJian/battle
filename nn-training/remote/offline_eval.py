"""remote/offline_eval.py —— 云机上的 A 层评估（离线课的 `eval_on_cloud` 开关）。

**为什么要它**（用户 2026-09-22 指令：「battle.offline.ipynb 增加配置项支持是否在云机跑
eval」，档位选定「A 层同口径，每 `eval_every` 轮」）：离线整段跑完再把权重搬回本机评估，
意味着**整段期间没有任何可信读数**——一条跑偏的腿要等到几天后才被发现。让云机按同一份
A 层语料自己评，读数就能逐轮回到课程账本（板子/门判读到的与 in-loop 完全同一口径）。

**「同口径」是构造性的，不是承诺**：语料（`eval_stages` × `a_eval_seed_list(it, n)`，含双轨
锚点+轮转）、行 schema（`rl.eval_local.eval_row`）、summary 结算（`rl.eval_local.settle_eval_summary`）
三处都取自 in-loop 的同一份实现；`wver` 也同定义（权重文件字节的 sha256）——于是云机评的
W(it) 行与本地/节点评的同一 W(it) 行在账本里**可以配对**（同 wver 同 (stage,seed)）。

**与 in-loop 的唯一差别是算力位置**：in-loop 把 400 局派给节点集群 + 本机份额；这里全部
在本机（云机本身就是算力）。所以 `node` 字段写 `"cloud"`——它在 summary 的 `nodes` 分布里
一眼可见，不会与本地/节点局混淆。

**与下一轮 PPO 并行**（用户 2026-09-22 口径）：「云机跑 in-loop eval 时，要像 trainer 一样与
下一轮 PPO 并行执行，因为 eval 是跑在 CPU 上的，不应该阻塞 PPO 的执行。」⇒ 真正被
`remote/run_loop` 用的是 `CloudEvalRunner`（后台线程 + 段末有界收线），`run_cloud_eval` 是
它一次执行的主体（也是单测/手动调用的直接入口）。

**绝不拖垮训练**：与 in-loop 的派发器同一条纪律——任何失败只记日志、绝不抛出；评估跑不成
只是「少一份读数」，训练照常。逐局失败（导出器 rc≠0/超时）只放弃那一局。
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 单局看门狗口径：**一律通过模块属性读**（`game_watch.X`）——import 会把值抄成第二份绑定，
# 测试 patch 了 game_watch 那份、调用点还在读旧绑定就是静默的错口径。
from common import game_watch
from common.protocol import UnreapableChildError
from platform_utils import cpu_worker_slots, rmtree_best_effort
from remote import serve_pool
from rl.eval_local import (
    a_eval_seed_list,
    eval_done_keys,
    eval_row,
    run_local_eval_game,
    settle_eval_summary,
)
from rl.log import log as _rl_log

# 单局评估的硬顶：**与 rollout 共用一份口径**（`common/game_watch.py`）。
# 旧值 900s（= policy.taskTimeoutSec）：一个卡住的评估局会占着一个 slot 15 分钟，而本轮
# `drain` 的预算只有 `DRAIN_TIMEOUT_SEC`（600s）——读数是「整轮丢掉」而不是「少一局」。
# 单局正常是亚秒~几秒级（实测逐局 `wallSec`：p50 1.2~1.6s / p90 3.2~4.2s / p99 7~8s；
# 用户口径 2026-09-22：「单局 >5s 肯定不正常」）⇒ 首次尝试 5s 就算超时，原地重跑，重试上限 ×4。
# `game_timeout_sec` 给了正数就完全按它（且不对重试放大——配置说了算）。
#: 新评估轮「上一轮还在飞」时的交接等待（秒）：**有界**等（不是无限 join）。
EVAL_HANDOFF_WAIT_SEC = 120.0
#: 「机器级停滞」的轮内重投上限（实验/排障用）：`0`/未设 = **不限**；正整数 = 重投这么多次就
#: 放弃（把还没评的局记成本轮 `failed`，交回下一轮/下次导入重评）。
#:
#: 为什么缺省不限（与 rollout 腿同一口径，`remote/iter_rollout.ENV_ROUND_RETRY_MAX`）：这一档失败
#: 是**机器**的病（D 状态 / 挂住的挂载点），现场 890s 之后自己好了；外层没有别的重订机会
#: （`run_cloud_eval` 永不抛，云机自主段的 3 次重试只盖 rollout 腿），轮内重投就是全部。
#: 「不限」并不意味着等：每一次都真的在跑活（只补没评的局，已落账的局不重跑）。
ENV_EVAL_ROUND_RETRY_MAX = "NN_EVAL_ROUND_RETRY_MAX"
#: 段末收线的缺省时限（秒）：给还在飞的评估局一点时间落账；超时只记一笔（已落的行有效）。
DRAIN_TIMEOUT_SEC = 600.0
#: 云机局在账本里的 node 名（见模块注释：与 local/<节点 id> 分开，便于按腿归因）。
CLOUD_NODE = "cloud"


@dataclass(frozen=True)
class CloudEvalPlan:
    """云机 A 层评估的口径（语料/环境从课程读；`rollout_workers` 是本机执行面，非课程）。"""

    stages: tuple[int, ...]
    n_seeds: int
    eval_every: int
    difficulty: str
    max_ticks: int
    lives: int | None
    level: int | None
    #: 同时段在跑的 rollout 并行度（`plan.workers`；0 = 未知）。**只用于缺省并发**：
    #: 它不是课程属性（课程不知道包里的计划），所以由 `eval_plan_of` 留 0、调用方
    #: `dataclasses.replace` 补上（见 `remote/plan_run._setup_cloud_eval`）。
    rollout_workers: int = 0

    @property
    def enabled(self) -> bool:
        return self.n_seeds > 0 and len(self.stages) > 0

    def due(self, it: int) -> bool:
        """第 `it` 轮是否该评（每 `eval_every` 轮一次；it<1 恒不评——W(0) 是 it0 基线的事）。"""
        if not self.enabled or it < 1:
            return False
        return int(it) % max(1, int(self.eval_every)) == 0


def eval_plan_of(course: Any) -> CloudEvalPlan:
    """课程 → 云机评估口径（课程缺席/字段缺席一律回落「不评估」，绝不猜）。"""
    if course is None:
        return CloudEvalPlan((), 0, 1, "hard", 12000, None, None)
    spec = str(getattr(course, "eval_stages", "") or "")
    if spec:
        from rl.course import parse_range

        stages = tuple(int(s) for s in parse_range(spec))
    else:
        # 与 in-loop 同义：没给 --eval-stages 就评真实关 0..total_stages-1
        try:
            stages = tuple(int(s) for s in course.stage_ids)
        except Exception:
            stages = ()
    player = getattr(course, "player", None)
    return CloudEvalPlan(
        stages=stages,
        n_seeds=max(0, int(getattr(course, "eval_games_per_stage", 0) or 0)),
        eval_every=max(1, int(getattr(course, "eval_every", 1) or 1)),
        difficulty=str(getattr(course, "difficulty", "hard") or "hard"),
        max_ticks=int(getattr(course, "max_ticks", 12000) or 12000),
        lives=(getattr(player, "lives", None) if player is not None else None),
        level=(getattr(player, "level", None) if player is not None else None),
    )


def eval_pairs(plan: CloudEvalPlan, it: int) -> list[tuple[int, int]]:
    """第 `it` 轮的 A 层语料（与 in-loop 逐局同序：先关、后种子）。"""
    seeds = a_eval_seed_list(int(it), int(plan.n_seeds))
    return [(int(s), int(sd)) for s in plan.stages for sd in seeds]


def default_slots() -> int:
    """并发局数缺省：与 rollout **同一口径**（`platform_utils.cpu_worker_slots`）。

    `max(cores − 4, floor(cores × 0.8))`（至少 1）——**不为 rollout 预留**：两者在云机离线段里
    是交替的（rollout → PPO → eval），按对方扣一次等于两笔账扣同一份钱（用户 2026-09-22：
    「不应该为 eval 保留 CPU 核数，两者都使用 max(cores − 4, cores × 0.8)」）。留出的那几核是给
    补传/日志/守护线程的，不随谁在跑变化。

    ⚠ **那条前提必须先是真的**（2026-09-25 云机卡死）：旧版提交点恰好落在下一轮 rollout 的
    开跑瞬间 ⇒ 两条腿同时各开满一份（96 核配额上 220+220）⇒ 2× 超订 ⇒ 成批踩 5s 硬顶 ⇒
    池回退放大 ⇒ 整轮停摆。现在「交替」由代码保证（`run_loop._maybe_cloud_eval` 提交后有界
    等本轮评估收线）——同一份公式只在那个前提下成立，不再靠注释假设。

    ⚠ **核数也按物理数目**（同上）：缺省走 `effective_cores()`（容器配额/亲和掩码），所以
    96 核的 Kaggle 会话给 92，而不是把宿主机报的 224 核当配额算出 220。

    老口径（先扣 `plan.workers` 再卡 64）在 96 vCPU 的 Kaggle TPU 会话上给到 64，比本口径少三成。
    `--eval-slots` / `CFG.eval_slots` 给了正数就完全按它（不做任何夹取）。
    """
    return cpu_worker_slots()


def eval_round_retry_max() -> int:
    """轮内重投上限（`0` = 不限）；见 `ENV_EVAL_ROUND_RETRY_MAX`。非法值 → 回落不限
    （响亮的事交给日志，不在评估腿里抛）。

    ⚠ 与 rollout 腿同一个形状：读的是**跑评估这盘机器**自己的 env（不是 hub/导出机）。
    """
    raw = (os.environ.get(ENV_EVAL_ROUND_RETRY_MAX) or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return 0


def find_bun(explicit: str = "") -> str:
    """定位 bun（显式 → PATH）；找不到返回空串，调用方据此响亮跳过本轮评估。"""
    cand = str(explicit or "").strip() or shutil.which("bun") or ""
    if cand and Path(cand).exists():
        return cand
    home = Path(os.path.expanduser("~")) / ".bun" / "bin" / "bun"
    return str(home) if home.exists() else ""


class CloudEvalRunner:
    """云机 A 层评估的**后台执行者**：与下一轮 PPO 并行，段末有界收线。

    用户口径（2026-09-22）：「云机跑 in-loop eval 时，要像 trainer 一样与下一轮 PPO 并行
    执行，因为 eval 是跑在 CPU 上的，不应该阻塞 PPO 的执行。」

    与 in-loop（`EvalDispatcher` + `_sweep_eval_tail`）同一条纪律：

      ① **提交即返回**——`submit(it)` 只把这一轮的评估丢给后台线程，rollout/PPO 照常跑；
         eval 局是 bun 子进程（CPU），PPO 在 GPU 上，两者本来就不抢同一个资源；
         **但 rollout 局也在 CPU 上** ⇒ 调用方提交后有界等本轮收线（`run_loop`
         `EVAL_ALTERNATE_WAIT_SEC`）——否则两条腿同时各开满一份，2× 超订就把单局墙钟推过
         5s 硬顶（2026-09-25 云机卡死的入口条件）；那个等待是**有界**的，本类不负责它；
      ② **逐轮不站等**——下一轮到点只做一次观测；上一轮仍在飞时**有界**等一小会
         （`EVAL_HANDOFF_WAIT_SEC`），仍不空闲就跳过本轮（下一轮到点再说），绝不把
         PPO 按在等待地里；
      ③ **段末收线**——`drain()` 给在飞的局有界时间落账（`DRAIN_TIMEOUT_SEC`），
         超时只记一笔 WARN：已落盘的行有效，剩余局下次会话（或本机 evalA）可续评。

    并发上限是 `slots`（缺省 = `default_slots()`，与 rollout 同一口径）：**不为 rollout/PPO
    预留核数**——它们是交替跑的（用户 2026-09-22），留出的那几核只服务补传/日志/守护线程。
    """

    def __init__(
        self,
        build_job: Any,
        *,
        log: Any = None,
        on_round_done: Any = None,
        drain_timeout_sec: float = DRAIN_TIMEOUT_SEC,
        handoff_wait_sec: float = EVAL_HANDOFF_WAIT_SEC,
    ) -> None:
        self._build = build_job
        #: 每轮评估跑完后回调 `(it, result)`（在后台线程里调；**不得抛**——调用方自己兜）。
        #: run_loop 用它把「这一轮有读数了」告诉补传腿（重投该轮，见
        #: `OfflineDeliverer.submit_eval_round`）——否则逐局行要等到段末 artifacts zip 才回去。
        self._on_round_done = on_round_done
        self.log = log or _rl_log
        self.drain_timeout_sec = float(drain_timeout_sec)
        self.handoff_wait_sec = float(handoff_wait_sec)
        self._lock = threading.Lock()
        self._pending: tuple[int, dict] | None = None
        self._current: int | None = None
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._stopping = False
        self._thread: threading.Thread | None = None
        #: 已跑完的评估结果（`run_cloud_eval` 的返回 dict，按提交顺序）
        self.results: list[dict] = []

    # ---- 提交 / 观测 ----

    def inflight_it(self) -> int | None:
        """正在跑的评估轮号（None = 空闲）。**零成本观测**（不阻塞后台线程）。"""
        with self._lock:
            return self._current

    def pending_it(self) -> int | None:
        with self._lock:
            return self._pending[0] if self._pending is not None else None

    def wait_idle(self, timeout: float | None = None) -> bool:
        """等后台空闲（提交的与在飞的全跑完）。True = 已空闲。"""
        return bool(self._idle.wait(timeout))

    def submit(self, it: int) -> bool:
        """提交第 `it` 轮评估（**不阻塞**）。返回 False = 本轮没提交（已有评估在飞）。

        「上一轮还在飞」时的处理是**有界等一小会**而不是排队：排队会让 eval 落后于训练
        越来越远（每轮 100 局、每轮间隔可能只有几分钟），而尾部积压的读数对训练毫无用处；
        in-loop 的选择也是「到边界做一次观测，跑不完的让它自己跑完」。
        """
        if self._stopping:
            return False
        busy = self.inflight_it() is not None or self.pending_it() is not None
        if busy:
            self.log(
                f"[eval-cloud] it{it}: 上一轮评估（it{self.inflight_it() or self.pending_it()}）"
                f"还在飞 —— 有界等 {self.handoff_wait_sec:.0f}s 交接"
            )
            self.wait_idle(self.handoff_wait_sec)
        with self._lock:
            if self._stopping:
                return False
            if self._current is not None or self._pending is not None:
                self.log(
                    f"[eval-cloud] it{it}: 上一轮仍在跑 —— 跳过本轮评估"
                    "（不把 PPO 按在等待上；下次到点再说）"
                )
                return False
            self._pending = (int(it), dict(self._build(int(it))))
            self._idle.clear()
        self._ensure_thread()
        self._wake.set()
        return True

    def drain(self, timeout: float | None = None) -> bool:
        """段末收线：给在飞的评估**有界**时间跑完。True = 已完全空闲。

        段末**必须**调（每个出口都要）：不调的话在飞的局随进程一起没，summary 不会结算
        ——那就白评了（逐局行可能已落盘，但本轮读数在板子上仍缺席）。
        """
        limit = self.drain_timeout_sec if timeout is None else float(timeout)
        self._stopping = True
        self._wake.set()
        t = self._thread
        if t is None:
            return True
        t.join(limit)
        if t.is_alive():
            cur = self.inflight_it()
            self.log(
                f"WARN [eval-cloud] 段末收线超时（{limit:.0f}s）：评估轮 it{cur} 仍在飞"
                "——已落盘的逐局行有效，剩余局下次会话（或本机 evalA）可续评"
            )
            return False
        self._thread = None
        return True

    # ---- 后台线程 ----

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="cloud-eval-runner")
        self._thread.start()

    def _loop(self) -> None:
        """后台循环：**已提交的活一定跑完**，只有在「要停且无事可做」时才退出。

        「已提交的活一定跑完」是刻意的：`submit` 那一刻这一轮就承诺了（权重快照、语料已定），
        而 `drain` 的时机可能恰好落在提交与取活之间（毫秒级窗口）——那时丢掉它就等于
        「段末最后一轮白评」。要停的是**后续**的轮，不是已经答应的那一轮。
        """
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                job = self._pending
                self._pending = None
                if job is not None:
                    self._current = job[0]
            if job is not None:
                try:
                    result = run_cloud_eval(**job[1])
                    self.results.append(result)
                    if self._on_round_done is not None:
                        try:
                            self._on_round_done(int(job[0]), result)
                        except Exception as e:  # 回调不得杀死后台循环
                            self.log(
                                f"[eval-cloud] it{job[0]} 完成回调异常（忽略）：{type(e).__name__}: {e}"
                            )
                except Exception as e:  # run_cloud_eval 已自吞异常；这是最后一道
                    self.log(
                        f"[eval-cloud] it{job[0]} 后台执行异常（忽略）：{type(e).__name__}: {e}"
                    )
                finally:
                    with self._lock:
                        self._current = None
                        idle = self._pending is None
                    if idle:
                        self._idle.set()
                if idle and self._stopping:
                    # 干完了，而且已经叫停 ⇒ 退出（不回到 `_wake.wait()` 上永久阻塞：
                    # `drain` 的那次唤醒已被本次执行用掉，再等就等不到了）。
                    return
                continue
            self._idle.set()
            if self._stopping:
                return


def run_cloud_eval(
    *,
    plan: CloudEvalPlan,
    it: int,
    weights_path: Path,
    eval_jsonl: Path,
    ts_root: str | Path,
    work_dir: str | Path,
    course: Any = None,
    course_fp: str = "",
    bun: str = "",
    slots: int = 0,
    game_timeout_sec: float = 0.0,
    rollout_winrate: float | None = None,
    log: Any = None,
) -> dict:
    """在云机跑第 `it` 轮的 A 层评估：逐局落账 + `settle_eval_summary`。**永不抛**。

    返回 `{ran, games, settled, failed, skipped, wver, sec, servePool, roundRetries, error?}`
    （给调用方记日志用）。

    `servePool` 只是本轮的池诊断计数（served/spawned/killed/fallback/reasons；没建池 = None）——
    **不进 wire**：它不经任何账本/manifest，hub 侧不需要认识这个键。`roundRetries` 同理
    （机器级停滞导致的轮内重投次数，见下）。

    **机器级停滞 ⇒ 轮内重投**（2026-09-25 用户口径：失败就重试，不关云机、不让任务失败、
    不睡不空转）：单局若抛 `UnreapableChildError`（SIGKILL 之后收不了尸），它不是「这一局的内容
    错了」而是「这台机器卡住了」——本函数**在轮内重投**，而且只补**没评的**局（账本
    `eval_done_keys` 说了算：已落账的局一个字不重跑）。重投次数缺省不限（`NN_EVAL_ROUND_RETRY_MAX`
    是操作员的上限），每次尝试自己都有界，不占用任何外层重试预算。内容面失败（rc≠0 / 确定性
    报错）仍归 `failed`（响亮记一笔，读数少一局）——那是这一轮该留的出路。
    """
    log = log or _rl_log
    out: dict[str, Any] = {
        "ran": False,
        "games": 0,
        "settled": 0,
        "failed": 0,
        "skipped": 0,
        "wver": "",
        "sec": 0.0,
        #: 机器级停滞导致的轮内重投次数（0 = 没卡过；`out` 只进日志/诊断，不进 wire）。
        "roundRetries": 0,
    }
    t0 = time.time()
    # 池的所有权在本函数（创建 → 轮末关）；先声明成 None，好让 finally 对**任何**出口都成立
    # （函数里有多处 `return out`，finally 一样会跑）。
    pool = None
    try:
        if not plan.enabled:
            log(f"[eval-cloud] it{it}: 课程没配 eval 语料（eval_games_per_stage=0）——跳过")
            return out
        pairs = eval_pairs(plan, it)
        if not pairs:
            return out
        wpath = Path(weights_path)
        if not wpath.is_file():
            log(f"[eval-cloud] it{it}: 权重不在盘上（{wpath}）——跳过本轮评估")
            return out
        from remote.artifacts import sha256_file

        key16 = sha256_file(wpath)[:16]
        out["wver"] = key16
        bun_bin = find_bun(bun)
        if not bun_bin:
            log("[eval-cloud] 找不到 bun（rollout/eval 的 TS 运行时）——跳过本轮评估")
            return out
        ts = Path(ts_root) if str(ts_root or "") else None
        if ts is None or not ts.is_dir():
            log(f"[eval-cloud] TS 运行时树不在（{ts_root}）——跳过本轮评估（导出器跑不起来）")
            return out

        done = eval_done_keys(eval_jsonl, key16, min_iter=1)
        todo = [p for p in pairs if p not in done]
        out["games"] = len(pairs)
        out["skipped"] = len(pairs) - len(todo)
        if not todo:
            log(f"[eval-cloud] it{it}: wver={key16[:12]}… 已评过（{len(pairs)} 局）——跳过")
            return out

        # 调用方（`plan_run._setup_cloud_eval`）通常已经把缺省解析成具体数；直接调本函数时
        # 退回 `default_slots()`——同一口径，不依赖任何「对方在抢」的估算。
        n_slots = max(1, int(slots or 0) or default_slots())
        log(
            f"[eval-cloud] it{it}: 开评 {len(todo)}/{len(pairs)} 局"
            f"（语料 {len(plan.stages)} 关 × {plan.n_seeds} 种，wver={key16[:12]}…，并发 {n_slots}）"
        )
        # 长驻 worker 池（`docs/nn/runtime-opt.md` §22.7）：逐局 spawn 时每局重付一次 bun 启动 +
        # wasm 编译 + 首用 attestation×3 + 权重解析。收益 ∝ **每 lane 局数**（§22.2）：本地 16 核
        # （n_slots=12、100 局 ⇒ 8 局/lane）约 1.2–1.3×；96 vCPU 的 TPU 会话（92 slots、100 局
        # ⇒ ~1 局/lane）则基本持平（冷启动本来就能并发，无复用）。池**每轮一个**并在轮末关掉：
        # 不把常驻进程留到段末（空闲占内存），而冷启动一轮只付一次。
        # 池上限取 `min(slots, 本轮局数)`：slots 远大于局数时（例：96 vCPU 会话给 92 slots、
        # 本轮只有 8 局），按 slots 起池等于为 8 局预热 92 个进程 —— 纯浪费（冷启动排队）。
        pool = serve_pool.make_pool(
            bun_bin, serve_pool.EVAL_SCRIPT, ts, min(n_slots, len(todo)), log
        )
        if pool is not None:
            ready_n = pool.start()
            if ready_n:
                log(
                    f"[eval-cloud] it{it} 长驻 worker 池：{ready_n}/{min(n_slots, len(todo))} 就绪"
                    f"（{serve_pool.EVAL_SCRIPT}）——逐局进程启动/权重解析只付一次，"
                    "单局失败自动回退一次性 spawn"
                )
            else:
                log(f"[eval-cloud] it{it} 长驻 worker 池起不来 ⇒ 本轮全部走一次性 spawn")
                pool.close()
                pool = None
        lock = threading.Lock()
        jsonl_lock = threading.Lock()
        seen: set[tuple[int, int]] = set()
        wins = [0]
        cleared_total = [0]
        outcomes: dict[str, int] = {}
        node_games: dict[str, int] = {}
        failed: list[tuple[int, int, str]] = []
        #: 逐局（成功那次尝试的）墙钟 + 局身份：轮末打分布用（5s 这条线靠真数据校准）。
        game_walls: list[tuple[float, str]] = []
        retried_games: list[int] = []
        #: 本轮「机器级停滞」（子进程收不了尸 ⇒ `UnreapableChildError`）的局：轮循环只补它们
        #: （见 `run_one` 的那一档与下面的重投循环）。**每次尝试前清空**。
        machine_stuck: list[tuple[int, int]] = []
        # 首次尝试的硬顶：调用方显式给了正数就完全按它（每次尝试都用它），否则节点兜底 +
        # 重试放宽（`attempt_timeout_sec`）——理由与 rollout 逐字相同。
        explicit = float(game_timeout_sec or 0.0) > 0
        base_cap = float(game_timeout_sec) if explicit else game_watch.DEFAULT_GAME_TIMEOUT_SEC

        def run_one(task: tuple[int, int]) -> None:
            stage, seed = task
            game_dir = Path(work_dir) / f"eval-{int(it)}-s{stage}-d{seed}"
            # 单局**原地重试**（与 rollout 同一口径，`common/game_watch.py`）：单局的失败几乎
            # 总是环境性的（宿主机饥饿/慢局/mini-batch 里卡住），而这一局是确定性的（种子固定）
            # ⇒ 重跑同一命令要么拿到同一份结果，要么再次响亮失败。旧行为是一把不过就丢一局，
            # 于是读数里那些「永远失败」的局每轮都没人管。
            manifest: dict | None = None
            err_txt = ""
            lab = game_watch.game_label(stage, seed)
            wall = 0.0
            for attempt in range(1, game_watch.GAME_MAX_ATTEMPTS + 1):
                cap = game_watch.attempt_timeout_sec(base_cap, attempt, explicit=explicit)
                if attempt > 1:
                    shutil.rmtree(game_dir, ignore_errors=True)  # 上一把可能留半截 _eval_report
                    log(
                        f"[eval-cloud] it{it}: "
                        + game_watch.retry_line("eval", lab, attempt, err_txt, cap)
                    )
                t_game = time.time()
                try:
                    manifest = run_local_eval_game(
                        bun_bin,
                        str(wpath),
                        int(stage),
                        int(seed),
                        game_dir,
                        int(plan.max_ticks),
                        plan.difficulty,
                        float(cap),
                        key16,
                        stage_json=(course.stage_json(int(stage)) if course is not None else "") or "",
                        lives_override=plan.lives,
                        player_level=plan.level,
                        cwd=str(ts),
                        log_fn=log,
                        attempt=attempt,
                        pool=pool,
                    )
                    wall = time.time() - t_game
                    break
                except UnreapableChildError:
                    # ★ **机器级停滞**（子进程 SIGKILL 之后收不了尸：D 状态 / 挂住的挂载点）。
                    # 两条纪律（与 rollout 腿逐字同源，见 `remote/protocol.UnreapableChildError`）：
                    #   ① **不原地重跑这一局**：`game_dir` 上可能还有活写者 ⇒ 两个进程写同一份
                    #      `_eval_report.json`（半截/交错）⇒ 静默错读数。重投的粒度是**整轮**：
                    #      先把它的半截产出删干净，再与其它没产出的局一起投（见轮循环）；
                    #   ② **不记 `failed`**：那是机器的问题，不是这一局的内容（记成失败就把机器
                    #      的错记在内容头上，而这一轮永远缺一个读数）。
                    # 这个分支立即返回（`wall`/`err_txt` 都不必写：它们只服务 `failed` 那一笔）。
                    with lock:
                        machine_stuck.append((int(stage), int(seed)))
                    return
                except Exception as e:  # 单局失败只放弃这一局（下一轮/下次导入会重试）
                    manifest = None
                    wall = time.time() - t_game
                    err_txt = f"{type(e).__name__}: {e}"
            if manifest is None:
                with lock:
                    failed.append((int(stage), int(seed), err_txt))
                return
            try:
                row = eval_row(
                    manifest,
                    it=int(it),
                    key16=key16,
                    task=(int(stage), int(seed)),
                    node=CLOUD_NODE,
                    # 与 in-loop 腿同字段（`rl/eval_dispatch` 的 wallSec）⇒ 两腿逐字段可比。
                    wall_sec=round(wall, 3),
                )
                with jsonl_lock, open(eval_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                with lock:
                    seen.add((int(stage), int(seed)))
                    game_walls.append((round(wall, 3), lab))
                    if attempt > 1:
                        retried_games.append(attempt)
                    wins[0] += int(row["win"])
                    cleared_total[0] += int(row["cleared"])
                    oc = str(row.get("outcome") or "?")
                    outcomes[oc] = outcomes.get(oc, 0) + 1
                    node_games[CLOUD_NODE] = node_games.get(CLOUD_NODE, 0) + 1
            except Exception as e:  # 落到这一支 = 局跑完了但账落不下去（磁盘/账本格式）
                with lock:
                    failed.append((int(stage), int(seed), f"落账失败 {type(e).__name__}: {e}"))

        eval_jsonl.parent.mkdir(parents=True, exist_ok=True)
        # ---- 轮循环：机器级停滞 ⇒ 轮内重投，**只补没评的局**（2026-09-25 用户口径：失败就重试，
        # 不许关云机让任务失败，也不许空转烧配额）。
        #
        # 为什么重投必须在**轮内**而不是交回调用方：这一腿的调用方（`CloudEvalRunner` / 云机自主段）
        # 根本没有重订机会——`run_cloud_eval` 的纪律是「永不抛」（抛出去这一轮连 summary 都没有），
        # 而 rollout 腿那 3 次重试也不盖这条腿。留在这里的重投既不睡（每一趟都真的在跑活）、
        # 也不吃任何外层预算，而且**机器一好就接上**。
        #
        # 出路（不许无限重投的那一档）：**内容面**失败（rc≠0 / 导出器确定性报错 / 落账失败）
        # 仍归 `failed`（响亮记一笔，读数少一局）——那是这一轮该接受的结局，不是机器问题。
        retry_cap = eval_round_retry_max()
        round_retries = 0
        pending = list(todo)
        try:
            while True:
                machine_stuck.clear()
                if n_slots <= 1:
                    for task in pending:
                        run_one(task)
                else:
                    with ThreadPoolExecutor(
                        max_workers=n_slots, thread_name_prefix="cloud-eval"
                    ) as ex:
                        list(ex.map(run_one, pending))
                if not machine_stuck:
                    break
                # `round_retries` = **真的重投过几次**（计数在重投前自增），所以上限判在自增之前。
                if retry_cap and round_retries >= retry_cap:
                    # 只有操作员显式设了上限才走这里（缺省不限）。**不上抛**：把还没评的局记成本轮
                    # `failed` 并照常收尾——评估腿抛出去等于整轮作废（连 summary 都没了）。
                    with lock:
                        failed.extend(
                            (s, d, f"机器级停滞：轮内重投已达操作员上限（{retry_cap} 次）")
                            for s, d in machine_stuck
                        )
                    log(
                        f"WARN [eval-cloud] it{it} 机器级停滞：轮内已重投 {round_retries} 次仍有"
                        f" {len(machine_stuck)} 局收不了尸（{ENV_EVAL_ROUND_RETRY_MAX}={retry_cap} 是"
                        "操作员设的上限）——这些局记成本轮失败，训练继续"
                    )
                    break
                round_retries += 1
                # 只补**没评的**局：盘上账本（`eval_done_keys`）说了算，已落的行不重评
                # （不许空转）；已在 `failed` 里的内容面失败也不重投（见上面的出路）。
                done = eval_done_keys(eval_jsonl, key16, min_iter=1)
                failed_keys = {(s, d) for s, d, _ in failed}
                pending = sorted(p for p in pairs if p not in done and p not in failed_keys)
                if not pending:
                    break
                log(
                    f"WARN [eval-cloud] it{it} 整轮重投第 {round_retries} 次：本次尝试有"
                    f" {len(machine_stuck)} 局收不了尸（机器级停滞，已结算 {len(seen)}/{len(pairs)} 局）"
                    f"——只补这 {len(pending)} 局，先清掉它们的半截产出（旧写者可能还在）；"
                    "不报失败、不睡、不占用外层重试预算、云机不停"
                    + ("" if not retry_cap else f"（上限 {retry_cap} 次）")
                )
                for stage, seed in pending:
                    rmtree_best_effort(
                        Path(work_dir) / f"eval-{int(it)}-s{stage}-d{seed}", ignore_errors=True
                    )
        finally:
            if pool is not None:
                log(f"[eval-cloud] it{it} " + pool.summary())
                pool.close()

        # 单局耗时分布（每轮都打）：<5s 这条线（以及重试次数）靠真数据校准，不靠猜。
        log(
            f"[eval-cloud] it{it} "
            + game_watch.game_time_summary("eval", game_walls, retried=len(retried_games))
        )
        out["servePool"] = (
            None
            if pool is None
            else {
                "served": pool.served,
                "spawned": pool.spawned,
                "killed": pool.killed,
                "fallback": pool.fallback,
                "reasons": dict(pool.fallback_reasons),
            }
        )
        out["settled"] = len(seen)
        out["failed"] = len(failed)
        out["roundRetries"] = round_retries
        for stage, seed, err in failed[:5]:
            log(f"[eval-cloud] it{it}: 局 ({stage},{seed}) 失败：{err}")
        if len(failed) > 5:
            log(f"[eval-cloud] it{it}: 另有 {len(failed) - 5} 局失败（同上口径）")
        # summary 走 in-loop 的同一份结算（含双轨/技能子指标/掉落口径）
        settle_eval_summary(
            eval_jsonl,
            key16,
            int(it),
            pairs,
            len(pairs),
            seen,
            wins,
            cleared_total,
            outcomes,
            node_games,
            jsonl_lock,
            t0,
            rollout_winrate,
            course_fp=course_fp or "",
        )
        out["ran"] = True
    except Exception as e:  # 评估是旁路：任何意外都只是「这一轮没有读数」
        log(f"[eval-cloud] it{it} 评估异常（已忽略，训练继续）：{type(e).__name__}: {e}")
        out["error"] = f"{type(e).__name__}: {e}"
    finally:
        if pool is not None:
            pool.close()  # 幂等：内层 finally 已关过就是空操作（防「创建后、执行前」抛出的漏）
        out["sec"] = round(time.time() - t0, 2)
    if out["ran"]:
        log(
            f"[eval-cloud] it{it} DONE：{out['settled']}/{out['games']} 局落账"
            f"（失败 {out['failed']}，{out['sec']}s，wver={out['wver'][:12]}…"
            # 正常轮恒空（机器卡过才留痕，与 rollout 腿的「整轮重投」同一个口径）
            + (f"，整轮重投 {out['roundRetries']} 次" if out.get("roundRetries") else "")
            + "）"
        )
    return out
