"""worker/ppo/ckpt_convert.py —— torch Adam `state_dict()` ⇄ Paddle Adam `state_dict()`（纯 numpy）。

用户口径（2026-10-27）：**hub 机统一用 torch 格式**，Paddle worker 自己负责转换；两类 worker
混跑同一门课。本模块只做**状态形状**的翻译，不碰文件、不 import 任何框架 —— 文件那半在
`worker/ppo/checkpoint_paddle.py`，真实数值对账在 `tools/paddle_parity.py`。

## 两种形状

torch（`torch.optim.Adam.state_dict()`，与 `remote/opt_delta`、hub 的 opt 链同一形状）::

    {"state": {0: {"step": tensor(f32, 0-dim), "exp_avg": t, "exp_avg_sq": t}, …},
     "param_groups": [{lr, betas, eps, weight_decay, amsgrad, maximize, foreach,
                       capturable, differentiable, fused, decoupled_weight_decay, params}, …]}

Paddle（`paddle.optimizer.Adam.state_dict()`）是**一张扁平的 `{name: tensor}`**，按
`p.name`（如 `m_0.w_0_moment1_0`）索引，没有 `step`，只有两个 f32 累加器::

    <param.name>_moment1_0 · <param.name>_moment2_0
    <param.name>_beta1_pow_acc_0 · <param.name>_beta2_pow_acc_0      （shape [1]）

## 步数：Paddle 没有 `step`，只有 beta 幂

实测（Paddle 3.3.1，`tmp/probe_paddle_adam.py`）：`beta1_pow_acc` 初始 = **beta**，
每步先用于 bias correction、再乘 beta ⇒ **t 步之后 = beta^(t+1)**。所以：

* torch → Paddle：`t` 来自 torch 的 `step`；pows 用**逐次 f32 乘**重放（`pow_from_step`），
  与 Paddle 自己的累加序列逐位一致（直接 `beta**(t+1)` 会在末位偏 ulp）。
* Paddle → torch：`step` 必须由调用方**显式记账**（引擎每 minibatch +1，续跑从 ckpt 读回）
  —— 从 pow 反解 `t` 在大 t 上会因 f32 下溢失真（pow → 0），那不是可恢复的量。

## 一致性判据（本模块的全部拒收面）

* 节点集合必须整齐：有 `exp_avg` 没 `exp_avg_sq`（或反之）= 半截状态 ⇒ 拒；
* `amsgrad` 节点拒（Paddle 的 plain Adam 没有 `max_exp_avg_sq`，静默丢掉 = 换算法）；
* 各节点的 `step` 必须相同（Adam 每步对所有带梯度的参数同步 +1）；
* 形状必须与模型参数逐一相同；参数下标必须落在 `named` 范围内。

## 缺项：torch 惰性建 state ⇒ 表本来就可能不全（2026-10-27 评审）

torch 的 Adam **谁有梯度才给谁建 state**：`spatial_tower` 档下 `fc.weight`/`fc.bias` 在塔路径里
根本不参与（`h` 未被使用 ⇒ `grad=None`） ⇒ 落盘的 `opt.pt` 只覆盖 44/46 个参数。而 Paddle 的
累加器更脆：`set_state_dict` 收半份表会在第一步炸（实测 `AssertionError: Optimizer set error,
conv2d_18.w_0_moment1_0 should in state dict`）⇒ `torch_state_to_paddle` **按 torch 的语义补齐**
缺项：零动量 + 与其它参数同一个 pow（= 「走过 t 步、动量一直是 0」，与 torch 那边
`exp_avg=0, exp_avg_sq=0, step=t` 的节点逐字段同义）。补齐只在**载入**这一侧发生 ——
回传的 delta 容器要靠 `drop_vacuous_nodes` 把这种「基准里没有、且恒为零」的节点再摘掉
（容器的父子契约不允许凭空多路径）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from common.errors import ProtocolError

__all__ = [
    "drop_vacuous_nodes",
    "paddle_state_to_torch",
    "pow_from_step",
    "torch_state_to_paddle",
]

#: `(torch 参数名, paddle 参数名, shape)`，顺序 = torch `model.parameters()` 的顺序。
ParamRef = tuple[str, str, tuple[int, ...]]

DEFAULT_BETAS = (0.9, 0.999)
DEFAULT_EPS = 1e-8


def pow_from_step(beta: float, steps: int) -> np.float32:
    """Paddle `beta_pow_acc` 走完 `steps` 步之后的值 = `beta^(steps+1)`（f32 逐次乘）。

    步数为 0 时返回 `beta` —— 累加器一旦创建就是这个值（Paddle 在首步之前不动它）。
    """
    p = np.float32(beta)
    b = np.float32(beta)
    for _ in range(max(0, int(steps))):
        p = np.float32(p * b)
    return p


def _as_f32(value: Any, what: str, shape: tuple[int, ...] | None = None) -> np.ndarray:
    try:
        arr = np.asarray(value)
    except Exception as e:  # pragma: no cover - numpy 的失败形态很杂，统一成 ProtocolError
        raise ProtocolError(f"{what}: 不是数组（{e}）") from None
    if arr.dtype.kind not in "fiu":
        raise ProtocolError(f"{what}: dtype={arr.dtype} 不是数值张量")
    arr = np.array(arr, dtype=np.float32, copy=True)
    if shape is not None and tuple(arr.shape) != tuple(shape):
        raise ProtocolError(f"{what}: shape {tuple(arr.shape)} != 模型参数 {tuple(shape)}")
    return arr


def _step_int(value: Any, what: str) -> int:
    arr = np.asarray(value, dtype=np.float64).reshape(-1)
    if arr.size != 1:
        raise ProtocolError(f"{what}: step 不是标量（shape {np.asarray(value).shape}）")
    v = float(arr[0])
    if not math.isfinite(v) or v < 0.0 or v != int(v):
        raise ProtocolError(f"{what}: step={v!r} 不是非负整数")
    return int(v)


def torch_state_to_paddle(
    state: Mapping[str, Any],
    named: Sequence[ParamRef],
    *,
    betas: tuple[float, float] = DEFAULT_BETAS,
) -> tuple[dict[str, np.ndarray], int]:
    """torch Adam state_dict → `(Paddle 扁平 state_dict, 已走步数 t)`。

    只翻有节点的参数（Paddle 的动量也是懒创建的）；`param_groups` 不进扁平表 ——
    Paddle 的 Adam 超参是构造参数，`set_state_dict` 不负责它们（lr 由调用方 `set_lr`）。
    """
    nodes = state.get("state")
    if not isinstance(nodes, Mapping):
        raise ProtocolError("torch opt state_dict 缺 'state' 映射")
    if not nodes:
        return {}, 0

    steps: int | None = None
    flat: dict[str, np.ndarray] = {}
    for raw_idx, node in nodes.items():
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            raise ProtocolError(f"torch opt state 的参数下标不是整数：{raw_idx!r}") from None
        if idx < 0 or idx >= len(named):
            raise ProtocolError(f"torch opt state 有参数下标 {idx}，但模型只有 {len(named)} 个参数")
        if not isinstance(node, Mapping):
            raise ProtocolError(f"torch opt state[{idx}] 不是映射")
        if node.get("max_exp_avg_sq") is not None:
            raise ProtocolError(
                f"torch opt state[{idx}] 是 amsgrad 节点（max_exp_avg_sq）——"
                "Paddle 侧是 plain Adam，静默丢弃等于换算法，拒收"
            )
        for key in ("step", "exp_avg", "exp_avg_sq"):
            if key not in node:
                raise ProtocolError(f"torch opt state[{idx}] 缺 {key!r}")
        t = _step_int(node["step"], f"state[{idx}].step")
        if steps is None:
            steps = t
        elif t != steps:
            raise ProtocolError(
                f"torch opt state 各参数的 step 不一致（{steps} vs state[{idx}]={t}）——拒收"
            )
        _, paddle_name, shape = named[idx]
        flat[f"{paddle_name}_moment1_0"] = _as_f32(
            node["exp_avg"], f"state[{idx}].exp_avg", shape
        )
        flat[f"{paddle_name}_moment2_0"] = _as_f32(
            node["exp_avg_sq"], f"state[{idx}].exp_avg_sq", shape
        )

    assert steps is not None
    beta1, beta2 = (float(betas[0]), float(betas[1]))
    pow1 = np.full((1,), pow_from_step(beta1, steps), dtype=np.float32)
    pow2 = np.full((1,), pow_from_step(beta2, steps), dtype=np.float32)
    for _torch_name, paddle_name, shape in named:
        if f"{paddle_name}_moment1_0" not in flat:
            # ★ 2026-10-27（评审）：torch 惰性建 state ⇒ 死分支上的参数（没有 grad）不在表里，
            # 而 Paddle 的累加器**必须**占位（半份表第一步就 assert，见模块头）。补齐语义 =
            # 「走过 t 步、动量一直是 0」：与 torch 那边 `exp_avg=0, exp_avg_sq=0, step=t`
            # 的节点逐字段同义（bias correction 用同一个 pow）。
            flat[f"{paddle_name}_moment1_0"] = np.zeros(shape, dtype=np.float32)
            flat[f"{paddle_name}_moment2_0"] = np.zeros(shape, dtype=np.float32)
        flat[f"{paddle_name}_beta1_pow_acc_0"] = pow1.copy()
        flat[f"{paddle_name}_beta2_pow_acc_0"] = pow2.copy()
    return flat, steps


def drop_vacuous_nodes(state: Mapping[str, Any], ref: Mapping[str, Any]) -> tuple[dict, list[int]]:
    """把 torch 形态的 `state` 投影回 `ref` 的**节点集** ⇒ `(投影后的 state, 被摘掉的下标)`。

    存在的理由（新纪元 delta 容器的父子契约，`remote/opt_delta.pack_opt_delta`：基准里没有的
    路径一律拒发）：`torch_state_to_paddle` 为让 Paddle 收下会把缺项补成**零动量**，而 Paddle
    的 `state_dict()` 会把它们原样交回 ⇒ 回传时节点集比基准多。那些「基准里没有**且**恒为零」
    的节点就是 torch 惰性建 state 的语义（该参数从没拿到过梯度），摘掉才能让 base + delta 与
    torch 自己的那一轮逐字段同义。

    反向护栏：若一个基准里没有的节点**动量非零**（参数这轮真的拿到过梯度），那是真的结构
    分歧 ⇒ `ProtocolError`（与 packer 的拒发同一条红线，但在这里就把话说清楚）。
    """
    nodes = state.get("state")
    ref_nodes = ref.get("state")
    if not isinstance(nodes, Mapping) or not isinstance(ref_nodes, Mapping):
        raise ProtocolError("drop_vacuous_nodes：两侧都必须有 'state' 映射")
    kept: dict[int, Any] = {}
    dropped: list[int] = []
    for raw_idx, node in nodes.items():
        if raw_idx in ref_nodes:
            kept[raw_idx] = node
            continue
        missing = [k for k in ("exp_avg", "exp_avg_sq") if k not in node]
        dirty = [
            k
            for k in ("exp_avg", "exp_avg_sq")
            if k in node and bool(np.any(np.asarray(node[k]) != 0.0))
        ]
        if missing or dirty:
            raise ProtocolError(
                f"delta：基准里没有参数 {raw_idx} 的节点，而这一轮的动量非零或字段缺失"
                f"（{dirty or missing}）—— 结构不一致，拒发而不是猜一个基准"
            )
        dropped.append(int(raw_idx))
    out = dict(state)
    out["state"] = kept
    return out, dropped


def paddle_state_to_torch(
    flat: Mapping[str, Any],
    named: Sequence[ParamRef],
    *,
    steps: int,
    lr: float,
    betas: tuple[float, float] = DEFAULT_BETAS,
    eps: float = DEFAULT_EPS,
    weight_decay: float = 0.0,
) -> dict[str, Any]:
    """Paddle 扁平 state_dict → torch Adam state_dict（可直接喂 `torch.optim.Adam`）。

    `steps` 由调用方记账（Paddle 只有 beta 幂，反解会在大 t 上失真，见模块头）。
    `param_groups` 的键序 = torch 2.7 `Adam.state_dict()` 的实际输出顺序（逐项对照过
    `tmp/probe-opt/opt.pt`）。
    """
    t = int(steps)
    if t < 0:
        raise ProtocolError(f"steps={steps} 不能为负")
    state: dict[int, dict[str, np.ndarray]] = {}
    for idx, (_torch_name, paddle_name, shape) in enumerate(named):
        m1 = flat.get(f"{paddle_name}_moment1_0")
        m2 = flat.get(f"{paddle_name}_moment2_0")
        if m1 is None and m2 is None:
            continue
        if m1 is None or m2 is None:
            raise ProtocolError(
                f"Paddle opt state 半截：{paddle_name} 只有 "
                f"{'moment1' if m1 is not None else 'moment2'}（另一半缺失）——拒收"
            )
        state[idx] = {
            "step": np.array(float(t), dtype=np.float32),
            "exp_avg": _as_f32(m1, f"{paddle_name}.moment1", shape),
            "exp_avg_sq": _as_f32(m2, f"{paddle_name}.moment2", shape),
        }
    return {
        "state": state,
        "param_groups": [
            {
                "lr": float(lr),
                "betas": (float(betas[0]), float(betas[1])),
                "eps": float(eps),
                "weight_decay": float(weight_decay),
                "amsgrad": False,
                "maximize": False,
                "foreach": None,
                "capturable": False,
                "differentiable": False,
                "fused": None,
                "decoupled_weight_decay": False,
                "params": list(range(len(named))),
            }
        ],
    }
