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

from platform_utils import cpu_worker_slots
from rl.eval_local import (
    a_eval_seed_list,
    eval_done_keys,
    eval_row,
    run_local_eval_game,
    settle_eval_summary,
)
from rl.log import log as _rl_log

#: 单局评估的超时（秒）——与 policy.taskTimeoutSec 的缺省一致（900）。
DEFAULT_GAME_TIMEOUT_SEC = 900.0
#: 新评估轮「上一轮还在飞」时的交接等待（秒）：**有界**等（不是无限 join）。
EVAL_HANDOFF_WAIT_SEC = 120.0
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
    #: `dataclasses.replace` 补上（见 `remote/run_loop._setup_cloud_eval`）。
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

    老口径（先扣 `plan.workers` 再卡 64）在 96 vCPU 的 Kaggle TPU 会话上给到 64，比本口径少三成。
    `--eval-slots` / `CFG.eval_slots` 给了正数就完全按它（不做任何夹取）。
    """
    return cpu_worker_slots()


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
    game_timeout_sec: float = DEFAULT_GAME_TIMEOUT_SEC,
    rollout_winrate: float | None = None,
    log: Any = None,
) -> dict:
    """在云机跑第 `it` 轮的 A 层评估：逐局落账 + `settle_eval_summary`。**永不抛**。

    返回 `{ran, games, settled, failed, skipped, wver, sec, error?}`（给调用方记日志/回传用）。
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
    }
    t0 = time.time()
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

        # 调用方（`run_loop._setup_cloud_eval`）通常已经把缺省解析成具体数；直接调本函数时
        # 退回 `default_slots()`——同一口径，不依赖任何「对方在抢」的估算。
        n_slots = max(1, int(slots or 0) or default_slots())
        log(
            f"[eval-cloud] it{it}: 开评 {len(todo)}/{len(pairs)} 局"
            f"（语料 {len(plan.stages)} 关 × {plan.n_seeds} 种，wver={key16[:12]}…，并发 {n_slots}）"
        )
        lock = threading.Lock()
        jsonl_lock = threading.Lock()
        seen: set[tuple[int, int]] = set()
        wins = [0]
        cleared_total = [0]
        outcomes: dict[str, int] = {}
        node_games: dict[str, int] = {}
        failed: list[tuple[int, int, str]] = []

        def run_one(task: tuple[int, int]) -> None:
            stage, seed = task
            game_dir = Path(work_dir) / f"eval-{int(it)}-s{stage}-d{seed}"
            try:
                manifest = run_local_eval_game(
                    bun_bin,
                    str(wpath),
                    int(stage),
                    int(seed),
                    game_dir,
                    int(plan.max_ticks),
                    plan.difficulty,
                    float(game_timeout_sec),
                    key16,
                    stage_json=(course.stage_json(int(stage)) if course is not None else "") or "",
                    lives_override=plan.lives,
                    player_level=plan.level,
                    cwd=str(ts),
                )
                row = eval_row(
                    manifest, it=int(it), key16=key16, task=(int(stage), int(seed)), node=CLOUD_NODE
                )
                with jsonl_lock, open(eval_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                with lock:
                    seen.add((int(stage), int(seed)))
                    wins[0] += int(row["win"])
                    cleared_total[0] += int(row["cleared"])
                    oc = str(row.get("outcome") or "?")
                    outcomes[oc] = outcomes.get(oc, 0) + 1
                    node_games[CLOUD_NODE] = node_games.get(CLOUD_NODE, 0) + 1
            except Exception as e:  # 单局失败只放弃这一局（下一轮/下次导入会重试）
                with lock:
                    failed.append((int(stage), int(seed), f"{type(e).__name__}: {e}"))

        eval_jsonl.parent.mkdir(parents=True, exist_ok=True)
        if n_slots <= 1:
            for task in todo:
                run_one(task)
        else:
            with ThreadPoolExecutor(max_workers=n_slots, thread_name_prefix="cloud-eval") as ex:
                list(ex.map(run_one, todo))

        out["settled"] = len(seen)
        out["failed"] = len(failed)
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
        out["sec"] = round(time.time() - t0, 2)
    if out["ran"]:
        log(
            f"[eval-cloud] it{it} DONE：{out['settled']}/{out['games']} 局落账"
            f"（失败 {out['failed']}，{out['sec']}s，wver={out['wver'][:12]}…）"
        )
    return out
