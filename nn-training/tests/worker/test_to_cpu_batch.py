"""tests/worker/test_to_cpu_batch.py — 产物导出段的**设备→主机取回**不变量（2026-10-29）。

背景（真机两笔账）：
  · 逐张量 `.cpu()` = 每次一次设备同步 ⇒ 168 个张量（model 42 + Adam 状态 42×3）× ~0.25s ≈ 42s；
  · 内部批量 API 在这台机器上**只对参数是一次传输**，对 126 个 Adam 状态退化成按张量付费
    （30s，且**不报错**）——所以现在主路是 `cat`：一个结果张量 ⇒ D2H 恒一次。

钉四件事：
  ① `_xla_fetch_cat` 的分组/切回（dtype 分组、shape 复原、0 维张量）；
  ② `to_cpu_batch` 走**一次**取回，不回退到逐张量；
  ③ 主路失败 ⇒ 依次退内部 API ⇒ 逐张量，**每次都打一行**（慢/回退不许静默，这正是 30s 藏起来的方式）；
  ④ `_to_cpu_state` 的两遍重建容器类型逐字保留；`HostModule` 不碰源模块、产物字节不变。

本机没有 torch_xla ⇒ 用 `is_xla` / 两个取回函数当注入点模拟「设备上的张量」。
"""

from __future__ import annotations

from collections import OrderedDict

import pytest
import torch

import worker.ppo.common as common


@pytest.fixture()
def fake_xla(monkeypatch):
    """把**全部**张量当成 XLA 张量，`cat` 路径记一次「批量取回」并回 CPU 副本。"""
    calls: list[int] = []

    def _fetch(tensors):
        calls.append(len(tensors))
        return [t.detach().clone() for t in tensors]

    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cat", _fetch)
    monkeypatch.setattr(common, "_xla_fetch_cpu", lambda ts: pytest.fail("主路没坏，不该落到内部 API"))
    return calls


def test_xla_fetch_cat_keeps_shapes_and_groups_by_dtype() -> None:
    """`cat` 的分组/切回：混合 dtype + 混合形状 + 0 维，逐张量形状/值/类型都要复原。"""
    src = [
        torch.arange(6, dtype=torch.float32).reshape(2, 3),
        torch.zeros((), dtype=torch.float32),
        torch.tensor([7, 8, 9], dtype=torch.int64),
        torch.ones(4, dtype=torch.float32),
    ]
    out = common._xla_fetch_cat(list(src))
    assert len(out) == len(src)
    for a, b in zip(src, out, strict=True):
        assert tuple(b.shape) == tuple(a.shape), f"形状被改：{tuple(a.shape)} → {tuple(b.shape)}"
        assert b.dtype == a.dtype
        assert torch.equal(b, a)


def test_to_cpu_batch_once_per_batch(fake_xla) -> None:
    ts = [torch.zeros(2), torch.ones(3), torch.full((1,), 5.0)]
    out = common.to_cpu_batch(ts)
    assert fake_xla == [3], f"应当只调一次批量取回（3 个张量），实际 {fake_xla}"
    assert [float(t.sum()) for t in out] == [0.0, 3.0, 5.0]
    assert all(t.device.type == "cpu" for t in out)


def test_to_cpu_batch_falls_back_to_internal_api(monkeypatch) -> None:
    used: list[int] = []

    def boom(tensors):
        raise RuntimeError("cat 被拒（模拟）")

    def _alt(tensors):
        used.append(len(tensors))
        return [t.detach().clone() for t in tensors]

    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cat", boom)
    monkeypatch.setattr(common, "_xla_fetch_cpu", _alt)
    out = common.to_cpu_batch([torch.full((2,), 1.5), torch.full((2,), 2.5)])
    assert used == [2], "主路失败应当轮到内部 API（一次）"
    assert [float(t.sum()) for t in out] == [3.0, 5.0]


def test_to_cpu_batch_last_resort_is_loud(monkeypatch) -> None:
    """两条批量路都坏 ⇒ 逐张量（值仍要对），**并打 WARN**：静默回退是 30s 藏起来的方式。"""
    warns: list[str] = []

    def boom(name):
        def _f(tensors):
            raise RuntimeError(f"{name} 坏了")

        return _f

    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cat", boom("cat"))
    monkeypatch.setattr(common, "_xla_fetch_cpu", boom("xlac"))
    monkeypatch.setattr(common, "log", warns.append)
    out = common.to_cpu_batch([torch.full((2,), 1.5), torch.full((2,), 2.5)])
    assert [float(t.sum()) for t in out] == [3.0, 5.0]
    # 行为判据：两条批量路各一行 + 回退一行（**内容**不钉措辞，只钉「不许静默」）
    assert len(warns) == 3, warns


