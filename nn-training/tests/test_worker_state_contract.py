"""`remote/worker.py` 的**模块级状态契约**（2026-09-23，S4 拆 worker 前的安全网）。

`remote/worker.py` 有 ~3450 行 / 68 个顶层函数，是 S4 剩下的两个神模块之一。它要拆的
**不是**类，而是一整片顶层函数——而顶层函数与「模块级可变状态」是同生共死的：

> 把函数搬到别的模块，它们读的就是**那个模块的**全局；于是 `worker._WIRE` / `worker._BULK`
> 变成两份互不相干的账 —— 测试里那些 `W._WIRE.clear()` / `W._BULK.reset()` 照旧绿，
> 但**行为已经错了**（本仓 S4 前两步的教训：搬代码 = 换命名空间，模块全局是唯一会
> 静默坏掉的东西）。

**实测清点（AST，①③ 的判据就是它）**——真·模块级可变状态共 6 处，按**宿主**分开钉：

| 名字 | 形态 | 宿主 | 谁改 | 测试可见 |
|---|---|---|---|---|
| `_opener` | 懒建单例（`global _opener`） | `worker` | `_get_opener` | 否 |
| `_ACTIVE_CODE_SHA` | `str \\\\| None`（会话代码指纹） | `worker` | `run_job`（`global`） | 间接（`test_remote_hotswap` 测异常语义） |
| `_POLL_WARN_AT` | `dict[str, float]`（告警节流 60s） | `worker` | `_warn_non_200` | 否（本文件补上） |
| `_BEST_RATE` | float（`global _BEST_RATE`） | **`wire`** | `_note_rate` | **是**（`test_wire_reroll` 重置它） |
| `_WIRE` | `dict[str, dict]`（每 job 传输账） | **`wire`** | `_wire_bucket` / `_wire_flush` | **是**（多文件直接 `.clear()` 并断言封顶） |
| `_BULK` | `BulkScheduler()` **实例** | **`wire`** | `set_bulk_log` / `pace` / `slot` / `control` / `reset` | **是**（直接 `.reset()` / `.inflight()`） |

**2026-09-23（S4 第四步）**：wire / 低速重抽 / bulk 节流整簇搬进 `remote/wire.py`，
**状态随簇搬迁**（顶层函数与模块全局同生共死）——`wire` 是那三份状态的**唯一所有者**，
`worker` 只做 `from remote.wire import … as …` 的**显式转发**。于是：

* 转发名与宿主是**同一个对象**（本文件 `test_worker_reexports_are_the_same_objects` 钉住）；
* `_WIRE` / `_BULK` 是原地可变 ⇒ `.clear()` / `.reset()` 从哪个入口进都一样；
* `_BEST_RATE` 是**重绑式**标量 ⇒ 注入点**必须**是 `remote.wire._BEST_RATE`
  （从 `worker` 重绑只改转发名，`_min_rate` 读不到）。

三类断言各司其职：**① 清点不许漂移**（拆走 / 新增状态都必须过这里）· **② 别处不许有自己的副本**
（防「两个 worker 各记一份账」）· **③ 状态行为可复现**（同一序列跑两遍逐字段相同 + 此前无覆盖的节流）。

注意 `remote/tailscale_boot.py` 里也有个 `_BEST_RATE`：那是**独立引导模块**（从 GitHub raw
单独拉取，见 `common/__init__.py` 的结构性豁免），与 worker 的这份**不是同一个变量**，
各有各的会话——别顺手合并。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.wire as wire_mod
import remote.worker as W
from remote.bulk_sched import BulkScheduler

WORKER = ROOT / "remote" / "worker.py"
WIRE = ROOT / "remote" / "wire.py"

#: 顶层**可变容器字面量**绑定（拆走或新增都必须改这里——这是有意的手工清单），按宿主分。
WORKER_MUTABLE_CONTAINERS = {"_POLL_WARN_AT"}
WIRE_MUTABLE_CONTAINERS = {"_WIRE"}
#: 函数内以 `global` **重绑**的脚本单例（懒建 / 会话级标量），按宿主分。
WORKER_GLOBAL_REBOUND = {"_opener", "_ACTIVE_CODE_SHA"}
WIRE_GLOBAL_REBOUND = {"_BEST_RATE"}
#: 顶层构造的**有状态对象**实例（这里是调度器）。
WIRE_STATEFUL_INSTANCES = {"_BULK"}
#: 结构性豁免：独立引导模块里**同名但不同物**的变量。
STANDALONE_BOOT_EXEMPT = {"remote/tailscale_boot.py": {"_BEST_RATE"}}

_ALL_WATCHED = (
    WORKER_MUTABLE_CONTAINERS
    | WIRE_MUTABLE_CONTAINERS
    | WORKER_GLOBAL_REBOUND
    | WIRE_GLOBAL_REBOUND
    | WIRE_STATEFUL_INSTANCES
)
#: 状态的宿主文件——扫描「别处副本」时跳过它们自己。
OWNERS = {"remote/worker.py", "remote/wire.py"}


@pytest.fixture(autouse=True)
def _restore_worker_state():
    """本文件动的是**跨用例共享**的模块状态——前后都恢复，别污染同一 xdist worker 的其它用例。"""
    best, sha = wire_mod._BEST_RATE, W._ACTIVE_CODE_SHA
    wire, poll = dict(wire_mod._WIRE), dict(W._POLL_WARN_AT)
    wire_mod._BULK.reset()
    yield
    wire_mod._BEST_RATE, W._ACTIVE_CODE_SHA = best, sha
    wire_mod._WIRE.clear()
    wire_mod._WIRE.update(wire)
    W._POLL_WARN_AT.clear()
    W._POLL_WARN_AT.update(poll)
    wire_mod._BULK.reset()


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _top_level_bindings(path: Path) -> dict[str, ast.expr | None]:
    out: dict[str, ast.expr | None] = {}
    for node in _tree(path).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out[node.target.id] = node.value
    return out


def _global_declared(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for stmt in node.body:
                if isinstance(stmt, ast.Global):
                    out.update(stmt.names)
    return out


# ────────────────────────── ① 清点不许漂移 ──────────────────────────


def test_worker_mutable_container_bindings_are_exactly_the_pinned_set() -> None:
    """`worker.py` 顶层可变容器字面量只剩 `_POLL_WARN_AT`（`_WIRE` 已随簇迁 `wire`）。"""
    found = {
        name
        for name, value in _top_level_bindings(WORKER).items()
        if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set))
    }
    assert found == WORKER_MUTABLE_CONTAINERS, (
        "worker 顶部可变容器清单变了：\n"
        f"  新增/未登记：{sorted(found - WORKER_MUTABLE_CONTAINERS)}\n"
        f"  已消失：{sorted(WORKER_MUTABLE_CONTAINERS - found)}\n"
        "拆分 worker 时请同步本清单：新增 = 新的共享点；消失 = 先确认没人再依赖它"
    )


def test_wire_mutable_container_bindings_are_exactly_the_pinned_set() -> None:
    """`wire.py` 顶层可变容器字面量只有 `_WIRE`——它是这份状态的**唯一宿主**。"""
    found = {
        name
        for name, value in _top_level_bindings(WIRE).items()
        if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set))
    }
    assert found == WIRE_MUTABLE_CONTAINERS, (
        "wire 顶部可变容器清单变了（多一份账 = 静默的读数损失）：\n"
        f"  新增/未登记：{sorted(found - WIRE_MUTABLE_CONTAINERS)}\n"
        f"  已消失：{sorted(WIRE_MUTABLE_CONTAINERS - found)}"
    )


def test_global_rebound_singletons_are_exactly_the_pinned_set() -> None:
    """函数内以 `global` 重绑的脚本单例按宿主各归各位（懒建 opener / 会话最好速率 / 代码指纹）。"""
    assert _global_declared(WORKER) == WORKER_GLOBAL_REBOUND
    assert _global_declared(WIRE) == WIRE_GLOBAL_REBOUND


def test_bulk_is_a_stateful_scheduler_instance_built_at_module_level() -> None:
    """`_BULK` 是 `wire.py` 顶层构造的实例（不是按需 new）——共享性就是它的语义。"""
    value = _top_level_bindings(WIRE)["_BULK"]
    assert isinstance(value, ast.Call)
    assert ast.unparse(value.func) == "BulkScheduler"
    assert isinstance(wire_mod._BULK, BulkScheduler)


def test_pinned_state_names_are_all_really_loaded_by_their_host_code() -> None:
    """清单里每个名字都必须**真的**被**宿主**代码读到（防「清单变僵尸条目」）。"""

    def loaded(path: Path) -> set[str]:
        return {
            n.id
            for n in ast.walk(_tree(path))
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }

    worker_watched = (
        WORKER_MUTABLE_CONTAINERS | WORKER_GLOBAL_REBOUND | set()
    )
    wire_watched = (
        WIRE_MUTABLE_CONTAINERS | WIRE_GLOBAL_REBOUND | WIRE_STATEFUL_INSTANCES
    )
    assert sorted(n for n in worker_watched if n not in loaded(WORKER)) == []
    assert sorted(n for n in wire_watched if n not in loaded(WIRE)) == []


def test_worker_reexports_are_the_same_objects() -> None:
    """**原地可变**的转发必须是同一对象（不是各建一份）——这是「两份账」故障的正面判据。

    只对 `_WIRE` / `_BULK` 成立：它们是**原地可变**（`.clear()` / `.reset()`），
    转发名永远指向同一个对象。

    `_BEST_RATE` **故意不在其列**：它是 `global` **重绑式**标量，float 不可变 ⇒ 重绑会
    换对象，转发的名字会停在旧值（这正是它的注入点必须是 `remote.wire._BEST_RATE` 的
    原因；见 `tests/test_wire_split.py::test_note_rate_rebinding_happens_on_the_wire_module`）。
    """
    for name in WIRE_MUTABLE_CONTAINERS | WIRE_STATEFUL_INSTANCES:
        assert name in vars(W), f"{name} 不在 remote.worker 的命名空间里"
        assert vars(W)[name] is vars(wire_mod)[name], (
            f"remote.worker.{name} 与 remote.wire.{name} 不是同一对象"
            "（转发成了副本 ⇒ 两份账/两个调度器）"
        )
    assert "_BEST_RATE" in vars(W) and "_BEST_RATE" in vars(wire_mod)


# ────────────────────────── ② 别处不许有自己的副本 ──────────────────────────


def test_no_other_module_owns_a_copy_of_the_worker_state() -> None:
    """同一份状态不许在别处再绑一次（否则拆出来就是**两份账**，而且很难发现）。

    `remote/tailscale_boot.py` 的同名 `_BEST_RATE` 是**结构性豁免**：那个模块要从
    GitHub raw 单独拉取、cell 侧在拿到 `code.zip` 之前就要 import 它，因此不得与
    worker 共享任何东西（见 `common/__init__.py` 的层契约）。
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "remote").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in OWNERS:
            continue
        exempt = STANDALONE_BOOT_EXEMPT.get(rel, set())
        offenders += [
            f"{rel}::{name}"
            for name in sorted(_top_level_bindings(path))
            if name in _ALL_WATCHED and name not in exempt
        ]
    assert offenders == [], (
        "这些模块各自绑了一份 worker 的模块级状态（拆分会变成两份账）：\n  "
        + "\n  ".join(offenders)
    )


