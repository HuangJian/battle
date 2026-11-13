"""tests/worker/test_bc_xla.py —— BC 上 TPU（XLA 路径，2026-11-13）。

真实 TPU 跑不进单测，所以这里把**设备句柄**与**XLA 助手**全部打桩，在 CPU 张量上把
训练循环走一遍。钉的是接线（哪条腿在什么设备上调什么），不是 TPU 数值：

  1. `_xla_entry` 先开 SPMD、再取 `xla_device()`（`use_spmd` 必须先于设备初始化），
     `torch_xla` 只在真的选了 TPU 时才 import；
  2. 单设备训练步走 `optimizer_step` + `xla_mark_step`；**SPMD 下必须裸 `opt.step()`**
     （复制参数上的梯度已由 XLA 归约，再调 `xm.optimizer_step` 会二次归约、放大 world_size 倍）；
  3. SPMD 的数据并行形状：参数复制（`(None,)*dim`）、batch/标签/掩码按 dim0 切到 `data` 轴，
     loader 必须 drop_last（dim0 切分要求整批），mb 必须是核数的倍数；
  4. 权重快照与每 epoch 回传走 **CPU 副本**（`_cpu_state_dict` / `HostModule`）——
     设备张量交给 `save_weights_json` / `on_epoch` 会逐张量同步（42 参数 ≈ 10s/次）；
  5. 非 TPU 的 XLA 后端（torch_xla 的 CPU 插件）**拒跑**，不静默慢两个数量级。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

torch = pytest.importorskip("torch")

import worker.train.bc as bc_mod
from worker.ppo.common import HostModule

N, CH, BOARD = 32, 17, 26
SCALAR_DIM, EXTRA_DIM = 46, 57


def _write_shard(root: Path, name: str, seed: int) -> None:
    rng = np.random.default_rng(seed)
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    np.save(d / "obs.npy", rng.integers(0, 256, (N, CH, BOARD, BOARD), dtype=np.uint8))
    np.save(d / "scalars.npy", rng.standard_normal((N, SCALAR_DIM)).astype(np.float32))
    np.save(d / "extra.npy", rng.standard_normal((N, EXTRA_DIM)).astype(np.float32))
    np.save(d / "a_move.npy", rng.integers(0, 4, N, dtype=np.uint8))
    np.save(d / "a_fire.npy", rng.integers(0, 3, N, dtype=np.uint8))
    np.save(d / "a_hold.npy", rng.integers(0, 2, N, dtype=np.uint8))
    np.save(d / "mask.npy", np.ones((N, 9), dtype=np.uint8))
    (d / "manifest.json").write_text(
        json.dumps({"outcome": "stage_clear", "samples": N}), encoding="utf-8"
    )


def _fake_xla(monkeypatch: pytest.MonkeyPatch, calls: dict[str, int]) -> None:
    """把 XLA 设备/助手换成 CPU 替身：训练循环本身在 CPU 张量上照样跑得通。"""
    monkeypatch.setattr(bc_mod, "_xla_spmd_mesh", lambda: (None, 1))  # 单设备 XLA
    monkeypatch.setattr(bc_mod, "is_xla", lambda _dev: True)
    monkeypatch.setattr(bc_mod, "xla_device", lambda: torch.device("cpu"))
    monkeypatch.setattr(bc_mod, "xla_enable_compile_cache", lambda _p: "fake")
    monkeypatch.setattr(
        bc_mod,
        "xla_fingerprint",
        lambda _d: {
            "device_type": "TPU",
            "attrs": "{'coords': '0,0'}",
            "global_device_count": 8,
            "addressable_device_count": 8,
            "replication_devices": 1,
        },
    )
    monkeypatch.setattr(bc_mod, "xla_device_speed_probe", lambda _d: 0.0015)
    monkeypatch.setattr(bc_mod, "xla_world_size", lambda: 8)

    def _mark(_dev: Any) -> None:
        calls["mark"] += 1

    def _fetch(vals: list[Any]) -> list[Any]:
        calls["cpu_fetch"] += 1
        return [v.detach().cpu() for v in vals]

    monkeypatch.setattr(bc_mod, "xla_mark_step", _mark)
    monkeypatch.setattr(bc_mod, "to_cpu_batch", _fetch)

    def _step(opt: Any, _dev: Any) -> None:
        calls["step"] += 1
        opt.step()

    monkeypatch.setattr(bc_mod, "optimizer_step", _step)


def _args(root: Path, tmp_path: Path, **over: Any) -> types.SimpleNamespace:
    ns = types.SimpleNamespace(
        data_dir=str(root),
        arch="student",
        out=str(tmp_path / "w.json"),
        notes="xla-test",
        resume=None,
        epoch_offset=0,
        ckpt_every=0,
        checkpoint=None,
        epochs=1,
        batch=16,
        lr=1e-3,
        val_split=0.25,
        mirror_p=0.5,
        seed=1234,
        num_workers=0,
        device="tpu",
        value_coef=0.0,
        on_epoch=None,
        action_v6=True,
        policy_extra=False,
        weight_exponent=1.0,
        fire_pos_weight="auto",
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_bc_train_on_xla_uses_xla_step_and_cpu_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "s0", 7)
    _write_shard(corpus, "s1", 8)
    calls = {"step": 0, "mark": 0, "cpu_fetch": 0}
    _fake_xla(monkeypatch, calls)
    epoch_views: list[Any] = []
    out = bc_mod.train(
        _args(
            corpus,
            tmp_path,
            on_epoch=lambda _g, model, _m: epoch_views.append(model),
        )
    )
    assert calls["step"] >= 1 and calls["mark"] >= 1, ("训练步必须走 XLA 助手", calls)
    assert calls["cpu_fetch"] >= 1, "best_state 必须一次批量取回 CPU"
    assert epoch_views and isinstance(epoch_views[0], HostModule), (
        "on_epoch 交给回传链路的必须是 CPU 视图",
        type(epoch_views[0]) if epoch_views else None,
    )
    assert Path(out["out"]).is_file()


def test_xla_entry_rejects_a_non_tpu_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """torch_xla 的 CPU 插件也返回 `xla:0`：只看设备串会把「TPU 机跑 CPU」当正常。"""
    monkeypatch.setattr(bc_mod, "_xla_spmd_mesh", lambda: (None, 1))
    monkeypatch.setattr(bc_mod, "xla_device", lambda: torch.device("cpu"))
    monkeypatch.setattr(bc_mod, "xla_enable_compile_cache", lambda _p: "fake")
    monkeypatch.setattr(
        bc_mod,
        "xla_fingerprint",
        lambda _d: {
            "device_type": "CPU",
            "attrs": "{}",
            "global_device_count": 1,
            "addressable_device_count": 1,
            "replication_devices": 1,
        },
    )
    monkeypatch.setattr(bc_mod, "xla_device_speed_probe", lambda _d: 0.018)
    monkeypatch.setattr(bc_mod, "xla_world_size", lambda: 1)
    with pytest.raises(RuntimeError, match="不是 TPU"):
        bc_mod._xla_entry(types.SimpleNamespace(out=str(tmp_path / "w.json"), batch=16))


# ------------------------------------------------------------------ SPMD（v5e-8 的 8 核）


def _fake_xla_modules(
    monkeypatch: pytest.MonkeyPatch, n: int = 8
) -> tuple[Any, dict[str, Any]]:
    """假 `torch_xla` 包：不装 torch_xla 的机器也能测 `_xla_spmd_mesh` 的判定与顺序。"""
    calls: dict[str, Any] = {"use_spmd": 0, "mesh_dims": []}
    xla = types.ModuleType("torch_xla")
    rt = types.ModuleType("torch_xla.runtime")
    dist = types.ModuleType("torch_xla.distributed")
    spmd = types.ModuleType("torch_xla.distributed.spmd")
    mesh = object()
    # `cast(Any, …)`：ModuleType 的动态属性既过 mypy（attr-defined）又过 ruff（B010 禁 setattr）。
    rt_any, spmd_any, xla_any, dist_any = cast(Any, rt), cast(Any, spmd), cast(Any, xla), cast(Any, dist)
    rt_any.global_device_count = lambda: n

    def _use_spmd() -> None:
        calls["use_spmd"] += 1

    def _get_1d_mesh(dim: str) -> Any:
        calls["mesh_dims"].append(dim)
        return mesh

    rt_any.use_spmd = _use_spmd
    spmd_any.get_1d_mesh = _get_1d_mesh
    xla_any.runtime = rt
    xla_any.distributed = dist
    dist_any.spmd = spmd
    for name, mod in (
        ("torch_xla", xla),
        ("torch_xla.runtime", rt),
        ("torch_xla.distributed", dist),
        ("torch_xla.distributed.spmd", spmd),
    ):
        monkeypatch.setitem(sys.modules, name, mod)
    return mesh, calls


def test_spmd_mesh_enables_spmd_before_returning_the_data_mesh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mesh, calls = _fake_xla_modules(monkeypatch, n=8)
    got, n = bc_mod._xla_spmd_mesh()
    assert got is mesh and n == 8
    assert calls["use_spmd"] == 1, "use_spmd 必须先于设备初始化"
    assert calls["mesh_dims"] == ["data"], "1-D data mesh（参数复制 / batch 按 dim0 切分）"


def test_spmd_mesh_is_none_on_single_core(monkeypatch: pytest.MonkeyPatch) -> None:
    _mesh, calls = _fake_xla_modules(monkeypatch, n=1)
    assert bc_mod._xla_spmd_mesh() == (None, 1)
    assert calls["use_spmd"] == 0, "单核不该开 SPMD"


def test_spmd_shards_batches_on_dim0_and_replicates_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[tuple[int, ...], tuple]] = []

    def _mark(t: Any, _mesh: Any, spec: tuple) -> None:
        seen.append((tuple(t.shape), spec))

    monkeypatch.setattr(bc_mod, "_mark_sharding", _mark)
    model = torch.nn.Linear(4, 3)
    assert bc_mod._shard_model(model, object()) == 2
    assert [s for _shape, s in seen] == [(None, None), (None,)]
    seen.clear()
    bc_mod._shard_batch(torch.zeros(16, 17, 26, 26), object())
    bc_mod._shard_batch(torch.zeros(16, dtype=torch.long), object())
    assert seen[0] == (
        (16, 17, 26, 26),
        (("data",), None, None, None),
    )
    assert seen[1] == ((16,), (("data",),))


@pytest.mark.parametrize("mb,n,ok", [(256, 8, True), (16, 8, True), (100, 8, False)])
def test_spmd_batch_must_be_divisible_by_the_core_count(mb: int, n: int, ok: bool) -> None:
    if ok:
        bc_mod._assert_spmd_batch(mb, n)
    else:
        with pytest.raises(ValueError, match="整数倍"):
            bc_mod._assert_spmd_batch(mb, n)


def test_spmd_train_shards_every_batch_and_skips_optimizer_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SPMD 下：批/标签全切 dim0、**不调** `xm.optimizer_step`（XLA 已归约梯度）。"""
    corpus = tmp_path / "corpus"
    _write_shard(corpus, "s0", 11)
    _write_shard(corpus, "s1", 12)
    _fake_xla(monkeypatch, {"step": 0, "mark": 0, "cpu_fetch": 0})
    monkeypatch.setattr(bc_mod, "_xla_spmd_mesh", lambda: (object(), 8))
    sharded: list[tuple[int, ...]] = []

    def _mark(t: Any, _mesh: Any, _spec: tuple) -> None:
        sharded.append(tuple(t.shape))

    monkeypatch.setattr(bc_mod, "_mark_sharding", _mark)

    def _no_xla_optimizer_step(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("SPMD 下不得调 xm.optimizer_step（会二次归约）")

    monkeypatch.setattr(bc_mod, "optimizer_step", _no_xla_optimizer_step)
    out = bc_mod.train(_args(corpus, tmp_path, batch=16))
    assert Path(out["out"]).is_file()
    assert (16, 17, 26, 26) in sharded, "obs 必须按 dim0 切分"
    assert (16,) in sharded, "标签/掩码必须按 dim0 切分"
    assert sharded.count((16,)) >= 3 * 2, "训练/验证的每个 batch 都要标注（含标签与掩码）"
