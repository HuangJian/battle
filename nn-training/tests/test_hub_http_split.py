"""拆分的**契约守卫**：hub 客户端的 HTTP 面永住 `remote/hub_http.py`（S5 第五刀，2026-09-27）。

`remote/hub_client.py` 从「发布/打包/校验落位 + HTTP」混居减到 **1299 行**；搬走的簇
（传输薄壳 `_request` · 回传消费 `probe_job_result`/`poll_job`/`wait_job`/`_wait_state_note` ·
停机达令 `set_cloud_halt`/`hub_halted`/`clear_halt_on_startup` · 失败回报 `report_job_failure` ·
失败类型 `HubClientError` · 三态/阈值常量）是「把请求发出去、把回答读回来」的**唯一**实现。

本文件钉七件事：

1. **定义唯一**——这些名字不许在 `hub_client.py` 里再实现一遍；
2. **无环 + 分层**——`hub_http` 走全局账本对账（顶层 intra-remote 边严格向下、不碰 `rl`）；
   `hub_client` 站在它上面 ⇒ `hub_client` 的层号**必须高于** `hub_http`；
3. **转发同一对象**——`hub_client` 的每个搬走名都是 `hub_http` 的转发（不是副本）；
4. **注入点分档**（这一步最容易静默坏）：patch `remote.hub_http._request` 才作用于宿主函数；
   `remote.hub_client._request` 是**转发名**，照它打补丁**不再**影响任何调用点——
   「patch 打偏而测试全绿」是本仓最贵的坑之一，故正反各一条断言；
5. **可注入薄壳的边界**——`hub_http` 顶层只准用 stdlib + `common.protocol`；`urllib.request` /
   `urllib.error` 只许出现在 `_request`（函数内），且 `common.net_http` 也是延迟；
6. **依赖面闭集**——`hub_http` 不得 import `remote.hub_client`（否则成环）；
7. **回传语义没变**（功能性）：`probe_job_result` 的三态分类（ready / pending / transient）
   与终局失败（410 → `JobFailedError`）就在这里判。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.hub_client as hub_client_mod
import remote.hub_http as hub_http_mod
from tests.helpers import remote_dag as dag

HTTP_FILE = ROOT / "remote" / "hub_http.py"
CLIENT_FILE = ROOT / "remote" / "hub_client.py"

#: 本次搬走的**定义**——只许在 `hub_http.py` 里出现。分三簇 + 常量/类型（按关注点列，便于对账）。
MOVED_NAMES = {
    # 传输薄壳（唯一碰 `urlopen` 的地方）
    "_request",
    # 回传消费（状态码分类的唯一实现 + 等待）
    "ProbeResult",
    "PROBE_READY",
    "PROBE_PENDING",
    "PROBE_TRANSIENT",
    "PROBE_TIMEOUT_SEC",
    "WAIT_REPORT_SEC",
    "_job_failed_from_body",
    "_wait_state_note",
    "probe_job_result",
    "poll_job",
    "wait_job",
    # 行政面（失败回报 + 停机达令）
    "report_job_failure",
    "set_cloud_halt",
    "hub_halted",
    "clear_halt_on_startup",
    # 失败类型（三环共用）
    "HubClientError",
}

#: `hub_http.py` 允许的 import 面（stdlib + 协议层；多一个即红）。
ALLOWED_IMPORTS = {
    "__future__",
    "json",
    "time",
    "typing",
    "urllib.error",
    "urllib.parse",
    "urllib.request",
    "common.protocol",
    "common.net_http",
}

#: 只许出现在**函数体内**（延迟）的模块名——与 `common/protocol` 这类顶层契约分开。
DEFERRED_ONLY_IMPORTS = {"urllib.error", "urllib.request", "common.net_http"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    """`def` / `class` / 顶层赋值定义的函数名 / 类名 / 变量名。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _module_names(path: Path, *, top_only: bool) -> set[str]:
    """文件里出现的仓内/标准库模块名（`import x` → `x`；`from a.b import c` → `a.b`）。"""
    tree = _tree(path)
    nodes = tree.body if top_only else list(ast.walk(tree))
    out: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def _imported_from(path: Path, module: str) -> set[str]:
    """`from <module> import (…)` 列出的名字（跨行括号写法也认）。"""
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, ast.ImportFrom) and node.module == module:
            out.update(a.name for a in node.names if a.name != "*")
    return out


