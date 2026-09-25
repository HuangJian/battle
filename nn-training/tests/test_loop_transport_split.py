"""S4 契约（2026-09-23）—— `rl/loop_steps.py` 的头两次拆分不得腐烂。

`rl/loop_steps.py` 原本 2328 行，里面塞着两种东西：

* **S4 第一步**：一组**模块级自由函数**（课程/rollout 源解析、transport 选择、hub 推送、
  节点 failover、kickstart 系数、远端可重试异常集合）——没有一个是方法 ⇒ 整体搬到
  `rl/loop_transport.py`，`loop_steps` 留门面 re-export（既有 `from rl.loop_steps import X`
  调用点不必改）。
* **S4 第二步**：`TrainingSteps`（1715 行）里的**远端 PPO 腿** 13 个方法（发布 → 领取 →
  三重校验落位 → failover → 事件落账）⇒ 搬到 `rl/loop_remote.py::TrainingRemote`，
  方向是 **`class TrainingSteps(TrainingRemote)`**（调用者依赖被调用者：簇的唯一入口
  `_remote_ppo` 由 TrainingSteps 的其余方法调用）。这样组合类与测试宿主都不必改。

* **S4 第十七刀**：`TrainingSteps` 里的 **in-loop 评估链** 8 个成员（派发 → 尾巴收拢 →
  join/交棒 → 收官 drain，即本类里唯一一条真正的方法间调用链）⇒ 搬到
  `rl/loop_eval.py::TrainingEval`，**追加**在既有基类之后
  （`class TrainingSteps(TrainingRemote, TrainingEval)`）。本文件只跟着改两条与基类元组
  相关的断言，完整契约在新家：`tests/test_loop_eval_split.py`。

本文件钉住头两次拆分**不会腐烂**的六条：

1. 那些名字在目标模块里**定义**，且**不再**在 `loop_steps` 里定义（门面只能是门面，
   不许哪天有人「就地补一个」）；
2. 门面 re-export 的是**同一个对象**（`is`，不是同名副本）；
3. **DI seam 随实现走**：`_push_job_round` 读 `rl.loop_transport._push_submit`，
   `TrainingRemote._push_submit_first/_push_fetch` 读 `rl.loop_remote_push._push_submit`——
   同名 seam 在多个模块并存是**几个各自真实的注入点**，不是重复定义。
   这一条是功能性的（真调一次），因为「patch 目标写错」正是这类拆分最容易犯、
   且**最静默**的错：测试会绿，而注入根本没生效。
4. 远端一族（组合根 + 四个混入）都不得反向 import `rl.loop_steps`（否则门面会变成环）。
5. 继承方向不得反过来（`TrainingRemote` 不能是 `TrainingSteps` 的子类）。
6. 远端一族直接从 `rl.loop_transport` 拿传输原语（首簇搬迁的预期收益）。

> **S4 第二十二刀（2026-09-25）**：远端 PPO 腿的 862 行连通分量按判据同源切成四簇
> （`loop_remote_push` / `loop_remote_job` / `loop_remote_fail` / `loop_remote_drive`），
> `rl/loop_remote.py` 退成**零方法的组合根**（`class TrainingRemote(TrainingRemoteDrive)`）。
> 本文件里与「13 个方法住在同一个类」相关的几条断言因此演进：定义面改成读组合根的 **MRO**，
> 入边/import 面改成读**一族**；完整契约在新家 `tests/test_loop_remote_split.py`。
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
        raise AssertionError("读了别的模块的 seam——DI seam 未随实现迁移")

    # `rl.loop_steps` 现在**根本不持有**这两个名字（S4 第二步后连 import 都没有）——
    # 这正是「seam 只剩一份、住在实现所在模块」的机械形式。
    for name in ("_push_submit", "_push_wait_result"):
        assert name not in vars(steps), f"{name} 不应再出现在 rl.loop_steps 的命名空间"
        monkeypatch.setattr(transport, name, boom, raising=False)

    monkeypatch.setattr(transport, "_push_submit", fake_submit)
    monkeypatch.setattr(transport, "_push_wait_result", fake_wait)

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


# ────────────────────────── S4 第二步：远端 PPO 腿（loop_remote） ──────────────────────────

#: 搬到 `TrainingRemote` 的 13 个方法（远端 PPO 腿，862 行）。
REMOTE_LEG = (
    "_abort_node_failure",
    "_handle_remote_failure",
    "_remote_ppo",
    "_remote_ppo_fetch",
    "_remote_ppo_land",
    "_remote_ppo_probe",
    "_remote_ppo_publish",
    "_remote_ppo_step",
    "_remote_iter",
    "_remote_run_segment",
    "_push_fetch",
    "_push_submit_first",
    "_push_submit_node",
)


def _class_methods(path: Path, cls_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == cls_name
    )
    return {m.name for m in cls.body if isinstance(m, ast.FunctionDef)}


#: S4 第二十二刀后远端腿一族 = 组合根 + 四个混入（判据同源切簇）。
REMOTE_FAMILY = (
    "loop_remote.py",
    "loop_remote_push.py",
    "loop_remote_job.py",
    "loop_remote_fail.py",
    "loop_remote_drive.py",
)


def test_remote_leg_is_defined_in_the_remote_family_only() -> None:
    """远端 PPO 腿定义在 `TrainingRemote` 的 **MRO** 里（S4 第二十二刀后 = 四个混入）；
    `TrainingSteps` 里**不得**再有同名方法。"""
    from rl.loop_remote import TrainingRemote

    defined = {n for k in TrainingRemote.__mro__ for n in vars(k)}
    assert set(REMOTE_LEG) <= defined, sorted(set(REMOTE_LEG) - defined)
    left = _class_methods(NN_ROOT / "rl" / "loop_steps.py", "TrainingSteps")
    crept_back = sorted(set(REMOTE_LEG) & left)
    assert crept_back == [], f"这些方法又回到 TrainingSteps 了：{crept_back}"


def test_training_steps_inherits_training_remote() -> None:
    """方向是**调用者依赖被调用者**：`TrainingSteps(TrainingRemote, TrainingEval, TrainingExport)`。

    S4 第十七刀**追加**了 `TrainingEval`：追加而不是插队 ⇒ 2026-09-23 写下的那句
    `__mro__[1] is TrainingRemote` 逐字仍然成立（追加时特意保住它，见
    `rl/loop_steps.py` 的 import 注释）。这里补上完整元组，免得「追加」变成「随便插」。
    """
    from rl.loop_eval import TrainingEval
    from rl.loop_remote import TrainingRemote

    assert issubclass(steps.TrainingSteps, TrainingRemote)
    assert not issubclass(TrainingRemote, steps.TrainingSteps)
    assert steps.TrainingSteps.__mro__[1] is TrainingRemote
    # S4 第二十一刀再**追加**了 `TrainingExport`（产物出包 4 方法）——同样是末位追加；
    # 上面那句 `__mro__[1] is TrainingRemote` 仍然逐字成立。
    from rl.loop_export import TrainingExport

    assert steps.TrainingSteps.__bases__ == (TrainingRemote, TrainingEval, TrainingExport)
    # 组合类与四个「继承真混入」的测试宿主因此都不必改；组合根仍在 rl/loop_remote.py，
    # 但 13 个方法已按判据同源搬到四个新家（`_remote_ppo` ∈ Job）。
    assert TrainingRemote.__module__ == "rl.loop_remote"
    assert TrainingRemote._remote_ppo.__module__ == "rl.loop_remote_job"


def test_loop_remote_family_does_not_import_loop_steps() -> None:
    """不得成环：远端一族谁都不许 import `loop_steps`（它只靠 `self.*` 回调）。"""
    for fname in REMOTE_FAMILY:
        assert "rl.loop_steps" not in _imports(NN_ROOT / "rl" / fname), fname


def test_loop_remote_family_uses_the_transport_layer_directly() -> None:
    """首簇搬迁的预期收益：新家直接从 `rl.loop_transport` 取传输原语（不经门面往返）。"""
    hits = [f for f in REMOTE_FAMILY if "rl.loop_transport" in _imports(NN_ROOT / "rl" / f)]
    assert hits, "远端一族应至少有一处直接依赖 rl.loop_transport"


def _module_class_reads(path: Path) -> set[str]:
    """该文件所有类的方法体里 `Name` 载入（含成员表达式里的 `self`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reads: set[str] = set()
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for node in ast.walk(cls):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                reads.add(node.id)
    return reads


