"""R2d：引擎池（`rl/engine_pool.py`）—— 惰性构建 / LRU / 两道上限 / 响亮驱逐。

这些性质是「单进程多课程」的内存契约（plan/r2-loop-task-queue §6.2 的实测表）：
每课栈是 MB 级、驱逐的代价是 **Adam 动量重置**，所以策略必须是「尽量不发生 + 一旦发生
就可见 + 绝不驱逐在用引擎」。测试用假引擎 + 假 logger ⇒ 不碰 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.engine_pool import MEASURED_COURSE_STACK_MB, EnginePool


class FakeEngine:
    """最小引擎替身：只提供 `release_torch`（池的默认释放钩子）。"""

    def __init__(self, course: str) -> None:
        self.course = course
        self.released = 0

    def release_torch(self) -> None:
        self.released += 1


def _pool(**kw: object) -> tuple[EnginePool, list[str], list[str]]:
    built: list[str] = []
    lines: list[str] = []

    def factory(course: str) -> FakeEngine:
        built.append(course)
        return FakeEngine(course)

    kw.setdefault("courses", 5)
    kw.setdefault("mb", 1000.0)
    pool = EnginePool(factory=factory, logger=lines.append, **kw)  # type: ignore[arg-type]
    return pool, built, lines


def test_lazy_build_then_hit() -> None:
    pool, built, _ = _pool()
    a1 = pool.get("a")
    a2 = pool.get("a")
    assert a1 is a2
    assert built == ["a"]  # 惰性 + 命中不重建
    assert pool.stats["builds"] == 1
    assert pool.stats["hits"] == 1
    assert pool.peek("b") is None  # peek 不构建
    assert built == ["a"]


def test_course_cap_evicts_lru_not_current() -> None:
    """容量=2：a 被 b 挤掉；再取 a 后，淘汰的应是**最久没用**的 b（而不是刚用过的 a）。"""
    pool, built, lines = _pool(courses=2)
    a = pool.get("a")
    pool.get("b")
    pool.get("a")  # a 变成最近使用
    pool.get("c")  # 越界 → 淘汰 b
    assert pool.loaded() == ["a", "c"]
    assert a.released == 0  # ★ 绝不驱逐正在用的
    assert pool.stats["evictions"] == 1
    evicted = [e for e in lines if "驱逐课程" in e]
    assert len(evicted) == 1 and "b" in evicted[0]
    # 代价必须写在日志里（权重可复原、动量重置 + 调大上限的建议）
    assert "Adam 动量重置" in evicted[0]
    assert "调大缓存上限" in evicted[0]


def test_course_cap_one_never_evicts_the_only_entry() -> None:
    """容量=1：只剩当前这门课时**不驱逐**（缓存不再增长，但也不会中途抽走栈）。"""
    pool, built, lines = _pool(courses=1)
    pool.get("a")
    assert pool.stats["evictions"] == 0
    assert pool.loaded() == ["a"]
    assert [ln for ln in lines if "驱逐课程" in ln] == []


def test_byte_cap_is_the_second_fence() -> None:
    """课程数够宽时，字节预算是唯一上限：2MB/课 × 上限 4MB ⇒ 只留 2 门。"""
    pool, _built, _ = _pool(courses=0, size_mb=2.0, mb=4.0)  # 0 = 不限课程数
    for c in ("a", "b", "c"):
        pool.get(c)
    assert pool.loaded() == ["b", "c"]
    assert pool.stats["evictions"] == 1
    assert pool.used_mb() == 4.0


def test_over_budget_keeps_current_and_is_loud() -> None:
    """单课就超预算 ⇒ 响亮记录一次，但**不驱逐在用引擎**（也不能死循环）。"""
    pool, _built, lines = _pool(courses=5, size_mb=2.0, mb=1.0)
    pool.get("a")
    assert pool.loaded() == ["a"]
    assert pool.stats["evictions"] == 0
    assert pool.stats["over_budget"] == 1
    assert any("超预算仍保留" in ln and "不驱逐在用引擎" in ln for ln in lines)


def test_release_hook_override_and_drop_missing() -> None:
    calls: list[tuple[str, str]] = []
    pool, _built, _ = _pool(courses=1, release=lambda c, e: calls.append((c, e.course)))
    pool.get("a")
    pool.get("b")  # 驱逐 a
    assert calls == [("a", "a")]
    assert pool.drop("nope") is False  # 不在缓存里 = 不是驱逐


def test_close_releases_everything() -> None:
    pool, _built, _ = _pool()
    a, b = pool.get("a"), pool.get("b")
    assert pool.snapshot()["loaded"] == ["a", "b"]
    pool.close()
    assert pool.loaded() == []
    assert (a.released, b.released) == (1, 1)
    assert pool.snapshot()["cap_mb"] == 1000.0


def test_snapshot_reports_the_measured_stack_size() -> None:
    pool, _built, _ = _pool(courses=5, mb=256.0)
    pool.get("a")
    snap = pool.snapshot()
    assert snap["used_mb"] == MEASURED_COURSE_STACK_MB
    assert snap["cap_courses"] == 5 and snap["builds"] == 1 and snap["misses"] == 1


def test_release_failure_is_swallowed() -> None:
    """释放钩子抛错不得让池崩（缓存条目已移除，GC 仍会收对象）。"""
    lines: list[str] = []
    pool = EnginePool(
        factory=FakeEngine,
        courses=1,
        mb=1000.0,
        release=lambda c, e: (_ for _ in ()).throw(RuntimeError("boom")),
        logger=lines.append,
    )
    pool.get("a")
    pool.get("b")
    assert pool.loaded() == ["b"]
    assert any("释放 a 的 torch 栈失败" in ln for ln in lines)
