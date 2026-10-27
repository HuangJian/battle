"""Paddle twin of `worker.models.student` — same parameter names, shapes and math.

Why this file exists: AI Studio training-task images ship Paddle, not PyTorch
(nn-training/remote/train_core.py is the only torch-dependent stage of a
kind=ppo job). The model is plain Conv2d/Linear/ReLU/GAP, so the twin is a
mechanical port; the weight JSON (`nn-weights-json`) is the interchange
format, which keeps the TS runtime unchanged.

Parity contract (tools/paddle_parity.py):
  · state_dict keys/shapes are identical to the torch model (OIHW conv layout);
  · forward logits match torch for the same weights within float32 noise;
  · `save_weights_json` emits byte-compatible metadata and f32 payloads.
"""

from __future__ import annotations

import base64
import json
import math
import os
from typing import Any

import numpy as np
import paddle
import paddle.nn as nn

from common.schema import (
    BOARD,
    FIRE3_DIM,
    FIRE_DIM,
    HOLD_DIM,
    MOVE4_DIM,
    MOVE_DIM,
    OBS_CHANNELS,
    OBS_SCHEMA_MAJOR,
    POLICY_EXTRA_DIM,
    SCALAR_DIM,
)
from worker.data.weights_meta import COVERAGE_RAISE, FORMAT, validate_weights_meta

# 架构默认档 / 空间塔冻结值 / 架构元数据面 —— 与 torch 双生**同一份**
# （`worker/models/arch_meta.py`）；下面这些是转发名（名字是契约，位置不是）。
from worker.models.arch_meta import (
    DEFAULT_D,
    DEFAULT_H,
    DEFAULT_HEAD_HIDDEN,
    SPATIAL_TOWER_C,
    SPATIAL_TOWER_FC_OUT,
    SPATIAL_TOWER_FEAT,
    SPATIAL_TOWER_POOL,
    StudentArchMixin,
    read_arch_kwargs,
)


def _kaiming_uniform(fan_in: int) -> paddle.ParamAttr:
    bound = math.sqrt(6.0 / max(1, fan_in))
    winit = paddle.nn.initializer.Uniform(-bound, bound)
    return paddle.ParamAttr(initializer=winit)


def _zero_bias() -> paddle.ParamAttr:
    return paddle.ParamAttr(initializer=paddle.nn.initializer.Constant(0.0))


def coord_channels(board: int) -> paddle.Tensor:
    r = paddle.arange(0, board, 1, dtype="float32") / (board - 1)
    x = paddle.tile(r.unsqueeze(0), repeat_times=[board, 1])
    y = paddle.transpose(x, [1, 0])
    ch = paddle.stack([x, y], axis=0)
    return paddle.cast(paddle.round(ch * 255.0), "uint8")


