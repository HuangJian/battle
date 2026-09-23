"""`remote/worker.py` 的**模块级状态契约**（2026-09-23，S4 拆 worker 前的安全网）。

`remote/worker.py` 有 ~3450 行 / 68 个顶层函数，是 S4 剩下的两个神模块之一。它要拆的
**不是**类，而是一整片顶层函数——而顶层函数与「模块级可变状态」是同生共死的：

> 把函数搬到 `remote/worker/wire.py`，它们读的就是 **`worker.wire` 的**模块全局；
> 于是 `worker._WIRE` / `worker._BULK` 变成两份互不相干的账 —— 测试里那些
> `W._WIRE.clear()` / `W._BULK.reset()` 照旧绿，但**行为已经错了**（本仓 S4 前两步的
> 教训：搬代码 = 换命名空间，模块全局是唯一会静默坏掉的东西）。

**实测清点（AST，①③ 的判据就是它）**——真·模块级可变状态共 6 处：

| 名字 | 形态 | 谁改 | 测试可见 |
|---|---|---|---|
| `_opener` | 懒建单例（`global _opener`） | `_get_opener` | 否 |
| `_BEST_RATE` | float（`global _BEST_RATE`） | `_note_rate` | **是**（`test_wire_reroll` / `test_boot_wire_guard` 重置它） |
| `_WIRE` | `dict[str, dict]`（每 job 传输账） | `_wire_bucket` / `_wire_flush` | **是**（多文件直接 `.clear()` 并断言封顶） |
| `_BULK` | `BulkScheduler()` **实例**（`remote/bulk_sched.py`） | `set_bulk_log` / `pace` / `slot` / `control` / `reset` | **是**（直接 `.reset()` / `.inflight()`） |
| `_POLL_WARN_AT` | `dict[str, float]`（告警节流 60s） | `_warn_non_200` | 否（**此前零覆盖**，本文件补上） |
| `_ACTIVE_CODE_SHA` | `str \\| None`（本会话的代码指纹） | `run_job`（`global`，定义在文件后段） | 间接（`test_remote_hotswap` 测异常语义；其**行为**在那里覆盖，本文件只钉状态形状） |

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

import remote.worker as W
from remote.bulk_sched import BulkScheduler

WORKER = ROOT / "remote" / "worker.py"

#: 顶层**可变容器字面量**绑定（拆走或新增都必须改这里——这是有意的手工清单）。
MUTABLE_CONTAINER_BINDINGS = {"_WIRE", "_POLL_WARN_AT"}
#: 函数内以 `global` **重绑**的脚本单例（懒建 / 会话级标量）。
GLOBAL_REBOUND = {"_opener", "_BEST_RATE", "_ACTIVE_CODE_SHA"}
#: 顶层构造的**有状态对象**实例（这里是调度器）。
STATEFUL_INSTANCES = {"_BULK"}
#: 结构性豁免：独立引导模块里**同名但不同物**的变量。
STANDALONE_BOOT_EXEMPT = {"remote/tailscale_boot.py": {"_BEST_RATE"}}


@pytest.fixture(autouse=True)
def _restore_worker_state():
    """本文件动的是**跨用例共享**的模块状态——前后都恢复，别污染同一 xdist worker 的其它用例。"""
    best, sha = W._BEST_RATE, W._ACTIVE_CODE_SHA
    wire, poll = dict(W._WIRE), dict(W._POLL_WARN_AT)
    W._BULK.reset()
    yield
    W._BEST_RATE, W._ACTIVE_CODE_SHA = best, sha
    W._WIRE.clear()
    W._WIRE.update(wire)
    W._POLL_WARN_AT.clear()
    W._POLL_WARN_AT.update(poll)
    W._BULK.reset()


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


def test_mutable_container_bindings_are_exactly_the_pinned_set() -> None:
    """顶层可变容器字面量只有 `_WIRE` / `_POLL_WARN_AT`——多一个少一个都要过这里。"""
    found = {
        name
        for name, value in _top_level_bindings(WORKER).items()
        if isinstance(value, (ast.Dict, ast.List, ast.Set))
    }
    assert found == MUTABLE_CONTAINER_BINDINGS, (
        "顶部可变容器清单变了：\n"
        f"  新增/未登记：{sorted(found - MUTABLE_CONTAINER_BINDINGS)}\n"
        f"  已消失：{sorted(MUTABLE_CONTAINER_BINDINGS - found)}\n"
        "拆分 worker 时请同步本清单：新增 = 新的共享点；消失 = 先确认没人再依赖它"
    )


def test_global_rebound_singletons_are_exactly_the_pinned_set() -> None:
    """函数内以 `global` 重绑的脚本单例只有 3 个（懒建 opener / 会话最好速率 / 代码指纹）。"""
    assert _global_declared(WORKER) == GLOBAL_REBOUND


def test_bulk_is_a_stateful_scheduler_instance_built_at_module_level() -> None:
    """`_BULK` 是**顶层构造的实例**（不是按需 new）——共享性就是它的语义。"""
    value = _top_level_bindings(WORKER)["_BULK"]
    assert isinstance(value, ast.Call)
    assert ast.unparse(value.func) == "BulkScheduler"
    assert isinstance(W._BULK, BulkScheduler)


def test_pinned_state_names_are_all_really_loaded_by_module_code() -> None:
    """清单里每个名字都必须**真的**被读到（防「清单变僵尸条目」，尤其 `_ACTIVE_CODE_SHA`
    定义在 2757 行却被 2141 行的 `run_job` 用——定义与使用离得很远，正是拆分最易漏的一类）。"""
    loaded = {
        n.id
        for n in ast.walk(_tree(WORKER))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    watched = MUTABLE_CONTAINER_BINDINGS | GLOBAL_REBOUND | STATEFUL_INSTANCES
    assert sorted(n for n in watched if n not in loaded) == []


# ────────────────────────── ② 别处不许有自己的副本 ──────────────────────────


def test_no_other_module_owns_a_copy_of_the_worker_state() -> None:
    """同一份状态不许在别处再绑一次（否则拆出来就是**两份账**，而且很难发现）。

    `remote/tailscale_boot.py` 的同名 `_BEST_RATE` 是**结构性豁免**：那个模块要从
    GitHub raw 单独拉取、cell 侧在拿到 `code.zip` 之前就要 import 它，因此不得与
    worker 共享任何东西（见 `common/__init__.py` 的层契约）。
    """
    watched = MUTABLE_CONTAINER_BINDINGS | GLOBAL_REBOUND | STATEFUL_INSTANCES
    offenders: list[str] = []
    for path in sorted((ROOT / "remote").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "remote/worker.py":
            continue
        exempt = STANDALONE_BOOT_EXEMPT.get(rel, set())
        offenders += [
            f"{rel}::{name}"
            for name in sorted(_top_level_bindings(path))
            if name in watched and name not in exempt
        ]
    assert offenders == [], (
        "这些模块各自绑了一份 worker 的模块级状态（拆分会变成两份账）：\n  "
        + "\n  ".join(offenders)
    )


def test_state_names_are_addressable_on_the_module() -> None:
    """拆分后的**注入点**必须仍然存在：测试靠 `vars(worker)` 拿这些名字，不靠 `__all__`。"""
    for name in MUTABLE_CONTAINER_BINDINGS | GLOBAL_REBOUND | STATEFUL_INSTANCES:
        assert name in vars(W), f"{name} 不在 remote.worker 的命名空间里"


# ────────────────────────── ③ 状态行为可复现 ──────────────────────────


def test_wire_buckets_land_in_the_module_dict_and_are_capped() -> None:
    """`_wire_bucket` 写进的是**模块**那份 `_WIRE`（不是本地账），且未 flush 的 job 数封顶。"""
    W._WIRE.clear()
    w = W._wire_bucket("job-a")
    assert W._WIRE["job-a"] is w, "函数与模块必须是同一份账（拆成分模块就断在这里）"
    for i in range(W.WIRE_MAX_JOBS + 5):
        W._wire_bucket(f"job-{i}")
    assert len(W._WIRE) <= W.WIRE_MAX_JOBS
    assert "job-a" not in W._WIRE, "最旧的账应先被丢掉"


def test_wire_bucket_snapshots_the_module_scheduler() -> None:
    """新建的传输账里 `sched0` 取自**模块那份** `_BULK`——两者必须是同一个调度器。

    这条是「拆成 `worker/wire.py` 后各拿一个 `BulkScheduler()`」的探测器：那样 `sched0`
    会取自另一个实例，`_wire_flush` 里的增量（`s1 - s0`）就永久失真。
    """
    W._BULK.reset()
    W._WIRE.clear()
    assert W._wire_bucket("job-sched")["sched0"] == W._BULK.stats()


def test_wire_flush_line_is_identical_across_two_identical_runs() -> None:
    """**同序列跑两遍、逐字节相同**（拆分口径的对账基础：传输账不许带上隐藏状态）。"""
    def once() -> str:
        W._WIRE.clear()
        W._BULK.reset()
        W._wire_start("j1")
        W._wire_add("j1", "payload", 2 * 1024 * 1024, 4.0)
        W._wire_add("j1", "result", 512 * 1024, 1.0)
        W._wire_time("j1", "ppo", 3.0)
        W._wire_hit("j1", "code")
        W._wire_note_reroll("j1", 256 * 1024)
        W._WIRE["j1"]["t0"] = 1000.0
        lines: list[str] = []
        W._wire_flush("j1", lines.append, wall_end=1010.0)
        assert len(lines) == 1
        return lines[0]

    assert once() == once()
    line = once()
    for token in ("job j1: wire", "payload=", "result=", "code=cache-hit", "reroll=1", "phases"):
        assert token in line, token
    assert W._WIRE == {}, "flush 必须把账清掉（否则下轮会串账）"


def test_note_rate_is_session_stateful_and_reproducible() -> None:
    """同一序列跑两遍逐字段相同；小 body 不参与采样；更慢的速率不覆盖本会话最好值。"""
    def once() -> tuple[float, float]:
        W._BEST_RATE = 0.0
        W._note_rate(rate=500.0, nbytes=W.WIRE_RATE_SAMPLE_MIN_BYTES - 1)  # 小 body：不采样
        assert W._BEST_RATE == 0.0
        W._note_rate(rate=500.0, nbytes=W.WIRE_RATE_SAMPLE_MIN_BYTES)
        W._note_rate(rate=100.0, nbytes=W.WIRE_RATE_SAMPLE_MIN_BYTES)  # 更慢：不覆盖
        return W._BEST_RATE, W._min_rate()

    first, second = once(), once()
    assert first == second == (500.0, max(W.WIRE_MIN_RATE, 125.0))


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
    assert isinstance(W._WIRE, dict)
    assert isinstance(W._POLL_WARN_AT, dict)
    assert isinstance(W._BEST_RATE, float) and W._BEST_RATE >= 0.0
    assert W._ACTIVE_CODE_SHA is None or isinstance(W._ACTIVE_CODE_SHA, str)
    assert isinstance(W._BULK, BulkScheduler)
    assert W._BULK.inflight() == 0
    assert W._opener is None or hasattr(W._opener, "open")
