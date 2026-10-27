"""worker/ppo/checkpoint_paddle.py —— Paddle worker 的 **torch 格式** checkpoint I/O。

用户口径（2026-10-27）：**hub 机统一收发 torch 格式**（model.pt / opt.pt），Paddle worker
自己转；两类 worker 混跑同一门课，所以两边写出的 checkpoint 必须能互相接手。本文件是
`worker/ppo/common.py` 里 `_ppo_save/_ppo_load` 的 Paddle 双生：

* 文件布局逐项相同：`model.pt` = `model.state_dict()`（torch 名/序），`opt.pt` =
  torch Adam `state_dict()`，`state.json` = `{epochs_done, rng}`；
* 落盘/读盘走 `common.torch_pickle`（**不 import torch**），状态翻译走
  `worker.ppo.ckpt_convert`（纯 numpy），`exp_avg_sq` 的传输下溢消毒与 torch 侧同一条
  规则（`sanitize_adam_sq_np`）；
* `state.json` 多写一个 `steps_done`：Paddle 的 Adam 只有 beta 幂没有 `step`，续跑转回
  torch 格式时需要整数步数（反解 pow 在大 t 上会失真，见 `ckpt_convert` 模块头）。
  torch 的 `_ppo_load` 只读 `epochs_done`/`rng`，多这个键不改变任何 torch 行为。

## 与 torch 侧的两处**有意的**语义对齐

1. `opt.load_state_dict` 会连 `param_groups` 一起换（含 lr）⇒ 这里同样 `opt.set_lr(ckpt lr)`。
   课程 Adam 的其余超参（betas/eps/weight_decay）Paddle 构造后不可改 ⇒ 载入时对不上就**拒收**，
   不静默拿本机默认值训练（那等于换算法）。
2. Paddle 的累加器是**懒创建**的：`set_state_dict` 先把整份扁平表挂起，等 `_add_accumulator`
   逐个认领（`optimizer.py` 里 `_accumulators_holder` 的 assert）。所以表必须覆盖**全部**参数
   —— 半份表会在第一步炸，或更糟：静默给没覆盖的参数用 fill_value。
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

from common.adam_sq import sanitize_adam_sq_np
from common.errors import ProtocolError
from common.torch_pickle import dump_torch_pickle, load_torch_pickle
from worker.ppo.ckpt_convert import (
    ParamRef,
    drop_vacuous_nodes,
    paddle_state_to_torch,
    torch_state_to_paddle,
)
from worker.ppo.np_core import _pack_np_state, _unpack_np_state

__all__ = [
    "load_ckpt",
    "load_model_pt",
    "load_opt_pt",
    "named_params",
    "save_ckpt",
    "torch_opt_state",
]

MODEL_FILE = "model.pt"
OPT_FILE = "opt.pt"
STATE_FILE = "state.json"


def _paddle():
    try:
        import paddle
    except ImportError as e:  # pragma: no cover - 只有环境真缺 paddle 才走到
        raise ProtocolError(f"Paddle checkpoint 需要 paddle（{e}）") from None
    return paddle


def named_params(model) -> list[ParamRef]:
    """`(torch 参数名, Paddle 运行名, shape)`，顺序 = 构造顺序。

    顺序就是 torch `model.parameters()` 的顺序（`student_paddle` 的双生构造与 torch 逐一对应，
    `tools/paddle_parity.py ckpt` 用例拿真实 torch ckpt 对账）；torch Adam 的 state 下标
    只认这个顺序。
    """
    return [
        (str(name), str(p.name), tuple(int(x) for x in p.shape))
        for name, p in model.named_parameters()
    ]


def _betas_of(opt) -> tuple[float, float]:
    return (float(opt._beta1), float(opt._beta2))


def _write_bytes(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def torch_opt_state(
    model, opt, *, steps_done: int, node_ref: Mapping[str, Any] | None = None
) -> dict:
    """活优化器 → **torch Adam `state_dict()` 形状**（numpy 叶子；hub 侧直接 `torch.load` 读）。

    `save_ckpt` 与回传 delta 的 R3（`train_core` 的 Paddle 核）共用这一份：两边各写一遍
    「Paddle 扁平表 → torch 节点」就会在 `step` 记账上分叉（一个 +1 一个不 +1，形状全对、
    第二轮的 bias correction 全错）。

    `node_ref`（2026-10-27 评审）：delta 容器要求**节点集继承基准**，而 Paddle 侧为了让
    `set_state_dict` 收下会把 torch 的缺项补成零动量（`ckpt_convert.torch_state_to_paddle`
    —— torch 惰性建 state：死分支参数没有节点）⇒ 回传前按基准投影，摘掉那些「基准里没有
    且恒为零」的节点（`ckpt_convert.drop_vacuous_nodes`；动量非零 = 真结构分歧 ⇒ 拒发）。
    `save_ckpt`（整份落盘，没有父容器）不传它。
    """
    flat = {str(k): np.array(v.numpy(), copy=True) for k, v in opt.state_dict().items()}
    state = paddle_state_to_torch(
        flat,
        named_params(model),
        steps=int(steps_done),
        lr=float(opt.get_lr()),
        betas=_betas_of(opt),
        eps=float(opt._epsilon),
    )
    if not state["state"] and int(steps_done) > 0:
        raise ProtocolError(
            f"Paddle opt state 为空但 steps_done={steps_done} —— 累加器没建/没被记账，"
            "写出这份 opt.pt 会静默丢动量"
        )
    if node_ref is not None:
        state, _dropped = drop_vacuous_nodes(state, node_ref)
    return state


def save_ckpt(
    model,
    opt,
    ckpt_path: str,
    *,
    epochs_done: int,
    steps_done: int,
) -> None:
    """epoch 粒度 checkpoint（与 `worker.ppo.common._ppo_save` 同布局，Paddle 侧实现）。"""
    os.makedirs(ckpt_path, exist_ok=True)
    model_state = OrderedDict(
        (str(k), np.array(v.numpy(), copy=True)) for k, v in model.state_dict().items()
    )
    _write_bytes(
        os.path.join(ckpt_path, MODEL_FILE),
        dump_torch_pickle(model_state, prefix="archive"),
    )
    _write_bytes(
        os.path.join(ckpt_path, OPT_FILE),
        dump_torch_pickle(torch_opt_state(model, opt, steps_done=steps_done), prefix="archive"),
    )
    with open(os.path.join(ckpt_path, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(
            {
                "epochs_done": int(epochs_done),
                "steps_done": int(steps_done),
                "rng": _pack_np_state(),
            },
            f,
        )


def _check_param_group(torch_state: Mapping[str, Any], opt) -> None:
    groups = torch_state.get("param_groups")
    if not isinstance(groups, list) or len(groups) != 1:
        raise ProtocolError(
            f"torch opt ckpt 的 param_groups 不是单组（{type(groups).__name__}"
            f"/{len(groups) if isinstance(groups, list) else '?'}）——训练核只支持单组 Adam"
        )
    g = groups[0]
    if bool(g.get("amsgrad")):
        raise ProtocolError("torch opt ckpt 是 amsgrad —— Paddle 侧 plain Adam 拒收")
    if bool(g.get("maximize")):
        raise ProtocolError("torch opt ckpt 是 maximize=True —— Paddle 侧 Adam 拒收")
    wd = float(g.get("weight_decay", 0.0) or 0.0)
    if wd != 0.0:
        raise ProtocolError(
            f"torch opt ckpt 的 weight_decay={wd} —— Paddle Adam 的 L2 在构造期绑定，"
            "构造后无法等价接管，拒收"
        )
    betas = tuple(float(x) for x in g.get("betas", (0.9, 0.999)))
    eps = float(g.get("eps", 1e-8))
    if betas != _betas_of(opt) or eps != float(opt._epsilon):
        raise ProtocolError(
            f"torch opt ckpt 超参与本机 Paddle Adam 不同（ckpt betas={betas} eps={eps} vs "
            f"本机 betas={_betas_of(opt)} eps={opt._epsilon}）——续跑会换算法，拒收"
        )


def _load_model_state(model, state: Mapping[str, Any]) -> None:
    current = model.state_dict()
    missing = sorted(set(current) - set(state))
    extra = sorted(set(state) - set(current))
    if missing or extra:
        raise ProtocolError(
            f"model.pt 与当前模型不匹配：缺 {missing[:5]}… 多 {extra[:5]}…"
            f"（缺 {len(missing)} / 多 {len(extra)}）——拒收，不做半份初始化"
        )
    arrays: dict[str, np.ndarray] = {}
    for k, v in state.items():
        arr = np.asarray(v)
        if arr.dtype.kind not in "fiu":
            raise ProtocolError(f"model.pt[{k}]: dtype={arr.dtype} 不是数值张量")
        if tuple(arr.shape) != tuple(current[k].shape):
            raise ProtocolError(
                f"model.pt[{k}]: shape {tuple(arr.shape)} != 模型 {tuple(current[k].shape)}"
            )
        arrays[k] = arr
    paddle = _paddle()
    with paddle.no_grad():
        for k, arr in arrays.items():
            current[k].set_value(paddle.to_tensor(np.ascontiguousarray(arr)))


def _load_opt_state(model, opt, torch_state: Mapping[str, Any]) -> int:
    _check_param_group(torch_state, opt)
    sanitize_adam_sq_np(torch_state)
    named = named_params(model)
    flat, steps = torch_state_to_paddle(torch_state, named, betas=_betas_of(opt))
    if torch_state["state"]:
        # ⚠ 2026-10-27（评审）：这里曾是**拒收**面 —— 「只覆盖 44/46 个参数」其实是 torch
        # 的正常形态（惰性建 state：死分支参数没有 grad ⇒ 没有节点），拒收等于把
        # `spatial_tower` 档的续跑全挡在门外。现在由 `torch_state_to_paddle` **补齐**缺项
        # （零动量 + 同一个 pow），覆盖率判据因此从「用户可见的拒收」降级为**转换器的
        # 后置条件**：它只在转换器哪天回归时才可能触发（用例：
        # tests/worker/test_ckpt_convert.py 的「缺 2 个参数的表 ⇒ 补齐」）。
        covered = sum(1 for _, pname, _ in named if f"{pname}_moment1_0" in flat)
        if covered != len(named):
            raise ProtocolError(
                f"opt 状态表只覆盖 {covered}/{len(named)} 个参数 —— 转换器没补齐"
                "（内部不一致；这不是 job 数据的问题）"
            )
        pd = _paddle()
        opt.set_state_dict({k: pd.to_tensor(v) for k, v in flat.items()})
    lr = float(torch_state["param_groups"][0]["lr"])
    opt.set_lr(lr)
    return steps


def load_model_pt(model, path: str | Path) -> None:
    """载入一份**裸** `model.pt`（restore 段从落地的整份 tar 里取出那一个成员时用）。"""
    state = load_torch_pickle(Path(path).read_bytes())
    if not isinstance(state, Mapping):
        raise ProtocolError(f"{path}: model.pt 顶层不是映射")
    _load_model_state(model, state)


def load_opt_pt(model, opt, path: str | Path) -> int:
    """载入一份**裸** `opt.pt` ⇒ 已走的 Adam 步数（后续 `steps_done` 记账的起点）。

    Paddle 的 Adam 没有 `step`，步数只能来自 torch 格式里那个 0-dim `step`（`ckpt_convert`
    模块头：从 beta 幂反解在大 t 上会因 f32 下溢失真）。
    """
    state = load_torch_pickle(Path(path).read_bytes())
    if not isinstance(state, Mapping):
        raise ProtocolError(f"{path}: opt.pt 顶层不是映射")
    return _load_opt_state(model, opt, state)


def load_ckpt(model, opt, ckpt_path: str | None) -> tuple[int, int]:
    """载入 torch 格式 checkpoint，返回 `(epochs_done, steps_done)`；目录不全 ⇒ `(0, 0)`。

    语义与 `worker.ppo.common._ppo_load` 一致：三件套缺一就算「没有 checkpoint」；
    model/opt/RNG 三者任一读不动都**响亮**失败（不做半份续跑）。
    """
    if not ckpt_path:
        return (0, 0)
    paths = {
        key: os.path.join(ckpt_path, name)
        for key, name in (
            ("state", STATE_FILE),
            ("model", MODEL_FILE),
            ("opt", OPT_FILE),
        )
    }
    if not all(os.path.exists(p) for p in paths.values()):
        return (0, 0)
    with open(paths["state"], encoding="utf-8") as f:
        st = json.load(f)
    load_model_pt(model, paths["model"])
    steps = load_opt_pt(model, opt, paths["opt"])
    _unpack_np_state(st["rng"])
    return int(st.get("epochs_done", 0)), int(st.get("steps_done", steps))
