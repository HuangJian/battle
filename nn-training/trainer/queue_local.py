"""queue_local —— 纯本地 rollout（单机线程池路径）。

从 trainer/dispatch_queue.py 拆出（2026-09-02）：run_rollout 只依赖本机并发（ThreadPoolExecutor
spawn bun 子进程），无分布式状态——独立成模块便于单测与职责分离。

**长驻池（2026-09-25）**：本机腿原先**每局** `Popen` 一个 bun——每局重付进程启动 + 模块
加载 + wasm 编译 + 权重解析（节点侧同病实测 1.59×，见 `docs/nn/runtime-opt.md` §20/§21/§22）。
现在两条本机腿（`run_rollout` / dispatcher 的本机槽）都先走 `worker/serve_pool.py` 的
`--serve` 长驻池，拿不准就回退原来的一次性 spawn（**只慢不错**，回退路径逐字节旧行为）。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import common.distribution
from common import game_watch  # 进度行节流口径与节点侧 rollout 共用一份（`progress_due`）
from common.log import log
from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from remote import weights_standard

# 进度行节流口径与节点侧 rollout 共用一份（`progress_due`，见 `common.game_watch`）；
# serve_pool = 节点侧同一份长驻池（`worker/serve_pool.py`）。
from worker import node_identity, serve_pool
from worker.cmd import build_rollout_cmd, corpus_fp_for_args, course_fp_for_args
from worker.reports import combine_reports
from worker.sibling_reuse import reuse_rollout, rollout_sources

REPO_ROOT = (
    Path(__file__).resolve().parents[2]
)  # 仓库根 battle2（trainer/ 上溯 3 层，修正 2026-09-02）

#: 本机腿交给长驻池的单局硬顶（秒）——**故意远大于任何合法长局**。
#:
#: 本机口径一直是「不限」（`game_watch` 对 `DEFAULT_GAME_TIMEOUT_SEC` 的注释：本机卡住的是
#: 自己的终端，云机上才等于「一个卡住的 bun 永远等下去」），这个数只给池一个「死 worker
#: 最终能被收掉」的兜底；池没服务成照样回退一次性 spawn，而那条路仍然不限。取 30min：
#: 8 并发实测单局 p99 16.8s（`docs/nn/runtime-opt.md` §8），超订下再慢两个数量级也到不了。
LOCAL_GAME_TIMEOUT_SEC = 1800.0


def split_local_head(
    tasks: list[tuple[int, int]], local_slots: int
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """B3 轮转取单：把本机保留段与本机任务交替发牌。

    旧形态把洗牌后的**前 k 个**任务整段留给本机（k = local_slots）⇒ 本机零延迟
    恒占队首。现在从远端先发一张，再本机/远端轮流；本机份额仍恰好 k，且远端
    任务保持原相对顺序。k = 0 / k >= n 退化为「全远端」/「全本机」。
    """
    k = min(max(0, int(local_slots)), len(tasks))
    if k == 0:
        return [], list(tasks)
    if k == len(tasks):
        return list(tasks), []
    remote_quota = len(tasks) - k
    head: list[tuple[int, int]] = []
    rest: list[tuple[int, int]] = []
    local_turn = False  # 远端先拿队首；本机槽不因零网络延迟固定抢第一张
    for task in tasks:
        take_local = (local_turn and len(head) < k) or len(rest) >= remote_quota
        if take_local:
            head.append(task)
            local_turn = False
        else:
            rest.append(task)
            local_turn = True
    return head, rest


class QueueEdgeGate:
    """派发队列「清空 / 重新可派」边沿 → 回调（在 dispatch 锁外执行）。

    stream 用它把 eval 闸做成队列状态驱动：全部任务已派出 ⇒ 解冻本课 eval；
    超时回队且仍有剩活 ⇒ 重新冻结。状态与一次性回调都在这里，dispatch 只喂两个布尔。
    """

    def __init__(
        self,
        on_drained: Callable[[], object] | None,
        on_active: Callable[[], object] | None,
        log_fn: Callable[[str], None],
    ) -> None:
        self._on_drained = on_drained
        self._on_active = on_active
        self._log = log_fn
        self._drained = False

    def seen(self, *, drained: bool, queue_open: bool) -> None:
        if drained:
            self._drained = True
            self._fire(self._on_drained, "on_queue_drained")
        elif self._drained and queue_open:
            self._drained = False
            self._fire(self._on_active, "on_queue_active")

    def _fire(self, cb: Callable[[], object] | None, name: str) -> None:
        if cb is None:
            return
        try:
            cb()
        except Exception as cb_err:
            self._log(f"[dist] {name} error: {str(cb_err)[:120]}")


def waste_block(stats: dict[str, int], local_idle_sec: float) -> dict[str, float | int]:
    """`dist.waste` 的单一组装点（只观测，不并入 dupRate）。"""
    return {
        "alloc_timeout_requeued": stats["alloc_timeout_requeued"],
        "late_original_settled": stats["late_original_settled"],
        "local_idle_thread_sec": round(float(local_idle_sec), 2),
    }


class LocalLane:
    """本机 worker 的孵化与远端失联补孵（B3）。

    初始只孵 `local_slots` 条；远端连续 `remote_dead_sec` 无结算时补到
    `cap_full`，恢复后让多孵的槽位自行退场。线程体仍由 dispatcher 的
    `worker` 闭包提供，本类只持有槽位账。
    """

    def __init__(
        self,
        worker: Callable[[dict | None, int | None], None],
        *,
        local_slots: int,
        cap_full: int,
        local_cap: list[int],
        last_remote_ok: list[float],
        remote_dead_sec: float,
        all_settled: threading.Event,
        halt_event: threading.Event | None,
        deadline: float,
        lock: Any,
        threads: list[threading.Thread],
    ) -> None:
        self.worker = worker
        self.local_slots = local_slots
        self.cap_full = cap_full
        self.local_cap = local_cap
        self.last_remote_ok = last_remote_ok
        self.remote_dead_sec = remote_dead_sec
        self.all_settled = all_settled
        self.halt_event = halt_event
        self.deadline = deadline
        self.lock = lock
        self.threads = threads
        self._slots: list[tuple[int, threading.Thread]] = []
        #: 本机槽「有容量却无活」的累计线程秒（P2-2 观测；不进 dupRate）。
        self.idle_sec = 0.0

    def note_idle(self, local: bool, active: bool, suspended: bool, t0: float) -> None:
        if local and active and not suspended:
            self.idle_sec += time.time() - t0

    def spawn(self, n: int) -> None:
        for _ in range(max(0, n)):
            slot = len(self._slots)
            t = threading.Thread(
                target=self.worker, args=(None, slot), daemon=True, name="rollout-local"
            )
            t.start()
            self.threads.append(t)
            self._slots.append((slot, t))

    def slot_allowed(self, slot: int) -> bool:
        """失联补孵的槽位在远端恢复后返回 False（调用方持锁）。"""
        return (
            slot < self.local_cap[0]
            or time.time() - self.last_remote_ok[0] > self.remote_dead_sec
        )

    def watch(self) -> None:
        while (
            not self.all_settled.is_set()
            and not (self.halt_event is not None and self.halt_event.is_set())
            and time.time() < self.deadline
        ):
            with self.lock:
                remote_alive = time.time() - self.last_remote_ok[0] <= self.remote_dead_sec
                want = self.local_slots if remote_alive else self.cap_full
                if self.local_cap[0] != want:
                    prev = self.local_cap[0]
                    self.local_cap[0] = want
                    log(f"[dist] local slots {prev} -> {want}")
                self._slots[:] = [(s, t) for s, t in self._slots if t.is_alive()]
                add = max(0, want - len(self._slots))
            if add:
                self.spawn(add)
            self.all_settled.wait(0.1)

    def start(self) -> None:
        self.spawn(self.local_slots)
        if self.local_slots < self.cap_full:
            t = threading.Thread(
                target=self.watch, daemon=True, name="rollout-local-watch"
            )
            t.start()
            self.threads.append(t)


def make_local_pool(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    pair: tuple[int, int] | None,
    args,
    wver: str,
    workers: int,
) -> serve_pool.ServePool | None:
    """按**真 argv** 决定这一轮要不要建长驻池（建不了 ⇒ None，整轮退回逐局 spawn）。

    为什么拿真 argv 探、而不是再写一遍「哪个模式用哪个导出器」：`build_rollout_cmd` 是脚本
    选择的**唯一来源**，抄一份就等着谁先漂。脚本不在 `worker/serve_pool` 的准入名单里
    （goal / intent 两种 RL 模式）⇒ `make_pool` 返回 None，本轮与本改动前逐字节相同。

    池的**生命周期归调用方**（用 try/finally `close()`）：`run_rollout` = 一轮采集；dispatcher
    = 一次 run（一个权重版本）。
    """
    if pair is None or workers <= 0:
        return None
    cmd = build_rollout_cmd(
        bun,
        args,
        weights=rl_path,
        out_dir=str(traj_dir / "w0"),  # 只为取脚本名，不落盘
        stage=int(pair[0]),
        seed=int(pair[1]),
        wver=wver,
        node_label="local",
    )
    if len(cmd) < 2:
        return None
    # 池是同质入口（`tools/sim/serve-any.ts`）——本轮跑哪个导出器由**每行的 mode token** 带过去，
    # `cmd[1]` 只用作准入门槛（名单外 ⇒ None ⇒ 整轮逐局 spawn，与加池前逐字节相同）。
    pool = serve_pool.make_pool(bun, REPO_ROOT, workers, log, for_script=cmd[1])
    if pool is None:
        return None
    ready = pool.start()
    if ready <= 0:
        pool.close()  # 起不来 = 本轮退回一次性路径（与没有池逐字节相同）
        return None
    log(
        f"[rollout] 长驻 worker 池：{ready}/{workers} 就绪（同质入口"
        f" {serve_pool.SERVE_ANY_SCRIPT}，mode={serve_pool.serve_mode_for(cmd[1])}）"
        "——逐局进程启动 / wasm 编译 / 权重解析只付一次；单局跑挂就地回退一次性 spawn（只慢不错）"
    )
    return pool


# 进度行节流：旧口径是「每 N 局一句」（N=10）——在高并发轮上等于每秒数行，云端离线课的
# 日志就是被它刷屏的（用户口径 2026-09-23：每分钟一句就够）。现在按**时间**节流，口径常量
# 在 `common/game_watch.PROGRESS_LOG_SEC`（与节点侧 `iter_rollout` 同一份，改一处两边同步）。


# ---------------------------------------------------------------------------
# 同族课程的局直取（plan/episode-reuse.plan.md；worker/sibling_reuse.py）：本机两条腿
# （`run_rollout.run_one` 与 dispatcher 的 `run_local_rollout`）共用同一份函数 —— 一处语义。
#
# 不建缓存（用户 2026-10-22 口径：缓存会让内存/硬盘爆炸）：源 = 兄弟课程**工作目录里已经
# 存在的 shard**（`episode_identity_fp` 相同 = level/关卡/难度/max_ticks/决策粒度全同；
# `wver` 逐字节同才算同一局）。找到就拷进本课波次目录并 D14 改戳，找不到就自采。
#
# 扫描索引**只活一轮**（`wver` 变即重建）：它是「盘上现在有什么」的目录快照，不是产物缓存
# ——不落盘、不跨轮、上限 `MAX_INDEX`；同型先例 = `worker/resume._SCAN_CACHE`。
_REUSE_INDEX: dict[tuple[int, int], tuple[Path, Path, str]] = {}
_REUSE_SIG: list[tuple[str, str]] = [("", "")]


def reuse_index(wver: str, args) -> dict[tuple[int, int], tuple[Path, Path, str]]:
    """本轮（同课程同 `wver`）的兄弟课现成局索引；换权重/换课就重建。"""
    sig = (str(getattr(args, "course_name", "") or ""), wver)
    if _REUSE_SIG[0] != sig:
        _REUSE_INDEX.clear()
        _REUSE_SIG[0] = sig
        _REUSE_INDEX.update(rollout_sources(None, wver, args))
    return _REUSE_INDEX


def _reuse_stamps(args) -> tuple[str, str]:
    """本课的 D14 血缘（改戳用）：(course_fp, corpus_fp)。"""
    return course_fp_for_args(args), corpus_fp_for_args(args)


def run_rollout(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]], args) -> dict:
    """Run one rollout generation into traj_dir with up to W concurrent games over
    the given (stage, seed) game pairs.

    Each game writes shards under traj_dir/w{i}/ — disjoint by construction, and
    discover_rl_shards() scans recursively, so the PPO side needs no knowledge of
    the layout. Per-game granularity saturates all cores regardless of how few
    stages/seeds the sweep has. 2026-09-25 起每局默认交给**长驻** bun（`--serve` 池，
    `make_local_pool`）：进程启动 / wasm 编译 / 权重解析只付一次；池拿不准（没就绪 /
    跑挂 / 超时）就地回退逐局 `Popen` —— 回退路径与本参数不存在时逐字节相同。
    Returns the aggregated report dict.
    """

    # 吞吐 T4：双缓冲复用前提——本地 shard 也必须写 wver（与 run_rollout_queue 的
    # local slot 对齐），否则主进程下一轮 completed_pairs 不命中、预采产物作废。
    # ★ 2026-10-15：本机 rollout 子进程（含长驻池）直接 `--weights` 读这个文件，
    #   delta 容器会让它 `JSON.parse` 报 BWD1；指纹也必须算在整份上。
    weights_standard.ensure_standard_weights(rl_path, log=log)
    wver = common.distribution.weights_fingerprint(rl_path)
    workers = max(1, min(args.workers, len(pairs)))
    # 兄弟课的现成局（本轮索引）：命中就不 spawn；找不到就自采。
    sources = {t: s for t, s in reuse_index(wver, args).items() if t in set(pairs)}
    reuse_n = [0]
    reuse_lock = threading.Lock()
    if sources:
        log(
            f"[reuse] rollout: {len(sources)}/{len(pairs)} 局在同族课程的工作目录里现成"
            "（同权重 + 同 (stage,seed) ⇒ 不重算；shard 拷入本课 traj）"
        )
    pool = make_local_pool(bun, rl_path, traj_dir, pairs[0] if pairs else None, args, wver, workers)

    def run_one(idx: int, si: int, seed: int) -> tuple[int, dict | None]:
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        log_path = wdir / "rollout.log"
        cmd = build_rollout_cmd(
            bun,
            args,
            weights=rl_path,
            out_dir=str(wdir),
            stage=si,
            seed=seed,
            wver=wver,
            node_label="local",
        )
        # 同族课程的现成局：命中就不 spawn（连子进程启动都不付）。
        src = sources.get((si, seed))
        if src is not None:
            cfp, cfp2 = _reuse_stamps(args)
            reused = reuse_rollout(src, wdir, stage=si, seed=seed, course_fp=cfp, corpus_fp=cfp2)
            if reused is not None:
                with reuse_lock:
                    reuse_n[0] += 1
                return 0, reused
        pooled = None
        if pool is not None:
            # `cmd[1:]` = 池的任务口径（`[脚本, …args]`，不含 bun 本身；池内部再去掉脚本路径）。
            pooled = pool.try_pool(
                cmd[1:],
                log_path,
                LOCAL_GAME_TIMEOUT_SEC,
                label=game_watch.game_label(si, seed),
                kind="rollout",
            )
        if pooled is None:
            with open(log_path, "w", encoding="utf-8") as log_f:
                p = subprocess.Popen(
                    cmd,
                    cwd=str(REPO_ROOT),
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    **_POPEN_NO_WINDOW,
                )
                rc = p.wait()
        else:
            rc = 0  # 池已把该局 stdout 落回 log_path；rc≠0 的局池不会当成功
        report = None
        if rc == 0:
            report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        return rc, report

    try:
        t0 = time.time()
        last_log_at = t0
        results: list[tuple[int, dict | None]] = [(1, None)] * len(pairs)
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(run_one, i, si, sd): i for i, (si, sd) in enumerate(pairs)}
            for done_n, fut in enumerate(as_completed(futures), 1):
                results[futures[fut]] = fut.result()
                now = time.time()
                if game_watch.progress_due(done_n, len(pairs), now, last_log_at):
                    last_log_at = now
                    log(f"[rollout] local {done_n}/{len(pairs)} games settled ({now - t0:.0f}s)")

        failed = [i for i, (rc, _r) in enumerate(results) if rc != 0]
        if failed:
            tail = (traj_dir / f"w{failed[0]}" / "rollout.log").read_text(encoding="utf-8")[-2000:]
            raise SystemExit(f"[run_rl] rollout worker(s) {failed} failed:\n{tail}")

        if reuse_n[0]:
            log(
                f"[reuse] rollout: 复用 {reuse_n[0]}/{len(pairs)} 局"
                "（同权重 + 同 (stage,seed) ⇒ 未重算；shard 已拷入本课 traj）"
            )

        reports = [r for _rc, r in results if r is not None]
        return combine_reports(reports)
    finally:
        if pool is not None:
            log(f"[rollout] {pool.summary()}")
            pool.close()


def run_local_rollout(
    bun: str,
    rl_path: str,
    traj_dir: Path,
    idx: int,
    task: tuple[int, int],
    args,
    wver: str,
    pool: serve_pool.ServePool | None = None,
) -> dict:
    """dispatcher 本机槽的单局（`pool` 非空时走长驻池，拿不准就地回退一次性 spawn）。

    兄弟课现成局走本模块的**本轮索引**（`reuse_index`，同 `wver` 只建一次）：命中就不 spawn。
    """
    si, sd = task
    wdir = traj_dir / f"w{idx}"
    wdir.mkdir(parents=True, exist_ok=True)
    cmd = build_rollout_cmd(
        bun,
        args,
        weights=rl_path,
        out_dir=str(wdir),
        stage=si,
        seed=sd,
        wver=wver,
        node_label="local",
    )
    log_path = wdir / "rollout.log"
    # 兄弟课的现成局（同 run_rollout.run_one 同一份函数）：命中即返回其报告 + 拷入的 shard，
    # 不 spawn、不占机器时长（`elapsedSec` 只算拷贝，源局耗时另列）。
    src = reuse_index(wver, args).get(task)
    if src is not None:
        cfp, cfp2 = _reuse_stamps(args)
        reused = reuse_rollout(src, wdir, stage=si, seed=sd, course_fp=cfp, corpus_fp=cfp2)
        if reused is not None:
            reused["_dir"] = str(wdir)
            return reused
    # 整局墙钟计时，与远端 agent 写入 manifest 的 elapsedSec 同口径——
    # 此前 local 局无耗时数据，巡检「采样机健康」的局均耗时列对 local 恒为 '—'。
    t0 = time.time()
    pooled = None
    if pool is not None:
        pooled = pool.try_pool(
            cmd[1:],
            log_path,
            LOCAL_GAME_TIMEOUT_SEC,
            label=game_watch.game_label(si, sd),
            kind="rollout",
        )
    if pooled is None:
        with open(log_path, "w", encoding="utf-8") as log_f:
            p = subprocess.Popen(
                cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT, **_POPEN_NO_WINDOW
            )
            rc = p.wait()
    else:
        rc = 0
    elapsed_sec = round(time.time() - t0, 3)
    if rc != 0:
        raise RuntimeError(f"local rollout rc={rc} (see {wdir}/rollout.log)")
    report: dict[str, Any] = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
    if report.get("wver") != wver:
        raise RuntimeError("local report wver mismatch")
    report["elapsedSec"] = elapsed_sec
    report["_dir"] = str(wdir)
    return report


def rescan_nodes(
    cfg,
    code_hash,
    upgrade_branch,
    dirty_files,
    local_bun,
    spawned_ids,
    alive,
    lock,
    weights_bytes,
    iter_id,
    wver,
    task_timeout,
    status_timeout,
    all_settled,
    deadline,
    rescan_sec,
    worker,
    extra_threads,
    halt_event=None,
    *,
    first_probe_sec: float = 5.0,
    wkind: str = "rollout",
    streaks=None,
    soft_streaks=None,
    fail_streak_max: int = 3,
    soft_streak_max: int = 9,
    rearm_cap: int = 3,
    rearm_warned=None,
) -> None:
    """v3.9 动态节点发现线程主体（dispatch.run() spawn，19 参逐位对应）。

    v3.14b：追加 halt_event —— KL 熔断后 rescan 线程必须立即退出，否则主线程
    join(timeout=max(30, window+taskTimeout)) 会白等满超时（实测 180s/次，
    集成 I2/I4 因此假死）。0 参代价：旧调用方不传 = 永不因 halt 退出（兼容）。

    2026-09-02 OO 拆分回归修复：旧签名带 log/common.distribution/node_ping/
    request_upgrade_guarded/is_self_node/mm 六个参数——log/common.distribution 本模块
    已顶层导入（拆出后不再需要注入），node_ping/request_upgrade_guarded/
    is_self_node 经 common.distribution.xxx 调用（裸参数从未使用），mm 就地内联。
    修复前 dispatch 的 18 个实参会整体错位 → 线程启动即抛
    `missing 6 required positional arguments`（rollout-rescan 线程报废，
    运行中上线的节点永远不被发现）。

    A4（2026-09-19 审计实测）：旧实现 `all_settled.wait(rescan_sec)` 之后紧跟
    「已结算即 return」——而 volume 短波形态下 **234 轮全部 <120s**（p50 7s，max 115s），
    于是本线程每轮都在首个 sleep 里被结算事件唤醒并退出：2.5h 里 **0 次扫描 pass**、
    130+ 次节点排除无一次回场。现在：
      · 首个 pass 提前到 `first_probe_sec`（缺省 5s），之后每 `rescan_sec`；
      · 候选 = 尚未孵化 ∪ **已停派**（真故障熔断 / 连续瞬断软停）的节点 —— 后者原先
        因 `spawned_ids` 命中被永久跳过，熔断即整轮出局；
      · 回场强制重握手（清缓存 + 重发权重，post_weights 自带 cached 探针）并**重置
        失败计数**、补孵 c_n 个采样线程；每节点有 `rearm_cap` 上界（缺省 3），
        用尽后不再补孵并告警一次；
      · ping 并行（与 B/C 层同款）；补 `kind=wkind` —— 旧实现漏传 kind，goal/intent
        腿的中途上线节点会把权重发进 rollout 桶，任务全 409；
      · `nd` 带 `ping`（旧实现漏带 ⇒ stageJson 任务在回场节点上被能力握手拒掉）。
    """
    configured = [n for n in cfg.get("nodes", []) if n.get("enabled", True)]
    streaks = {} if streaks is None else streaks
    soft_streaks = {} if soft_streaks is None else soft_streaks
    rearm_warned = set() if rearm_warned is None else rearm_warned
    rearm_counts: dict[str, int] = {}
    # 首个 pass 不等满 rescan_sec（短波形态下那样等于永不扫描，见 docstring）。
    next_probe_at = time.time() + max(0.0, first_probe_sec)
    while (
        not all_settled.is_set()
        and not (halt_event is not None and halt_event.is_set())
        and time.time() < deadline
    ):
        # 用 all_settled.wait 替代 time.sleep：所有游戏已结算时立即唤醒（不再等满
        # rescan_sec，round done 不滞后——实测 2026-09-05）。
        # 防忙等的地板：50ms（不写死 0.5s——那会让 `recoverPingSec` 这类旋钮在
        # 小值时不生效，配置与实际行为说谎；生产缺省 5s/20s，地板不参与）。
        wait_sec = min(
            max(0.05, next_probe_at - time.time()),
            max(0.05, deadline - time.time()),
        )
        all_settled.wait(wait_sec)
        if (
            all_settled.is_set()
            or (halt_event is not None and halt_event.is_set())
            or time.time() >= deadline
        ):
            return
        # 下个 pass 的间隔：地板必须与上面 wait 的地板同源（0.05s）——写死 1.0s 会让
        # `recoverPingSec` 这类旋钮在小值时不生效（配置与实际行为说谎，与上面注释同一
        # 口径）。2026-09-20 实测代价：tests/trainer/test_rollout_dispatch_resilience.py 的回场
        # 用例配 0.05s 却按 1.0s 走 ⇒ 4 轮回场要 >3s 纯等待（pytest-timeout 线程栈可见）。
        # 生产缺省 20s/5s 远大于地板，不受影响；防忙等的下界由循环里的 0.05s wait 担任。
        next_probe_at = time.time() + max(0.05, rescan_sec)

        # 候选 = 尚未孵化 ∪ 已停派（真故障熔断 / 连续瞬断软停）。
        with lock:
            cands: list[tuple[dict, str, str]] = []
            for n in configured:
                nid = str(n.get("id") or n.get("url") or "?")
                if nid not in spawned_ids:
                    cands.append((n, nid, "new"))
                elif (
                    streaks.get(nid, 0) >= fail_streak_max
                    or soft_streaks.get(nid, 0) >= soft_streak_max
                ):
                    cands.append((n, nid, "rearm"))
        if not cands:
            continue
        # 并行 ping（串行 3s/台 会让一个 pass 卡十几秒，与 B/C 层同款修法）。
        pings = common.distribution.ping_nodes_parallel(
            [n for n, _nid, _why in cands], timeout=status_timeout
        )
        for (n, nid, why), ping in zip(cands, pings, strict=True):
            if ping is None:
                continue  # 仍未上线，下个 pass 再试
            # 回场上界：每节点每轮最多补孵 rearm_cap 次（防「永远失败的节点」无限起线程）。
            if why == "rearm":
                with lock:
                    capped = rearm_counts.get(nid, 0) >= rearm_cap
                    first_warn = capped and nid not in rearm_warned
                    if first_warn:
                        rearm_warned.add(nid)
                if capped:
                    if first_warn:
                        log(
                            f"[dist] rescan {nid}: 本轮回场上界 {rearm_cap} 次已用尽 — "
                            f"不再补孵（节点持续失败，见日志根因）"
                        )
                    continue
            if ping.get("codeHash") != code_hash:
                # guarded 重启（跨代去重 + 脏树拒发，同 ping 门）；dedup 静默跳过
                # （rescan 周期 ~15s，重复刷屏无信息量）。F2：日志带两侧 hash——
                # 与 ping 门同口径，运维一眼看出差异在哪一侧。
                common.distribution.forget_weights_node(nid)
                local_short = code_hash[:8]
                remote_short = str(ping.get("codeHash") or "")[:8] or "none"
                if not upgrade_branch:
                    continue
                ok, reason = common.distribution.request_upgrade_guarded(
                    nid,
                    n["url"],
                    n.get("authKey", ""),
                    upgrade_branch,
                    str(ping.get("codeHash")),
                    dirty=dirty_files,
                    expected_hash=code_hash,
                )
                if reason == "restart-requested":
                    log(
                        f"[dist] rescan {nid}: codeHash stale "
                        f"(local={local_short} remote={remote_short}) — requested "
                        f"upgrade to {upgrade_branch} (accepted)"
                    )
                elif reason.startswith("dirty-tree"):
                    log(
                        f"[dist] rescan {nid}: codeHash stale "
                        f"(local={local_short} remote={remote_short}) — remote restart "
                        f"suppressed ({reason}: uncommitted training-tree changes)"
                    )
                elif common.distribution.is_self_node(n["url"], nid):
                    log(
                        f"[dist] rescan {nid}: self node stale "
                        f"(local={local_short} remote={remote_short}) — restart-only "
                        f"({reason})"
                    )
                elif reason != "dedup":
                    log(
                        f"[dist] rescan {nid}: codeHash stale "
                        f"(local={local_short} remote={remote_short}) — upgrade "
                        f"request failed ({reason})"
                    )
                continue
            remote_full = str(ping.get("bunVersion", "?"))
            # mm 就地内联（param 已删）：major.minor 一致性红线（确定性，M4）
            if ".".join(remote_full.split(".")[:2]) != ".".join(str(local_bun).split(".")[:2]):
                continue
            # 中途上线/回场前再核一次本轮 bootId（与 dispatch 的钉**同一个键**——两处必
            # 共用 `round_key("rollout", iter_id)`，见 biz/node_identity 与它的接线守卫）：
            # 不一致 = 这一轮里这个端口换过进程 ⇒ 本轮不再回场（下一轮重新钉）。
            boot_why = node_identity.note_ping(
                node_identity.round_key("rollout", iter_id), nid, ping
            )
            if boot_why:
                log(f"[dist] rescan {nid}: {boot_why} — 本轮不再回场")
                continue
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            # 权重：新上线节点走进程内复用缓存；**回场节点强制重握手**（它可能刚重启，
            # 桶里那份也可能已被别的客户端挤掉；post_weights 自带 cached 探针，命中即
            # 'kept'）。kind 必须与 rollout 腿一致——旧实现漏传，goal/intent 腿会把权重
            # 发进 rollout 桶，任务全 409（与 A1 同一类陷阱）。
            if why == "new" and common.distribution.weights_already_pushed(wver, nid, kind=wkind):
                mode = "kept(cache)"
            else:
                if why == "rearm":
                    common.distribution.forget_weights_node(nid)
                try:
                    mode = common.distribution.post_weights(
                        n["url"],
                        n.get("authKey", ""),
                        iter_id,
                        wver,
                        weights_bytes,
                        timeout=min(300.0, max(60.0, task_timeout)),
                        kind=wkind,
                    )
                except common.distribution.DistError as e:
                    log(f"[dist] rescan {nid}: weights POST failed ({e}) — skip this round")
                    continue
            # nd 带 ping：漏带会让 stageJson 任务在回场/中途上线节点上被能力握手拒掉
            # （旧实现 bug，自定义关课程下等于把这些节点白孵）。
            nd = {
                "id": nid,
                "url": n["url"],
                "key": n.get("authKey", ""),
                "c": c_n,
                "ping": ping,
            }
            with lock:
                spawned_ids.add(nid)
                if why == "new":
                    alive.append(nd)
                # 回场：重置失败计数（否则 worker 在循环顶部立刻又 return）。
                streaks[nid] = 0
                soft_streaks[nid] = 0
                if why == "rearm":
                    rearm_counts[nid] = rearm_counts.get(nid, 0) + 1
            if why == "new":
                log(
                    f"[dist] rescan: node {nid} online mid-run — weights {mode}, "
                    f"spawning {c_n} workers"
                )
            else:
                log(
                    f"[dist] rescan: node {nid} 回场（此前停派）— weights {mode}，"
                    f"重置失败计数并补孵 {c_n} 个采样线程"
                    f"（第 {rearm_counts[nid]}/{rearm_cap} 次）"
                )
            for _ in range(c_n):
                t = threading.Thread(target=worker, args=(nd,), daemon=True)
                t.start()
                extra_threads.append(t)


def register_inflight(inflight: dict[tuple[int, int], int], task: tuple[int, int]) -> None:
    """主副本派发登记（纯函数）：**所有**主副本派发一律入表，即竞速候选。

    早派任务必须可见（it6：a97 积压 3 局、空闲槽无从竞速）。副本数天然上界 =
    节点数（pick_race_target 同节点排除），无 tailFanoutDup。重试 requeue 不出表、
    再派发再登记（计数累加），结算/终局失败路径负责扣减。"""
    inflight[task] = inflight.get(task, 0) + 1


def resolve_tail_join_sec(policy: dict, all_settled: bool, halted: bool) -> float:
    """收尾 join 的 grace 秒数（纯函数；2026-09-19 收紧）。

    all_settled / halt 后默认 **0**：计划对局已齐（或已熔断），在飞副本只剩竞速
    输家——结果注定被 dedup 丢弃，等待无数据价值（x20-rebirth it19：30s×3 波纯开销）。
    policy.tailGraceJoinSec 可覆写（e2e 用 2s 验有界）。
    窗口到期未齐 → tailGraceJoinSecDeadline（默认 5s）给在飞 worker 极短补结算窗；
    缺口由 volume 补波 / resume 兜底。绝不再用 queueWindow+taskTimeout（旧 2700s 洞）。
    """
    if all_settled or halted:
        return float(policy.get("tailGraceJoinSec", 0))
    return float(policy.get("tailGraceJoinSecDeadline", 5))


def ensure_games(m: dict) -> dict:
    """将单局 agent manifest（无 games 键）转换为 combine_reports 可消费的格式。

    分布式 agent 返回的 manifest 没有 games/totalSamples/totalTicks 等聚合字段，
    而 _rl_report.json（本地 rollout）已含这些键。本函数为缺失格式补上，使
    results 与 resumed_manifests 输出同构，消除 KeyError('games')。
    """
    if "games" in m:
        return m
    score = m.get("score")
    return {
        "games": 1,
        "outcomes": {str(m.get("outcome", "unknown")): 1},
        "totalSamples": int(m.get("nSamples") or 0),
        "totalTicks": int(m.get("ticks") or 0),
        "scoreList": [score] if isinstance(score, (int, float)) else [],
        "dimLists": {},
    }


def next_attempt_tag(iter_id: str, seq: list[int]) -> str:
    """发一个本轮唯一的 attempt tag（`rollout:<iter>:aN`）。"""
    seq[0] += 1
    return f"rollout:{iter_id}:a{seq[0]}"


def abort_other_tags(tags: set[str], current: str | None) -> None:
    """把同一任务其余在飞副本断连；**当前 attempt 已经结束，不能连自己一起标
    aborted**（旧实现把成功那份也标了，下一波 tag 从 a1 重来时整批瞬断）。"""
    for tag in tags:
        if tag != current:
            common.distribution.abort_active_requests(tag)


def remember_task_key(target: dict | None, key: str) -> None:
    """`fetch_task` 发请求前的回调：把 agent 认可的 taskKey 记到本次 attempt。"""
    if target is not None:
        target["task_key"] = key


def note_cancel_target(
    inflight_targets: dict[tuple[int, int], dict[str, dict]],
    task: tuple[int, int],
    tag: str,
    nd: dict,
) -> dict | None:
    """登记远端取消目标；旧 agent（无 cancelSupport）不登记。"""
    if not bool((nd.get("ping") or {}).get("cancelSupport")):
        return None
    target = {"url": nd["url"], "auth": nd["key"], "task_key": ""}
    inflight_targets.setdefault(task, {})[tag] = target
    return target


def drop_attempt(
    inflight_tags: dict[tuple[int, int], set[str]],
    inflight_targets: dict[tuple[int, int], dict[str, dict]],
    task: tuple[int, int],
    tag: str | None,
) -> None:
    """摘掉这一份 attempt 的断连 tag 与取消目标（其它副本继续保留）。"""
    if not tag:
        return
    tags = inflight_tags.get(task)
    if tags is not None:
        tags.discard(tag)
        if not tags:
            inflight_tags.pop(task, None)
    targets = inflight_targets.get(task)
    if targets is not None:
        targets.pop(tag, None)
        if not targets:
            inflight_targets.pop(task, None)


def prune_timeout_blocks(
    task_timeout_blocks: dict[tuple[int, int], set[str]],
    inflight: dict[tuple[int, int], int],
    pending: deque[tuple[int, int]],
) -> None:
    """清理已不在活跃集里的任务冷却黑名单。"""
    for task in list(task_timeout_blocks):
        if task not in inflight and task not in pending:
            task_timeout_blocks.pop(task, None)


def _cancel_remote_targets(targets: list[dict], log: Any) -> None:
    """daemon 线程：把超时 attempt 的取消请求送到各节点（失败只记数，不抛）。"""
    ok = 0
    tried = 0
    for t in targets:
        key = str(t.get("task_key") or "")
        if not key:
            continue
        tried += 1
        if common.distribution.cancel_task(
            str(t.get("url") or ""), str(t.get("auth") or ""), key
        ):
            ok += 1
    if tried:
        log(f"[dist] alloc-timeout: cancelled {ok}/{tried} remote task(s)")


def reap_alloc_timeouts(
    *,
    inflight: dict[tuple[int, int], int],
    inflight_ts: dict[tuple[int, int], float],
    inflight_nodes: dict[tuple[int, int], set[str]],
    inflight_tags: dict[tuple[int, int], set[str]],
    inflight_targets: dict[tuple[int, int], dict[str, dict]],
    task_timeout_blocks: dict[tuple[int, int], set[str]],
    pending: deque[tuple[int, int]],
    timeout_sec: float,
    log: Any,
) -> int:
    """把超时任务放回 `pending` 并取消原请求；返回本次重排的任务数。

    `inflight_ts` 是**首次派发**的墙钟锚点，竞速副本不刷新它；超时任务回队前
    对该任务的每个 attempt tag 调 `abort_active_requests`——只关这一份请求，
    避免「超时回队 + 原请求继续跑」把同一局执行两遍。

    已知局限：卡在 `urlopen`（响应头还没回来）的连接不在注册表里，关不到；
    那部分只能等原请求自己的 timeout（见 `common.distribution.abort_active_requests`）。
    """
    now = time.time()
    reaped = [task for task, ts in list(inflight_ts.items()) if now - ts > timeout_sec]
    if not reaped:
        return 0

    tags: list[str] = []
    cancel_targets: list[dict] = []
    for task in reaped:
        blocked = inflight_nodes.pop(task, set())
        inflight.pop(task, None)
        inflight_ts.pop(task, None)
        tags.extend(inflight_tags.pop(task, set()))
        cancel_targets.extend(inflight_targets.pop(task, {}).values())
        pending.append(task)
        if blocked:
            task_timeout_blocks.setdefault(task, set()).update(blocked)
            log(
                f"[dist] alloc-timeout s{task[0]}/seed{task[1]} "
                f"({timeout_sec:.0f}s) — re-queued, cooling nodes: {blocked}"
            )
        else:
            log(
                f"[dist] alloc-timeout s{task[0]}/seed{task[1]} "
                f"({timeout_sec:.0f}s) — re-queued"
            )

    # 关掉超时那一份的 HTTP 响应（异步轮询会立刻抛；同步请求若已拿到响应头也能被
    # reset）。关不到的部分由原请求自己的 timeout 兜底——至少不再让 reaper 被
    # 竞速副本反复「续命」。
    aborted = sum(common.distribution.abort_active_requests(tag) for tag in tags)
    log(
        f"[dist] alloc-timeout: aborted {aborted}/{len(tags)} "
        "in-flight request(s) — the re-queued copy is the only live one"
    )
    # 断本端连接只省本端的等待；有 cancelSupport 的节点还要真正停掉远端算力。
    # daemon 线程：reaper 在 dispatch 锁内跑，HTTP 不得持锁。
    if cancel_targets:
        threading.Thread(
            target=_cancel_remote_targets,
            args=(cancel_targets, log),
            daemon=True,
            name="alloc-cancel",
        ).start()
    return len(reaped)


def pick_tail_race(inflight: dict[tuple[int, int], int], dup: int) -> tuple[int, int] | None:
    """v3.10 长尾竞速选择（保留给历史单测；生产路径用 pick_race_target）。

    副本数 < dup 时选字典序最小的 in-flight 任务。"""
    cand: tuple[int, int] | None = None
    for t, c in inflight.items():
        if c < dup and (cand is None or t < cand):
            cand = t
    return cand


def pop_inflight(
    inflight: dict[tuple[int, int], int],
    inflight_nodes: dict[tuple[int, int], set[str]],
    task: tuple[int, int],
    nd_id: str,
) -> None:
    """一个副本出表（结算/失败/丢弃）。计数归零时连同节点集一起清。

    A/B/C 三层共用一处（原先只是 `eval_dispatch` 里的局部闭包，现提到本模块）。
    """
    if task in inflight:
        inflight[task] -= 1
        if inflight[task] <= 0:
            inflight.pop(task, None)
            inflight_nodes.pop(task, None)
        else:
            inflight_nodes.get(task, set()).discard(nd_id)


def clear_inflight(
    inflight: dict[tuple[int, int], int],
    inflight_nodes: dict[tuple[int, int], set[str]],
    task: tuple[int, int],
) -> None:
    """任务已结算（胜者拿到）⇒ 整个出表；在飞的竞速副本回来时按 dup 丢弃。"""
    inflight.pop(task, None)
    inflight_nodes.pop(task, None)


def pick_race_target(
    inflight: dict[tuple[int, int], int],
    nd_id: str,
    inflight_nodes: dict[tuple[int, int], set[str]],
    timeout_blocks: dict[tuple[int, int], set[str]],
) -> tuple[int, int] | None:
    """in-flight race 选靶（纯函数，单测覆盖）：空闲槽复制哪个已在跑的任务。

    规则（2026-09-16 用户裁定：不判节点快慢、无 dup 上限）：
    - nd_id 已持有该任务 → 不派回（每节点每任务最多 1 份）
    - nd_id 在 timeout_blocks[task] → 冷却期内不重抢
    - 返回字典序最小的可竞速任务；无则 None
    副本数上界 = 节点数（同节点排除），先返回者结算、败者丢弃。"""
    for t in sorted(inflight):
        current = inflight_nodes.get(t, set())
        blocked = timeout_blocks.get(t, set())
        if nd_id not in current and nd_id not in blocked:
            return t
    return None


def race_tier_ok(speeds: dict[str, float], nid: str, top_n: int = 3) -> bool:
    """v3.11 竞速派档（纯函数，单测覆盖）：竞速副本只派给**快节点**，避免副本恰好落入
    慢节点、竞速形同虚设（用户 2026-08-31 观察："两个副本都分派到慢速节点，不还是要等"）。

    - 本机（local）：豁免（实测最快、无网络往返），永久参与竞速。
    - 无速度样本（首轮/全空）：乐观放行——没数据时不该设门槛。
    - 其余按 EWMA 耗时（speed 表，即各节点最近任务平均耗时）排序，取 top_n 快档；
      不在快档的节点不参与竞速（慢节点对竞速是负资产）。
    - 节点数 ≤ top_n：全员参与（退化回无门槛，正确）。"""
    if nid == "local":
        return True
    if not speeds:
        return True
    ranked = sorted((v, k) for k, v in speeds.items() if k != "local")
    if not ranked:
        return True
    tier = {k for _v, k in ranked[:top_n]}
    return nid in tier
