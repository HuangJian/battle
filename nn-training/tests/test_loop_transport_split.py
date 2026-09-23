"""S4 契约（2026-09-23）—— 传输/发布层的实现只有一份，`rl/loop_steps.py` 只是门面。

`rl/loop_steps.py` 原本把两件事塞在一个 2328 行文件里：`TrainingSteps` mixin（单轮结算与
梯度步）**与**一组模块级自由函数（课程/rollout 源解析、transport 选择、hub 推送、节点
failover、kickstart 系数、远端可重试异常集合）。后者没有一个是方法，只是历史上按大小
拆文件时被一起搬了过来。S4 把它们整体移到 `rl/loop_transport.py`，`loop_steps` 留门面
re-export——所有既有的 `from rl.loop_steps import X` 调用点因此不必改。

本文件钉住这次拆分**不会腐烂**的四条：

1. 那些名字在 `loop_transport` 里**定义**，且**不再**在 `loop_steps` 里定义（门面只能是
   门面，不许哪天有人「就地补一个」）；
2. 门面 re-export 的是**同一个对象**（`is`，不是同名副本）；
3. **DI seam 随实现走**：`_push_job_round` 读 `rl.loop_transport._push_submit`，而
   `TrainingSteps` 的方法仍读 `rl.loop_steps._push_submit`——两边各自解析自己的模块全局。
   这一条是功能性的（真调一次 `_push_job_round`），因为「patch 目标写错」正是这类拆分最
   容易犯、且**最静默**的错：测试会绿，而注入根本没生效。
4. `loop_transport` 不得反向 import `rl.loop_steps`（否则门面会变成环）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

import rl.loop_steps as steps
import rl.loop_transport as transport

NN_ROOT = Path(__file__).resolve().parent.parent

#: 拆到 `loop_transport` 的定义（2 个异常类 + 19 个函数）。
MOVED_DEFS = (
    "BundleExportedError",
    "SmokeVoidRoundError",
    "_course_cf_tunnel",
    "_gate_round_shards",
    "_gpu_push_nodes",
    "_hub_push_opt_in",
    "_kickstart_ref_payload",
    "_push_job_round",
    "_push_over_nodes",
    "_remote_forward_agg",
    "_rollout_source",
    "_run_segment_iters",
    "_run_wait_sec",
    "_wire_from_result",
    "fatal_remote_http",
    "kickstart_coef",
    "kickstart_warn_kind",
    "remote_retryable_exceptions",
    "require_remote_transport",
    "resolve_hub_push",
    "resolve_transport",
)

#: 随迁的 4 个模块级常量。
MOVED_CONSTS = (
    "FATAL_REMOTE_HTTP",
    "REMOTE_TRANSPORTS",
    "ROLLOUT_SRCS",
    "RUN_WAIT_DEFAULT_SEC",
)

MOVED = MOVED_DEFS + MOVED_CONSTS


def _top_level_names(path: Path) -> set[str]:
    """该文件**顶层**定义的名字（函数 / 类 / 赋值目标）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


def test_moved_names_are_defined_in_loop_transport_only() -> None:
    """定义在 `loop_transport`；`loop_steps` 里**不得**再有同名顶层定义。"""
    transport_defs = _top_level_names(NN_ROOT / "rl" / "loop_transport.py")
    steps_defs = _top_level_names(NN_ROOT / "rl" / "loop_steps.py")

    assert set(MOVED) <= transport_defs, sorted(set(MOVED) - transport_defs)
    crept_back = sorted(set(MOVED) & steps_defs)
    assert crept_back == [], (
        f"这些名字又在 rl/loop_steps.py 里被定义了（应只留门面 re-export）：{crept_back}"
    )


def test_training_steps_still_lives_in_loop_steps() -> None:
    """反向事实：mixin 本体没被顺手带走。"""
    assert steps.TrainingSteps.__module__ == "rl.loop_steps"


def test_facade_reexports_the_same_objects() -> None:
    """门面必须是同一对象（`is`），不是同名副本（否则 patch 一半、改一半）。"""
    for name in MOVED:
        assert getattr(steps, name) is getattr(transport, name), name


def test_facade_is_declared_in_dunder_all() -> None:
    """门面名要进 `__all__`——否则 ruff 的 F401 会把「有意的 re-export」判成漏删导入。"""
    assert set(MOVED) <= set(steps.__all__)


def test_loop_transport_does_not_import_loop_steps() -> None:
    """门面不得变成环：`loop_transport` 不许反向 import `loop_steps`。"""
    assert "rl.loop_steps" not in _imports(NN_ROOT / "rl" / "loop_transport.py")


def test_moved_push_path_reads_the_loop_transport_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DI seam 随实现迁移（功能性断言，离线：submits/waits 全 fake）。

    把 `loop_steps` 的 seam 换成「一读就炸」，`_push_job_round` 仍应**正常跑完**——
    证明它读的是 `loop_transport` 的全局。若哪天有人把 seam 挪错命名空间（或把实现搬回
    `loop_steps`），本用例会立刻点名，而不是让 e2e 的 patch 静默失效。
    """
    submitted: list[str] = []

    def fake_submit(
        url: str, key: str, manifest: object, payload: object, code: object, **kw: object
    ) -> dict:
        submitted.append(url)
        return {"ok": True}

    def fake_wait(url: str, key: str, jid: str, **kw: object) -> dict:
        return {"job_id": jid, "from": url}

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("读了 rl.loop_steps 的 seam——DI seam 未随实现迁移")

    monkeypatch.setattr(transport, "_push_submit", fake_submit)
    monkeypatch.setattr(transport, "_push_wait_result", fake_wait)
    monkeypatch.setattr(steps, "_push_submit", boom)
    monkeypatch.setattr(steps, "_push_wait_result", boom)

    out = transport._push_job_round(
        [{"url": "http://a.example", "authKey": "k"}],
        {"job_id": "j"},
        "j",
        b"payload",
        b"code",
        SimpleNamespace(smoke=False),
        5.0,
        lambda _m: None,
    )

    assert submitted == ["http://a.example"]
    assert out["from"] == "http://a.example"


def test_loop_steps_seam_still_exists_for_class_methods() -> None:
    """类方法用的同名 seam 仍在 `rl.loop_steps`（e2e/test_push_mode_integration.py 的 patch 目标）。

    两边**各自**解析自己的模块全局——这不是重复定义，而是两个不同的注入点。功能侧的
    证明在 e2e 的 `test_push_publish_phase_submits_and_wait_phase_switches_node`（它 patch
    `rl.loop_steps.*` 并驱动 `TrainingSteps` 的方法）。
    """
    for name in ("_push_submit", "_push_wait_result", "dist_common"):
        assert name in vars(steps), f"类方法的 seam {name} 不应离开 rl.loop_steps"
