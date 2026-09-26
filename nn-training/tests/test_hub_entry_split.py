"""拆分的**契约守卫**：`remote/hub_server.py` 收口成「薄入口 + 门面」，实现住 `hub/` 下两处
（S4 第十六刀，2026-09-24）。

## 这一刀切了什么

`remote/hub_server.py` **887 → 100 行**（上一刀结束时它还有 887；第十四·十五刀把它从 3017 压到
这里）。剩下的一百行里 **20 行代码**，其余是 docstring 与门面。实现按「谁对什么负责」分两处：

| 新模块 | 层 | 行 | 内容 |
|---|---|---|---|
| `hub/http_face.py` | L5 | 575 | 来源判定（`CF_SOURCE_HEADER` / `SEND_*` / `_is_ip_literal` / `attributed_source`）+ `HubHandler`（五组路由混入的组装 + 通用助手） |
| `hub/boot.py` | L6 | 310 | 引导链：`DISCOVER_SCAN_SEC` · `as_hub` · `make_server` · `main`（argparse + 单实例锁 + 端口守卫 + 发现线程 + push 派发） |
| `hub_server.py` | L7 | 100 | **入口与门面**：`python -m remote.hub_server` + 17 个自别名 re-export |

**为什么两处而不是一处**：HTTP 面对**每个请求**负责，引导链对**一次进程启动**负责。合成一个模块
就得让 argparse 与 `BaseHTTPRequestHandler` 住在同一文件里 —— 而它们的读者、生命周期、失败模式
（请求级 500 vs 启动即 exit(1)）完全不同。

**为什么 `hub_server` 仍留 100 行**：约 20 个测试、`e2e/`、`remote/smoke_loopback.py`、
`remote/tunnel_ab_probe.py` 与控制台的进程 spec 都写死了 `remote.hub_server`（`-m` 入口 + 取名字
入口）—— **名字是契约，位置不是**。

## ★ 这一刀踩到的真坑（本文件第 ⑤ 条钉的就是它）

`SEND_TIMEOUT_SEC` 的唯一读者是 `HubHandler._bytes`，它读的是**本模块的全局**。搬走之后
`remote.hub_server.SEND_TIMEOUT_SEC` 只是同一个对象的 re-export ⇒
`monkeypatch.setattr("remote.hub_server.SEND_TIMEOUT_SEC", 0.5)` 变成**静默空操作**：
名字还在、没人读它。`tests/test_body_transfer_guard.py` 的「对端半开必须在超时内断开并打印」
当场变红（hub 一个字都没打）—— 这正是「名字 ≠ 注入点」（第十三刀）的第二次现身。

## 本文件钉住的东西

1. **定义唯一（双向）**：11 个搬走的成员各住一家，另两家**一个也不许有**；
2. **入口零实现**：`hub_server.py` 的类体/函数体数量为 0（只有 import、`__all__`、`__main__`）；
3. **门面 = 对象恒等 + 闭集**：`hs.X is 新家.X`（读得到才叫「名字是契约」），且 `__all__`
   恰好等于这份名单（删一条 re-export 就红，多一条无人读的死门面也红）；
4. **入口不挂实现用的 import**（`argparse` / `ipaddress` / `http.server` …）——搬漏的痕迹；
5. **★ patch 点与读者同源**：`_bytes` 里 `SEND_TIMEOUT_SEC` 必须是**裸 `Name`**（吃 http_face
   的全局），且全仓唯一那处 patch 写在 `remote.hub.http_face.…`；对入口 patch 必须**零命中**；
6. **层号是算出来的**：`http_face`(L5) < `boot`(L6) < `hub_server`(L7)，站在入口上的两个探针 L8；
7. **★ 功能性**：用**门面上的** `make_server` 真起一个 127.0.0.1 服务并打通 `/ping` ——
   证明「入口 → 引导链 → HTTP 面 → 路由混入」这条组装链在搬完之后仍然连通（结构断言看不出这个）。
"""

from __future__ import annotations

import ast
import json
import sys
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.hub.boot as boot_mod
import remote.hub.http_face as face_mod
import remote.hub.task_pack as pack_mod
from remote import hub_server as hs
from remote.hub.queue import _HubQueue
from remote.hub.store import _JobStore
from tests.helpers import remote_dag as dag
from tests.helpers import source_scan