def test_state_names_are_addressable_on_the_worker_module() -> None:
    """拆分后的**注入点**必须仍然存在：测试靠 `vars(worker)` 拿这些名字，不靠 `__all__`。"""
    for name in _ALL_WATCHED:
        assert name in vars(W), f"{name} 不在 remote.worker 的命名空间里"


# ────────────────────────── ③ 状态行为可复现 ──────────────────────────


def test_wire_buckets_land_in_the_module_dict_and_are_capped() -> None:
    """`_wire_bucket` 写进的是**模块**那份 `_WIRE`（不是本地账），且未 flush 的 job 数封顶。"""
    wire_mod._WIRE.clear()
    w = wire_mod._wire_bucket("job-a")
    assert wire_mod._WIRE["job-a"] is w, "函数与模块必须是同一份账（拆成分模块就断在这里）"
    assert W._WIRE["job-a"] is w, "worker 的转发名也必须指向这份账"
    for i in range(wire_mod.WIRE_MAX_JOBS + 5):
        wire_mod._wire_bucket(f"job-{i}")
    assert len(wire_mod._WIRE) <= wire_mod.WIRE_MAX_JOBS
    assert "job-a" not in wire_mod._WIRE, "最旧的账应先被丢掉"


def test_wire_bucket_snapshots_the_module_scheduler() -> None:
    """新建的传输账里 `sched0` 取自**模块那份** `_BULK`——两者必须是同一个调度器。

    这条是「拆成子模块后各拿一个 `BulkScheduler()`」的探测器：那样 `sched0`
    会取自另一个实例，`_wire_flush` 里的增量（`s1 - s0`）就永久失真。
    """
    wire_mod._BULK.reset()
    wire_mod._WIRE.clear()
    assert wire_mod._wire_bucket("job-sched")["sched0"] == wire_mod._BULK.stats()
    assert wire_mod._BULK is W._BULK


