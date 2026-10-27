"""worker/models/arch_meta.py —— 模型族的**框架中立**共用面（2026-10-27，Paddle 双生）。

`worker/models/student.py`（torch）与 `worker/models/student_paddle.py`（paddle）是同一份架构的
两个实现。**权重 JSON 的 arch 元数据**（h / d / head_hidden / policyExtra / spatialTower /
actionV6）与**由它派生的头宽**必须逐字节同源：同一个 weights 文件喂两个框架必须建出同形状的
模型，否则 `tools/paddle_parity.py` 的键/形状对账当场红，而混跑时那台机器写出的 checkpoint
会静默换形。

**为什么单独成家**：`arch()` / `policy_input_dim` / `value_anchor_slices` 曾在这两份实现里
逐字节各存一份（复用账本 +3 组），`build_ppo` 与之同款（+1 组）。收敛的落点必须**两个框架都能
import** ⇒ 本模块只碰 `common.schema` 与 `worker.data.weights_meta`（免框架的那半），
不 import torch / paddle / numpy。两份实现各自保留**转发名**（名字是契约，位置不是）。

DAG 层号 **L1**（`tests/helpers/remote_dag.py`）：顶层只 import `worker.data.weights_meta`(L0)。
"""

from __future__ import annotations

import os
from typing import Any, TypeVar

from common.schema import POLICY_EXTRA_DIM
from worker.data.weights_meta import read_weights_meta

#: 架构默认档（h=64 / d=8 / head_hidden=128，plan/RL-Net-Selection.md §4.3 的甜点）。
DEFAULT_H = 64
DEFAULT_D = 8
DEFAULT_HEAD_HIDDEN = 128

# 空间塔冻结值（plan/policy-spatial-head.plan.md §3.3；TS 孪生 src/nn/spatial-tower.ts）。
SPATIAL_TOWER_C = 8
SPATIAL_TOWER_POOL = 4
SPATIAL_TOWER_FC_OUT = 112
SPATIAL_TOWER_FEAT = SPATIAL_TOWER_C * SPATIAL_TOWER_POOL * SPATIAL_TOWER_POOL  # 128

ModelT = TypeVar("ModelT")


def read_arch_kwargs(weights_path: str | None) -> dict[str, Any]:
    """权重文件的 `arch` 元数据 → `PPOStudent(...)` 的构造关键字（纯读，免框架）。

    `None` / 路径不在 ⇒ 全默认档（legacy 128）。**给了存在的路径就必须读得动**：坏文件在这里
    响亮失败，不静默退回默认架构 —— 那等于「建了错的模型却照跑」（`remote/worker.py` §3.3-2
    与 `engine.build_ppo` 的同一契约）。

    只读**清单**（`read_weights_meta`，不解码 base64 张量）：arch 判据用不上那几百 KB 的
    权重本体，而两侧的 `load_state_into` 本来就要整份解码一次（那是它的事）。
    """
    h = d = head_hidden = None
    policy_extra = spatial_tower = action_v6 = False
    if weights_path and os.path.exists(weights_path):
        meta = read_weights_meta(weights_path)
        a = meta.get("arch", {})
        h = a.get("h", DEFAULT_H)
        d = a.get("d", DEFAULT_D)
        head_hidden = a.get("head_hidden", DEFAULT_HEAD_HIDDEN)
        policy_extra = bool(a.get("policyExtra", False))
        spatial_tower = bool(a.get("spatialTower", False))
        action_v6 = bool(a.get("actionV6", False))
    return {
        "h": h or DEFAULT_H,
        "d": d or DEFAULT_D,
        "head_hidden": head_hidden or DEFAULT_HEAD_HIDDEN,
        "policy_extra": policy_extra,
        "spatial_tower": spatial_tower,
        "action_v6": action_v6,
    }


def build_ppo(model_cls: type[ModelT], weights_path: str | None) -> ModelT:
    """按权重文件的 arch 元数据构建 PPO 模型（**两个框架共用这一份实现**）。

    调用方是两个转发名：`worker.ppo.engine.build_ppo`（torch）与
    `worker.models.student_paddle.build_ppo`（paddle）——名字是契约，实现只有这里一份。
    """
    return model_cls(**read_arch_kwargs(weights_path))


class StudentArchMixin:
    """`StudentNet` 的**架构元数据面**：`arch()` / `policy_input_dim` / `value_anchor_slices`。

    三个成员只读 `self` 上的形状参数（子类 `__init__` 里赋值）⇒ 两个框架共用一份实现。
    这里只写**注解**（不赋值，运行时零影响）：mypy 看得见属性，`nn.Module` / `nn.Layer`
    子类不必各自再写一遍。
    """

    in_ch: int
    board: int
    scalar_dim: int
    h: int
    d: int
    head_hidden: int
    policy_extra: bool
    spatial_tower: bool
    action_v6: bool

    @property
    def policy_input_dim(self) -> int:
        """策略头输入 `pf` 的宽度（obs-v5 起 value 头也吃同一份）。

          · legacy（无 extra、无塔）= `head_hidden`（128）；
          · 腿 A（policy_extra）= `head_hidden + POLICY_EXTRA_DIM`（128+37=165）；
          · 腿 B（spatial_tower）= `SPATIAL_TOWER_FC_OUT + scalar_dim + POLICY_EXTRA_DIM`
            （112+40+37=189）。

        单一来源：`move_head`/`fire_head`（以及 obs-v5 的 `value_head`）全部由本属性派生，
        不许再在别处写 165/189 字面量（plan §2.6）。
        """
        if self.spatial_tower:
            return SPATIAL_TOWER_FC_OUT + self.scalar_dim + POLICY_EXTRA_DIM
        if self.policy_extra:
            return self.head_hidden + POLICY_EXTRA_DIM
        return self.head_hidden

    def value_anchor_slices(self) -> list[tuple[int, int]]:
        """value 头里 v5 新列的列区间（门控牵引只拴这些列）。

        x26-obs it90 诊断：冷 critic 吃 pf 后在新列上 35 倍爆炸（0.0086→0.30），
        advantage 污染→暖策略游走；旧列 drift 仅 0.015。
          · 腿 A：value 165 列里 critic 从没见过 extra ⇒ [128,165) 全拴
            （含迁移来的 extra9 旧列——对 critic 而言位置也是新的）；
          · 腿 B：整头相对旧 128 无前缀 ⇒ 全拴；
          · legacy：value 全是旧 hidden ⇒ 空（不碰）。
        区间一律由 arch 派生，不写字面量（与 `policy_input_dim` 同纪律）。
        """
        if self.spatial_tower:
            return [(0, self.policy_input_dim)]
        if self.policy_extra:
            return [(self.head_hidden, self.policy_input_dim)]
        return []

    def arch(self) -> dict:
        """权重 JSON 的 `arch` 元数据（写入方 `save_weights_json`，读回方 `read_arch_kwargs`）。"""
        d = {
            "kind": "student",
            "in_ch": self.in_ch,
            "board": self.board,
            "scalar_dim": self.scalar_dim,
            "h": self.h,
            "d": self.d,
            "head_hidden": self.head_hidden,
        }
        # v4：只在开启时写入（旧 arch 字典逐字节不变 ⇒ 旧权重/旧消费方零影响）。
        if self.policy_extra:
            d["policyExtra"] = True
        if self.spatial_tower:
            d["spatialTower"] = True
        if self.action_v6:
            d["actionV6"] = True
        return d
