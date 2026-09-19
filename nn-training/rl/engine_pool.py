"""engine_pool —— 单进程多课程共享的引擎（torch 栈）持有者（R2c-3 前置，plan §6.2）。

**为什么缓存的是 torch 栈而不是权重文件**：R2c-3 §6.1 实测（`scripts/measure_checkpoint_rss.py`）
每课栈 = 1.3–1.8MB（model+grads+Adam+refs），而与课程数无关的 torch 基线 ≈294MB 只随**进程**
存在一次 ⇒ 单进程 N 课的约束是**数量**（torch 线程/句柄/池），不是字节。缓存住栈 = 同一份
权重不必反复从盘上读（用户 2026-09-18 口径：「每个课程缓存一个最新 checkpoint 在内存里，
如果缓存有就不再读硬盘」）。

**驱逐的代价不对称，所以策略是「响亮、可见、尽量不发生」**：

- `TrainingLoop._ensure_local_ppo_stack()` 能从盘上重建权重（`build_model(args.bc, args.out)`，
  `args.out` 每轮都写），**但 `torch.optim.Adam` 是新建的** —— 动量/二阶矩**丢了**（用户
  同一轮口径：「动量丢了就得重训」）。
- 因此容量默认 = 并行课程上限（`DEFAULT_CACHE_COURSES`），正常路线上永不驱逐；
  `DEFAULT_CACHE_MB = 256MB` ≈ 130 课只是**第二道保险**；
- 一旦越界就记一行「驱逐了谁 + 重建代价」，**绝不静默滑过去**，且**绝不驱逐正在用的引擎**
  （任务中途把栈抽走会让 `_serial_ppo` 撞 `None.load_episodes`）。

纯逻辑（无 torch / 无 IO）：`factory` 与 `release` 都是注入的接缝 ⇒ 可单测。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rl.log import log

#: 每课 torch 栈的实测增量（MB，R2c-3 §6.1）：model + grads + Adam + refs。
#: 只用于**预算换算**（真实内存由分配器持有，这里做的是保守的上界估计）。
MEASURED_COURSE_STACK_MB = 2.0

#: 缓存课程数上限默认 = 并行课程上限（plan §6.2 定案）。超了才驱逐 —— 正常永不。
DEFAULT_CACHE_COURSES = 5

#: 字节上限默认（MB，plan §6.2 定案）：≈130 课，正常永不触发；真触发就该被看见。
DEFAULT_CACHE_MB = 256.0


@dataclass
class EnginePool:
    """课程 → 引擎（`TrainingLoop`）的 LRU 缓存：惰性构建 + 两道上限 + 响亮驱逐。

    `factory(course) -> engine`：构建一门课的引擎（**必须廉价**：不碰 torch，栈由引擎自己的
    `_setup()` 在第一次执行时拉起 —— 否则「扫到但没在训」的课也会烧内存）。
    `release(course, engine)`：驱逐时的释放钩子；缺省调 `engine.release_torch()`。
    `size_mb`：单课栈的字节估算（默认取实测常数；真机可用 RSS 实测覆盖）。
    """

    factory: Callable[[str], Any]
    courses: int = DEFAULT_CACHE_COURSES
    mb: float = DEFAULT_CACHE_MB
    size_mb: float = MEASURED_COURSE_STACK_MB
    release: Callable[[str, Any], None] | None = None
    logger: Callable[[str], None] = log
    _items: dict[str, Any] = field(default_factory=dict)
    #: LRU 顺序：末尾 = 最近用过（`get` 命中/新建都移到末尾）。
    _lru: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(
        default_factory=lambda: {
            "hits": 0,
            "misses": 0,
            "builds": 0,
            "evictions": 0,
            "over_budget": 0,
        }
    )

    # ------------------------------------------------------------------ 读

    def get(self, course: str) -> Any:
        """取该课引擎（没有就建）。**越界才有驱逐**，驱逐对象永远不是 `course`。"""
        eng = self._items.get(course)
        if eng is not None:
            self.stats["hits"] += 1
            self._touch(course)
            return eng
        self.stats["misses"] += 1
        eng = self.factory(course)
        self.stats["builds"] += 1
        self._items[course] = eng
        self._touch(course)
        self._evict(keep=course)
        return eng

    def peek(self, course: str) -> Any | None:
        """看缓存里有没有（不构建、不改变 LRU）—— 读面/测试用。"""
        return self._items.get(course)

    def loaded(self) -> list[str]:
        return sorted(self._items)

    def used_mb(self) -> float:
        return self.size_mb * len(self._items)

    # ---------------------------------------------------------------- 驱逐

    def drop(self, course: str, *, reason: str = "LRU") -> bool:
        """驱逐一门课（释放 torch 栈 + 移出缓存）。返回是否真的驱逐了。

        **代价记录是本方法的职责**（调用方不必再写一遍）：权重从 `args.out` 重建、Adam 动量
        重置 —— 这行日志就是「为什么这轮 PPO 的效果变差了」的答案。
        """
        eng = self._items.pop(course, None)
        if eng is None:
            return False
        if course in self._lru:
            self._lru.remove(course)
        self.stats["evictions"] += 1
        try:
            self._release_engine(course, eng)
        except Exception as e:  # 释放失败不阻断（引擎对象已不在缓存里，GC 仍会收）
            self.logger(
                f"[enginepool] 释放 {course} 的 torch 栈失败（忽略）：{type(e).__name__}: {e}"
            )
        if reason == "进程退出":
            self.logger(f"[enginepool] 释放课程 {course} 的引擎缓存（进程退出）")
        else:
            self.logger(
                f"[enginepool] 驱逐课程 {course} 的引擎缓存（{reason}）——下次用到它时按盘上权重"
                "重建 torch 栈：权重可复原，**Adam 动量重置**；若这是常事请调大缓存上限"
            )
        return True

    def close(self) -> None:
        """进程收尾：逐个驱逐（= 释放全部 torch 栈）。"""
        for course in list(self._items):
            self.drop(course, reason="进程退出")

    def _release_engine(self, course: str, eng: Any) -> None:
        if self.release is not None:
            self.release(course, eng)
            return
        fn = getattr(eng, "release_torch", None)
        if callable(fn):
            fn()

    def _touch(self, course: str) -> None:
        if course in self._lru:
            self._lru.remove(course)
        self._lru.append(course)

    def _over_budget(self) -> bool:
        if self.courses and len(self._items) > self.courses:
            return True
        return self.mb > 0 and self.used_mb() > self.mb

    def _why_over(self) -> str:
        if self.courses and len(self._items) > self.courses:
            return f"课程数超上限（{len(self._items)}>{self.courses}）"
        return f"字节超预算（{self.used_mb():.1f}MB>{self.mb}MB）"

    def _evict(self, keep: str) -> None:
        """驱逐到不越界；`keep`（正在用的那门）永不驱逐；仍有越界则响亮记录。"""
        while self._over_budget():
            victim = next((c for c in self._lru if c != keep), None)
            if victim is None:
                self.stats["over_budget"] += 1
                self.logger(
                    f"[enginepool] 超预算仍保留：{keep}（{self._why_over()}）——"
                    "上限配置过小或单课超预算；缓存不再增长，但**不驱逐在用引擎**"
                )
                return
            self.drop(victim, reason=self._why_over())

    # ---------------------------------------------------------------- 读面

    def snapshot(self) -> dict[str, Any]:
        """控制台/诊断读面（与 `Supervisor.snapshot` 同风格）。"""
        return {
            "loaded": self.loaded(),
            "used_mb": round(self.used_mb(), 2),
            "cap_courses": self.courses,
            "cap_mb": self.mb,
            **dict(self.stats),
        }
