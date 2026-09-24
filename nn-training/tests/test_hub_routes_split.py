"""拆分的**契约守卫**：`HubHandler` 的 25 个路由方法永住 `remote/hub/{schedule,result,blob,offline}.py`
（S4 第十一刀，2026-09-24），且四组共用的形状**只准有一份实现**。

## 这一刀切了什么

`remote/hub_server.py` 3728 → 3017 行：`HubHandler` 里 13 个 `_get_*` + 12 个 `_post_*`
（644 行）按**域**分成四组混入，与第三步的 `AdminRoutes` 并列进 MRO：

```
HubHandler(AdminRoutes, ScheduleRoutes, ResultRoutes, BlobRoutes, OfflineRoutes, BaseHTTPRequestHandler)
    schedule   取活/租约/打点：peek · priority · claim · start · ready · abandon · heartbeat · release
    result     回传与终局：result(POST) · fail · status · result(GET) · epoch · resume · bc-metrics
    blob       字节服务：payload · code · ts_code · blob · shared_code
    offline    离线段：task-pack · resume · resume_blob · artifact · result
```

## 为什么 `result` 那组让 handler 升到 L5

`_post_result` 走 `remote.push_dispatch.accept_result`——「推模式与拉模式必须用**同一个**校验
函数」这条纪律（一份对不上账的结果被静默落盘成一轮看起来正常的训练，就是那条纪律要挡的事）。
于是 `hub.result` 的拓扑秩是 **4**，handler 被迫升到 **5**（与 `remote.worker` 对称：
两个宿主各组装自己的 L4 执行单元）。

> **S4 第十六刀之后**：这个「handler 层」有了自己的模块 `hub/http_face.py`（L5），而 `hub_server`
> 退成薄入口门面（L7，连 `smoke_loopback` / `tunnel_ab_probe` 一起到 L8）—— 本文件因此改成读
> `HTTP_FACE` 取 `HubHandler` 的类体，层号断言也同步成 5 / 7。账本的秩断言会把标错层的当场
> 报出来——这不是口味问题。

## Phase B：把重复了 3–5 遍的形状收成 5 个通用助手

| 助手（实现只住 `hub_server`） | 收掉的形状 | 原处数 |
|---|---|---|
| `_job_or_404(known=…)` | 鉴权 + 取 job_id + 404 | 15 |
| `_job_body(cap, known=…)` | 上者 + 读小 JSON 体 | 4 |
| `_lease_token()` | `X-Lease-Token` / `lease-token` 双写法 | 5 |
| `_serve_path(p, missing=…)` | 定位 → 不存在就 404 说清原因 → `_bytes` | 4 |
| `_read_raw_body()` | 按 Content-Length 读满（上限留给调用方） | 3 |

## 本文件钉住的东西

1. **定义唯一**：25 个方法住混入，`HubHandler` 不得再定义任何一个（组合类只能是组合类）；
2. **接线正确**：`HubHandler.X is Mixin.X`（同一函数对象——MRO 真的把它们接上了）；
3. **★ 助手唯一实现 + 活着**：5 个助手只住 `HubHandler`（S4 第十六刀起 = `hub/http_face.py` =
   它自己就在 L5），且各自都有调用点（死助手 = 绿着的空话）；
4. **★ 漂移警报**：混入里**不许**再出现那四种被收掉的内联形状（谁再抄一遍就红）；
5. **鉴权只在没有 job 的地方内联**（`_get_peek` / `_post_priority` 等）——计数与位置都钉住；
6. **依赖方向**：四组都零上向依赖，且 `hub/` 包不 import 组装模块；
7. **★ 功能性**：用**真的 `HubHandler`**（`object.__new__` 起一个无 socket 的实例）+ 记录式
   `_json`/`_bytes` 走一遍新助手的两侧契约（`known=` 闸、404 的具体原因、上限归调用方）。
"""

from __future__ import annotations

import ast
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

