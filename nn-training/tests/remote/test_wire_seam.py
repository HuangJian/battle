"""`remote/seams.py::WIRE` 的契约守卫（P0-8 第二代，2026-11-28）。

P0-8 的第一代样板是 `hub/seams.py::NET`（第三方边界）。这一代收的是**包内传输原语**：
`remote/http._request` / `remote/wire._wire_add` 被 `job_lifecycle` / `bc_job` 的用例
`monkeypatch.setattr(…, "_request", …)` 打了 31 处 —— patch 的前提是「知道实现叫这个名字」，
于是实现一改名，用例就得跟着改，重构只能退化成搬文件。

本文件钉四件事：

1. **不再裸调**：两个宿主里 `_request` / `_wire_add` 的**调用点为 0**（只许作为 `PortPair`
   的组装实参出现一次）；
2. **端口是调用时解析的**：`WIRE` 不许出现在函数缺省值 / 类属性 / 闭包捕获里 —— 否则
   `monkeypatch.setattr(JL, "WIRE", …)` 会静默打空（这是「patch 打偏而测试全绿」的老坑）；
3. **端口的形状是两份能力**（`request` + `add`），`PortPair` 不可变、`add` 缺省不记账；
4. **替身真的被读到**（行为断言，不是 AST 形状）：把 `WIRE` 换成「必然不联网」的替身，
   `peek_jobs` 必须返回替身的数据 —— 不联网还能拿到数据，才算注入生效。

层号也在这里钉一条：`remote/seams.py` 零仓内 import（L0）。它一旦 import `remote.http`，
就会变成 L3、与 `job_lifecycle` 同秩，层账本（`tests/test_remote_dag.py`）当场红。
"""

from __future__ import annotations

import ast
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.bc_job as bc_mod
import remote.job_lifecycle as jl_mod
from remote.seams import PortPair, WirePort

SEAMS_FILE = ROOT / "remote" / "seams.py"
HOSTS = (ROOT / "remote" / "job_lifecycle.py", ROOT / "remote" / "bc_job.py")
TIGHT = ("_request", "_wire_add")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _transport_calls(path: Path) -> list[str]:
    """`_request(...)` / `_wire_add(...)` 的**调用**（裸名与属性式都算）。

    `PortPair(request=_request)` 里的 `_request` 是 `Name(Load)`，不是调用 ⇒ 不计。
    """
    out: list[str] = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in TIGHT:
            out.append(func.id)
        elif isinstance(func, ast.Attribute) and func.attr in TIGHT:
            out.append(ast.unparse(func))
    return out


def _module_assign(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ) or (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        ):
            return node.value
    return None


# ───────────────────────── 1. 不再裸调 ─────────────────────────


@pytest.mark.parametrize("path", HOSTS, ids=lambda p: p.name)
def test_hosts_never_call_transport_primitives_directly(path: Path) -> None:
    calls = _transport_calls(path)
    assert calls == [], (
        f"{path.name} 里又出现传输原语直调 {calls} —— 调用点必须是 `WIRE.request(…)` / "
        "`WIRE.add(…)`，否则 patch `WIRE` 会漏掉这一处（改回私有名 = G19 的债回流）"
    )


@pytest.mark.parametrize("path", HOSTS, ids=lambda p: p.name)
def test_host_builds_its_port_from_its_own_primitives(path: Path) -> None:
    value = _module_assign(_tree(path), "WIRE")
    assert isinstance(value, ast.Call), f"{path.name} 缺 `WIRE = PortPair(…)` 组装行"
    assert ast.unparse(value.func) == "PortPair", ast.unparse(value)
    kw = {k.arg for k in value.keywords}
    if path.name == "job_lifecycle.py":
        assert kw == {"request", "add"}, ast.unparse(value)
        assert {ast.unparse(k.value) for k in value.keywords} == {"_request", "_wire_add"}
    else:
        # bc_job 不写 job 行 ⇒ 只接发送面，`add` 走缺省 `_noop`（不是忘了接）。
        assert kw == {"request"}, ast.unparse(value)


# ───────────────────────── 2. 调用时解析 ─────────────────────────


