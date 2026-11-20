"""路由表守卫（2026-11-19，`python.refactor.md` 附录 B 的 `hub/http_face.py` 行）。

`HubHandler` 的两个端点面原先各是一条 30 支的顺序 `elif path == …` / `startswith+endswith`
链：加一个端点 = 往链条中间插一行，插错位置会静默遮蔽后面的同后缀路由
（`/jobs/{id}/result` 与 `/jobs/{id}/result-ready` 这类恰好不重叠，但那是运气），
而把处理方法 stub 掉的单元用例全绿 —— 「请求能不能走到这个方法」只有真链才测得到的盲区。
现在路由是 `GET_ROUTES` / `POST_ROUTES` 两张数据表 + 纯函数 `match_route`。

本文件钉住五件事：

  ① **每条路由都有处理方法**：表里写错名字，运行期是「AttributeError 乘 500」；
  ② **没有一条路径同时命中两行**，且 `(形态, 参数)` 不重复声明；
  ③ **分派函数里不许再有路径比较**：回退成 `elif` 链就红（这就是这一刀要挡的形状）；
  ④ **参数绑定**：带参数的三族端点（workers/halt|resume、admin 的 POST 面、offline 租约面）
     真的把参数传对了 —— 从**公开入口** `do_GET` / `do_POST` 验，包装方法写错一个布尔值
     也「可调用」，只有真走一遍分派 + 查询串解析才看得见；
  ⑤ **两条边界仍在**：未知路径 404、处理方法抛 `ProtocolError` ⇒ 400。

口径上刻意**不**冻结路由条数：加端点不是欠债，让每次加端点都要改一个数字只会训练人
「顺手改基线」。（④ 的探针沿用 `test_hub_routes_split.py::_Probe` 的写法：`object.__new__`
绕开 `BaseHTTPRequestHandler.__init__`，用子类重写而不是往实例上挂属性。）
"""

from __future__ import annotations

import ast
from email.message import Message
from io import BytesIO
from pathlib import Path

import pytest

from common.protocol import (
    OFFLINE_CLAIM_PATH,
    OFFLINE_HEARTBEAT_PATH,
    OFFLINE_RELEASE_PATH,
    ProtocolError,
)
from hub.http_face import GET_ROUTES, POST_ROUTES, HubHandler, Route

HTTP_FACE = Path(__file__).resolve().parents[2] / "hub" / "http_face.py"


def _matches(route: Route, path: str) -> bool:
    """独立重实现匹配语义（三个形态各一句）——不 import `match_route` 来测 `match_route`。"""
    if route.kind == "exact":
        return path == route.value
    if route.kind == "job_suffix":
        return path.startswith("/jobs/") and path.endswith(route.value)
    return path.startswith("/jobs/") and route.value in path


def _sample(route: Route) -> str:
    """该行**应当**命中的一条代表路径（表里每一行各自造一条）。"""
    if route.kind == "exact":
        return route.value
    if route.kind == "job_suffix":
        return f"/jobs/j1{route.value}"
    return f"/jobs/j1{route.value}step"


# ───────────────────────── ①② 表自身 ─────────────────────────


def test_every_route_names_a_handler_that_exists() -> None:
    """表里的名字必须在 `HubHandler` 上是可调用的方法（含混入带来的那 26 个）。"""
    for table in (GET_ROUTES, POST_ROUTES):
        for route in table:
            handler = getattr(HubHandler, route.handler, None)
            assert callable(handler), f"{route.kind}:{route.value} → {route.handler} 不在 HubHandler 上"


def test_no_route_is_declared_twice() -> None:
    """同一张表里 `(形态, 参数)` 不许重复：重复声明 = 后一行永远不可达。"""
    for table in (GET_ROUTES, POST_ROUTES):
        keys = [(r.kind, r.value) for r in table]
        assert len(keys) == len(set(keys)), f"重复的路由行：{keys}"


def test_no_path_matches_two_rows() -> None:
    """每一行的代表路径只命中它自己 —— 顺序即优先级，但「其实重叠了」必须当场红。

    重叠不是口味问题：两条都命中时，谁先声明谁赢，于是「新增一条更具体的路由」会静默
    改变既有端点的行为，而端到端用例只覆盖它自己那条路径。
    """
    for table in (GET_ROUTES, POST_ROUTES):
        for route in table:
            path = _sample(route)
            hits = [r.handler for r in table if _matches(r, path)]
            assert hits == [route.handler], f"{path} 命中 {hits}（期望只有 {route.handler}）"


# ───────────────────────── ③ 形状：分派只剩查表 ─────────────────────────