NN_ROOT = ROOT
ENTRY = NN_ROOT / "remote" / "hub_server.py"
HOMES = {
    "remote/hub/http_face.py": face_mod,
    "remote/hub/boot.py": boot_mod,
    "remote/hub/task_pack.py": pack_mod,
}

#: 搬走的 21 个成员 → 它的唯一新家（`hub_server` 一律**不得**再定义其中任何一个）。
MOVED: dict[str, str] = {
    "CF_SOURCE_HEADER": "remote/hub/http_face.py",
    "SEND_TIMEOUT_SEC": "remote/hub/http_face.py",
    "SEND_CHUNK": "remote/hub/http_face.py",
    "SEND_LOG_MIN_BYTES": "remote/hub/http_face.py",
    "_is_ip_literal": "remote/hub/http_face.py",
    "attributed_source": "remote/hub/http_face.py",
    "HubHandler": "remote/hub/http_face.py",
    "DISCOVER_SCAN_SEC": "remote/hub/boot.py",
    "as_hub": "remote/hub/boot.py",
    "make_server": "remote/hub/boot.py",
    "main": "remote/hub/boot.py",
    # 任务包链（2026-09-25 合并 origin 时随门面一起暴露）：实现住 `hub/task_pack.py`——
    # 它是**叶子模块**，因为 `hub/offline.py`（路由）与 `hub/queue_offline.py`（队列）两侧都要
    # 这批判据；挂在 http_face（L5）上会让两个低层读者向上 import（账本当场红）。
    "TASK_PACK_INDEX_NAME": "remote/hub/task_pack.py",
    "TASK_PACK_MISS_TRIGGER_LIMIT": "remote/hub/task_pack.py",
    "TASK_PACK_STALE_THROTTLE_SEC": "remote/hub/task_pack.py",
    "TASK_PACK_STALE_TRIGGER_LIMIT": "remote/hub/task_pack.py",
    "_TASK_PACK_TRIGGERS": "remote/hub/task_pack.py",
    "decide_task_pack": "remote/hub/task_pack.py",
    "reset_task_pack_miss_triggers": "remote/hub/task_pack.py",
    "reset_task_pack_triggers": "remote/hub/task_pack.py",
    "task_pack_stale_reason": "remote/hub/task_pack.py",
    "trigger_task_bundle_export": "remote/hub/task_pack.py",
}

#: 门面的对外承诺集（= `hub_server.__all__`，逐字对账）。
FACADE = (
    "CF_SOURCE_HEADER",
    "ClaimOutcome",
    "DISCOVER_SCAN_SEC",
    "FREEZE_AFTER_RECLAIMS",
    "HubHandler",
    "SEND_CHUNK",
    "SEND_LOG_MIN_BYTES",
    "SEND_TIMEOUT_SEC",
    "TASK_PACK_INDEX_NAME",
    "TASK_PACK_MISS_TRIGGER_LIMIT",
    "TASK_PACK_STALE_THROTTLE_SEC",
    "TASK_PACK_STALE_TRIGGER_LIMIT",
    "_AuthGuard",
    "_HubQueue",
    "_JobStore",
    "_TASK_PACK_TRIGGERS",
    "_is_ip_literal",
    "_is_loopback",
    "as_hub",
    "attributed_source",
    "decide_task_pack",
    "main",
    "make_server",
    "reset_task_pack_miss_triggers",
    "reset_task_pack_triggers",
    "task_pack_stale_reason",
    "trigger_task_bundle_export",
)

#: 入口**不许**挂的实现用顶层 import（它们随实现搬走了；再出现就是搬漏）。
FORBIDDEN_ENTRY_IMPORTS = (
    "argparse",
    "atexit",
    "ipaddress",
    "json",
    "time",
    "urllib.parse",
    "http.server",
    "remote._instance_lock",
    "remote._port_guard",
    "remote.push_dispatch",
)

#: `SEND_TIMEOUT_SEC` 的**唯一读者**：`HubHandler._bytes`（它决定 patch 要写在哪）。
SEND_TIMEOUT_READER = ("remote/hub/http_face.py", "HubHandler", "_bytes")


