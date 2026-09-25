"""serve_pool.py —— 节点侧的**长驻 worker 池**（`--serve` 协议），与 sampler-agent 的
`persistPool` 同一份契约。

为什么有它（`docs/nn/runtime-opt.md` §20/§21/§22）：逐局 `spawn` 一个新 bun 时，每局都要重付
进程启动 + 模块加载 + 首用 attestation×3 + 权重解析。本机 8 并发实测：100 局逐局直开
19.60s vs 走池 12.12s = **1.59×**（手机同值 2165 → 3435 局/h）。而这条收益此前**只有**
`sampler-agent` 的 `/v1/task` 路径吃得到 —— 节点侧的三条腿（kind=iter / kind=run 的 rollout、
云机离线 eval）走的是各自的逐局 `Popen`，一点没吃到。

两条消费方（同一份协议、同一份回退纪律）：
  * `remote/iter_rollout.py`：把 worker 的 stdout **按局切回** `w{i}/rollout.log`（`try_pool`）；
  * `remote/offline_eval.py`（经 `rl/eval_local.py`）：每局只把 stdout 当**失败尾巴**用，
    不需要逐局日志文件 ⇒ `try_capture` 直接把行交回调用方。

协议（TS 侧唯一实现 = `tools/sim/serve-loop.ts::runServe`，**不要各写一套**）：
  * 进程按 `[bun, <exporter>.ts, "--serve"]` 起（cwd = TS 代码根）；
  * 就绪先打 `__SERVE_READY__`；
  * stdin 每行 = 一局的 argv（**JSON 数组，不含脚本路径**，等价于 agent 侧的 `args.slice(1)`）；
  * 跑完打 `__SERVE_OK__`，失败打 `__SERVE_ERR__ <msg>`；
  * 导出器自己的对局日志混在同一路 stdout 上 ⇒ 池按**任务**收集这些行、落回该局自己的
    `rollout.log`（诊断口径与一次性路径同形）。

硬前提（serve-loop.ts 的 docstring 同款）：被池化的导出器必须「一个任务一局、每局新建
World」，只有这样 serve 与一次性调用的产物才逐字节一致 —— 各导出器的 serve 测试钉这条。

**失败一律回退一次性 spawn**（与 agent 侧同策略）：池只负责「省掉每局启动」，任何时候拿不准
就交回调用方的一次性路径 —— **只慢不错、绝不丢局**。

**但回退本身要有限度**（2026-09-25 云机卡死取证）：一次超时 = kill worker + 该局一次性 spawn
+ 池补位再冷启动一个 ⇒ 一次超时放大成**三份进程**；过载时「回退越多、进程越多、越慢」是
正反馈，能把整轮锁死。所以本轮累计回退到阈值（`FALLBACK_BREAKER_MIN`，随池宽度走）就地
**熔断**：**停掉补位**（一个 worker 都不再新建），只留一行现场（`_fallback`）。

  ★ 熔断的语义**只有「不再补位」这一条**（2026-09-25 二次取证修正）：手上还活着的 worker 是
    **暖进程**，接着服务到自然耗尽；旧实现把熔断做成了「余下局全走一次性」——那等于在最挤的
    时刻把每一局都换成「冷启动一个 bun + wasm 编译 + attestation」，正是它要掐掉的放大器
    （现场：熔断行之后整轮 900s+，机器被堆死的进程压住）。
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import NamedTuple

from platform_utils import kill_process_tree, popen_own_group

#: 与 `tools/sim/serve-loop.ts` 逐字对齐的三个标记（改一侧必须同步另一侧）。
SERVE_READY = "__SERVE_READY__"
SERVE_OK = "__SERVE_OK__"
SERVE_ERR = "__SERVE_ERR__"

#: 支持 `--serve` 的导出器白名单 —— `tools/agent/sampler-agent.ts::PERSIST_SERVE_ENTRIES`
#: 的节点侧镜像。**只按这份名单建池**：名单外的 argv（单测里的 python 桩、将来新加的导出器）
#: 一律走一次性路径，池连起都不起 —— 否则「不支持 serve 的脚本」会安静吃掉一个就绪超时。
#: rollout 导出器（kind=iter / kind=run 的逐局 rollout）。
ROLLOUT_SCRIPT = "tools/sim/export-rl-rollout.ts"
#: eval 导出器（云机离线评估；`rl/eval_local.py` 建 cmd 时也用这个常量）。
EVAL_SCRIPT = "tools/sim/export-eval-game.ts"

SERVE_CAPABLE_SCRIPTS: frozenset[str] = frozenset({ROLLOUT_SCRIPT, EVAL_SCRIPT})

#: 就绪等待上限：bun 冷启动 + wasm 实例化 + 首用 attestation 都在这里（本机 <1s，
#: Termux 实测 ~2.5s）——**不占**单局硬顶，否则慢节点上第一局必被看门狗误杀。
READY_TIMEOUT_SEC = 60.0

#: 关池开关（诊断/回退用）：`NN_SERVE_POOL=0` 让整轮回到逐局 spawn（口径 A，旧行为）。
ENV_SWITCH = "NN_SERVE_POOL"

#: 本轮累计回退局数的**熔断下限**：到线就停用池（余下局直接一次性 spawn）。
#:
#: 为什么必须有（2026-09-25 云机卡死取证）：一次超时 = ① kill 一个长驻 worker ② 这一局改
#: 一次性 `spawn`（再冷启动一次 bun）③ 池补位又冷启动一个 ⇒ **一次超时放大成三份进程**。
#: 过载时回退越多、进程越多、游戏越慢 ⇒ 回退更多：正反馈把机器锁死在 5s 硬顶之外，整轮
#: 从此不再推进（现场：220 worker 的健康轮 p90=2.62s，下一轮被 2× 超订后成批踩 5s 线，
#: 5s 后一次刷出 60+ 行回退，随后 4 分钟零行）。熔断是这条回路的唯一刹车：**不再补位**
#: （第三份进程就是补位那个冷启动），但**已经暖着的 worker 继续用**——它们是这条回路里
#: 唯一「不花新启动成本」的部分，把它们一起扔掉只会让余下每一局都付一次冷启动。
#: 阈值随池宽度走（大池按 1/4 收线，小池 4 条起步）——见 `_breaker_after`。
FALLBACK_BREAKER_MIN = 4

#: 回退行的**详情**条数上限（超出部分只由熔断行/轮末汇总报数）。
#:
#: 对齐日志节食口径（`remote/log_bundle.py`）：这一族行本来只回答「哪一局、为什么」，
#: 而过载时它们成屏幕刷（现场 60+ 行/2s），把真正要看的东西埋掉；且原行**不带 kind/
#: label/where**，rollout 与 eval 共用本模块时根本分不出是哪条腿。
FALLBACK_LOG_DETAIL_LIMIT = 5


def pool_enabled() -> bool:
    """池总开关：缺省开；`NN_SERVE_POOL=0/false/off/no` 关（回到逐局 spawn）。"""
    raw = (os.environ.get(ENV_SWITCH) or "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def serve_capable(argv: list[str]) -> bool:
    """这一局的 argv 是否指向一个支持 `--serve` 的导出器（见 `SERVE_CAPABLE_SCRIPTS`）。

    按**规范化路径**比，顺带认 basename：协议层已把 argv[0] 钉成 TS 根下的相对脚本名
    （`protocol.ROLLOUT_SCRIPTS`），真实节点走的是精确匹配那一支；basename 兜底只为了让
    「绝对路径指向同一个导出器」（测试桩、将来换个代码根）也能被认出来。
    """
    if not argv:
        return False
    key = str(argv[0]).replace("\\", "/").lstrip("./")
    if key in SERVE_CAPABLE_SCRIPTS:
        return True
    base = key.rsplit("/", 1)[-1]
    return any(entry.rsplit("/", 1)[-1] == base for entry in SERVE_CAPABLE_SCRIPTS)


def make_pool(
    bun: str,
    script: str,
    ts_dir: str | Path,
    workers: int,
    log=lambda msg: None,
    *,
    ready_timeout_sec: float = READY_TIMEOUT_SEC,
) -> ServePool | None:
    """按导出器建池（**两条腿共用同一个准入**）：开关关着 / 脚本不在白名单 ⇒ 返回 None。

    两个前置都是**便宜且确定**的判据，不靠试：
      1. 总开关 `NN_SERVE_POOL` 没关（关了就整条腿回到逐局 spawn）；
      2. 脚本在 `SERVE_CAPABLE_SCRIPTS` 内（真的实现了 `--serve`）。
    返回 None 的语义 = 「本条腿不建池」，调用方按原样走一次性路径（行为与加池前逐字节相同）。

    池的**生命周期由调用方拥有**：rollout = 一个 job（= 一个权重版本）；eval = 一轮评估
    （`run_cloud_eval`）。用 `with` 或 try/finally 保证 `close()`（否则留下常驻进程）。
    """
    if not pool_enabled() or not script or not serve_capable([str(script)]):
        return None
    return ServePool(
        bun, str(script), Path(ts_dir), workers, log, ready_timeout_sec=ready_timeout_sec
    )


class TaskOutcome(NamedTuple):
    """一个任务在池里的结局（`_submit` 的返回）。`reason` = 失败原因（成功时空串）。"""

    ok: bool
    sec: float
    lines: list[str]
    reason: str


class _Worker:
    """一个长驻 worker：spawn 起来后靠 stdin 喂任务、靠标记行判结果。"""

    def __init__(self, proc: subprocess.Popen, script: str) -> None:
        self.proc = proc
        self.script = script
        self.busy = False
        self.dead = False
        #: 就绪或**已退出**都会置位 —— 等待方靠它立刻醒，不会为一个死进程等满就绪上限。
        self.ready = threading.Event()
        self.settled = threading.Event()
        self.result: tuple[bool, str] | None = None  # (ok, msg)
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._thread = threading.Thread(target=self._read, name="serve-reader", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        """读线程：分离「协议标记」与「对局日志」，前者唤醒等待者，后者留给 submit 落盘。"""
        out = self.proc.stdout
        if out is not None:
            try:
                for raw in out:
                    line = raw.rstrip("\n")
                    if line.startswith(SERVE_OK):
                        self.result = (True, "")
                        self.settled.set()
                    elif line.startswith(SERVE_ERR):
                        self.result = (False, line[len(SERVE_ERR):].strip() or "worker error")
                        self.settled.set()
                    elif line.startswith(SERVE_READY):
                        self.ready.set()
                    else:
                        with self._lock:
                            self._lines.append(line)
            except (OSError, ValueError):
                pass
        # 进程没了 ⇒ 唤醒可能还在等的 submit / start（否则要等满整个超时）。
        self.dead = True
        self.settled.set()
        self.ready.set()

    def drain(self) -> list[str]:
        with self._lock:
            out = self._lines
            self._lines = []
        return out

    def kill(self) -> None:
        """收掉这个 worker（POSIX 下连它的**整个进程组**一起）。**不等回收**。

        为什么是进程组：worker 是长驻的 `bun`，它自己可能带子进程（编译缓存 worker/子工具）；
        只杀进程本身会留下孤儿继续吃 CPU/内存（机器越跑越卡）。为什么不等回收：kill 是异步的
        —— 等就把这一层变成阻塞点（一次超时 = 一个 worker 要收，而它可能正卡在不可中断的 IO
        里；有界的等属于 `popen` 侧的纪律，池这里只负责把信号递出去）。
        """
        kill_process_tree(self.proc)
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except OSError:
            pass


class ServePool:
    """固定大小的长驻 worker 池（一份 spec 一个池 —— iter 的 argv 只有一个脚本）。

    线程安全：调用方是 `iter_rollout` 的 `ThreadPoolExecutor`（并发数 = 池上限），
    `try_pool` 可被多线程同时调用。
    """

    def __init__(
        self,
        bun: str,
        script: str,
        ts_dir: Path,
        max_workers: int,
        log=lambda msg: None,
        *,
        ready_timeout_sec: float = READY_TIMEOUT_SEC,
    ) -> None:
        self.bun = bun
        self.script = script
        self.ts_dir = Path(ts_dir)
        self.max_workers = max(1, int(max_workers))
        self.log = log
        self.ready_timeout_sec = float(ready_timeout_sec)
        self._workers: list[_Worker] = []
        self._lock = threading.Lock()
        # 诊断计数（轮末一行汇总：池到底省下多少、又回退了多少）
        self.served = 0
        self.spawned = 0
        self.killed = 0
        self.fallback = 0
        self.fallback_reasons: dict[str, int] = {}
        self.closed = False
        #: 熔断阈值（本轮累计回退到它就停用池）；见 `FALLBACK_BREAKER_MIN`。
        self.breaker_after = max(FALLBACK_BREAKER_MIN, self.max_workers // 4)
        #: 已熔断 = **不再补位**（一个 worker 都不再新建）；手上的暖 worker 继续服务。
        self.disabled = False
        #: 熔断后因**没有暖 worker 可用**而走一次性路径的局数（不计进 `fallback`——否则计数被灌满）。
        self.bypassed = 0
        self._fallback_logged = 0

    # ---------------- 生命周期 ----------------

    def _spawn(self) -> _Worker | None:
        argv0 = str(self.ts_dir / self.script)
        try:
            proc = subprocess.Popen(
                [self.bun, argv0, "--serve"],
                cwd=str(self.ts_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,  # 行缓冲：任务行必须立刻送达 worker（否则池会安静地卡住）
                encoding="utf-8",
                errors="replace",
                # 自带进程组 ⇒ 收池/kill 时能连带它自己起的子进程（`_Worker.kill`）。
                **popen_own_group(),
            )
        except OSError:
            return None
        with self._lock:  # 计数会被多个任务线程并发加（`_acquire` 里按需补位）
            self.spawned += 1
        return _Worker(proc, self.script)

    def start(self) -> int:
        """起满池并等齐 `__SERVE_READY__`；返回**真正就绪**的 worker 数。

        先起满再发第一个任务、且把就绪等待与单局硬顶分开：否则第一局的硬顶里会混进冷启动
        （Termux 上 ~2.5s）而被看门狗误杀 —— 那是「池看起来不如一次性」的假象。

        **并发起（先全部 Popen，再统一等就绪）**：冷启动（bun + wasm 编译 + attestation）
        彼此独立，串行等就绪会让它们排成一行 —— 实测 8 局的轮上池反而比逐局 spawn 慢
        （那些进程本来能并发冷启动）。就绪上限是**整批共用**的，不是每个 worker 各一份。
        起不来的（进程当场退出/超时）不保留也不会重试：本轮回到一次性路径。
        """
        ws: list[_Worker] = []
        for _ in range(self.max_workers):
            w = self._spawn()
            if w is None:
                break
            ws.append(w)
        deadline = time.time() + self.ready_timeout_sec
        for w in ws:
            left = deadline - time.time()
            if left > 0:
                w.ready.wait(left)
        ready: list[_Worker] = []
        for w in ws:
            if w.dead:
                self.killed += 1
                w.kill()
            else:
                ready.append(w)
        with self._lock:
            self._workers = ready
        return len(ready)

    def close(self) -> None:
        self.closed = True
        with self._lock:
            ws, self._workers = self._workers, []
        for w in ws:
            w.kill()

    # ---------------- 跑一局 ----------------

    def owns(self, argv0: str) -> bool:
        """这一局是不是**本池那个脚本**的（按 basename 比，兼容相对/绝对两种写法）。

        只防「喂错池」：真正的准入（白名单 + 开关）在 `iter_rollout._make_pool` 建池那一步。
        """

        def base(p: str) -> str:
            return str(p).replace("\\", "/").lstrip("./").rsplit("/", 1)[-1]

        return base(argv0) == base(self.script)

    def _acquire(
        self, timeout_sec: float | None = None, *, replenish: bool = True
    ) -> _Worker | None:
        """取一个空闲 worker；`replenish=True` 时池没满则补位（新 worker 也要等就绪）。

        `timeout_sec` = 这一局**本次尝试的硬顶**（`_submit` 传进来）：补位冷启动的等待
        不得超过它 —— 否则「机器一慢」会把一个游戏线程按在就绪等待里（旧行为是固定 60s，
        远超单局 5s 的硬顶，而看门狗在这段里什么都打不出来）。等不到就绪 ⇒ 当场放弃这个
        新 worker，**交给调用方的一次性路径**（与「起不来」同一个出口，只慢不错）。

        `replenish=False`（熔断后）：**只用还活着的（暖的）worker，一个都不新建** —— 冷启动就是
        「一次超时 → 三份进程」里的第三份，熔断的全部意义就是停掉它（见 `_fallback`）。
        """
        with self._lock:
            for w in self._workers:
                if not w.busy and not w.dead:
                    w.busy = True
                    return w
            if (
                not replenish
                or self.closed
                or self.disabled
                or len(self._workers) >= self.max_workers
            ):
                return None
        fresh = self._spawn()
        if fresh is None:
            return None
        budget = self.ready_timeout_sec
        if timeout_sec is not None:
            budget = min(budget, max(1.0, float(timeout_sec)))
        ready = fresh.ready.wait(budget)
        with self._lock:
            if (
                not ready
                or fresh.dead
                or self.closed
                or self.disabled
                or len(self._workers) >= self.max_workers
            ):
                # 没等到就绪 / 起不来 / 关池 / 已熔断 / 被别的线程先占满 ⇒ 多出来的这个直接收掉
                fresh.kill()
                self.killed += 1
                return None
            self._workers.append(fresh)
            fresh.busy = True
        return fresh

    def _drop(self, w: _Worker) -> None:
        with self._lock:
            if w in self._workers:
                self._workers.remove(w)
        self.killed += 1
        w.kill()

    def _fallback(self, reason: str) -> None:
        """记一次回退；到熔断线就**停掉补位**并响亮记一行（这一行是全轮唯一的刹车现场）。

        熔断的语义**只有一条：不再补位**（不再新建 worker）。**不是**停用整个池 —— 见模块
        docstring 里那条 ★：温暖 worker 继续服务，直到自然耗尽。旧实现把「熔断」做成了「余下局
        全走一次性」，那等于在最挤的时刻把每一局都换成一次冷启动，正是它要掐掉的放大器。

        计数在锁外自增不致命（GIL 下 dict 操作原子、计数只是诊断口径），但熔断只能置一次。
        """
        self.fallback += 1
        self.fallback_reasons[reason] = self.fallback_reasons.get(reason, 0) + 1
        if not self.disabled and self.fallback >= self.breaker_after:
            self.disabled = True
            reasons = ",".join(f"{k}={v}" for k, v in sorted(self.fallback_reasons.items()))
            self.log(
                f"[serve-pool] 熔断：本轮停掉**补位**（fallback={self.fallback} ≥ 阈值"
                f"{self.breaker_after}；served={self.served} spawned={self.spawned} "
                f"killed={self.killed}｜{reasons}）——手上还暖的 worker 接着用（省一次冷启动），"
                "没有暖 worker 的局走一次性 spawn；不再新建 worker"
                "（避免「一次超时 → 三份进程」的放大回路）"
            )

    def _log_fallback(self, reason: str, why: str, *, kind: str, label: str, where: str) -> None:
        """回退行的**详情**（前 `FALLBACK_LOG_DETAIL_LIMIT` 条；之后只由熔断行/汇总报数）。

        必须带 `kind`/`label`/`where`：rollout 与 eval 共用本模块，缺了它们行与行之间完全
        同形（2026-09-25 的现场就是这样——一屏同形行，分不出哪条腿、哪一局、现场在哪）。
        """
        if self._fallback_logged >= FALLBACK_LOG_DETAIL_LIMIT:
            return
        self._fallback_logged += 1
        self.log(
            f"[serve-pool] 一局回退一次性 spawn（kind={kind} {label} {reason}: {why[:200]}"
            f"；现场 {where}）"
        )

    @staticmethod
    def _postmortem(w: _Worker, lines: list[str]) -> str:
        """worker 已经不在时，给它留一句**验尸行**：退出码 + 它最后说的几句话。

        为什么必须有（2026-09-24 取证）：「worker 没了」的回退只慢不错，但**现场会被覆盖**
        —— 回退的那一局由一次性路径用 `w` 模式重写同一份 `rollout.log`，worker 死前吐出的
        traceback / 守卫消息随之消失，轮末只剩 `killed=1` 这种没有信息量的计数（2026-09-23
        的 pre-commit flake 就是这样查了一轮而拿不到凶手）。

        退出码是「谁杀的」最廉价的指纹：`rc=0`＝进程自行退出（如 stdin EOF 的正常收尾）；
        `0xC0000005`（访问违例）/ `0xC000013A`（控制台 Ctrl+C 广播）/ `0xC0000409`（fail-fast）
        这类 NTSTATUS 值一看就知道不是我们的协议错；`rc=1` 且尾行带 SystemExit 消息 ⇒
        沙箱删除/写守卫（见 tools/githook/_sandbox-sanitize.sh）。
        """
        rc = w.proc.poll()
        if rc is None:
            detail = "rc=<仍在运行>"
        elif rc == 0:
            detail = "rc=0（自行退出，不是被我们 kill）"
        else:
            detail = f"rc={rc}（0x{rc & 0xFFFFFFFF:08X}）"
        tail = " | ".join(ln for ln in lines[-3:] if ln.strip())
        return detail + (f"；尾行：{tail[:200]}" if tail else "")

    def _submit(
        self,
        argv: list[str],
        timeout_sec: float,
        *,
        label: str,
        attempt: int,
        kind: str,
        where: str,
    ) -> TaskOutcome:
        """把一个任务（一局）交给池里的空闲 worker，返回它的结局 —— **从不抛**。

        `argv` = 与一次性路径**同一份** `[<script>, ...]`（内部去掉脚本路径再送：对齐
        `serve-loop.ts` 的「不含入口路径」约定）；`kind` 只影响告警行的措辞（`rollout`/`eval`）；
        `where` 是告警里的「现场」（rollout = 该局日志路径，eval = 该局 out 目录）。

        等待**按 `GAME_POLL_SEC` 轮询**（与一次性路径同规）：慢局在卡住期间就能被点名，
        而不是等硬顶到了才知道某一局有问题。超时 → kill + 记账（不在这里判定「这一局是坏的」：
        那种判定归调用方的重试逻辑，池不能把一次可能的成功变成失败）。
        """
        if self.closed or len(argv) < 2 or not self.owns(argv[0]):
            return TaskOutcome(False, 0.0, [], "not-ours")
        if self.disabled:
            # 熔断后**不计回退**（否则计数与日志都被余下几百局灌满），也**不再补位**；但手上还
            # 暖着的 worker 接着用 —— 它们是这条回路里唯一不花新启动成本的部分（见 `_fallback`）。
            w = self._acquire(timeout_sec, replenish=False)
            if w is None:
                self.bypassed += 1
                return TaskOutcome(False, 0.0, [], "pool-disabled")
        else:
            w = self._acquire(timeout_sec)
            if w is None:
                self._fallback("no-slot")
                return TaskOutcome(False, 0.0, [], "no-slot")
        w.result = None
        w.settled.clear()
        w.drain()
        t0 = time.time()
        stdin = w.proc.stdin
        if stdin is None:
            self._log_fallback(
                "no-stdin", self._postmortem(w, w.drain()), kind=kind, label=label, where=where
            )
            self._fallback("no-stdin")
            self._drop(w)
            return TaskOutcome(False, 0.0, [], "no-stdin")
        try:
            stdin.write(json.dumps(argv[1:]) + "\n")
            stdin.flush()
        except (OSError, ValueError):
            self._log_fallback(
                "write-failed",
                self._postmortem(w, w.drain()),
                kind=kind,
                label=label,
                where=where,
            )
            self._fallback("write-failed")
            self._drop(w)
            return TaskOutcome(False, 0.0, [], "write-failed")
        # 轮询等待：软告警 + 硬顶都在这里判（与一次性路径同一套 game_watch 口径）
        from remote import game_watch

        warned = False
        got = False
        while True:
            if w.settled.wait(game_watch.GAME_POLL_SEC):
                got = True
                break
            elapsed = time.time() - t0
            if (
                not warned
                and not game_watch.warn_is_redundant(timeout_sec)
                and elapsed >= game_watch.SLOW_GAME_WARN_SEC
            ):
                warned = True
                self.log(
                    game_watch.slow_warn_line(kind, label, elapsed, timeout_sec, attempt, where)
                )
            if elapsed >= timeout_sec or w.dead:
                break
        elapsed = time.time() - t0
        lines = w.drain()
        if got and not w.dead and w.result and w.result[0]:
            w.busy = False
            self.served += 1
            return TaskOutcome(True, elapsed, lines, "")
        # 验尸**必须在 `_drop` 之前**：`_drop` 会 kill（kill 之后 poll() 只剩我们自己的退出码，
        # 真正的凶手指纹就没了）。
        if got and w.result and not w.result[0]:
            reason, why = "err", w.result[1]
        else:
            reason = "dead" if w.dead else "timeout"
            why = self._postmortem(w, lines)
        self._log_fallback(reason, why, kind=kind, label=label, where=where)
        self._fallback(reason)
        self._drop(w)
        return TaskOutcome(False, elapsed, lines, reason)

    def try_pool(
        self,
        argv: list[str],
        log_path: Path,
        timeout_sec: float,
        *,
        label: str = "?",
        attempt: int = 1,
        kind: str = "rollout",
    ) -> float | None:
        """尽力在池里跑一局（rollout 腿）；**成功返回墙钟秒，任何不确定都返回 None**。

        该局的 stdout **无论成败都落回 `log_path`**（诊断口径与一次性路径同形：看门狗的超时行
        与 `_first_rollout_log_tail` 都指向它）。调用方拿到 None 就走自己的一次性 `Popen`。
        """
        o = self._submit(
            argv, timeout_sec, label=label, attempt=attempt, kind=kind, where=str(log_path)
        )
        self._write_log(log_path, o.lines)
        return round(o.sec, 3) if o.ok else None

    def try_capture(
        self,
        argv: list[str],
        timeout_sec: float,
        *,
        label: str = "?",
        attempt: int = 1,
        kind: str = "eval",
        where: str = "",
    ) -> tuple[float, list[str]] | None:
        """池里跑一局并**把 stdout 行交回**（eval 腿：那一路只把输出当失败尾巴用，不落盘）。

        成功返回 `(墙钟秒, 行列表)`；任何不确定返回 None（调用方走一次性 `Popen`）。
        """
        o = self._submit(
            argv, timeout_sec, label=label, attempt=attempt, kind=kind, where=where
        )
        return (round(o.sec, 3), o.lines) if o.ok else None

    @staticmethod
    def _write_log(log_path: Path, lines: list[str]) -> None:
        """把该局在 worker stdout 上产生的行**原样**落回它自己的 rollout.log。

        逐局一份日志是既有诊断口径（`_first_rollout_log_tail`、看门狗的超时行都指向它）；
        池化后这些行混在一条流里，必须按任务切回来 —— 否则「哪一局卡了」就查不出来了。
        """
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except OSError:
            pass

    def summary(self) -> str:
        """轮末一行汇总（池的收益与代价都要能被看见）。"""
        reasons = ",".join(f"{k}={v}" for k, v in sorted(self.fallback_reasons.items())) or "-"
        return (
            f"serve_pool: served={self.served} spawned={self.spawned} killed={self.killed} "
            f"fallback={self.fallback}（{reasons}）"
            + (
                f"｜已熔断（停补位）：余下 {self.bypassed} 局没有暖 worker 可用，走一次性"
                if self.disabled
                else ""
            )
        )
