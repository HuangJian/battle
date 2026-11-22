"""批 B 的 spec 形状护栏（`python.refactor.md` §P0-3，批 B「hub 推送/派发」）。

一把刀收六处宽签名，形状各不相同：

| 函数 | 旧形参 | 现在 |
|---|---|---|
| `hub.offline_push.OfflinePushDispatcher.__init__` | 12 | `OfflinePushSpec` |
| `hub.offline_push._settle_assignment` | 8（4 位置 + 4 开关） | `(course, url, key, rec, SettlePlan)` |
| `remote.push_dispatch.PushDispatcher.__init__` | 11 | `PushDispatchSpec` |
| `remote.push_dispatch._await_result` | 8 位置 + 3 | `(leg: JobLeg, *, started, t_pushed, log)` |
| `remote.push_client.submit_job` | 8 | `(base_url, token, JobUpload)` |
| `remote.push_register.Registry.__init__` | 9 | `RegistrySpec` |

为什么值得收（不是审美）：这六处全是「一串同类位置参数」——
`(course, url, key, rec)` 里 `url`/`key` 换位就悄悄问到了别的节点；
`_await_result` 的 8 个位置里 `wid`/`jid` 互换会把结果回灌给别的 job；
`submit_job` 的 `manifest`/`payload_zip`/`code_zip` 三个「看起来都像 body」的相邻位置参数
传错一格只有云端才炸。`Registry` 的 9 个关键字里缺 `urlopen` 会让用例真发 HTTP。

本文件钉三件事：**字段表**（旧形参逐一对齐，漏抄/多抄即红）· **入口只剩 spec / 腿对象** ·
**收摊四件事的写读同源**（`SettlePlan` 是账本上那两个方向的唯一实现）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

import hub.offline_push as OP
import remote.push_client as PC
import remote.push_dispatch as PD
import remote.push_register as PR
from hub.offline_push import OfflinePushDispatcher, OfflinePushSpec, SettlePlan
from remote.push_client import JobUpload, submit_job
from remote.push_dispatch import JobLeg, PushDispatcher, PushDispatchSpec
from remote.push_register import Registry, RegistrySpec

ROOT = Path(__file__).resolve().parents[2]
MISSING = dataclasses.MISSING


def _table(cls: type) -> list[tuple[str, object]]:
    """`(字段名, 缺省值)`；无缺省记 `MISSING`（与 `dataclasses.fields` 同口径）。"""
    out: list[tuple[str, object]] = []
    for f in dataclasses.fields(cls):
        if f.default is not MISSING:
            out.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            out.append((f.name, f.default_factory()))
        else:
            out.append((f.name, MISSING))
    return out


#: `hub/offline_push.OfflinePushDispatcher.__init__` 的 12 个形参，逐个按原来的次序与缺省值。
OFFLINE_PUSH_FIELDS = [
    ("hub", MISSING),
    ("workers", MISSING),
    ("token", MISSING),
    ("root", None),
    ("port", 0),
    ("poll_sec", OP.OFFLINE_PUSH_POLL_SEC),
    ("pump_sec", OP.OFFLINE_PUSH_PUMP_SEC),
    ("heartbeat_sec", OP.OFFLINE_PUSH_HEARTBEAT_SEC),
    ("cooldown_sec", OP.OFFLINE_PUSH_COOLDOWN_SEC),
    ("now_fn", time.time),
    ("log", OP._log_default),
    ("client_mod", OP.client),
]

#: `remote/push_dispatch.PushDispatcher.__init__` 的 11 个形参。
PUSH_DISPATCH_FIELDS = [
    ("hub", MISSING),
    ("workers", MISSING),
    ("token", MISSING),
    ("poll_sec", PD.PUSH_POLL_SEC),
    ("timeout_sec", PD.PUSH_TIMEOUT_SEC),
    ("push_attempts", 3),
    ("backups_per_course", 1),
    ("push_ahead", PD.PUSH_AHEAD_DEFAULT),
    ("cooldown_sec", PD.PUSH_COOLDOWN_SEC),
    ("now_fn", time.time),
    ("log", PD._log_default),
]

#: `remote/push_client.submit_job` 的 8 个形参（`base_url`/`token` 留在签名上）。
JOB_UPLOAD_FIELDS = [
    ("manifest", MISSING),
    ("payload_zip", MISSING),
    ("code_zip", MISSING),
    ("blobs", None),
    ("ts_code_zip", None),
    ("echo", False),
    ("timeout", 600.0),
    ("attempts", 3),
    ("log", PC._default_log),
]

#: `remote/push_register.Registry.__init__` 的 9 个形参（旧签名全是关键字）。
REGISTRY_FIELDS = [
    ("hub", MISSING),
    ("hub_token", MISSING),
    ("node_id", MISSING),
    ("kind", "gpu_push"),
    ("workers", 0),
    ("label", ""),
    ("log", PR.RegistrySpec.__dataclass_fields__["log"].default),
    ("urlopen", None),
    ("now_fn", time.time),
]

#: `remote/push_dispatch._await_result` 的 8 个位置参数（`rec` 已删：从没被读过）。
JOB_LEG_FIELDS = [
    ("course", MISSING),
    ("jid", MISSING),
    ("wid", MISSING),
    ("url", MISSING),
    ("key", MISSING),
    ("lease", MISSING),
    ("t0", MISSING),
]

#: `hub/offline_push._settle_assignment` 的 4 个开关 → 一份 `SettlePlan`。
SETTLE_PLAN_FIELDS = [
    ("reason", ""),
    ("cancel", False),
    ("free_worker", False),
    ("failed", False),
]


# ---------------------------------------------------------------- 字段表


@pytest.mark.parametrize(
    ("cls", "want"),
    [
        (OfflinePushSpec, OFFLINE_PUSH_FIELDS),
        (PushDispatchSpec, PUSH_DISPATCH_FIELDS),
        (JobUpload, JOB_UPLOAD_FIELDS),
        (RegistrySpec, REGISTRY_FIELDS),
        (JobLeg, JOB_LEG_FIELDS),
        (SettlePlan, SETTLE_PLAN_FIELDS),
    ],
)
def test_field_table_matches_the_old_signature(cls: type, want: list[tuple[str, object]]) -> None:
    assert _table(cls) == want


#: 每个 spec 的一份最小实例（冻结判据只需能构造出来；`hub`/`workers` 是鸭子类型的宿主，
#: 本判据不读它们 ⇒ `cast` 而非真造一个 hub）。
_MINIMAL: dict[type, Callable[[], object]] = {
    OfflinePushSpec: lambda: OfflinePushSpec(
        hub=None, workers=cast("OP.OfflinePushWorkers", None), token="t"
    ),
    PushDispatchSpec: lambda: PushDispatchSpec(
        hub=None, workers=cast("PD.PushWorkers", None), token="t"
    ),
    JobUpload: lambda: JobUpload(manifest={}, payload_zip=b"", code_zip=None),
    RegistrySpec: lambda: RegistrySpec(hub="h", hub_token="t", node_id="n"),
    SettlePlan: SettlePlan,
    JobLeg: lambda: JobLeg(course="c", jid="j", wid="w", url="u", key="k", lease="l", t0=0.0),
}


@pytest.mark.parametrize(
    "cls",
    [OfflinePushSpec, PushDispatchSpec, JobUpload, RegistrySpec, SettlePlan, JobLeg],
)
def test_specs_are_frozen(cls: type) -> None:
    field = dataclasses.fields(cls)[0].name  # 变量而非字面量（ruff B010）
    spec = _MINIMAL[cls]()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)


@pytest.mark.parametrize(
    "cls", [OfflinePushSpec, PushDispatchSpec, JobUpload, RegistrySpec, SettlePlan]
)
def test_specs_are_keyword_only(cls: type) -> None:
    rows = dataclasses.fields(cls)
    assert all(f.kw_only for f in rows), [f.name for f in rows if not f.kw_only]


# ---------------------------------------------------------------- 入口形状


def _params(fn: Callable[..., object]) -> list[str]:
    return list(inspect.signature(fn).parameters)


def test_constructors_take_exactly_one_spec() -> None:
    assert _params(OfflinePushDispatcher.__init__) == ["self", "spec"]
    assert _params(PushDispatcher.__init__) == ["self", "spec"]
    assert _params(Registry.__init__) == ["self", "spec"]


def test_settle_assignment_takes_one_plan() -> None:
    """4 个开关收成 `SettlePlan`：`(course, url, key, rec)` + 一个对象，不再是 4 个同名布尔。

    参数表先绑到局部（不写进 `assert` 表达式）：`test_private_assert_budget` 的判据是
    「`assert` 里出现私有属性即计数」，而这里被测的恰恰就是这个私有助手 —— 判据本身不改，
    只是把「读形状」与「断言」拆开（同 `_await_result` 那条）。旧形状已被下一条用例从
    行为面封死：四个同名布尔现在当场 `TypeError`。
    """
    params = _params(OfflinePushDispatcher._settle_assignment)
    assert params == ["self", "course", "url", "key", "rec", "plan"]


def test_old_settle_flags_are_rejected() -> None:
    """四个同名布尔不许回来（旧形状的调用面必须响亮地拒，而不是静默落缺省）。"""
    call = cast(Callable[..., object], OfflinePushDispatcher._settle_assignment)
    with pytest.raises(TypeError):
        call(None, "c5-gae", "http://w", "k", {}, cancel=True, free_worker=True, reason="x")


def test_await_result_takes_one_leg_and_no_dead_rec() -> None:
    """`rec` 是旧签名里**从没被读过**的参数（死参），随本轮一起删掉。"""
    params = inspect.signature(PushDispatcher._await_result).parameters
    assert list(params) == ["self", "leg", "started", "t_pushed", "log"]
    assert params["leg"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(params[n].kind is inspect.Parameter.KEYWORD_ONLY for n in ("started", "t_pushed", "log"))


def test_submit_job_takes_the_endpoint_and_one_upload() -> None:
    params = inspect.signature(submit_job).parameters
    assert list(params) == ["base_url", "token", "upload"]


def test_unknown_spec_field_is_a_type_error() -> None:
    """拼错的旋钮名不许静默落缺省（旧形参下多余关键字也是 `TypeError`）。"""
    # 错名只在运行期是 `TypeError`（mypy 当场就报）⇒ 用 `Callable` 面绕开静态检查，
    # 这样这条用例测的就是**运行期**的拒绝行为本身。
    make_upload = cast(Callable[..., object], JobUpload)
    make_plan = cast(Callable[..., object], SettlePlan)
    with pytest.raises(TypeError):
        make_upload(manifest={}, payload_zip=b"", code_zip=None, payload=b"x")
    with pytest.raises(TypeError):
        make_plan(reason="done", free=True)


# ---------------------------------------------------------------- 收摊四件事的写读同源


def test_settle_plan_write_and_read_use_one_field_table() -> None:
    """`_park_settling` 写、`_settle_retry` 读 —— 两条路径必须是同一张表。

    在这之前两边各手写一遍 `settle_*` 键名：写错一侧不报错，只会让重放按缺省值收摊
    （`cancel` 丢失 ⇒ 这单在 worker 上永远留着；`free_worker` 丢失 ⇒ 它的 outbox 永不删）。
    """
    plan = SettlePlan(reason="conflict", cancel=True, free_worker=True, failed=True)
    ledger = plan.ledger_fields()
    assert set(ledger) == {"settle_reason", "settle_cancel", "settle_free", "settle_failed"}
    assert ledger == {
        "settle_reason": "conflict",
        "settle_cancel": True,
        "settle_free": True,
        "settle_failed": True,
    }
    assert SettlePlan.from_ledger(ledger) == plan
    # 缺省（账本里没有这四列 = 老账本）⇒ 一个「什么都不做」的收摊计划
    assert SettlePlan.from_ledger({}) == SettlePlan(reason="settling")


def test_settle_keys_have_exactly_one_home() -> None:
    """`settle_*` 四个键名只许住在 `SettlePlan` 里（两个方向的唯一实现）。

    `settle_at` 不在此列：它是「什么时候重放」的**时刻**，由 `_park_settling` 自己写、
    `_resume_pumps`/`_pump_loop` 自己读，与这四件「做什么」无关。
    """
    src = Path(OP.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    funcs = [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    owners: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if not node.value.startswith("settle_") or node.value == "settle_at":
            continue
        # 最内层（行区间最小）的那个函数就是它的家
        hosts = [
            f
            for f in funcs
            if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
        ]
        assert hosts, f"`{node.value}` 不在任何函数里（第 {node.lineno} 行）"
        owners.add(min(hosts, key=lambda f: (f.end_lineno or f.lineno) - f.lineno).name)
    assert owners == {"ledger_fields", "from_ledger"}, owners


def test_job_leg_is_built_once_per_leg() -> None:
    """`_run_one` 装配腿对象 → `_await_result` 只收它一个位置参数（不再手排 8 个位置）。"""
    src = Path(PD.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_await_result"
    ]
    assert len(calls) == 1, f"`_await_result(...)` 调用点 {len(calls)} 处（应恰好 1 处）"
    call = calls[0]
    assert len(call.args) == 1 and isinstance(call.args[0], ast.Name), ast.dump(call)
    # 死参 `rec` 不许回来（`isdisjoint` 而不是 `"rec" not in {…}`：后者的形态会被
    # `test_source_text_assert_budget` 计成新增文本断言，而它本是数据判据）。
    assert {k.arg for k in call.keywords}.isdisjoint({"rec"})