@pytest.mark.parametrize("path", HOSTS, ids=lambda p: p.name)
def test_port_name_is_resolved_at_call_time(path: Path) -> None:
    """`WIRE` 不许被「提前绑定」：缺省值 / 类属性 / 模块级别名都会让 patch 静默失效。"""
    bad: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defaults = list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None
            ]
            for d in defaults:
                if any(isinstance(n, ast.Name) and n.id == "WIRE" for n in ast.walk(d)):
                    bad.append(f"{node.name}: 缺省值里绑了 WIRE")
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(
                    isinstance(n, ast.Name) and n.id == "WIRE" for n in ast.walk(stmt.value)
                ):
                    bad.append(f"{node.name}: 类属性里绑了 WIRE")
    # 模块级别名（`_WIRE = WIRE`）同样会让后续 patch 打空。
    for stmt in _tree(path).body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if (
                    isinstance(t, ast.Name)
                    and t.id != "WIRE"
                    and any(
                        isinstance(n, ast.Name) and n.id == "WIRE" for n in ast.walk(stmt.value)
                    )
                ):
                    bad.append(f"模块级别名 {t.id} = WIRE")
    assert bad == [], bad


# ───────────────────────── 3. 端口形状 ─────────────────────────


def test_port_pair_is_frozen_and_add_defaults_to_noop() -> None:
    pair = PortPair(lambda spec: (200, b"{}"))
    pair.add("j1", "result", 10, 0.1)  # 缺省 NoOp：不抛、也不把账记到别人头上
    # 不可变是设计承诺（端口实例不该被就地改写）。走 `__setattr__` 直调：
    # 直接赋值要一条 `type: ignore`（G6 只降不增），`setattr(...)` 被 ruff 的 B010 顶回来，
    # 而 dunder 调用两者都不碰 —— 运行期走的是同一个方法（frozen 版一定抛）。
    with pytest.raises(dataclasses.FrozenInstanceError):
        pair.__setattr__("request", lambda spec: (500, b""))


def _assert_port(port: WirePort) -> None:
    # Protocol 不做 isinstance（不是 runtime_checkable）⇒ 判据取「两份能力都在」。
    assert callable(port.request) and callable(port.add)


def test_default_ports_satisfy_the_protocol_shape() -> None:
    for port in (jl_mod.WIRE, bc_mod.WIRE):
        _assert_port(port)


def test_seams_module_has_no_project_imports() -> None:
    """L0 判据：seams 一旦 import `remote.*` / `common.*` 就抬层，与读者同秩。"""
    mods: list[str] = []
    for node in ast.walk(_tree(SEAMS_FILE)):
        if isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
    project = [
        m for m in mods if m.startswith(("remote", "common", "hub", "worker", "trainer", "biz"))
    ]
    assert project == [], f"remote/seams.py 抬层了：{project}"


# ───────────────────────── 4. 替身真的被读到 ─────────────────────────


def test_stub_wire_is_actually_read_by_peek_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """不联网也能拿到数据 ⇒ 注入生效（URL 是 `127.0.0.1:1`，真发必失败）。

    第二个断言是「另一半」：`add` 也必须走端口 —— 把 `add` 换成一个只记账的替身，
    `post_result` 成功时应当**恰好**记一笔 `result` 段。
    """
    seen: list[str] = []

    def fake_request(spec):
        return 200, b'{"jobs": [{"job_id": "j1", "stage": 1}], "halt": false}'

    monkeypatch.setattr(
        jl_mod, "WIRE", PortPair(fake_request, add=lambda jid, seg, n, s: seen.append(seg))
    )
    got = jl_mod.peek_jobs("http://127.0.0.1:1", "tok")
    assert got is not None and got[0][0]["job_id"] == "j1", got

    monkeypatch.setattr(
        jl_mod,
        "WIRE",
        PortPair(lambda spec: (200, b"{}"), add=lambda jid, seg, n, s: seen.append(seg)),
    )
    assert (
        jl_mod.post_result(jl_mod.PostResultSpec("http://127.0.0.1:1", "tok", "j1", {"a": 1}))
        == 200
    )
    assert seen == ["result"], seen
