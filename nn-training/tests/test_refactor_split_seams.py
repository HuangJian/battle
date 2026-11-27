"""2026-11-27 拆分批（engineering §385–§387）的**永久差分**。

那九刀各自用 `tmp/split_*_diff.py` 做过 HEAD-vs-新对比，证据随 `tmp/` 一起没了。本文件
把「差分当时证明了什么」固化成不依赖 git 历史的回归判据：

1. **声明表**：`run_loop._build_parser` / `remote.worker._worker_parser` / `worker.cli._flag_rows`
   的 27 / 20 / 93 行逐项指纹。拆成多段后最容易发生的退化是漏一行、默认值串位、顺序漂移。
2. **补传构造**：四个 `_init_*` 各跑一次（曾经拆出过一次重复 `_init_probe_state` +
   `_load_ledger`，空目录下看不出、有账本时会丢拒收指纹并重复日志）。
3. **云端起步**：`rollout_node._run_plan` 的磁盘 / bun 失败必须返回 `None`（由 `run` 统一
   映射退出码 2）；`_resolve_deliverer` 的三条分支（显式 > hub/outbox > 无）。
4. **PPO 单步**：legacy 与 v6（policy_mask + ref + demo）两组确定性输入下，13 项遥测与
   step 后参数和的黄金值 —— 拆分只许搬代码，不许改数学。

数表用 sha256 而不是逐行抄写：行数 + 指纹同时钉住「少一行」与「同一行改了默认值」；
指纹变了先对照 git 历史确认是不是有意的，再更新常量。
"""

from __future__ import annotations

import ast
import hashlib
import types
from pathlib import Path

import numpy as np
import pytest
import torch

from common.contracts import ActionSpace, head_layout
from remote import plan_handoff, run_loop
from remote import worker as remote_worker
from remote.artifacts import ArtifactStore
from remote.deliver_proc import FactorySpec
from remote.deliver_spec import OfflineDelivererSpec
from remote.offline_deliver import OfflineDeliverer
from worker import cli
from worker.ppo import backend
from worker.ppo.engine import _torch_backend

NN_ROOT = Path(__file__).resolve().parents[1]


# ── 1. 声明表指纹 ─────────────────────────────────────────────────────────


def _canon(v):
    """把 argparse / flag 行里的可调用与容器归一成可哈希、跨进程稳定的形状。"""
    if callable(v):
        return ("callable", getattr(v, "__name__", type(v).__name__))
    if isinstance(v, dict):
        return tuple(sorted((k, _canon(x)) for k, x in v.items()))
    if isinstance(v, (list, tuple)):
        return tuple(_canon(x) for x in v)
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    return (type(v).__name__, repr(v))


def _digest(rows) -> str:
    return hashlib.sha256(repr(_canon(rows)).encode("utf-8")).hexdigest()


def _action_rows(ap) -> list[tuple]:
    return [
        (
            tuple(a.option_strings),
            a.dest,
            _canon(a.default),
            type(a).__name__,
            a.nargs,
            _canon(a.const),
            _canon(a.choices),
            a.required,
        )
        for a in ap._actions
    ]


def test_run_loop_parser_table_is_frozen() -> None:
    rows = _action_rows(run_loop._build_parser())
    assert len(rows) == 27
    assert _digest(rows) == "6cd0284768d6ed5d555d5ef3bff739516a9b01d9938193a32a25c21885c07999", (
        "`_build_parser` 的 action 表变了（漏行 / 默认值 / 顺序）；确认是有意改动后更新指纹"
    )


def test_worker_parser_table_is_frozen() -> None:
    rows = _action_rows(remote_worker._worker_parser())
    assert len(rows) == 20
    assert _digest(rows) == "aa98d7e2174232b2212f234a9ffe213dc93c374a1f8a74746e9f32a5a04acf87", (
        "`_worker_parser` 的 action 表变了；确认是有意改动后更新指纹"
    )