import remote.hub.blob as blob_mod
import remote.hub.offline as offline_mod
import remote.hub.result as result_mod
import remote.hub.schedule as schedule_mod
from remote import hub_server as hs
from tests.helpers import remote_dag as dag

NN_ROOT = Path(__file__).resolve().parent.parent
HUB_DIR = NN_ROOT / "remote" / "hub"
HUB_SERVER = NN_ROOT / "remote" / "hub_server.py"
#: ★ `HubHandler` 的**真家**（S4 第十六刀从 `hub_server.py` 搬出）。本文件里凡是「读 `HubHandler`
#: 的类体 / 看它挂着哪些 import」的地方都必须读**这里** —— 读 `HUB_SERVER` 会读到薄入口（只
#: 剩 re-export），于是断言变成「对空气下判据」（第十六刀前它就是那么挂的：`StopIteration`）。
HTTP_FACE = HUB_DIR / "http_face.py"

MIXINS: dict[str, tuple[type, tuple[str, ...]]] = {
    "schedule": (
        schedule_mod.ScheduleRoutes,
        (
            "_get_peek",
            "_post_priority",
            "_post_claim",
            "_post_start",
            "_post_ready",
            "_post_abandon",
            "_post_heartbeat",
            "_post_release",
        ),
    ),
    "result": (
        result_mod.ResultRoutes,
        (
            "_post_result",
            "_post_fail",
            "_get_status",
            "_get_result",
            "_post_bc_epoch",
            "_get_bc_resume",
            "_get_bc_metrics",
        ),
    ),
    "blob": (
        blob_mod.BlobRoutes,
        ("_get_payload", "_get_code", "_get_ts_code", "_get_blob", "_get_shared_code"),
    ),
    "offline": (
        offline_mod.OfflineRoutes,
        (
            "_get_task_pack",
            "_get_offline_resume",
            "_get_offline_resume_blob",
            "_post_offline_artifact",
            "_post_offline_result",
        ),
    ),
}
ROUTE_METHODS = tuple(m for _, (_, ms) in MIXINS.items() for m in ms)

#: 四组共用、实现只住 `hub_server` 的助手（`{名字: 至少几处调用点}`）。
SHARED_HELPERS = {
    "_job_or_404": 10,
    "_job_body": 4,
    "_lease_token": 4,
    "_serve_path": 4,
    "_read_raw_body": 3,
}

#: 混入里**不许**再出现的内联形状 → （正则式子串, 该走哪个助手）。
DRIFT = {
    "self._job_id()": "_job_or_404 / _job_body",
    'self.headers.get("X-Lease-Token"': "_lease_token",
    "self.rfile.read(": "_read_raw_body / _read_capped_body",
    "self._bytes(p.read_bytes())": "_serve_path",
}

#: 每个混入里**允许**内联 `_auth_ok` 的次数（只有「拿不到 job」的端点才该自己鉴权）。
ALLOWED_INLINE_AUTH = {"schedule": 2, "result": 0, "blob": 1, "offline": 5}

ALLOWED_IMPORTS = {
    "remote.hub.blob": {"common.protocol"},
    "remote.hub.offline": {"common.protocol"},
    "remote.hub.result": {"common.protocol", "remote.push_dispatch"},
    "remote.hub.schedule": {"common.protocol"},
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_methods(path: Path, cls: str) -> set[str]:
    tree = _tree(path)
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls)
    return {m.name for m in node.body if isinstance(m, ast.FunctionDef)}


def _code(path: Path) -> str:
    """源码正文（剥掉 `#` 注释；docstring 保留——那里提到名字也算「写了」）。"""
    return "\n".join(ln.split("#", 1)[0] for ln in path.read_text(encoding="utf-8").splitlines())


# ───────────────────────── ① 定义唯一 + 接线 ─────────────────────────