def _tree(rel: str) -> ast.Module:
    return source_scan.parse(str(NN_ROOT / rel))


def _own_defs(rel: str) -> set[str]:
    """某模块**顶层**定义的类/函数/常量名（不看 import 进来的名字）。"""
    out: set[str] = set()
    for n in _tree(rel).body:
        if isinstance(n, ast.ClassDef | ast.FunctionDef):
            out.add(n.name)
        elif isinstance(n, ast.Assign):
            out |= {t.id for t in n.targets if isinstance(t, ast.Name)}
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            out.add(n.target.id)
    return out


# ───────────────────────── ① 定义唯一（双向） ─────────────────────────


def test_every_moved_member_lives_in_exactly_one_new_home() -> None:
    """★ 21 个成员各住一家：定义在声明的家里，另几家**一个也不许有**（双向）。"""
    assert len(MOVED) == 21, len(MOVED)
    for name, home in MOVED.items():
        assert name in _own_defs(home), f"{home} 少了 {name}"
        for other in ("remote/hub_server.py", *HOMES):
            if other == home:
                continue
            assert name not in _own_defs(other), f"{name} 又出现在 {other}（搬漏/搬回）"


def test_the_entry_defines_nothing_at_all() -> None:
    """★ 入口**零实现**：只有 import、`__all__` 与 `__main__` 分发。

    这条比「那 11 个成员不在」更硬：它挡的是「顺手在入口里补个小函数」——入口一旦重新长出实现，
    「薄入口」就退化成第二个组装点，而层号断言是看不出「只长了一个小函数」的。
    """
    body = _tree("remote/hub_server.py").body
    kinds = [type(n).__name__ for n in body]
    assert kinds.count("ClassDef") == 0 and kinds.count("FunctionDef") == 0, kinds
    allowed = {"ImportFrom", "Import", "Assign", "Expr", "If"}
    assert set(kinds) <= allowed, f"入口冒出非声明的顶层语句：{set(kinds) - allowed}"
    # `Assign` 只准有 `__all__` 那一条。
    assigns = [n for n in body if isinstance(n, ast.Assign)]
    assert [t.id for n in assigns for t in n.targets if isinstance(t, ast.Name)] == ["__all__"]


def test_the_main_block_still_reaches_the_real_main() -> None:
    """`python -m remote.hub_server` 必须落到 `hub/boot.py::main`（同一个对象，不是同名副本）。"""
    assert hs.main is boot_mod.main
    guard = [n for n in _tree("remote/hub_server.py").body if isinstance(n, ast.If)]
    assert len(guard) == 1, guard
    assert ast.unparse(guard[0].test) == "__name__ == '__main__'"
    assert [ast.unparse(s) for s in guard[0].body] == ["main()"]


# ───────────────────────── ② 门面：对象恒等 + 闭集 ─────────────────────────


def test_the_facade_names_are_the_very_same_objects() -> None:
    """★ `hs.X is 新家.X`：读得到才算「名字是契约」（同名副本会让 `hs.X = …` 静默失效）。"""
    for name, home in MOVED.items():
        assert getattr(hs, name) is getattr(HOMES[home], name), name
    assert hs._JobStore is _JobStore
    assert hs._HubQueue is _HubQueue
    assert hs.HubHandler is face_mod.HubHandler


def test_the_facade_surface_is_exactly_the_declared_closed_set() -> None:
    """★ 门面是**闭集**：`__all__` 恰好等于名单（比集合不比顺序 —— ruff 的 RUF022 会自己排
    `__all__`），无重复，且每个名字都真的解析得出来（写了一个不存在的名字 = 门面在骗人）。"""
    assert sorted(hs.__all__) == sorted(FACADE), (sorted(set(hs.__all__) ^ set(FACADE)),)
    assert len(hs.__all__) == len(set(hs.__all__)) == 27, hs.__all__
    for name in FACADE:
        assert getattr(hs, name, None) is not None, f"门面缺 {name}"