def test_remote_leg_methods_read_their_own_module_seams() -> None:
    """seam 住在**实现所在模块**：每条腿的方法体读的是它**新家**的全局。

    S4 第二十二刀把 13 个方法切成四簇，DI seam 随之分散（`_push_submit` /
    `_push_wait_result` 在直推腿，`dist_common` 在驱动入口）。功能侧的证明在 e2e 的
    `test_push_publish_phase_submits_and_wait_phase_switches_node`（patch
    `rl.loop_remote_push.*` 并驱动真宿主 `st._push_submit_first` / `st._push_fetch`）。
    """
    expect = {
        "loop_remote_push.py": ("_push_submit", "_push_wait_result", "log"),
        "loop_remote_job.py": ("log",),
        "loop_remote_fail.py": ("log",),
        "loop_remote_drive.py": ("dist_common", "log"),
    }
    for fname, names in expect.items():
        reads = _module_class_reads(NN_ROOT / "rl" / fname)
        for name in names:
            assert name in reads, f"{fname} 的方法体里应读到 {name}"
    # 组合根已退成薄门面：它**不再**读任何 DI seam（否则「seam 只剩一份」就不成立）。
    root_reads = _module_class_reads(NN_ROOT / "rl" / "loop_remote.py")
    for name in ("_push_submit", "_push_wait_result", "dist_common", "log"):
        assert name not in root_reads, f"组合根不该再读 {name}"