def test_every_route_method_lives_in_its_mixin() -> None:
    """★ 25 个路由方法**定义**在混入里；`HubHandler` 不得再定义任何一个。"""
    defined_here = _class_methods(HTTP_FACE, "HubHandler")
    for mod, (cls, methods) in MIXINS.items():
        in_mixin = _class_methods(HUB_DIR / f"{mod}.py", cls.__name__)
        missing = sorted(set(methods) - in_mixin)
        assert missing == [], f"{mod}.py::{cls.__name__} 少了 {missing}"
        crept_back = sorted(set(methods) & defined_here)
        assert crept_back == [], f"这些方法又回到 HubHandler 了：{crept_back}"
    assert len(ROUTE_METHODS) == 25, len(ROUTE_METHODS)


def test_the_mixins_are_actually_wired_into_the_handler() -> None:
    """★ 接线的**对象级**证明：`HubHandler.X is Mixin.X`（不是同名副本，也不是漏了一个）。"""
    for mod, (cls, methods) in MIXINS.items():
        for m in methods:
            assert getattr(hs.HubHandler, m) is getattr(cls, m), f"{mod}::{m} 没接上 HubHandler"


def test_the_http_face_no_longer_needs_the_route_only_imports() -> None:
    """搬走之后，handler 那一侧不该还挂着只有路由组用过的协议名（否则是搬漏的痕迹）。

    S4 第十六刀之前这条读 `hub_server.py`；现在 `HubHandler` 住 `hub/http_face.py` ⇒ 读那里
    —— 否则它测的就只是「薄入口没有 protocol import」（那条永远为真，等于空话）。
    """
    # 这几个名字现在只被路由组读（实现搬到 common.protocol 的常量不在此列）。
    src = HTTP_FACE.read_text(encoding="utf-8")
    for name in (
        "FAIL_BODY_MAX",
        "WIRE_V2_MAGIC",
        "unpack_result_v2",
        "TS_CODE_NAME",
        "blob_path",
        "has_offline_capability",
        "OFFLINE_RESUME_BLOB_NAMES",
        "OFFLINE_ARTIFACT_BODY_MAX",
        "OFFLINE_RESULT_BODY_MAX",
    ):
        assert f"    {name},\n" not in src, f"{name} 仍被 hub_server import（搬漏）"


# ───────────────────────── ② 通用助手 ─────────────────────────


def test_the_shared_helpers_have_exactly_one_implementation() -> None:
    """★ 5 个助手只住 `HubHandler`（S4 第十六刀起 = `hub/http_face.py`）：混入里不许再实现一遍。"""
    root_defs = _class_methods(HTTP_FACE, "HubHandler")
    for name in SHARED_HELPERS:
        assert name in root_defs, f"hub/http_face.py::HubHandler 少了助手 {name}"
        for mod in MIXINS:
            assert name not in _class_methods(HUB_DIR / f"{mod}.py", MIXINS[mod][0].__name__), (
                f"{mod}.py 又实现了一份 {name}"
            )


def test_every_shared_helper_is_actually_called() -> None:
    """★ 死助手警报：每个助手都要有调用点（只定义不用 = 一条绿着的空话）。"""
    for name, least in SHARED_HELPERS.items():
        hits = sum(_code(HUB_DIR / f"{m}.py").count(f"self.{name}(") for m in MIXINS)
        assert hits >= least, f"{name} 只有 {hits} 处调用（期望 ≥{least}）——助手是不是又死了"


def test_the_duplicated_shapes_are_gone_from_the_mixins() -> None:
    """★ 漂移警报：那四种被收掉的内联形状不许再出现在混入里。"""
    for mod in MIXINS:
        code = _code(HUB_DIR / f"{mod}.py")
        for frag, should_use in DRIFT.items():
            assert frag not in code, (
                f"{mod}.py 又抄了一遍 `{frag}` —— 该走 `{should_use}`（形状只准有一份）"
            )