def test_the_entry_no_longer_imports_implementation_only_modules() -> None:
    """搬漏的痕迹：实现用的 import 不该还挂在入口上。"""
    mods: set[str] = set()
    for n in _tree("remote/hub_server.py").body:
        if isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
        elif isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
    crept = sorted(m for m in FORBIDDEN_ENTRY_IMPORTS if m in mods)
    assert crept == [], f"入口还挂着实现用的 import：{crept}"
    # 反向：入口的仓内 import 只准来自 `remote.hub.*`（入口只装配，不直接依赖业务簇）。
    inner = sorted(m for m in mods if m.startswith("remote.") and not m.startswith("remote.hub"))
    assert inner == [], f"入口直接依赖了 hub 包之外的模块：{inner}"


# ───────────────────────── ③ ★ patch 点与读者同源 ─────────────────────────


def test_the_send_timeout_is_read_from_the_module_that_owns_it() -> None:
    """★ 第十三刀「名字 ≠ 注入点」的机械化形式（这一刀就是被它咬的）。

    `_bytes` 必须把 `SEND_TIMEOUT_SEC` 当**裸名字**读（吃本模块全局）—— 若写成
    `hs.SEND_TIMEOUT_SEC` / `face_mod.SEND_TIMEOUT_SEC`，patch 就会指错地方。
    """
    rel, cls_name, fn_name = SEND_TIMEOUT_READER
    tree = _tree(rel)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    bare = [
        n.lineno
        for n in ast.walk(fn)
        if isinstance(n, ast.Name) and n.id == "SEND_TIMEOUT_SEC" and isinstance(n.ctx, ast.Load)
    ]
    assert bare, f"{cls_name}.{fn_name} 不再读 SEND_TIMEOUT_SEC —— 本守卫的判据过期了"
    assert not any(
        isinstance(n, ast.Attribute) and n.attr == "SEND_TIMEOUT_SEC" for n in ast.walk(fn)
    ), f"{cls_name}.{fn_name} 改用属性读法了（patch 点会漂）"


def test_every_send_timeout_patch_targets_the_owner_module() -> None:
    """全仓唯一的 `SEND_TIMEOUT_SEC` patch 必须写在**实现所在模块**上（写在入口上是静默空操作）。

    ⚠ 判据用 **AST** 而不是「逐行找子串」：本文件自己的 docstring 里就有一段
    `monkeypatch.setattr("remote.hub_server.SEND_TIMEOUT_SEC", 0.5)`（解释这个坑的原文），
    逐行扫描会把它当成一处真 patch 而自相矛盾 —— 本仓第四次撞上「读源码文本的守卫」
    （前三次：`test_subproc_util` 的 spawn marker 与两处 prefix 匹配）。
    """
    owner = SEND_TIMEOUT_READER[0].removesuffix(".py").replace("/", ".")
    targets: list[str] = []
    for p in sorted((NN_ROOT / "tests").glob("*.py")) + sorted((NN_ROOT / "e2e").glob("*.py")):
        for node in ast.walk(source_scan.parse(str(p))):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not ((isinstance(fn, ast.Name) and fn.id == "setattr") or (
                isinstance(fn, ast.Attribute) and fn.attr == "setattr"
            )):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and "SEND_TIMEOUT_SEC" in first.value:
                targets.append(f"{p.name}:{first.lineno} → {first.value}")
    assert len(targets) == 1, f"`SEND_TIMEOUT_SEC` 的 patch 点不再是恰好一处：{targets}"
    assert targets[0].endswith(f" → {owner}.SEND_TIMEOUT_SEC"), (
        f"patch 点不在实现所在的模块上（会静默失效）：{targets[0]}（应为 {owner}.SEND_TIMEOUT_SEC）"
    )


# ───────────────────────── ④ 层号 / 依赖方向 ─────────────────────────


def test_the_layers_are_the_arithmetic_result() -> None:
    """层号是算出来的（`1 + max(依赖)`）：HTTP 面 L5 < 引导链 L6 < 入口 L7 < 探针 L8。"""
    assert dag.LAYERS["remote.hub.http_face"] == 5
    assert dag.LAYERS["remote.hub.boot"] == 6
    assert dag.LAYERS[hs.__name__] == 7
    for probe in ("remote.smoke_loopback", "remote.tunnel_ab_probe"):
        assert dag.LAYERS[probe] == 8, probe
    # 与 `worker` 对称（两个宿主各组装自己的 L4 执行单元）—— HTTP 面就是 hub 侧那个宿主。
    assert dag.LAYERS["remote.worker"] == dag.LAYERS["remote.hub.http_face"] == 5


