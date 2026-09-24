"""S4 第三步契约（2026-09-23）—— `HubHandler` 的 admin 控制面拆成 `remote/hub/admin.py::AdminRoutes`。

`remote/hub_server.py` 3974 行里有三个大状态类（`_JobStore` 1002 / `_HubQueue` 1033 /
`HubHandler` 1343）与零散小函数。第三刀取 `HubHandler`（49 方法 / 1343 行）里**最安全**的一组：
admin 控制面 9 方法（停机恢复 / 课程热切 / 队列与状态 / push-worker 清单 / net-probe）。
依据（plan §5.3.2 实测）：`HubHandler` 只有 3 个类属性 ⇒ 本组方法近乎无状态；本组只往外调 4 个
通用助手；**测试接缝为零**（全仓对 `remote.hub_server` 的 patch 只有 `SEND_TIMEOUT_SEC`）。

方向：`class HubHandler(AdminRoutes, BaseHTTPRequestHandler)` —— 组合类依赖混入（派发表调用它）。
`NET_PROBE_MAX` / `_deterministic_fill` 只被本组使用，**随迁**以免与「hub_server import admin
拿混入」成环；搬后无其它读者，故**不留门面**。

本文件钉住四条（含一条**功能性**断言：net-probe 的边界与确定性不变量——那是隧道 A/B 台架
的物理前提，不是装饰）：

1. 9 个 admin 方法**定义**在 `AdminRoutes`；`HubHandler` **不得**再定义（组合类只能是组合类）；
2. `HubHandler` 的 MRO 里 `AdminRoutes` 在 `BaseHTTPRequestHandler` **之前**（否则 typeshed 的
   `headers` / `rfile` 精确类型会被混入的声明遮蔽——`no-any-return` 的成因）；
3. `remote/hub/` **不 import `remote.hub_server`**（混入包不得反向依赖组装模块，否则成环）；
4. net-probe 行为：确定性填充（固定种子 / 64KiB 块重复）· `bytes` 越界 400 · 上行体越界 413。
"""

from __future__ import annotations

import ast
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

import remote.hub.admin as admin_mod
from remote.hub.admin import NET_PROBE_MAX, AdminRoutes, _deterministic_fill

NN_ROOT = Path(__file__).resolve().parent.parent

ADMIN_METHODS = (
    "_admin_courses",
    "_admin_halt",
    "_admin_net_probe",
    "_admin_net_probe_upload",
    "_admin_offline",
    "_admin_push_workers",
    "_admin_queue",
    "_admin_status",
    "_admin_unfreeze",
)


def _class_methods(path: Path, cls_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls_name
    )
    return {m.name for m in cls.body if isinstance(m, ast.FunctionDef)}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


# ────────────────────────── 结构 ──────────────────────────


def test_admin_methods_are_defined_in_admin_routes_only() -> None:
    """定义在 `AdminRoutes`；`HubHandler` 里**不得**再有同名定义（也别「就地补一个」）。"""
    defined = _class_methods(NN_ROOT / "remote" / "hub" / "admin.py", "AdminRoutes")
    assert set(ADMIN_METHODS) <= defined, sorted(set(ADMIN_METHODS) - defined)
    left = _class_methods(NN_ROOT / "remote" / "hub_server.py", "HubHandler")
    crept_back = sorted(set(ADMIN_METHODS) & left)
    assert crept_back == [], f"这些方法又回到 HubHandler 了：{crept_back}"


def test_hub_handler_declares_all_mixins_before_the_base_handler() -> None:
    """组合类的**基类顺序**必须是「所有 `*Routes` 混入在前、`BaseHTTPRequestHandler` 在最后」。

    第十一刀（2026-09-24）后混入从一个变五个（admin + schedule/result/blob/offline），断言
    也随之从「`bases[1]` 就是 handler」改成「handler 在最后一个，前面全是混入」——旧写法的
    用意（混入必须**早于** handler）不变，只是不再假定混入恰好只有一个。

    MRO 顺序即基类左→右的优先序，所以这条源码断言与运行期 `__mro__` 断言等价。为何要看顺序：
    混入里为 `headers` / `rfile` 声明的类型必须与 typeshed 逐字一致，否则（若顺序写反、或有人
    把声明换成 `Any`）会把组合类里 `self.headers.get(...)` / `self.rfile.read(n)` 的推断拓成
    `Any` ⇒ `hub_server` 里做 `-> str` / `-> bytes | None` 的方法报 `no-any-return`。

    ⚠ 本文件**故意不 import `remote.hub_server`**（本测试不起服务进程，也不需要那个类）；
    它也因此不触发 `tests/test_subproc_util.py` 的「起服务必须借端口」源码守卫
    （那个守卫以字面量 `remote.hub_server` 为标记）。
    """
    tree = ast.parse((NN_ROOT / "remote" / "hub_server.py").read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "HubHandler"
    )
    bases = [ast.unparse(b) for b in cls.bases]
    assert bases[-1] == "BaseHTTPRequestHandler", bases
    mixins = bases[:-1]
    assert "AdminRoutes" in mixins, bases
    assert all(b.endswith("Routes") for b in mixins), (
        f"`BaseHTTPRequestHandler` 之前的基类应当全是 `*Routes` 混入：{bases}"
    )
    assert len(mixins) >= 5, f"四组路由混入 + admin 都应当在列：{mixins}"