def test_auth_is_inlined_only_where_there_is_no_job() -> None:
    """鉴权只在「拿不到 job」的端点内联；有 job 的一律经 `_job_or_404`。

    计数一起钉住：多一处 = 又抄了一遍 404 边界；少一处 = 某个端点漏了鉴权。
    """
    for mod, want in ALLOWED_INLINE_AUTH.items():
        got = _code(HUB_DIR / f"{mod}.py").count("if not self._auth_ok():")
        assert got == want, f"{mod}.py 内联鉴权 {got} 处（期望 {want}）"


# ───────────────────────── ③ 依赖方向 / 账本 ─────────────────────────


def test_the_mixins_only_import_downward() -> None:
    """四组混入的顶层仓内依赖是登记过的那些（多一个就说明又搬漏/搬多了）。"""
    for mod, allowed in ALLOWED_IMPORTS.items():
        dag.assert_remote_module(mod, allowed_project_imports=allowed)


def test_the_hub_package_never_imports_the_assembly_module() -> None:
    """`hub/` 包不得反向依赖组装模块（否则 `hub_server → hub.x → hub_server` 成环）。"""
    for p in sorted(HUB_DIR.glob("*.py")):
        leaves = {i.rsplit(".", 1)[-1] for i in _imports_of(p)}
        assert "hub_server" not in leaves, p.name


def _imports_of(path: Path) -> list[str]:
    out: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.append(node.module)
    return out


def _ledger_key(leaf: str) -> str:
    """按键的**叶子名**从账本取完整模块名。

    刻意不写字面量模块名：`tests/test_subproc_util.py` 按**带引号的 argv 元素**扫「起真服务进程」的
    标记（hub / worker 侧的服务模块名各一条），本文件只是要查一张字典，不该被算成起了进程。
    """
    hits = [k for k in dag.LAYERS if k.endswith(f".{leaf}")]
    assert len(hits) == 1, f"账本里 leaf={leaf!r} 命中 {hits}"
    return hits[0]


def test_the_ledger_puts_the_handler_above_all_mixins() -> None:
    """宿主的秩必须**严格大于**每个混入——否则顶层边就成「同层边」，账本会当场红。"""
    layers = dag.LAYERS
    server = layers[_ledger_key("hub_server")]
    for mod in MIXINS:
        assert layers[f"remote.hub.{mod}"] < server, mod
    # `result` 那一组的秩是**算出来的**：`accept_result` 在 L3 ⇒ 它只能 L4，宿主因此 L5。
    assert layers["remote.hub.result"] == 4
    assert layers["remote.push_dispatch"] < layers["remote.hub.result"] < server
    # S4 第十六刀之后 `HubHandler` 住 `hub/http_face.py`（L5）：它才是「组装五组混入」的那一层，
    # 与 `worker`（作业壳）同层；而 `hub_server` 这个**入口**在它上面两格（L7）。
    assert layers["remote.hub.http_face"] == layers[_ledger_key("worker")] == 5
    assert server == 7


# ───────────────────────── ④ 功能性：真 HubHandler，无 socket ─────────────────────────