def test_flag_rows_table_is_frozen() -> None:
    rows = cli._flag_rows("per-tick", {})
    assert len(rows) == 93
    assert _digest(rows) == "a7ffba4ef0869c3d6eb7a7b37f4e8707f3ab17013dc4d0379f8ac8da77bc3bd1", (
        "`_flag_rows` 的 93 行数据表变了；确认是有意改动后更新指纹"
    )


# ── 2. 补传构造：四个阶段各跑一次 ─────────────────────────────────────────


def test_offline_deliverer_init_runs_each_stage_once() -> None:
    tree = ast.parse((NN_ROOT / "remote/offline_deliver.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OfflineDeliverer")
    fn = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "__init__"
    )
    calls = [
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
        and (node.func.attr.startswith("_init_") or node.func.attr == "_load_ledger")
    ]
    assert calls == [
        "_init_wire_state",
        "_init_backup_state",
        "_init_transport_state",
        "_init_observability_state",
        "_init_background_state",
        "_init_probe_state",
        "_load_ledger",
    ], f"`OfflineDeliverer.__init__` 的阶段调用序列变了：{calls}"


def test_offline_deliverer_state_partition(tmp_path: Path) -> None:
    logs: list[str] = []
    spec = OfflineDelivererSpec(
        base_url="http://hub/",
        token="tok",
        run_id="run-1",
        artifacts_dir=tmp_path / "art",
        course="c1",
        worker_name="w1",
        backup_root=tmp_path / "backup",
        metrics_parts=True,
        adaptive=True,
        background=False,
        log=logs.append,
    )
    d = OfflineDeliverer(spec)
    assert d.base_url == "http://hub" and d.run_id == "run-1"
    assert d.root == tmp_path / "art" and d.backup_root == tmp_path / "backup"
    assert d.course == "c1" and d.worker_name == "w1"
    assert d.metrics_parts is True and d.adaptive is True
    assert d.enabled is True


def test_offline_deliverer_reports_missing_identity(tmp_path: Path) -> None:
    logs: list[str] = []
    d = OfflineDeliverer(
        OfflineDelivererSpec(
            base_url="",
            token="",
            run_id="bad/id",
            artifacts_dir=tmp_path,
            background=False,
            log=logs.append,
        )
    )
    assert d.enabled is False
    assert d.run_id == ""
    assert len(logs) == 1  # 缺身份只记一行（不是每轮一个 400）


# ── 3. 云端起步 / 补传腿装配 ──────────────────────────────────────────────


def _cfg(**over) -> dict:
    cfg = {
        "hub_port": 8901,
        "hub_url": "",
        "link": "direct",
        "agent_port": 8443,
        "workers": 8,
        "node_id": "n1",
        "work_dir": "",
        "ts_authkey": "ts",
        "hub_ip": "10.0.0.1",
        "hub_token": "tok",
        "code_dir": "/code",
    }
    cfg.update(over)
    return cfg


def test_run_plan_returns_none_on_disk_failure(monkeypatch) -> None:
    """磁盘门必须在起 agent 之前把整段拒掉；`run` 统一把它映射成退出码 2。"""
    import remote.rollout_node as rn

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "local")
    monkeypatch.setattr(rn, "resolve_link", lambda env, want, has_ts: "direct")
    monkeypatch.setattr(rn.node_report, "startup_disk", lambda cfg: ((10, 20), "disk below floor"))
    plan = rn._run_plan(_cfg(), lambda line: None, Path("/code"), "ts", "10.0.0.1", "tok")
    assert plan is None


class _Sentinel:
    pass


def _deliverer_setup(**over):
    setup = types.SimpleNamespace(
        deliverer=None,
        hub_url="http://hub",
        hub_token="tok",
        hub_course="c1",
        hub_lease="lease",
        outbox_dir="",
        floor_it=0,
        deliver=False,
        course=None,
        work_dir=Path("/work"),
    )
    for k, v in over.items():
        setattr(setup, k, v)
    return setup


