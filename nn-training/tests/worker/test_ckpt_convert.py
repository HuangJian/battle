"""tests/worker/test_ckpt_convert.py — torch⇄Paddle Adam 状态翻译（纯 numpy，2026-10-27）。

用户口径：hub 统一 torch 格式，Paddle worker 自己转。本文件的判据：

  ① 真 torch Adam 的 `state_dict()` → Paddle 扁平表：m1/m2 逐元素相同、pows = `beta^(t+1)`
     （Paddle 3.3.1 的累加器初值/步进语义，`tmp/probe_paddle_adam.py` 实测）；
  ② 往返（torch → Paddle → torch）后 `exp_avg/exp_avg_sq/step` 逐位不变，`param_groups`
     键序与 torch 2.7 实际输出一致；
  ③ **续跑语义**：Paddle 扁平表 + Paddle 的 Adam 更新公式（`lr*sqrt(1-b2^pow)/(1-b1^pow)`）
     走一步，与 torch 在原状态上走一步的权重在浮点噪声内相同 —— 这条直接钉住「pow 的指数
     取 t 还是 t+1」这类会让训练爆掉的错误；
  ④ 拒收面：amsgrad / 半截节点 / step 不一致 / 非整数 step / 越界下标 / 形状不符。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import torch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from worker.ppo.ckpt_convert import (
    DEFAULT_BETAS,
    drop_vacuous_nodes,
    paddle_state_to_torch,
    pow_from_step,
    torch_state_to_paddle,
)

NAMED = [
    ("stem.weight", "conv2d_0.w_0", (4, 3, 3, 3)),
    ("fc.weight", "linear_0.w_0", (3, 4)),
    ("fc.bias", "linear_0.b_0", (3,)),
]


def _trained_adam(steps: int = 3) -> tuple[torch.nn.Module, torch.optim.Adam]:
    torch.manual_seed(3)
    model = torch.nn.Sequential(
        torch.nn.Conv2d(3, 4, 3, padding=1), torch.nn.Flatten(), torch.nn.Linear(4 * 2 * 2, 3)
    )
    opt = torch.optim.Adam(model.parameters(), lr=3e-4)
    for _ in range(steps):
        x = torch.randn(2, 3, 2, 2)
        loss = model(x).pow(2).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    return model, opt


def _real_named(model: torch.nn.Module) -> list[tuple[str, str, tuple[int, ...]]]:
    names = [n for n, _ in model.named_parameters()]
    assert names == ["0.weight", "0.bias", "2.weight", "2.bias"]
    paddle_names = ["conv2d_0.w_0", "conv2d_0.b_0", "linear_0.w_0", "linear_0.b_0"]
    return [
        (n, pn, tuple(p.shape))
        for (n, p), pn in zip(model.named_parameters(), paddle_names, strict=True)
    ]


def test_pow_from_step_replays_f32_multiplication() -> None:
    """0 步 = beta（Paddle 累加器初值）；t 步 = beta^(t+1)，且逐次 f32 乘与直接幂一致。"""
    assert pow_from_step(0.9, 0) == np.float32(0.9)
    p = np.float32(0.9)
    for t in range(0, 500):
        assert pow_from_step(0.9, t) == p
        assert abs(float(p) - 0.9 ** (t + 1)) <= 2e-7
        p = np.float32(p * np.float32(0.9))


def test_torch_state_to_paddle_shapes_and_pows() -> None:
    model, opt = _trained_adam(steps=3)
    named = _real_named(model)
    flat, steps = torch_state_to_paddle(opt.state_dict(), named)
    assert steps == 3
    sd = opt.state_dict()
    for idx, (_, pname, shape) in enumerate(named):
        m1 = flat[f"{pname}_moment1_0"]
        m2 = flat[f"{pname}_moment2_0"]
        assert m1.shape == shape and m1.dtype == np.float32
        assert np.array_equal(m1, sd["state"][idx]["exp_avg"].numpy())
        assert np.array_equal(m2, sd["state"][idx]["exp_avg_sq"].numpy())
        assert flat[f"{pname}_beta1_pow_acc_0"].shape == (1,)
        assert flat[f"{pname}_beta1_pow_acc_0"][0] == pow_from_step(0.9, steps)
        assert flat[f"{pname}_beta2_pow_acc_0"][0] == pow_from_step(0.999, steps)


def test_roundtrip_preserves_values_and_group_shape() -> None:
    model, opt = _trained_adam(steps=2)
    named = _real_named(model)
    src = opt.state_dict()
    flat, steps = torch_state_to_paddle(src, named)
    back = paddle_state_to_torch(flat, named, steps=steps, lr=3e-4)

    assert list(back) == ["state", "param_groups"]
    assert list(back["param_groups"][0]) == list(src["param_groups"][0])
    for idx in src["state"]:
        node = back["state"][idx]
        assert np.array_equal(node["exp_avg"], src["state"][idx]["exp_avg"].numpy())
        assert np.array_equal(node["exp_avg_sq"], src["state"][idx]["exp_avg_sq"].numpy())
        assert float(node["step"]) == float(src["state"][idx]["step"])


def _paddle_step_np(
    weight: np.ndarray,
    grad: np.ndarray,
    flat: dict[str, np.ndarray],
    pname: str,
    lr: float,
    betas: tuple[float, float] = DEFAULT_BETAS,
    eps: float = 1e-8,
) -> np.ndarray:
    """Paddle 3.3.1 Adam 的单步公式（adam.py 文档 + `_add_moments_pows` 初值）。"""
    b1, b2 = (np.float32(betas[0]), np.float32(betas[1]))
    m = b1 * flat[f"{pname}_moment1_0"] + (1 - b1) * grad
    v = b2 * flat[f"{pname}_moment2_0"] + (1 - b2) * grad * grad
    pow1 = flat[f"{pname}_beta1_pow_acc_0"][0]
    pow2 = flat[f"{pname}_beta2_pow_acc_0"][0]
    lr_t = np.float32(lr) * np.sqrt(np.float32(1) - pow2) / (np.float32(1) - pow1)
    return np.asarray(weight - lr_t * m / (np.sqrt(v) + np.float32(eps)))


def test_paddle_formula_continuation_matches_torch() -> None:
    """Paddle 拿转换后的状态再走一步 == torch 在原状态上走一步（pow 指数语义的硬判据）。"""
    model, opt = _trained_adam(steps=3)
    named = _real_named(model)
    flat, steps = torch_state_to_paddle(opt.state_dict(), named)
    assert steps == 3

    x = torch.randn(2, 3, 2, 2, generator=torch.Generator().manual_seed(99))
    loss = model(x).pow(2).mean()
    opt.zero_grad()
    loss.backward()
    torch_grads = []
    for p in model.parameters():
        assert p.grad is not None
        torch_grads.append(p.grad.detach().numpy())
    torch_before = [p.detach().numpy().copy() for p in model.parameters()]
    opt.step()

    for p, g, w0, pname in zip(
        model.parameters(), torch_grads, torch_before, [n[1] for n in named], strict=True
    ):
        w_np = _paddle_step_np(w0, g, flat, pname, lr=3e-4)
        d = float(np.abs(w_np - p.detach().numpy()).max())
        assert d < 1e-6, f"{pname}: max|Δ|={d:.3e}"


def test_empty_state_roundtrip() -> None:
    empty = {"state": {}, "param_groups": [{"lr": 3e-4, "betas": (0.9, 0.999), "params": [0, 1]}]}
    flat, steps = torch_state_to_paddle(empty, NAMED)
    assert flat == {} and steps == 0
    back = paddle_state_to_torch({}, NAMED, steps=0, lr=3e-4)
    assert back["state"] == {}


def _opt_like(**node_overrides) -> dict:
    torch.manual_seed(5)
    node = {
        "step": torch.tensor(7.0),
        "exp_avg": torch.randn(4, 3, 3, 3),
        "exp_avg_sq": torch.rand(4, 3, 3, 3) + 0.1,
    }
    for k, v in node_overrides.items():
        if v is None:
            node.pop(k, None)
        else:
            node[k] = v
    return {
        "state": {0: node},
        "param_groups": [{"lr": 3e-4, "betas": (0.9, 0.999), "params": [0, 1, 2]}],
    }


def test_rejects_amsgrad_node() -> None:
    with pytest.raises(ProtocolError, match="amsgrad"):
        torch_state_to_paddle(_opt_like(max_exp_avg_sq=torch.ones(4, 3, 3, 3)), NAMED)


def test_rejects_half_node_and_bad_step() -> None:
    with pytest.raises(ProtocolError, match="缺"):
        torch_state_to_paddle(_opt_like(exp_avg_sq=None), NAMED)
    with pytest.raises(ProtocolError, match="非负整数"):
        torch_state_to_paddle(_opt_like(step=torch.tensor(1.5)), NAMED)


def test_rejects_inconsistent_steps() -> None:
    st = _opt_like()
    st["state"][1] = {
        "step": torch.tensor(8.0),
        "exp_avg": torch.zeros(3, 4),
        "exp_avg_sq": torch.zeros(3, 4),
    }
    with pytest.raises(ProtocolError, match="step 不一致"):
        torch_state_to_paddle(st, NAMED)


def test_rejects_out_of_range_and_shape_mismatch() -> None:
    st = _opt_like()
    st["state"][9] = st["state"][0]
    with pytest.raises(ProtocolError, match="参数下标"):
        torch_state_to_paddle(st, NAMED)
    st = _opt_like()
    st["state"][0]["exp_avg"] = torch.zeros(3, 4)
    with pytest.raises(ProtocolError, match="shape"):
        torch_state_to_paddle(st, NAMED)


def test_paddle_half_state_rejected_on_reverse() -> None:
    flat = {"linear_0.w_0_moment1_0": np.zeros((3, 4), np.float32)}
    with pytest.raises(ProtocolError, match="半截"):
        paddle_state_to_torch(flat, NAMED, steps=1, lr=3e-4)


# ─────────────── ⑤ 缺项补齐（torch 惰性建 state）与 delta 容器的父子契约（2026-10-27 评审） ───────────────


def test_partial_state_is_filled_with_zero_momentum() -> None:
    """★ tower 档的真实形状：torch 的 `state` 只覆盖拿到过梯度的参数，缺项必须补齐。

    为什么（现场实测，2026-10-27 评审）：`spatial_tower=True` 时 `fc.weight`/`fc.bias` 在塔路径里
    根本不参与（`h` 未被使用 ⇒ `grad=None`）⇒ 真 torch 的 `opt.state_dict()` 是 44/46。而 Paddle
    的累加器**必须**占位：`set_state_dict` 收半份表会在第一步炸（`Optimizer set error, … should in
    state dict`）—— 拒收那条路（旧行为）等于把 tower 档的续跑全挡在门外。

    补齐语义 = 「走过 t 步、动量一直是 0」：与 torch 那边 `exp_avg=0, exp_avg_sq=0, step=t` 的节点
    逐字段同义（bias correction 用与其它参数同一个 pow）。
    """
    model, opt = _trained_adam(steps=3)
    named = _real_named(model)
    st = opt.state_dict()
    assert set(st["state"]) == {0, 1, 2, 3}
    del st["state"][2]  # 摘掉一个参数的节点 = 死分支参数（真实形态里是 fc.weight / fc.bias）
    flat, steps = torch_state_to_paddle(st, named)
    assert steps == 3

    _, pname, shape = named[2]
    assert np.array_equal(flat[f"{pname}_moment1_0"], np.zeros(shape, np.float32))
    assert np.array_equal(flat[f"{pname}_moment2_0"], np.zeros(shape, np.float32))
    assert flat[f"{pname}_beta1_pow_acc_0"][0] == pow_from_step(0.9, 3)
    assert flat[f"{pname}_beta2_pow_acc_0"][0] == pow_from_step(0.999, 3)
    # 全表覆盖：`opt.set_state_dict` 只收完整表 —— 这正是补齐存在的理由。
    # （写成集合差而不是 `assert "…" in flat`：纯数据判据，不撞文本断言口径。）
    want = {
        f"{pname_i}_{suffix}"
        for _, pname_i, _ in named
        for suffix in ("moment1_0", "moment2_0", "beta1_pow_acc_0", "beta2_pow_acc_0")
    }
    assert want - set(flat) == set(), f"补齐没覆盖到：{sorted(want - set(flat))}"
    # 其它参数的读数不受影响（补齐不碰已有节点）。
    for idx, (_, pname_i, shape_i) in enumerate(named):
        if idx == 2:
            continue
        assert np.array_equal(flat[f"{pname_i}_moment1_0"], st["state"][idx]["exp_avg"].numpy())
        assert flat[f"{pname_i}_moment1_0"].shape == shape_i
    # 往返回来就是「零动量 + 同一个 step」的那个节点。
    back = paddle_state_to_torch(flat, named, steps=steps, lr=3e-4)
    assert set(back["state"]) == {0, 1, 2, 3}
    assert np.array_equal(back["state"][2]["exp_avg"], np.zeros(shape, np.float32))
    assert float(back["state"][2]["step"]) == 3.0


def _one_node(idx: int, value: float) -> dict:
    """torch 形态的最小 state（`value=0` = 补齐出来的那种「零动量」节点）。"""
    leaf = np.full((2,), value, np.float32)
    return {
        "state": {
            idx: {
                "step": np.array(3.0, np.float32),
                "exp_avg": leaf.copy(),
                "exp_avg_sq": leaf.copy(),
            }
        },
        "param_groups": [],
    }


def test_drop_vacuous_nodes_keeps_the_base_node_set() -> None:
    """★ delta 容器的父子契约（`pack_opt_delta`：基准里没有的路径一律拒发）。

    补齐会在活优化器里留下「基准里没有、且恒为零」的节点 ⇒ 回传前按基准投影掉：hub 侧
    base + delta 才会与 torch 自己的那一轮逐字段同义。
    """
    base = _one_node(0, 0.5)
    new = _one_node(0, 0.5)
    new["state"][1] = _one_node(1, 0.0)["state"][1]
    out, dropped = drop_vacuous_nodes(new, base)
    assert set(out["state"]) == {0}
    assert dropped == [1]
    assert out["state"][0] is new["state"][0]  # 保序保对象（只摘多余的）
    assert set(new["state"]) == {0, 1}  # 不改原对象


def test_drop_vacuous_nodes_refuses_a_dirty_extra_node() -> None:
    """基准里没有的节点若**动量非零**：那是真结构分歧 ⇒ 拒发，不猜一个基准。"""
    with pytest.raises(ProtocolError, match="结构不一致"):
        drop_vacuous_nodes(_one_node(1, 0.25), _one_node(0, 0.5))
