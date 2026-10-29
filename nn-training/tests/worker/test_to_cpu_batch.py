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


def test_materialize_to_cpu_keeps_values(fake_xla):
    module = torch.nn.Linear(3, 2)
    before = {k: v.detach().clone() for k, v in module.state_dict().items()}
    common.materialize_to_cpu(module)
    assert fake_xla == [len(before)], f"params+buffers 一次取回（{len(before)} 个），实际 {fake_xla}"
    after = module.state_dict()
    for k, v in before.items():
        assert torch.equal(after[k], v), f"{k} 的值被改了"


def test_materialize_to_cpu_empty_module(monkeypatch):
    monkeypatch.setattr(common, "is_xla", lambda _dev: True)
    monkeypatch.setattr(common, "_xla_fetch_cpu", lambda ts: pytest.fail("不该被调用"))
    common.materialize_to_cpu(torch.nn.Module())