def test_moved_names_are_defined_in_hub_http_and_not_redefined_in_hub_client() -> None:
    """定义唯一：搬走的名字只在 `hub_http.py` 里实现。"""
    assert _defined(HTTP_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(HTTP_FILE))
    leftovers = MOVED_NAMES & _defined(CLIENT_FILE)
    assert leftovers == set(), (
        f"hub_client.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"
    )


def test_hub_http_sits_below_its_remote_dependencies() -> None:
    """无环 + 分层：对账走**全局账本**（`tests/helpers/remote_dag.py`），不再各自写一份。

    原先这里是「不得反向 import `remote.hub_client`」的特指断言；现在是一般化的
    「顶层 intra-remote 边必须严格向下」+「任何 import 不得碰 `rl`」，
    与 `tests/test_remote_dag.py` 的整图对账共用**同一份实现**。
    """
    dag.assert_remote_module("remote.hub_http")


def test_hub_client_sits_strictly_above_hub_http() -> None:
    """★ 方向不变式：`hub_http` 是**底座**，`hub_client` 站在它上面。

    `hub_client` 顶层 `from remote.hub_http import …` ⇒ 它的层号必须严格大于 `hub_http` 的。
    这条钉住「谁是底座谁是用例」，防止以后把边反过来（那会成环）或把两层并成一层
    （`assert_remote_module` 会判同层为上向违规）。
    """
    assert dag.LAYERS["remote.hub_http"] < dag.LAYERS["remote.hub_client"], (
        f"hub_client(L{dag.LAYERS['remote.hub_client']}) 必须严格高于 "
        f"hub_http(L{dag.LAYERS['remote.hub_http']})"
    )
    assert "remote.hub_http" in dag.module_deps("remote.hub_client"), (
        "hub_client 顶层不再 import hub_http —— 门面/转向关系变了，本守卫需重判"
    )