class TorchLinear(nn.Layer):
    """`nn.Linear` 的 torch 布局等价物：weight 是 (out, in)，不是 Paddle 的 (in, out)。

    为什么不用 `nn.Linear`：权重 JSON 的 `params` 是 torch state_dict 的逐字节镜像
    （TS 运行时、hub 归档、torch worker 都按 (out, in) 解释）。Paddle 的 Linear 存
    (in, out) ⇒ 直接互换会得到「形状错/静默转置」两种坏结局之一。这里用
    `matmul(x, W, transpose_y=True)` 复现 torch 的前向，state_dict 键名与形状随之一致。
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        weight_attr: paddle.ParamAttr | None = None,
        bias_attr: paddle.ParamAttr | None = None,
    ):
        super().__init__()
        self.weight = self.create_parameter(
            [out_features, in_features],
            attr=weight_attr,
            default_initializer=nn.initializer.XavierUniform(),
        )
        self.bias = self.create_parameter(
            [out_features],
            is_bias=True,
            attr=bias_attr,
            default_initializer=nn.initializer.Constant(0.0),
        )

    def forward(self, x: paddle.Tensor) -> paddle.Tensor:
        return paddle.matmul(x, self.weight, transpose_y=True) + self.bias


class ConvMixerBlock(nn.Layer):
    def __init__(self, h: int):
        super().__init__()
        self.dw = nn.Conv2D(
            h,
            h,
            5,
            padding=2,
            groups=h,
            # fan_in = (in_channels / groups) * kh * kw = 1 * 25 (depthwise)
            weight_attr=_kaiming_uniform(25),
            bias_attr=_zero_bias(),
        )
        self.pw = nn.Conv2D(
            h,
            h,
            1,
            weight_attr=_kaiming_uniform(h),
            bias_attr=_zero_bias(),
        )

    def forward(self, x: paddle.Tensor) -> paddle.Tensor:
        y = paddle.nn.functional.relu(self.pw(paddle.nn.functional.relu(self.dw(x))))
        return x + y


class StudentNet(StudentArchMixin, nn.Layer):
    def __init__(
        self,
        in_ch: int = OBS_CHANNELS,
        board: int = BOARD,
        scalar_dim: int = SCALAR_DIM,
        h: int = DEFAULT_H,
        d: int = DEFAULT_D,
        head_hidden: int = DEFAULT_HEAD_HIDDEN,
        policy_extra: bool = False,
        spatial_tower: bool = False,
        action_v6: bool = False,
    ):
        super().__init__()
        self.in_ch = in_ch
        self.board = board
        self.scalar_dim = scalar_dim
        self.h = h
        self.d = d
        self.head_hidden = head_hidden
        self.policy_extra = bool(policy_extra)
        self.spatial_tower = bool(spatial_tower)
        self.action_v6 = bool(action_v6)
        self.move_dim = MOVE4_DIM if self.action_v6 else MOVE_DIM
        self.fire_dim = FIRE3_DIM if self.action_v6 else FIRE_DIM
        if self.spatial_tower and not self.policy_extra:
            raise ValueError("spatial_tower 需要 policy_extra=True")

        self.stem = nn.Conv2D(
            in_ch + 2,
            h,
            3,
            padding=1,
            weight_attr=_kaiming_uniform((in_ch + 2) * 3 * 3),
            bias_attr=_zero_bias(),
        )
        self.blocks = nn.LayerList([ConvMixerBlock(h) for _ in range(d)])
        self.fc = TorchLinear(
            h + scalar_dim,
            head_hidden,
            weight_attr=_kaiming_uniform(h + scalar_dim),
            bias_attr=_zero_bias(),
        )
        if self.spatial_tower:
            self.spatial_proj = nn.Conv2D(
                h,
                SPATIAL_TOWER_C,
                1,
                weight_attr=_kaiming_uniform(h),
                bias_attr=_zero_bias(),
            )
            self.spatial_fc = TorchLinear(
                SPATIAL_TOWER_FEAT,
                SPATIAL_TOWER_FC_OUT,
                weight_attr=_kaiming_uniform(SPATIAL_TOWER_FEAT),
                bias_attr=_zero_bias(),
            )
        head_in = self.policy_input_dim
        self.move_head = TorchLinear(
            head_in, self.move_dim, weight_attr=_kaiming_uniform(head_in), bias_attr=_zero_bias()
        )
        self.fire_head = TorchLinear(
            head_in, self.fire_dim, weight_attr=_kaiming_uniform(head_in), bias_attr=_zero_bias()
        )
        self.hold_head = (
            TorchLinear(
                head_in,
                HOLD_DIM,
                weight_attr=_kaiming_uniform(head_in),
                bias_attr=_zero_bias(),
            )
            if self.action_v6
            else None
        )

        # P1-10: fold the 1/255 input scale into the stem weights (weights-file
        # format stays unchanged; TS runtime does no division).
        with paddle.no_grad():
            self.stem.weight.set_value(self.stem.weight * (1.0 / 255.0))

    # 架构元数据面（`arch` / `policy_input_dim` / `value_anchor_slices`）在
    # `worker/models/arch_meta.py`：与 torch 双生共用同一份实现（本类经
    # `StudentArchMixin` 继承它）。

    def _spatial(self, obs: paddle.Tensor) -> paddle.Tensor:
        coords = coord_channels(self.board).astype("float32").unsqueeze(0)
        coords = paddle.expand(coords, [obs.shape[0], 2, self.board, self.board])
        x = paddle.concat([obs.astype("float32"), coords], axis=1)
        x = paddle.nn.functional.relu(self.stem(x))
        for b in self.blocks:
            x = b(x)
        return x

    def features(
        self,
        obs: paddle.Tensor,
        scalars: paddle.Tensor,
        buf: paddle.Tensor | None = None,
    ) -> paddle.Tensor:
        x = buf if buf is not None else self._spatial(obs)
        x = x.mean(axis=[2, 3])
        x = paddle.concat([x, scalars], axis=1)
        return paddle.nn.functional.relu(self.fc(x))

    def _require_extra(self, extra: paddle.Tensor | None) -> paddle.Tensor:
        if extra is None:
            raise ValueError(
                f"student: policy_extra=True 的模型必须喂 POLICY_EXTRA({POLICY_EXTRA_DIM})"
            )
        return extra.astype("float32")

    def policy_features(
        self,
        obs: paddle.Tensor,
        scalars: paddle.Tensor,
        extra: paddle.Tensor | None,
        hidden: paddle.Tensor | None = None,
        buf: paddle.Tensor | None = None,
    ) -> paddle.Tensor:
        if self.spatial_tower:
            b = buf if buf is not None else self._spatial(obs)
            z = paddle.nn.functional.relu(self.spatial_proj(b))
            p = paddle.nn.functional.adaptive_avg_pool2d(
                z, [SPATIAL_TOWER_POOL, SPATIAL_TOWER_POOL]
            ).flatten(1)
            p = self.spatial_fc(p)
            return paddle.concat([p, scalars, self._require_extra(extra)], axis=1)
        h = hidden if hidden is not None else self.features(obs, scalars, buf)
        if self.policy_extra:
            return paddle.concat([h, self._require_extra(extra)], axis=1)
        return h

    def forward(
        self,
        obs: paddle.Tensor,
        scalars: paddle.Tensor,
        extra: paddle.Tensor | None = None,
    ) -> tuple[paddle.Tensor, ...]:
        if self.spatial_tower:
            buf = self._spatial(obs)
            h = self.features(obs, scalars, buf)
            pf = self.policy_features(obs, scalars, extra, hidden=h, buf=buf)
        else:
            h = self.features(obs, scalars)
            pf = self.policy_features(obs, scalars, extra, hidden=h)
        if self.action_v6:
            assert self.hold_head is not None
            return self.move_head(pf), self.fire_head(pf), self.hold_head(pf)
        return self.move_head(pf), self.fire_head(pf)


class PPOStudent(StudentNet):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.value_head = TorchLinear(
            self.policy_input_dim,
            1,
            weight_attr=paddle.ParamAttr(
                initializer=paddle.nn.initializer.Normal(mean=0.0, std=0.01)
            ),
            bias_attr=_zero_bias(),
        )

    def forward(
        self,
        obs: paddle.Tensor,
        scalars: paddle.Tensor,
        extra: paddle.Tensor | None = None,
    ) -> tuple[paddle.Tensor, ...]:
        if self.spatial_tower:
            buf = self._spatial(obs)
            h = self.features(obs, scalars, buf)
            pf = self.policy_features(obs, scalars, extra, hidden=h, buf=buf)
        else:
            h = self.features(obs, scalars)
            pf = self.policy_features(obs, scalars, extra, hidden=h)
        if self.action_v6:
            assert self.hold_head is not None
            return (
                self.move_head(pf),
                self.fire_head(pf),
                self.hold_head(pf),
                self.value_head(pf),
            )
        return self.move_head(pf), self.fire_head(pf), self.value_head(pf)

    @paddle.no_grad()
    def predict(
        self,
        obs: paddle.Tensor,
        scalars: paddle.Tensor | None = None,
        extra: paddle.Tensor | None = None,
    ):
        self.eval()
        if scalars is None:
            scalars = paddle.zeros([obs.shape[0], self.scalar_dim])
        out = self.forward(obs, scalars, extra)
        return (
            paddle.nn.functional.softmax(out[0], axis=-1),
            paddle.nn.functional.softmax(out[1], axis=-1),
        )


def tensor_to_b64(t: paddle.Tensor) -> str:
    arr = t.numpy().astype("<f4")
    return base64.b64encode(arr.tobytes()).decode("ascii")


def save_weights_json(
    model: paddle.nn.Layer, path: str, extra_meta: dict[str, Any] | None = None
) -> None:
    params: dict[str, Any] = {}
    for name, p in model.state_dict().items():
        arr = p.numpy()
        if not np.isfinite(arr).all():
            raise ValueError(
                f"[weights] 参数 {name} 含非有限值（NaN/Inf）——拒绝写出坏权重；"
                "请检查训练是否发散（loss/gnorm 爆炸）"
            )
        params[name] = {"shape": list(p.shape), "data": tensor_to_b64(p)}
    meta = {
        "format": FORMAT,
        "version": 1,
        "schema_major": OBS_SCHEMA_MAJOR,
        "arch": getattr(model, "arch", lambda: {})(),
        "num_params": sum(int(p.numel()) for p in model.parameters()),
        "params": params,
    }
    if extra_meta:
        meta.update(extra_meta)
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp_path = abs_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp_path, abs_path)


def load_weights_json(
    path: str, allow_legacy_schema: bool = False
) -> tuple[dict[str, Any], dict[str, paddle.Tensor]]:
    with open(path, encoding="utf-8") as f:
        meta = json.load(f)
    validate_weights_meta(meta, path, allow_legacy_schema=allow_legacy_schema)
    params: dict[str, paddle.Tensor] = {}
    for k, v in meta["params"].items():
        raw = base64.b64decode(v["data"])
        arr = np.frombuffer(raw, dtype="<f4").reshape(v["shape"]).copy()
        params[k] = paddle.to_tensor(arr)
    return meta, params


def load_state_into(
    model: paddle.nn.Layer, path: str, allow_legacy_schema: bool = False
) -> list[str]:
    meta, params = load_weights_json(path, allow_legacy_schema=allow_legacy_schema)
    named = dict(model.named_parameters())
    expected = set(named.keys())
    matched = expected & set(params.keys())
    coverage = len(matched) / max(1, len(expected))
    if coverage < COVERAGE_RAISE:
        raise ValueError(
            f"[weights] {path}: 参数覆盖率 {coverage:.0%}（{len(matched)}/{len(expected)}）"
            f"—— 权重与模型族严重不匹配（arch={meta.get('arch')}），拒绝静默随机初始化"
        )
    not_loaded: list[str] = []
    with paddle.no_grad():
        for k, v in params.items():
            p = named.get(k)
            if p is None or list(p.shape) != list(v.shape):
                not_loaded.append(k)
                continue
            p.set_value(v)
    model.eval()
    return sorted(k for k in expected if k not in params) + sorted(not_loaded)


def build_ppo(weights_path: str | None) -> PPOStudent:
    """`worker.ppo.engine.build_ppo` 的 Paddle 双生（同一份实现在 `worker/models/arch_meta.py`）。

    转发名保留：调用点（`remote/train_core` 的 Paddle 核、`tools/paddle_parity.py`、测试）
    按这个名字与语义用它；arch 判据与 torch 侧逐字节同源 —— 那是混跑时两台机器建出
    同形状模型（因而能互相接手 checkpoint）的唯一保证。
    """
    return PPOStudent(**read_arch_kwargs(weights_path))
