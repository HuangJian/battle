"""P′-4a：IntentNet 维度参数化（默认 8 不变；option 线 6 槽 + 注入 7）。"""

from __future__ import annotations

import numpy as np
import torch

from common.schema import BOARD, OBS_CHANNELS, SCALAR_DIM
from worker.models.arch_meta import NetSpec
from worker.models.intent_net import IntentNet, build_intent_model, export_intent_weights


def _forward_shapes(intent_dim: int) -> tuple[tuple[int, ...], ...]:
    model = IntentNet(NetSpec(h=16, d=2), inject=True, with_value=True, intent_dim=intent_dim)
    obs = torch.zeros(1, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    scal = torch.zeros(1, SCALAR_DIM)
    inject = torch.zeros(1, intent_dim + 1)
    intent, enemy, anchor, value = model.forward_rl(obs, scal, inject)
    return tuple(tuple(t.shape) for t in (intent, enemy, anchor, value))


def test_default_intent_dim_is_8() -> None:
    model = IntentNet(NetSpec(h=16, d=2))
    assert model.intent_dim == 8
    assert model.inject_dim == 9
    assert model.intent_head.out_features == 8
    assert model.arch()["intentDim"] == 8
    assert model.arch()["injectDim"] == 9


def test_option6_heads_and_forward_shape() -> None:
    model = IntentNet(NetSpec(h=16, d=2), inject=True, with_value=True, intent_dim=6)
    assert model.inject_dim == 7
    head_in = model.head_hidden + 7
    assert model.intent_head.in_features == head_in
    assert model.intent_head.out_features == 6
    assert model.value_head is not None and model.value_head.in_features == head_in
    arch = model.arch()
    assert arch["intentDim"] == 6 and arch["injectDim"] == 7
    shapes = _forward_shapes(6)
    assert shapes == ((1, 6), (1, 5), (1, 16), (1, 1))


def test_export_roundtrip_preserves_intent_dim(tmp_path) -> None:
    # 导出校验会与默认 StudentNet 主干逐键对账 ⇒ 必须用默认 h/d（与生产导出同档）。
    model = IntentNet(NetSpec(), inject=True, with_value=True, intent_dim=6)
    out = str(tmp_path / "opt6.json")
    export_intent_weights(model, out)

    from worker.data.weights_io import load_weights_json

    meta, params = load_weights_json(out)
    assert meta["arch"]["intentDim"] == 6
    rebuilt = build_intent_model(meta, params)
    assert rebuilt.intent_dim == 6
    assert rebuilt.intent_head.weight.shape == (6, rebuilt.head_hidden + 7)

    # 同一权重双跑逐位一致（导出/加载不该改数值）。
    obs = torch.zeros(2, OBS_CHANNELS, BOARD, BOARD, dtype=torch.uint8)
    scal = np.zeros((2, SCALAR_DIM), dtype=np.float32)
    inject = np.zeros((2, 7), dtype=np.float32)
    model.eval()
    rebuilt.eval()
    with torch.no_grad():
        a = model.forward_rl(obs, torch.from_numpy(scal), torch.from_numpy(inject))[0]
        b = rebuilt.forward_rl(obs, torch.from_numpy(scal), torch.from_numpy(inject))[0]
    assert torch.equal(a, b)