def test_resolve_deliverer_prefers_explicit(monkeypatch, tmp_path: Path) -> None:
    called: list[FactorySpec] = []
    monkeypatch.setattr(plan_handoff, "make_deliverer", lambda spec: called.append(spec))
    explicit = _Sentinel()
    got = plan_handoff._resolve_deliverer(
        _deliverer_setup(deliverer=explicit),
        store=ArtifactStore(tmp_path / "art", run_id="r"),
        backup=Path("/backup"),
        log=lambda line: None,
    )
    assert got is explicit and called == []


def test_resolve_deliverer_outbox_then_none(monkeypatch, tmp_path: Path) -> None:
    captured: list[FactorySpec] = []
    sentinel = _Sentinel()

    def _factory(spec):
        captured.append(spec)
        return sentinel

    monkeypatch.setattr(plan_handoff, "make_deliverer", _factory)
    monkeypatch.setattr(plan_handoff, "worker_tag", lambda **kw: "w1")
    store = ArtifactStore(tmp_path / "art", run_id="r")
    got = plan_handoff._resolve_deliverer(
        _deliverer_setup(outbox_dir="/outbox"),
        store=store,
        backup=Path("/backup"),
        log=lambda line: None,
    )
    assert got is sentinel
    assert captured[0].outbox_dir == "/outbox"
    assert captured[0].artifacts_dir == store.root and captured[0].backup_root == Path("/backup")

    captured.clear()
    got_none = plan_handoff._resolve_deliverer(
        _deliverer_setup(),
        store=store,
        backup=Path("/backup"),
        log=lambda line: None,
    )
    assert got_none is None
    assert captured == []


# ── 4. BC 装配：两段返回契约由真实语料用例覆盖 ──────────────────────────────
# `test_bc_policy_heads.py` 已经用真 `train()` + 真模型跑这两段；这里不再打私有助手，
# 避免把「私有名可打桩」当成契约。


# ── 5. PPO 单步黄金值（legacy / v6+pm+ref+demo）──────────────────────────


class _TinyModel(torch.nn.Module):
    """固定权重的三列线性模型：输出按 head 布局切片（legacy 3 元组 / v6 4 元组）。"""

    def __init__(self, layout) -> None:
        super().__init__()
        self.layout = layout
        self.lin = torch.nn.Linear(3, layout.mask_dim + 1)
        with torch.no_grad():
            for i, p in enumerate(self.parameters()):
                p.copy_(torch.linspace(-1.0, 1.0, p.numel()).reshape_as(p) * 0.2 * (i + 1))

    def forward(self, obs, sc, extra=None):
        x = self.lin(obs)
        if self.layout.has_hold:
            return x[:, 0:4], x[:, 4:7], x[:, 7:9], x[:, 9:10]
        return x[:, 0:5], x[:, 5:7], x[:, 7:8]


def _tiny_model(layout):
    return _TinyModel(layout)