def test_wire_flush_line_is_identical_across_two_identical_runs() -> None:
    """**同序列跑两遍、逐字节相同**（拆分口径的对账基础：传输账不许带上隐藏状态）。"""

    def once() -> str:
        wire_mod._WIRE.clear()
        wire_mod._BULK.reset()
        wire_mod._wire_start("j1")
        wire_mod._wire_add("j1", "payload", 2 * 1024 * 1024, 4.0)
        wire_mod._wire_add("j1", "result", 512 * 1024, 1.0)
        wire_mod._wire_time("j1", "ppo", 3.0)
        wire_mod._wire_hit("j1", "code")
        wire_mod._wire_note_reroll("j1", 256 * 1024)
        wire_mod._WIRE["j1"]["t0"] = 1000.0
        lines: list[str] = []
        wire_mod._wire_flush("j1", lines.append, wall_end=1010.0)
        assert len(lines) == 1
        return lines[0]

    assert once() == once()
    line = once()
    for token in ("job j1: wire", "payload=", "result=", "code=cache-hit", "reroll=1", "phases"):
        assert token in line, token
    assert wire_mod._WIRE == {}, "flush 必须把账清掉（否则下轮会串账）"


def test_note_rate_is_session_stateful_and_reproducible() -> None:
    """同一序列跑两遍逐字段相同；小 body 不参与采样；更慢的速率不覆盖本会话最好值。"""

    def once() -> tuple[float, float]:
        wire_mod._BEST_RATE = 0.0
        wire_mod._note_rate(rate=500.0, nbytes=wire_mod.WIRE_RATE_SAMPLE_MIN_BYTES - 1)
        assert wire_mod._BEST_RATE == 0.0  # 小 body：不采样
        wire_mod._note_rate(rate=500.0, nbytes=wire_mod.WIRE_RATE_SAMPLE_MIN_BYTES)
        wire_mod._note_rate(rate=100.0, nbytes=wire_mod.WIRE_RATE_SAMPLE_MIN_BYTES)  # 更慢：不覆盖
        return wire_mod._BEST_RATE, wire_mod._min_rate()

    first, second = once(), once()
    assert first == second == (500.0, max(wire_mod.WIRE_MIN_RATE, 125.0))


