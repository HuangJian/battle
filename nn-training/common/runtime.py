"""并发的**唯一所有者**：`Runtime` —— executor 生命周期、线程命名、取消点（§P1-6 / B6）。

## 为什么要有这个对象

2026-11-17 量到全仓 **10 处** `with ThreadPoolExecutor(...) as ex:` 就地建池（生产包 9 + `tools/e2a_readout` 1），
四件事一件也守不住：

1. **没有所有者**：池的生死绑在词法作用域上 —— 一个提前 `return`、一次异常、一个 `break` 就把
   「还有在飞的活」和「池」分家，而 `close` 由解释器替你做，你没法说「这一轮结束了、别再投了」。
2. **没有上限**：`min(8, len(nodes))` 在 `common/distribution` 出现**两次**，`len(items)` 一次，
   `n_slots`（云 eval 槽位，按几十量级）一次；同一进程里两层并发叠加，谁都不报数。
   `describe()` 是让这个数第一次可见的那一行。
3. **没有取消点**：`halt_event` 是本仓的事实约定（6 个模块传它），但它到不了池边 ——
   `submit` 完就只能等。`check()` / `sleep()` / `stop=` 把熔断接到池边（**接哪儿由调用方定**）。
4. **线程名各写各的**：10 处里只有 1 处写了 `thread_name_prefix`，`py-spy` 里 8 个
   `ThreadPoolExecutor-3_0` 分不清是谁 —— 与 G15 同一条理由（转储里名字是唯一身份）。

## 这一刀**没做**什么（写在这里，免得下一个人当成隐藏的债）

* **不节流**：`pool(workers=N)` 的 N 是调用方给的（多半来自 `--workers` / 课程配置），一律照办 ——
  静默砍并发是拿吞吐换好看的数字。上限只约束**隐式**请求（`workers=None`）与 `describe()["over"]`。
* **不读 env**：上限走 `Runtime(ceiling=)` 参数。`os.environ` 直读有 G13 计数（只许调小），
  给并发再加一根环境变量旋钮等于把「同一根旋钮两个默认值」的病再种一遍（§P1-5）。
* **不接管裸 `threading.Thread`**：`thread()` 只提供所有者那一半（名字 / 登记 / join），存量按文件冻结逐步搬。
* **不替调用方接 `halt_event`**：把 6 个模块的 halt 语义一起改需要一轮真跑；`stop=` 已经吃
  `threading.Event`，接的时候是调用方一行的事。
设计说明与读数 → `docs/nn/engineering.md` §119 / §122。

## 时钟：两个可注入的读数（2026-11-17，B6 的 clock 半边）

`Runtime(clock=…, monotonic=…)` 收两个**零参可调用**（缺省 `time.time` / `time.monotonic`），
调用点写 `rt.clock()` / `rt.monotonic()`。为什么是两个而不是一个：这两个读数在语义上不可互换 ——
墙钟用于**时间戳**（台账 `ts`、与 hub 给的时间比先后），单调钟用于**时长/超时**（绝不回跳）。
收成一个 `now()` 就等于邀请「拿单调钟去比墙钟时间戳」这类错，而这类错只在跨机器时差下才显形。

不新建 `Clock` 类型：既成约定是 `clock: Callable[[], float] = time.time`（`remote/prefetch` 一族），
再造同名 Protocol 只会与 `common/logutil.Clock`（「能给时间戳 + `strftime` 的东西」）撞名。

**`sleep()` 仍是真等待**：时钟注入改变的是「读到了几点」，不是「时间怎么流逝」。想跳过等待的用例
别 mock 时钟 —— 用 `stop` 已置位 + 断言返回 `False`（那正是既有的取消语义）。

## 裸线程的归属（2026-11-17，B6 的最后一半）

`ThreadPoolExecutor` 的账收完了（G16）；**裸 `threading.Thread(...)`** 还剩一批（50 处 / 35 文件，
G18 按文件冻结、只许调小）。`rt.thread(target, name=…)` 是它们的所有者：名字 `<scope>-<name>`、
登记在本域、`close()` 时 join（`join_threads()` 报仍活着的名字）、`describe()["threads"]` 可读。
**不替 target 包 `check()`**（往别人线程体里塞可能抛异常的取消点会改写各家退出语义）；手上没有
`Runtime` 的（模块级扇出）先把 runtime 从调用方传下来，别在模块里造隐藏单例 —— 详见 §122。

"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from types import TracebackType
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")

#: 线程名前缀格式（stdlib 会在后面补 `_<序号>`）。`<scope>-<pool>` 两段都必需：
#: scope 回答「哪一轮/哪条腿」，pool 回答「这个域里的哪个池」。
NAME_FORMAT = "{scope}-{pool}"

#: 缺省上限的下限：单核云机也得能跑两个并发，否则池退化成串行。
MIN_CEILING = 2


def thread_ceiling() -> int:
    """缺省线程上限 = 逻辑核数（不少于 `MIN_CEILING`）。

    为什么以核数为界：隐式请求（`workers=None`）的调用方**没有给出自己的理由**，
    那么「和机器一样宽」是唯一有依据的缺省。自己给了数字的调用方不受它约束（见 `pool`）。
    """
    return max(MIN_CEILING, os.cpu_count() or MIN_CEILING)


#: 派生种子的域标签：改它 = 把所有流**重新洗一遍**（下游可复现性断代，别随手改）。
SEED_TAG = "nn-runtime-stream-v1"


def substream(seed: int, name: str) -> tuple[int, int]:
    """`(seed, 流名)` → **128 bit** 派生种子（两条 64 bit 整数）。

    为什么不用 `hash()` / `random.seed(seed + n)`：前者带进程级盐（`PYTHONHASHSEED`）⇒ 不可复现，
    后者在名字多了以后必然撞（「demo + 1」与「epoch」同种）。这里用 `blake2b` 对
    `SEED_TAG:seed:name` 取摘要：跨进程 / 跨机器 / 跨版本逐位稳定，改流名就换流，互不干扰。
    返回值直接可喂 `np.random.default_rng([a, b])`（numpy 侧不必知道派生规则）。
    """
    digest = hashlib.blake2b(f"{SEED_TAG}:{int(seed)}:{name}".encode(), digest_size=16).digest()
    return int.from_bytes(digest[:8], "big"), int.from_bytes(digest[8:], "big")


def _callable_name(fn: Callable[..., object]) -> str:
    """`describe()["clock"]` 的读数：`time.time` / 测试里的 `<lambda>` 一眼分得清。

    不用 `repr`：它带内存地址 ⇒ 同一轮两次 `describe()` 不一样（读数要能进 JSON 台账）。
    """
    mod = getattr(fn, "__module__", None) or getattr(fn, "__name__", None)
    qual = getattr(fn, "__qualname__", None) or getattr(fn, "__name__", None)
    if mod and qual:
        return f"{mod}.{qual}"
    return type(fn).__name__


class RuntimeCancelledError(RuntimeError):
    """取消点被触发（`Runtime.stop` 已置位）。

    故意**不**并进 `common.errors` 那一族：那组异常表达**失败分类**（网络 / 协议 / 节点），
    而取消表达「这一轮不许再往下走」。混一族就会把熔断记成故障 —— 两者的善后完全不同。
    """


class ThreadOwner:
    """持有一条后台线程的对象：`running` 是它的只读问句。

    结果回传 / outbox 物化 / 心跳写三条腿都要回答同一个问题——「线程还活着吗」。
    实现只留这一份：`tests/test_reuse_ledger.py` 的逐字节重复审计会把三份同文实现
    记为新增债（实测：三处各抄一遍 ⇒ 重复组 14 → 15，门禁红）。
    """

    _thread: threading.Thread | None

    @property
    def running(self) -> bool:
        """后台线程还活着吗（未启动 / 已收线 ⇒ False；诊断 / 用例用）。"""
        t = self._thread
        return t is not None and t.is_alive()


class Runtime:
    """一个并发域的所有者：池、线程名、取消事件、上限报数都归它。

    它就是 `ThreadPoolExecutor` 的**同形替身**：`with Runtime(scope=…, workers=N) as ex:` 之后
    `ex.submit(...)` / `ex.map(...)` 照旧（转发到默认池 `"main"`），退出时由它自己收尾：

        with Runtime(scope="cloud-eval", workers=n_slots) as ex:
            ex.map(run_one, pairs)                      # 有序、池内共享
            ex.pool("compress").submit(fn, arg)         # 要独立线程名就开独立池
            if ex.sleep(5.0):                           # 可取消的等待
                ex.check()                              # 取消点：置位即抛 RuntimeCancelledError

    `close()`（`__exit__` 调的）：等待在飞的活收尾；**只有** `stop` 已置位时才取消排队的活 ——
    「正常退出」不等于「取消」，把两者混起来会让一次提前 `return` 静默丢掉已经投出去的局。
    """

    def __init__(
        self,
        *,
        scope: str,
        workers: int | None = None,
        stop: threading.Event | None = None,
        ceiling: int | None = None,
        seed: int | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not scope:
            raise ValueError("Runtime 必须给 scope（线程名/日志里要认得出是谁）")
        self.scope = scope
        self.workers = workers
        #: 本域的根种子；`substream()` 从它派生。None = 这个域不碰随机（纯并发）。
        self.seed = seed
        self._streams: dict[str, tuple[int, int]] = {}
        #: 本域的两个时间读数（见模块 docstring「时钟」）：`clock()` = 墙钟秒（时间戳），
        #: `monotonic()` = 单调秒（时长/超时）。缺省就是 `time` 模块那两个函数。
        self.clock = clock
        self.monotonic = monotonic
        self.ceiling = ceiling if ceiling is not None else thread_ceiling()
        # `stop` 允许外部传入：`halt_event` 那种已有的事件不用复制一份语义。
        self._stop = stop if stop is not None else threading.Event()
        self._pools: dict[str, ThreadPoolExecutor] = {}
        self._sizes: dict[str, int] = {}
        #: 本域起过的裸线程（`thread()`）；`close()` 会 join 它们。
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._closed = False

    # ── 取消点 ───────────────────────────────────────────────────────────────

    @property
    def stop_event(self) -> threading.Event:
        """本域的取消事件（与传入的 `stop` 是**同一个对象**）。"""
        return self._stop

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def request_stop(self) -> None:
        """置位取消：之后的 `check()/submit()/map()` 一律拒绝新活。"""
        self._stop.set()

    def check(self) -> None:
        """取消点：已置位就抛 `RuntimeCancelledError`。放在循环体内 = 显式的可取消边界。"""
        if self._stop.is_set():
            raise RuntimeCancelledError(f"{self.scope}: 已取消（stop 已置位）")

    def sleep(self, sec: float) -> bool:
        """可取消的 `time.sleep`：`True` = 睡满了，`False` = 被取消打断。

        返回布尔而不是抛异常：轮询循环里「被打断」通常是**正常出路**（该退了），
        逼调用方写 `try/except` 会把控制流藏进异常里。

        注意这是**真等待**（`stop.wait(sec)`）：注入的时钟只改「读到的值」，不改时间流逝 ——
        想跳过等待的用例用「`stop` 已置位 + 断言 `False`」，别去 mock 时钟。
        """
        if sec <= 0:
            return not self._stop.is_set()
        return not self._stop.wait(sec)

    # ── 裸线程 ───────────────────────────────────────────────────────────────

    def thread(
        self,
        target: Callable[..., object],
        /,
        *args: object,
        name: str,
        kwargs: dict[str, object] | None = None,
        daemon: bool = False,
        start: bool = True,
    ) -> threading.Thread:
        """起一个**本域的**线程并登记：名字 `<scope>-<name>`、`close()` 时 join。

        名字要带上区分位（`worker-3` 而不是三个同名的 `worker`）：`Thread` 不会像池那样自动补序号，
        转储里三条同名线程和一条没名字没区别。`start()` 已代为调用（本仓 51 处裸线程没有一处
        是先造后等的形态）；要「造好再起」就直接用 `threading.Thread` —— 那种形态的归属问题
        不是名字，而是谁在等它。

        取消语义与池一致：**已开跑的不打断**（Python 线程杀不掉），target 想支持中途收工就自己调
        `rt.check()` / `rt.sleep()` —— 本方法不往你的 target 里塞任何东西（见模块 docstring）。

        `start=False` 只给「必须先挂号、后开跑」的调用方（`worker/eval_exec` 的槽位池：
        先 `pool.append(_Slot(t, stop))` 再 `t.start()`，否则一个已经能领牌的 worker 会在
        池里还没有它的时候被当成「池子空」再孵一个 —— 同一节点两条线程，公平发牌当场错乱）。
        登记在这时就发生，收尾仍归 `close()` / `join_threads()`。
        """
        if not name:
            raise ValueError("Runtime.thread 必须给 name（转储里靠它分清是谁）")
        with self._lock:
            if self._closed:
                raise RuntimeError(f"Runtime[{self.scope}] 已关闭，不能再起线程 {name!r}")
            # 登记表只留活着的：长驻对象（补传代理按需重启子进程、节点池伸缩）会一批批起线程，
            # 攒着已退出的那些只会让 `describe()` 越读越长。收尾要 join 仍走 `join_threads()`。
            self._threads = [t for t in self._threads if t.is_alive()]
            t = threading.Thread(
                target=target,
                args=args,
                kwargs=kwargs or {},
                name=NAME_FORMAT.format(scope=self.scope, pool=name),
                daemon=daemon,
            )
            self._threads.append(t)
        if start:
            t.start()
        return t

    def join_threads(self, timeout: float | None = None) -> list[str]:
        """join 本域登记的线程，返回**仍活着**的那些名字（空表 = 都收尾了）。

        为什么返回名字而不是布尔：收尾失败时人要看的是「谁没收」，不是「有没有失败」。
        """
        with self._lock:
            threads = list(self._threads)
            self._threads.clear()
        for t in threads:
            t.join(timeout)
        return sorted(t.name for t in threads if t.is_alive())

    # ── 池 ───────────────────────────────────────────────────────────────────

    def substream(self, name: str) -> tuple[int, int]:
        """本域里名为 `name` 的派生种子（**同域同名 = 同一个数**，与调用次数无关）。

        这就是「随机从哪来」的单一入口：调用方拿它喂 `np.random.default_rng([...])`，
        于是同一个 seed 下每条流各走各的 —— 加一次 demo 抽样不会把 epoch 顺序推歪。
        """
        if self.seed is None:
            raise ValueError(
                f"Runtime[{self.scope}] 没有 seed，派不出子流 {name!r}"
                "（要么给 Runtime(seed=…)，要么这个域本来就不该要随机数）"
            )
        cached = self._streams.get(name)
        if cached is None:
            cached = substream(self.seed, name)
            self._streams[name] = cached
        return cached

    @property
    def in_use(self) -> int:
        """本域所有池的 worker 数之和（`describe()` 的底下那一半）。"""
        with self._lock:
            return sum(self._sizes.values())

    def pool(self, name: str = "main", *, workers: int | None = None) -> ThreadPoolExecutor:
        """取本域里名为 `name` 的池（**同域同名 = 同一个池**）。

        `workers` 优先级：显式参数 > `Runtime(workers=…)` > 上限减去已用（至少 1）。
        前两档是「调用方说了算」，第三档才是这条上限真正约束的隐式请求。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError(f"Runtime[{self.scope}] 已关闭，不能再开池 {name!r}")
            ex = self._pools.get(name)
            if ex is not None:
                return ex
            n = self._resolve_workers(workers)
            ex = ThreadPoolExecutor(
                max_workers=n,
                thread_name_prefix=NAME_FORMAT.format(scope=self.scope, pool=name),
            )
            self._pools[name] = ex
            self._sizes[name] = n
            return ex

    def _resolve_workers(self, workers: int | None) -> int:
        """**调用方持有 `self._lock`**（`threading.Lock` 不可重入，这里不再抢一次）。"""
        want = workers if workers is not None else self.workers
        if want is not None:
            return max(1, int(want))
        return max(1, self.ceiling - sum(self._sizes.values()))

    def submit(self, fn: Callable[..., R], /, *args: object, **kwargs: object) -> Future[R]:
        """投到默认池（`"main"`）；要独立线程名就 `rt.pool("名字").submit(...)`。"""
        self.check()
        return self.pool().submit(fn, *args, **kwargs)

    def map(
        self,
        fn: Callable[[T], R],
        items: Iterable[T],
        *,
        name: str = "main",
        workers: int | None = None,
    ) -> list[R]:
        """有序 `map`：结果顺序 == `items` 顺序（本仓所有并行扇出都靠这个保序）。

        取消语义：开投之前 + 每投一个之前各 `check()` 一次；一旦置位，**已投的取消、
        已开跑的不打断**（Python 线程杀不掉，等它们自然收尾），然后抛 `RuntimeCancelledError`。
        """
        seq = list(items)
        if not seq:
            return []
        self.check()
        ex = self.pool(name, workers=workers)
        futs: list[Future[R]] = []
        try:
            for item in seq:
                self.check()
                futs.append(ex.submit(fn, item))
        finally:
            if self._stop.is_set():
                for f in futs:
                    f.cancel()
        return [f.result() for f in futs]

    # ── 读数与收尾 ───────────────────────────────────────────────────────────

    def describe(self) -> dict[str, object]:
        """一行读数：`over=True` 就是「这个域超订了」，之前没有任何地方能看见它。"""
        with self._lock:
            pools = dict(self._sizes)
            used = sum(pools.values())
            return {
                "scope": self.scope,
                "ceiling": self.ceiling,
                "workers": used,
                "over": used > self.ceiling,
                "pools": pools,
                # 本域起过的裸线程：名字 → 还活不活（`close()` 之后再问就是空表）。
                "threads": {t.name: t.is_alive() for t in self._threads},
                "stopped": self._stop.is_set(),
                "closed": self._closed,
                # 读数：这一轮用的是不是被测试换过的时钟（`time.time/time.monotonic` = 真钟）。
                "clock": f"{_callable_name(self.clock)}/{_callable_name(self.monotonic)}",
            }

    def close(self, *, wait: bool = True) -> None:
        """关掉本域全部池（幂等）。`wait=False` 只给「进程马上要退」的场合。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pools = list(self._pools.values())
            self._pools.clear()
            self._sizes.clear()
            cancel = self._stop.is_set()
        for ex in pools:
            ex.shutdown(wait=wait, cancel_futures=cancel)
        if wait:
            # 裸线程不能取消（Python 线程杀不掉），只能等 —— 与池的「正常退出不取消排队活」同一口径。
            self.join_threads()

    def __enter__(self) -> Runtime:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