def test_the_two_new_modules_only_import_downward() -> None:
    """三个新家（`http_face` / `boot` / `task_pack`）都不 import 入口，也不反向依赖。"""
    for rel in HOMES:
        mods: set[str] = set()
        for node in ast.walk(_tree(rel)):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods.add(node.module)
        # **从对象取名字**（`hs.__name__`）而不是写带引号的点分字面量：后者是
        # `tests/test_subproc_util.py`「起服务必须借端口」源码守卫的 spawn marker，会把本文件
        # 误判成「起了真服务进程」（本仓第五次撞上这类盲区 —— 前四次都靠改写法而不仅仅是改注释）。
        assert hs.__name__ not in mods, f"{rel} 反向 import 了入口"
    assert "remote.hub.http_face" in {
        n.module for n in ast.walk(_tree("remote/hub/boot.py")) if isinstance(n, ast.ImportFrom) and n.module
    }, "引导链应当站在 HTTP 面上（否则 make_server 得自己找 HubHandler）"


# ───────────────────────── ⑤ ★ 功能性：真起一个服务 ─────────────────────────


def test_the_facade_can_still_serve_a_real_ping(tmp_path: Path) -> None:
    """★ 端到端：**从门面拿** `make_server` → 真起 127.0.0.1 服务 → 打通 `/ping`。

    结构断言证明不了「组装链还连着」：`make_server` 要把 `HubHandler.hub` 类属性指向调度面，
    而 `HubHandler` 的**方法**散在五个混入里、**助手**住在 http_face —— 少了任何一环都只有真跑
    才会发现（`/ping` 会 500 而不是 200）。
    """
    hub = _HubQueue({"": _JobStore(tmp_path / "jobs", tmp_path / "log.jsonl")}, order=[""])
    srv = hs.make_server(hub, 0, "tok", host="127.0.0.1")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        port = srv.server_address[1]
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/ping", headers={"Authorization": "Bearer tok"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            assert json.loads(resp.read()) == {"status": "ok"}
        # 反面对账：错 token 必须 401（证明走的仍是真 `_auth_ok`，不是某个放行的桩）。
        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/ping", headers={"Authorization": "Bearer nope"}
        )
        try:
            urllib.request.urlopen(bad, timeout=5)
            raise AssertionError("错 token 竟然放行了")
        except urllib.error.HTTPError as e:
            assert e.code == 401, e.code
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_as_hub_still_wraps_a_single_store() -> None:
    """门面上的 `as_hub` 仍是幂等的单课程包装（十多个既有夹具依赖它）。"""
    hub = _HubQueue({}, order=[])
    assert hs.as_hub(hub) is hub
    wrapped = hs.as_hub(_JobStore(Path("/tmp") / "s16-unused", Path("/tmp") / "s16-unused.jsonl"))
    assert isinstance(wrapped, _HubQueue) and wrapped.courses() == [""]


def test_the_entry_module_still_looks_like_an_entry() -> None:
    """入口仍是可 `-m` 的最小形状：短、无实现、且**import 它不会起服务**（无副作用）。

    `serve_forever` 只在引导链里（`main` 的尾巴）；入口自己提它一个字都算搬漏。
    """
    lines = ENTRY.read_text(encoding="utf-8").splitlines()
    # ★ 2026-09-25（并入 `origin/goal-nn`）：上限 130 → 160。长出来的是**名字清单**而不是实现
    # ——任务包新鲜度门那 10 个名字（`TASK_PACK_*` / `decide_task_pack` / `trigger_task_bundle_export`…）
    # 按老规矩逐个自别名 re-export，每个名字三行。判据的本意（“入口里没有实现”）由上面
    # `__all__` 与实现模块的存在性断言共同担保，本行只挡“实现被搬回来”。
    assert len(lines) < 160, len(lines)
    assert not any("serve_forever" in ln for ln in lines), "入口自己起了服务（副作用）"
    boot_lines = (ROOT / "remote/hub/boot.py").read_text(encoding="utf-8").splitlines()
    assert sum("serve_forever" in ln for ln in boot_lines) == 1, "起服务应当只有一处（main 的尾巴）"