def test_net_probe_support_names_moved_with_the_group() -> None:
    """两个只被本组使用的名字随迁（否则 hub_server ↔ admin 成环）；无其它读者故不留门面。"""
    assert NET_PROBE_MAX == 16 * 1024 * 1024
    src = (NN_ROOT / "remote" / "hub_server.py").read_text(encoding="utf-8")
    for name in ("NET_PROBE_MAX", "_deterministic_fill", "_PROBE_BLOCK", "random."):
        assert name not in src, f"{name} 仍留在 hub_server（应随 admin 组迁走）"


def test_hub_package_never_imports_hub_server() -> None:
    """混入包不得反向依赖组装模块（否则 hub_server → hub.admin → hub_server 成环）。"""
    for name in (
        "admin.py",
        "blob.py",
        "offline.py",
        "result.py",
        "schedule.py",
        "__init__.py",
    ):
        # 用**叶子名**判据而不是带引号的点分字面量：后者是 `tests/test_subproc_util.py`
        # 「起服务必须借端口」源码守卫的标记（它假设「写过那个 patch 目标 = 会 spawn」）。
        # 本文件确实不起服务（用进程内 stub），所以不该被那个守卫接管。
        leaves = {imp.rsplit(".", 1)[-1] for imp in _imports(NN_ROOT / "remote" / "hub" / name)}
        assert "hub_server" not in leaves, name


# ────────────────────────── 功能性：net-probe 的不变量 ──────────────────────────


class _Stub(AdminRoutes):
    """最小宿主：只喂 net-probe 需要的东西（鉴权放行 / 记录响应 / 假请求体）。"""

    def __init__(self, query: str = "", content_length: str | None = None) -> None:
        self.path = f"/admin/net-probe{query}"
        self.headers = Message()
        if content_length is not None:
            self.headers["Content-Length"] = content_length
        self.rfile = BytesIO(b"x" * 4096)
        self.sent: list[tuple[str, Any, int]] = []

    def _auth_ok(self) -> bool:
        return True

    def _json(self, obj: object, status: int = 200) -> None:
        self.sent.append(("json", obj, status))

    def _bytes(self, data: bytes, *a: object, **k: object) -> None:
        self.sent.append(("bytes", len(data), 200))

    def _query_course(self) -> str:
        return ""


def test_deterministic_fill_is_seeded_and_block_repeated() -> None:
    """「绝不用随机」是物理前提：同一 N 每次逐字节相同，隧道 A/B 的差异才只可能来自协议。"""
    assert _deterministic_fill(100) == _deterministic_fill(100)
    assert len(_deterministic_fill(0)) == 0
    assert len(_deterministic_fill(NET_PROBE_MAX)) == NET_PROBE_MAX
    # 64KiB 固定块重复
    assert _deterministic_fill(70000)[:65536] == _deterministic_fill(65536)
    assert _deterministic_fill(70000)[65536:] == _deterministic_fill(65536)[:4464]


def test_net_probe_downlink_bounds_and_payload() -> None:
    """下行：合法 N 回 N 字节；非整数 400；越界 400（上限是 NET_PROBE_MAX）。"""
    st = _Stub("?bytes=16")
    st._admin_net_probe()
    assert st.sent == [("bytes", 16, 200)]

    st = _Stub("?bytes=abc")
    st._admin_net_probe()
    assert st.sent[-1][2] == 400 and "整数" in st.sent[-1][1]["error"]

    st = _Stub(f"?bytes={NET_PROBE_MAX + 1}")
    st._admin_net_probe()
    assert st.sent[-1][2] == 400 and "越界" in st.sent[-1][1]["error"]


def test_net_probe_uplink_bounds_413_and_drains_body() -> None:
    """上行：越界 413（不是 400）且**绝不整体入内存**；合法则读掉后回字节数。"""
    st = _Stub(content_length=str(NET_PROBE_MAX + 1))
    st._admin_net_probe_upload()
    assert st.sent[-1][2] == 413 and "请求体越界" in st.sent[-1][1]["error"]

    st = _Stub(content_length="2048")
    st._admin_net_probe_upload()
    assert st.sent == [("json", {"bytes": 2048}, 200)]


def test_admin_module_has_no_module_level_mutable_state() -> None:
    """搬过来的模块级状态只有不可变的 `_PROBE_BLOCK`（避免「拆出隐藏状态」这类事故）。"""
    mutable = {
        name
        for name, value in vars(admin_mod).items()
        if not name.startswith("__")
        and isinstance(value, (list, dict, set))
        and getattr(value, "__module__", None) == "remote.hub.admin"
    }
    assert mutable == set(), f"admin 模块出现了模块级可变状态：{sorted(mutable)}"