class _Probe(hs.HubHandler):
    """**真 `HubHandler` 的子类**，只把三个「写出去」的方法换成记录。

    没有 socket 可写（被测的是路由逻辑，不是 HTTP 栈）⇒ `_json` / `_bytes` 记进列表、
    `_auth_ok` 答预设值。用**子类重写**而不是往实例上挂属性：后者是「替换方法」这个动作，
    `mypy` 的 `method-assign` 与 `ruff` 的 `B010` 会在它身上打架（一个要求别赋值、另一个要求
    别 `setattr`）；子类重写是两个 linter 都认的正当写法。`object.__new__` 绕开
    `BaseHTTPRequestHandler.__init__`（那个会开始处理请求）。

    实例字段与父类同名方法的关系：`path`/`headers`/`rfile`/`hub` 直接塞进去，因为
    `__init__` 被绕过了（父类那份只在有连接时才需要）。
    """

    calls_json: list[tuple[object, int]]
    calls_bytes: list[bytes]
    ok: bool

    def __new__(cls, path: str, hub: Any = None) -> _Probe:
        self = object.__new__(cls)
        self.path = path
        self.headers = Message()
        self.rfile = BytesIO(b"")
        self.hub = hub
        self.calls_json = []
        self.calls_bytes = []
        self.ok = True
        return self

    def __init__(self, path: str, hub: Any = None) -> None:
        """空实现：一切已在 `__new__` 里装好；父类那份会**开始处理请求**，必须绕开。"""

    def _json(self, obj: object, status: int = 200) -> None:
        self.calls_json.append((obj, status))

    def _bytes(
        self,
        data: bytes,
        status: int = 200,
        ctype: str = "application/octet-stream",
        filename: str = "",
    ) -> None:
        self.calls_bytes.append(data)

    def _auth_ok(self) -> bool:
        return self.ok

    def auth(self, ok: bool) -> _Probe:
        """（探针侧的旋钮，非父类成员）只改鉴权答案。"""
        self.ok = ok
        return self


def _job(tmp_path: Path, jid: str = "j1", *, manifest: bool = False) -> Path:
    d = tmp_path / jid
    d.mkdir(parents=True, exist_ok=True)
    if manifest:
        (d / "manifest.json").write_text("{}", encoding="utf-8")
    return d


def test_serve_path_reports_the_specific_reason(tmp_path: Path) -> None:
    """★ 功能性（`_serve_path`）：文件不在 → 404 + **具体**原因；在 → 原样递字节。

    「具体原因」是它存在的理由：`no payload`（还没发布）与 `no ts_code zip`（这轮没打）
    是两条完全不同的下一步，退回通用 not found 就把这条信息抹掉了。
    """
    d = _job(tmp_path)
    rec = _Probe("/jobs/j1/code", hub=_FakeHub(d)).auth(True)
    rec._get_code()
    assert rec.calls_json == [({"error": "no code zip"}, 404)], rec.calls_json

    (d / "code.zip").write_bytes(b"ZIP")
    rec2 = _Probe("/jobs/j1/code", hub=_FakeHub(d)).auth(True)
    rec2._get_code()
    assert rec2.calls_bytes == [b"ZIP"], rec2.calls_bytes
    assert rec2.calls_json == [], rec2.calls_json


def test_job_or_404_gates_auth_then_identity_then_manifest(tmp_path: Path) -> None:
    """★ 功能性（`_job_or_404`）：鉴权不过 ⇒ 静默 None；未知 job ⇒ 404；`known=True` 还查 manifest。

    `known=True` 那一档是 `count` 不出来的语义：没有它，`/start`/`/fail`/`/result` 会在一个
    不存在的 job 上打点/落账。
    """
    d = _job(tmp_path, manifest=False)
    hub = _FakeHub(d)

    rec = _Probe("/jobs/j1/status", hub=hub).auth(False)
    assert rec._job_or_404() is None
    assert rec.calls_json == [], "鉴权不过时助手不该自己回错（`_auth_ok` 已经回过）"

    rec = _Probe("/jobs", hub=hub).auth(True)
    assert rec._job_or_404() is None
    assert rec.calls_json == [({"error": "not found"}, 404)]

    rec = _Probe("/jobs/j1/status", hub=hub).auth(True)
    assert rec._job_or_404() == "j1", "known=False 不查 manifest"

    rec = _Probe("/jobs/j1/start", hub=hub).auth(True)
    assert rec._job_or_404(known=True) is None, "known=True 必须挡住没有 manifest 的 job"
    assert rec.calls_json == [({"error": "not found"}, 404)]

    (d / "manifest.json").write_text("{}", encoding="utf-8")
    rec = _Probe("/jobs/j1/start", hub=hub).auth(True)
    assert rec._job_or_404(known=True) == "j1"