def test_the_dispatch_has_no_path_comparisons_left() -> None:
    """`do_GET` / `do_POST` 里不许再有 `path ==` / `startswith` / `endswith` 这类比较。

    判据是 **AST 里的 `Compare` 节点数为 0**（不是「源码里有没有 elif 字样」）：新加一支
    路由只能往表里加一行；把链写回来就红。用 `pytest.fail` 而不是 `assert` —— 本文件读的是
    生产 `.py`，计数类判据不走文本断言口径。
    """
    tree = ast.parse(HTTP_FACE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HubHandler")

    def _is_path_compare(node: ast.AST) -> bool:
        """比较的左/右操作数里出现 `path` 或字符串字面量 ⇒ 这是一次路径判定。

        `name is None`（查表结果判空）不算——那一支不是路由判定；把它的噪音也算进来会让
        这条守卫逼人写 `if not name:`，从而漏掉真回归。
        """
        if not isinstance(node, ast.Compare):
            return False
        for operand in (node.left, *node.comparators):
            if isinstance(operand, ast.Name) and operand.id == "path":
                return True
            if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                return True
        return False

    for fn_name in ("do_GET", "do_POST"):
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
        compares = [ast.unparse(n) for n in ast.walk(fn) if _is_path_compare(n)]
        if compares:
            pytest.fail(f"{fn_name} 里又长出了路径比较：{compares} —— 路由只走 GET_ROUTES/POST_ROUTES")


# ───────────────────────── ④ 参数绑定（公开入口） ─────────────────────────


class _Recorder(HubHandler):
    """最小 handler：不跑 `__init__`（它会读 socket），只手装 `do_GET`/`do_POST` 用到的几样。

    被钉的三族端点用**签名逐字一致**的重写记下调用（mypy 的 override 检查也要求逐字一致），
    其余一切照父类 —— 被测的是分派，不是那些端点的语义。
    """

    calls: list[tuple[str, tuple[object, ...], dict[str, object]]]
    json_calls: list[tuple[object, int]]

    def __new__(cls, path: str) -> _Recorder:
        self = object.__new__(cls)
        self.path = path
        self.headers = Message()
        self.rfile = BytesIO(b"")
        self.client_address = ("127.0.0.1", 1)  # `log_message` 要知道对端
        self.calls = []
        self.json_calls = []
        return self

    def __init__(self, path: str) -> None:
        """空实现：一切已在 `__new__` 里装好；父类那份会**开始处理请求**，必须绕开。"""

    def _note(self, name: str, *args: object, **kwargs: object) -> None:
        self.calls.append((name, args, kwargs))

    def _json(self, obj: object, status: int = 200) -> None:
        self.json_calls.append((obj, status))

    def _admin_halt(self, halt: bool, course: str = "") -> None:
        self._note("_admin_halt", halt, course)

    def _admin_push_workers(self, set_action: bool = False) -> None:
        self._note("_admin_push_workers", set_action=set_action)

    def _admin_courses(self, post: bool = False) -> None:
        self._note("_admin_courses", post=post)

    def _post_offline_lease(self, action: str) -> None:
        self._note("_post_offline_lease", action)


def test_get_routes_bind_their_arguments() -> None:
    """halt/resume 的布尔 + `?course=` 是两条**不同**语义；push-workers 的 GET 面不带 `set_action`。"""
    halt = _Recorder("/admin/workers/halt?course=c9")
    halt.do_GET()
    assert halt.calls == [("_admin_halt", (True, "c9"), {})], halt.calls

    resume = _Recorder("/admin/workers/resume?course=c9")
    resume.do_GET()
    assert resume.calls == [("_admin_halt", (False, "c9"), {})], resume.calls

    push = _Recorder("/admin/push-workers")
    push.do_GET()
    assert push.calls == [("_admin_push_workers", (), {"set_action": False})], push.calls


def test_post_routes_bind_their_arguments() -> None:
    """POST 面的三个参数族：admin 的 `post=` / `set_action=`，offline 租约的 action 字符串。"""
    courses = _Recorder("/admin/courses")
    courses.do_POST()
    assert courses.calls == [("_admin_courses", (), {"post": True})], courses.calls

    push = _Recorder("/admin/push-workers")
    push.do_POST()
    assert push.calls == [("_admin_push_workers", (), {"set_action": True})], push.calls

    for path, action in (
        (OFFLINE_CLAIM_PATH, "claim"),
        (OFFLINE_HEARTBEAT_PATH, "heartbeat"),
        (OFFLINE_RELEASE_PATH, "release"),
    ):
        lease = _Recorder(path)
        lease.do_POST()
        assert lease.calls == [("_post_offline_lease", (action,), {})], (path, lease.calls)


# ───────────────────────── ⑤ 两条边界 ─────────────────────────


def test_unknown_path_is_a_404_on_both_faces() -> None:
    """表里没有的路径 ⇒ 404（不是 500、不是空 200），且**没有**任何处理方法被调到。"""
    for face in ("do_GET", "do_POST"):
        rec = _Recorder("/nope/at/all")
        getattr(rec, face)()
        assert rec.calls == [], rec.calls
        assert rec.json_calls == [({"error": "not found"}, 404)], rec.json_calls


class _Refusing(_Recorder):
    """一条「协议错 ⇒ 400」的处理方法（`ProtocolError` 是 `ValueError` 的子类）。"""

    def _get_status(self) -> None:
        raise ProtocolError("lease token mismatch")


def test_protocol_errors_stay_a_400() -> None:
    """处理方法抛 `ProtocolError` ⇒ 顶层边界答 400 + 原文（不是 500 internal）。"""
    rec = _Refusing("/jobs/j1/status")
    rec.do_GET()
    assert rec.json_calls == [({"error": "lease token mismatch"}, 400)], rec.json_calls