def test_poll_warn_throttles_per_url_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """（此前零覆盖）非 200 告警按 `base_url:status` 每 60s 最多一条；401/403 带封禁提示。"""
    clock = {"now": 1000.0}
    monkeypatch.setattr(W.time, "time", lambda: clock["now"])
    W._POLL_WARN_AT.clear()
    lines: list[str] = []
    log = lines.append

    W._warn_non_200("http://hub", 403, log)
    assert len(lines) == 1 and "封禁" in lines[0]
    W._warn_non_200("http://hub", 403, log)  # 同 (url, status) 且在 60s 内
    assert len(lines) == 1
    W._warn_non_200("http://hub", 500, log)  # 不同 status ⇒ 另算一条
    assert len(lines) == 2
    clock["now"] += 61.0
    W._warn_non_200("http://hub", 403, log)  # 过窗 ⇒ 再报
    assert len(lines) == 3
    assert set(W._POLL_WARN_AT) == {"http://hub:403", "http://hub:500"}


def test_poll_warn_without_log_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """`log=None` 直接返回，且**不**占用节流窗（否则一次静默调用会吃掉一分钟的告警）。"""
    monkeypatch.setattr(W.time, "time", lambda: 5000.0)
    W._POLL_WARN_AT.clear()
    W._warn_non_200("http://hub", 403, None)
    assert W._POLL_WARN_AT == {}


def test_state_shapes_are_pinned() -> None:
    """运行期形状（拆分后逐字段对账的口径）：类型 + `_opener` 的懒建不变量。"""
    assert isinstance(wire_mod._WIRE, dict)
    assert isinstance(W._POLL_WARN_AT, dict)
    assert isinstance(wire_mod._BEST_RATE, float) and wire_mod._BEST_RATE >= 0.0
    assert W._ACTIVE_CODE_SHA is None or isinstance(W._ACTIVE_CODE_SHA, str)
    assert isinstance(wire_mod._BULK, BulkScheduler)
    assert wire_mod._BULK.inflight() == 0
    assert W._opener is None or hasattr(W._opener, "open")