def test_to_cpu_batch_slow_fetch_is_reported(monkeypatch) -> None:
    """取回成功但慢（≥ `FETCH_SLOW_WARN_SEC`）⇒ 也要一行（别让 30s 再次匿名）。"""
    warns: list[str] = []
    clock = iter([0.0, 99.0])  # 一次调用：起点 0，结束 99

    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cat", lambda ts: [t.clone() for t in ts])
    monkeypatch.setattr(common, "log", warns.append)
    monkeypatch.setattr(common.time, "monotonic", lambda: next(clock))
    common.to_cpu_batch([torch.zeros(2), torch.ones(2)])
    assert len(warns) == 1, f"慢取回必须留一行读数：{warns}"


def test_to_cpu_state_one_fetch_keeps_container_types(fake_xla) -> None:
    sd = OrderedDict([("a", torch.zeros(1)), ("b", torch.ones(1))])
    obj = {"state": sd, "list": [torch.full((1,), 2.0)], "tuple": (torch.full((1,), 3.0),)}
    out = common._to_cpu_state(obj)
    assert fake_xla == [4], f"4 个叶子应当只付一次批量取回，实际 {fake_xla}"
    assert isinstance(out["state"], OrderedDict), "容器类型必须保留（改 dict 会改 pickle 字节）"
    assert list(out["state"]) == ["a", "b"]
    assert isinstance(out["list"], list) and isinstance(out["tuple"], tuple)
    assert float(out["state"]["b"].sum()) == 1.0
    assert float(out["tuple"][0].sum()) == 3.0


def test_to_cpu_state_returns_object_when_no_tensor(monkeypatch) -> None:
    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cat", lambda ts: pytest.fail("不该被调用"))
    obj = {"a": 1, "b": [2, 3]}
    assert common._to_cpu_state(obj) == obj


def test_host_module_batches_once_and_keeps_values(fake_xla) -> None:
    """`HostModule` 的 `state_dict()` = 一次批量取回的 CPU 副本（值逐字相同）。"""
    module = torch.nn.Linear(3, 2)
    before = {k: v.detach().clone() for k, v in module.state_dict().items()}
    host = common.HostModule(module)
    assert isinstance(host, torch.nn.Module), "写权重的入参契约是 Module（零 cast）"
    assert fake_xla == [len(before)], f"params+buffers 一次取回（{len(before)} 个），实际 {fake_xla}"
    sd = host.state_dict()
    for k, v in before.items():
        assert torch.equal(sd[k], v), f"{k} 的值被改了"
    assert all(t.device.type == "cpu" for t in sd.values())


def test_host_module_never_writes_the_source_module(monkeypatch, fake_xla) -> None:
    """★ 2026-10-29 云机现场：XLA 上 `p.data = <CPU 张量>` 必炸
    （`RuntimeError: Attempted to call variable.set_data(tensor), but variable and tensor have
    incompatible tensor type`）⇒ 视图这条路**一次都不许**走源模块的写入面。

    钉法：把 `Module._apply`（`.to()` 的实现，也是唯一会碰 Parameter 的入口）改成必抛，
    视图仍要能出完整的 CPU 副本。
    """
    module = torch.nn.Linear(3, 2)

    def boom(self, fn):
        raise RuntimeError("Attempted to call `variable.set_data(tensor)` …")

    monkeypatch.setattr(torch.nn.Module, "_apply", boom)
    host = common.HostModule(module)
    assert host.state_dict()["weight"].shape == (2, 3)
    assert len(list(host.parameters())) == 2, "`num_params` 那一格要的是源模块的参数"
    assert host.arch() == {}, "源模块没有 arch ⇒ 空 dict（`save_weights_json` 的既有兜底）"


def test_host_module_writes_the_same_bytes_as_the_module(tmp_path) -> None:
    """`weights.json` 必须**逐字节相同**：视图不许改产物身份（`init_weights_fp` 就是它）。"""
    from worker.data.weights_io import save_weights_json

    module = torch.nn.Linear(3, 2)
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    save_weights_json(module, str(a))
    save_weights_json(common.HostModule(module), str(b))
    assert a.read_bytes() == b.read_bytes(), "权重字节变了 ⇒ 每轮 init_weights_fp 都会漂"


def test_log_if_slow_only_when_over_threshold(monkeypatch) -> None:
    """产物段的**分段读数**：慢过 5s 才打一行，且带张量数（`art` 只有一个总数时靠它分家）。"""
    clock = {"t": 100.0}
    logs: list[str] = []
    monkeypatch.setattr(common.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(common, "log", logs.append)
    assert common._log_if_slow("快", 100.0, 42) < 5.0
    assert logs == [], "没超阈值不许打"
    clock["t"] = 140.0
    common._log_if_slow("慢", 100.0, 126)
    assert logs == ["[art] 慢 40.0s（126 个张量）"], logs
