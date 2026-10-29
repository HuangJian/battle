"""tests/worker/test_to_cpu_batch.py — 产物导出段的**批量化**不变量（2026-10-29）。

背景（真机账）：云机轮 `产物落盘 43.4s`，字节只有 1MB —— 全部买在「逐张量设备同步」上
（model 42 个参数 + Adam 状态 42×3 = 168 次 D2H × ~0.25s）。批量取回把 168 次压成 1 次。

钉三件事：
  ① XLA 张量走**一次**批量取回（不是循环里逐张量 `.cpu()`）；
  ② 批量 API 不可用/抛错 ⇒ 回退逐张量且**值正确**（回退 = 改动前的行为，绝不抛）；
  ③ `_to_cpu_state` 的两遍重建**容器类型逐字保留**（state_dict 是 OrderedDict，
     换成 dict 会改 pickle 字节）+ `materialize_to_cpu` 就地换掉参数。

本机没有 torch_xla ⇒ 用 `is_xla` / `_xla_fetch_cpu` 两个注入点模拟「设备上的张量」。
"""

from __future__ import annotations

from collections import OrderedDict

import pytest
import torch

import worker.ppo.common as common


@pytest.fixture()
def fake_xla(monkeypatch):
    """把**全部**张量当成 XLA 张量，并记录批量取回被调了几次、每次几个。"""
    calls: list[int] = []

    def _fetch(tensors):
        calls.append(len(tensors))
        return [t.detach().clone() for t in tensors]

    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cpu", _fetch)
    return calls


def test_to_cpu_batch_single_fetch(fake_xla):
    ts = [torch.zeros(2), torch.ones(3), torch.full((1,), 5.0)]
    out = common.to_cpu_batch(ts)
    assert fake_xla == [3], f"应当只调一次批量取回（3 个张量），实际 {fake_xla}"
    assert [float(t.sum()) for t in out] == [0.0, 3.0, 5.0]
    assert all(t.device.type == "cpu" for t in out)


def test_to_cpu_batch_falls_back_without_raising(monkeypatch):
    attempts: list[int] = []

    def _boom(tensors):
        attempts.append(len(tensors))
        raise RuntimeError("no _xla_get_cpu_tensors")

    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cpu", _boom)
    ts = [torch.full((2,), 1.5), torch.full((2,), 2.5)]
    out = common.to_cpu_batch(ts)  # 绝不抛
    assert attempts == [2]  # 只试一次批量，失败即回退
    assert [float(t.sum()) for t in out] == [3.0, 5.0]


def test_to_cpu_state_one_fetch_keeps_container_types(fake_xla):
    sd = OrderedDict([("a", torch.zeros(1)), ("b", torch.ones(1))])
    obj = {"state": sd, "list": [torch.full((1,), 2.0)], "tuple": (torch.full((1,), 3.0),)}
    out = common._to_cpu_state(obj)
    assert fake_xla == [4], f"4 个叶子应当只付一次批量取回，实际 {fake_xla}"
    assert isinstance(out["state"], OrderedDict), "state_dict 的容器类型必须保留（改 dict 会改 pickle 字节）"
    assert list(out["state"]) == ["a", "b"]
    assert isinstance(out["list"], list) and isinstance(out["tuple"], tuple)
    assert float(out["state"]["b"].sum()) == 1.0
    assert float(out["tuple"][0].sum()) == 3.0


def test_to_cpu_state_returns_object_when_no_tensor(monkeypatch):
    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cpu", lambda ts: pytest.fail("不该被调用"))
    obj = {"a": 1, "b": [2, 3]}
    assert common._to_cpu_state(obj) == obj


def test_host_module_batches_once_and_keeps_values(fake_xla):
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


def test_host_module_never_writes_the_source_module(monkeypatch, fake_xla):
    """★ 2026-10-29 云机现场：XLA 上 `p.data = <CPU 张量>` 必炸
    （`RuntimeError: Attempted to call variable.set_data(tensor), but variable and tensor have
    incompatible tensor type`）⇒ 视图这条路**一次都不许**走源模块的写入面。

    钉法：把 `Module._apply`（`.to()` 的实现，也是唯一会碰 Parameter 的入口）改成必抛，
    视图仍要能出完整的 CPU 副本。改动前那条 `p.data = c` 在这里会以同样的方式炸。
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