def test_job_body_keeps_the_order_gate_then_body(tmp_path: Path) -> None:
    """★ 功能性（`_job_body`）：`known=True` 那一档必须**先** 404——不能先去读体。

    顺序错了两件事都会变味：① 写错的 URL 也能让服务端白读一段远端体；② 客户端会先收到
    「体不对」而不是「这份 job 不存在」，排障时被指向错的方向。这里用 `/start`（`known=True`）。
    """
    d = _job(tmp_path, manifest=False)
    hub = _FakeHub(d)

    # 没有 manifest（= 未知 job）⇒ 404，尽管请求体也是空的：证明闸在读体**之前**。
    rec = _Probe("/jobs/j1/start", hub=hub).auth(True)
    rec._post_start()
    assert rec.calls_json == [({"error": "not found"}, 404)], rec.calls_json

    # 闸放行、体为空 ⇒ 400（这次才走到读体那一步）。
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    rec = _Probe("/jobs/j1/start", hub=hub).auth(True)
    rec._post_start()
    assert rec.calls_json == [({"error": "空请求体"}, 400)], rec.calls_json

    rec = _Probe("/jobs/j1/start", hub=hub).auth(True)
    rec.headers["Content-Length"] = "2"
    rec.rfile = BytesIO(b"{}")
    rec._post_start()
    assert rec.calls_json == [({"job_id": "j1", "status": "computing"}, 200)], rec.calls_json
    assert hub.started == ["j1"], hub.started


def test_ready_is_the_known_false_sibling_of_start(tmp_path: Path) -> None:
    """同一族里两档的差别被钉住：`/ready` 是 `known=False` ⇒ 没有 manifest 也放行。

    这一条是**契约**而非口味：回传就要开始的那份 job 可能还没落 manifest，`/ready` 若也查
    manifest，回传路径会被一个与它无关的前置条件挡掉。改档要连着改这条断言。
    """
    d = _job(tmp_path, manifest=False)
    hub = _FakeHub(d)
    rec = _Probe("/jobs/j1/ready", hub=hub).auth(True)
    rec.headers["Content-Length"] = "2"
    rec.rfile = BytesIO(b"{}")
    rec._post_ready()
    assert rec.calls_json == [({"job_id": "j1", "status": "ready"}, 200)], rec.calls_json
    assert hub.ready == ["j1"], hub.ready


def test_read_raw_body_leaves_the_bound_to_the_caller(tmp_path: Path) -> None:
    """★ 功能性（`_read_raw_body`）：它只管读满；上限由调用方判（`BC_EPOCH_BODY_MAX`）。"""
    d = _job(tmp_path, manifest=True)
    hub = _FakeHub(d)
    hub.BC_EPOCH_BODY_MAX = 4

    rec = _Probe("/jobs/j1/epoch", hub=hub).auth(True)
    rec.headers["Content-Length"] = "9"
    rec.rfile = BytesIO(b"x" * 9)
    rec._post_bc_epoch()
    assert rec.calls_json == [({"error": "epoch body too large"}, 400)], rec.calls_json

    rec = _Probe("/jobs/j1/epoch", hub=hub).auth(True)
    rec.headers["Content-Length"] = "oops"  # 非法声明 ⇒ 与搬移前同一条 400
    rec._post_bc_epoch()
    assert rec.calls_json == [
        ({"error": "read body failed: invalid literal for int() with base 10: 'oops'"}, 400)
    ], rec.calls_json


class _FakeHub:
    """只提供路由真正用到的那几个面（鸭子类型；调度面的语义由别的测试守）。"""

    BC_EPOCH_BODY_MAX = 4096

    def __init__(self, job_dir: Path) -> None:
        self._dir = job_dir
        self.ready: list[str] = []
        self.started: list[str] = []

    def _job_dir(self, jid: str) -> Path:
        return self._dir

    def set_ready(self, jid: str, worker_id: str = "") -> bool:
        self.ready.append(jid)
        return True

    def start_job(self, jid: str, worker_id: str = "") -> None:
        self.started.append(jid)