def _step_case(v6: bool, *, pm: bool, ref: bool, demo: bool):
    layout = head_layout(ActionSpace.V6 if v6 else ActionSpace.LEGACY)
    t, n = 6, layout.mask_dim
    mask = torch.ones(t, n)
    mask[1, 0] = 0.0
    mask[4, -1] = 0.0
    e = {
        "obs": torch.linspace(-1.0, 1.0, t * 3).reshape(t, 3),
        "scalars": torch.linspace(0.5, -0.5, t * 2).reshape(t, 2),
        "mask": mask,
        "a_move": torch.tensor([0, 1, 2, 3 % layout.move_dim, 0, layout.move_dim - 1]),
        "a_fire": torch.tensor([0, 1, 0, 1, 0, 1]),
        "lp_move": torch.linspace(-0.1, -0.9, t),
        "lp_fire": torch.linspace(-0.2, -0.8, t),
        "adv": torch.linspace(-1.0, 1.0, t),
        "ret": torch.linspace(1.0, -1.0, t),
    }
    if layout.has_hold:
        e["a_hold"] = torch.tensor([0, 1, 0, 1, 0, 1])
        e["lp_hold"] = torch.linspace(-0.3, -0.7, t)
    if pm:
        e["policy_mask"] = torch.tensor([1.0, 0.0, 1.0, 1.0, 0.0, 1.0])
    refs = None
    if ref:
        refs = tuple(
            torch.log_softmax(torch.linspace(-1.0, 1.0, t * d).reshape(t, d), -1)
            for d in (layout.move_dim, layout.fire_dim)
        )
        if layout.has_hold:
            refs += (
                torch.log_softmax(
                    torch.linspace(-0.5, 0.5, t * layout.hold_dim).reshape(t, layout.hold_dim),
                    -1,
                ),
            )
    demo_t = {
        "obs": torch.linspace(-0.5, 0.5, 5 * 3).reshape(5, 3),
        "scalars": torch.linspace(0.2, -0.2, 5 * 2).reshape(5, 2),
        "masks": torch.ones(5, n),
        "actions": torch.tensor([[0], [1], [2], [0], [1]]),
    }
    model = _tiny_model(layout)
    params = list(model.parameters())
    opt = torch.optim.SGD(params, lr=0.05)
    st = backend.StepState(
        layout=layout,
        tensored=[e],
        ref_cache=[refs],
        demo_on=demo,
        demo_n=5,
        demo_t=demo_t,
    )
    cfg = backend.StepConfig(
        clip=0.2,
        ent_coef=0.01,
        vf_coef=0.5,
        kl_coef=0.1,
        kickstart_kl=0.2,
        value_anchor=0.1,
        demo_bc_coef=0.3,
        demo_per_mb=3,
        max_grad_norm=1.0,
    )
    row = backend.run_step(
        _torch_backend(torch.device("cpu")),
        model,
        opt,
        st,
        cfg,
        types.SimpleNamespace(demo=np.random.default_rng(0)),
        0,
    )
    return row, float(sum(p.sum() for p in params))


LEGACY_ROW = {
    "policy": 0.013971530832350254,
    "value": 1.19014573097229,
    "entropy": 2.126676559448242,
    "kl": 0.5106298923492432,
    "per_step_kl": 0.5106298923492432,
    "clip_fraction": 0.8333333134651184,
    "kickstart": 0.0,
    "demo_bc": 0.0,
    "mean_ret": 0.0,
    "mean_adv": 0.0,
    "gnorm": 1.171521782875061,
    "value_new_rms": 0.0,
    "policy_new_rms": 0.0,
}

V6_ROW = {
    "policy": 0.06257371604442596,
    "value": 1.2026911973953247,
    "entropy": 3.1628904342651367,
    "kl": 0.7802297472953796,
    "per_step_kl": 0.7802297472953796,
    "clip_fraction": 1.0,
    "kickstart": 0.1703536957502365,
    "demo_bc": 1.4615182876586914,
    "mean_ret": 0.0,
    "mean_adv": 0.0,
    "gnorm": 1.2141014337539673,
    "value_new_rms": 0.0,
    "policy_new_rms": 0.0,
}


def _assert_row(row: dict, expected: dict) -> None:
    assert list(row) == list(expected)
    for key, want in expected.items():
        assert row[key] == pytest.approx(want, rel=1e-5, abs=1e-7), (key, row[key], want)


def test_run_step_legacy_matches_golden() -> None:
    row, params_sum = _step_case(False, pm=False, ref=False, demo=False)
    _assert_row(row, LEGACY_ROW)
    assert params_sum == pytest.approx(-0.09545266628265381, rel=1e-5, abs=1e-7)


def test_run_step_v6_with_mask_ref_demo_matches_golden() -> None:
    row, params_sum = _step_case(True, pm=True, ref=True, demo=True)
    _assert_row(row, V6_ROW)
    assert params_sum == pytest.approx(-0.09255453199148178, rel=1e-5, abs=1e-7)