def test_hub_client_forwards_every_moved_name() -> None:
    """`hub_client` 的命名空间里每个搬走的名字都在，且**函数/类/常量**是同一个对象。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(hub_client_mod, name), f"remote.hub_client 丢了转发名 {name}"
    # `HubClientError` 是类型，恒等断言同样成立（旧家的 raise 必须指向同一个对象，
    # 否则外部的 `except HubClientError` 会漏接）。
    for name in sorted(MOVED_NAMES):
        assert getattr(hub_client_mod, name) is getattr(hub_http_mod, name), (
            f"remote.hub_client.{name} 不是 remote.hub_http.{name}（转发成了副本）"
        )


def test_hub_client_module_has_no_request_call_site_any_more() -> None:
    """★ 警报：`hub_client.py` 里已**没有** `_request` 的调用点（唯一宿主是 hub_http）。

    它仍转发 `_request`（历史 import 与故障注入打这个名字），但「patch
    `remote.hub_client._request` 期望某个函数行为改变」从此是**空操作**——
    写这类测试的人应该去 patch `remote.hub_http._request`。
    """
    calls = {
        node.func.id
        for node in ast.walk(_tree(CLIENT_FILE))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_request" not in calls, "hub_client.py 里又出现了 `_request` 调用点——seam 分档需重判"
    assert hasattr(hub_client_mod, "_request"), "`_request` 的转发名不该消失（有测试 import 它）"


def test_request_seam_is_the_hub_http_module(monkeypatch) -> None:
    """档位一：`probe_job_result`（在 hub_http）解析 `_request` 于 **hub_http** 命名空间。

    所以「patch `remote.hub_http._request`，同时把 `remote.hub_client._request` 换成炸弹」
    必须仍然成功——**反面**（只 patch hub_client）是这一步最容易犯的静默错误，
    下一条专门钉它。
    """
    monkeypatch.setattr(
        hub_client_mod,
        "_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏：patch 了转发名")),
    )
    monkeypatch.setattr(hub_http_mod, "_request", lambda *a, **k: (200, b'{"ok": 1}'))
    probe = hub_http_mod.probe_job_result("http://hub", "t", "j1")
    assert probe.state == hub_http_mod.PROBE_READY
    assert probe.result == {"ok": 1}


def test_patching_the_forwarding_name_is_a_no_op_by_design(monkeypatch) -> None:
    """★ 档位二（反面）：patch `remote.hub_client._request` **不影响**宿主函数。

    这是**刻意**的分档（本次拆分的收益之一：注入点单一且显式）。测试要拿这条来提醒
    「别对着转发名打补丁」；若哪天有人在 hub_client 里加了 `_request(...)` 调用点，
    本用例会绿着骗人——所以它同时断言那是空操作（真实 `_request` 仍被用到）。
    """
    calls: list[str] = []

    def fake_real(base, token, path, timeout=30.0, **kw):
        calls.append(path)
        return 200, b'{"ok": 1}'

    monkeypatch.setattr(hub_http_mod, "_request", fake_real)
    # 打偏：这个赋值不该改变任何行为
    monkeypatch.setattr(
        hub_client_mod,
        "_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("这个补丁该是空操作")),
    )
    probe = hub_http_mod.probe_job_result("http://hub", "t", "j1")
    assert probe.result == {"ok": 1}
    assert calls == ["/jobs/j1/result"]


def test_hub_http_imports_stay_within_the_allowed_surface() -> None:
    """依赖面闭集：stdlib + `common.protocol`；`urllib.*` / `common.net_http` 只许函数内。"""
    modules = _module_names(HTTP_FILE, top_only=False)
    extra = sorted(modules - ALLOWED_IMPORTS)
    assert extra == [], f"remote/hub_http.py 引入了允许面之外的依赖：{extra}"
    top = _module_names(HTTP_FILE, top_only=True)
    leaked = sorted(top & DEFERRED_ONLY_IMPORTS)
    assert leaked == [], (
        f"这些只该出现在 `_request`（函数内）里，不该是模块级：{leaked}"
        "——否则薄壳不再是「可注入的单点」，导入期就攥住了 urllib/网络栈"
    )


def test_hub_http_never_imports_hub_client() -> None:
    """无环：HTTP 面不得反向 import 门面（否则成环，`test_remote_dag` 会更早红）。"""
    back = sorted(_module_names(HTTP_FILE, top_only=False) & {"remote.hub_client"})
    assert back == [], f"remote/hub_http.py 反向 import 了门面：{back}"


# ─────────────────────── ⑥ 回传语义没变（功能性） ───────────────────────


def test_probe_classifies_ready_pending_transient_and_terminal_failure(monkeypatch) -> None:
    """三态分类：200+dict → ready；202/404 → pending；5xx/网络错 → transient；410 → 终局失败。

    分类住在这个抛出点上是**唯一**能用的判据——「还没好」与「永远好不了」必须分开，
    混在一起就是一个静默挂死的等待环（历史 hub 与 push 两段轮询就是这么漂开的）。
    """
    from common.protocol import JobFailedError

    reply: dict[str, object] = {}

    def fake(_base, _token, _path, timeout=0.0):
        st, body = reply["st"], reply["body"]
        if isinstance(st, Exception):
            raise st
        return st, body

    monkeypatch.setattr(hub_http_mod, "_request", fake)

    reply["st"], reply["body"] = 200, b'{"job_id": "j1"}'
    probe = hub_http_mod.probe_job_result("http://hub", "t", "j1")
    assert probe.state == hub_http_mod.PROBE_READY and probe.result == {"job_id": "j1"}

    reply["st"], reply["body"] = 202, b"{}"
    assert hub_http_mod.probe_job_result("http://hub", "t", "j1").state == hub_http_mod.PROBE_PENDING
    reply["st"] = 404
    assert hub_http_mod.probe_job_result("http://hub", "t", "j1").state == hub_http_mod.PROBE_PENDING

    reply["st"], reply["body"] = 503, b""
    assert (
        hub_http_mod.probe_job_result("http://hub", "t", "j1").state == hub_http_mod.PROBE_TRANSIENT
    )
    reply["st"] = OSError("tunnel down")
    assert (
        hub_http_mod.probe_job_result("http://hub", "t", "j1").state == hub_http_mod.PROBE_TRANSIENT
    )

    # 终局：410 抛 JobFailedError（不走三态）——原因取 error/reason，kind 取 fail_kind。
    reply["st"], reply["body"] = (
        410,
        '{"reason": "bun 缺失", "fail_kind": "ProtocolError"}'.encode(),
    )
    try:
        hub_http_mod.probe_job_result("http://hub", "t", "j1")
    except JobFailedError as e:
        assert e.kind == "ProtocolError" and "bun 缺失" in str(e)
    else:  # pragma: no cover - 走到这里说明终局失败被吞了
        raise AssertionError("410 必须抛 JobFailedError（终局失败不得混入 transient）")
