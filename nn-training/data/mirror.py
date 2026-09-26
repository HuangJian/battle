"""data/mirror.py — mirrorX 的**纯 numpy** 实现（不 import torch）。

历史：这段逻辑原本住在 `data/dataset.py`，而 dataset.py 必须在模块顶层
`import torch` / `from torch.utils.data import Dataset`（`NNDataset`/`_AugWrapper`
要在类定义期继承 `Dataset`）。于是「只想验证镜像自洽」的用例（`test_dataset_mirror`、
`e2e/test_run_rl.py` 的 mirror 用例）被迫把整个 torch 拖进测试路径。

2026-09-26 抽取：镜像逻辑只要 numpy + schema 常量，与 DataLoader/Dataset 无关。
`data/dataset.py` 从这里再导出（`mirror_x` / LUT / `_flip_direction`），既有调用点
一行不改；test 侧改为直接 import 本模块 ⇒ 免 torch。

mirrorX (plan §NN-M1, nn2 N5 / nn3 N5) 是左右镜像，必须保持 (input, target) 自洽：
  * obs 网格按宽度轴翻转（[:, :, ::-1]）；
  * 方向编码通道（self / enemy-* / bullet）的 dirIdx 互换 left<->right
    （值 = (hi<<3) | (flippedDirIdx+1)）；
  * move **标签** left<->right 翻转；
  * scalar 相对方向 x 分量取反（SCALAR_X_INDICES）；
  * 其余 obs/scalars 不变（y、距离、地形类型……）。
只翻网格不翻标签（或反之）是明令禁止的——会产出自相矛盾的 (input, target)。
"""

from __future__ import annotations

import numpy as np

from schema import CH, DIRECTION_CHANNELS, SCALAR_X_INDICES

_MOVE_FLIP = np.array([0, 1, 2, 4, 3], dtype=np.int64)  # none,up,down,left<->right

# ---- v3 mirror LUT（dsf A2 / hy E6：显式查表，不依赖位运算巧合）----
# 翻转表按 **d+1 值空间**（1..4）索引：left(d=2)↔right(d=3) ⇒ 值 3↔4 互换，
# up/down（1/2）与其余槽位原样。与 _MOVE_FLIP（left=3↔right=4 标签空间）同构不同域，
# 勿混用。
_LOW3_FLIP = np.array([0, 1, 2, 4, 3, 5, 6, 7], dtype=np.int32)


def _build_enemy_lut() -> np.ndarray:
    """ch6(self)/ch7-10(敌车) v3 布局：val = (hi<<3) + (d+1)。

    hi = bonus<<3 | tier（敌，v3.2 A1 bit6 布局的 (col>>3) 分解）或 star（self）——
    高位翻转不变、低 3 位翻转。256 项全值域查表。
    """
    lut = np.arange(256, dtype=np.int32)
    low = lut & 7
    hi = lut >> 3
    flipped = (hi << 3) + _LOW3_FLIP[low]
    flipped[lut == 0] = 0
    return np.asarray(flipped, dtype=np.uint8)


def _build_bullet_lut() -> np.ndarray:
    """ch11(子弹) v3 混合基布局：val = (sb<<3) + (owner<<2) + (d+1)，合法域 1..32。

    解码注意：rest = owner*4 + (d+1) ∈ **1..8**，player-right 的 rest=8 会溢出
    bit3 ⇒ sb 必须按 `sb = (v-1) >> 3` 解（`v>>3` 在 rest=8 时错一档——hy E6
    伪码同错，本测试 test_player_bullet_direction_flip 先于实现抓到）。
    owner 判定 **rest >= 5**（`rest>>2` 在 rest=4——敌 right——会误判成玩家）；
    sb 与 owner 翻转不变，仅 d 翻转（speedBucket 翻转前后不变，单测断言）。
    域外值（0 / >32）原样返回。
    """
    lut = np.arange(256, dtype=np.int32)
    sb = (lut - 1) >> 3
    rest = lut - (sb << 3)
    owner = (rest >= 5).astype(np.int32)
    low = rest - (owner << 2)  # d+1 ∈ 1..4
    new_d1 = _LOW3_FLIP[low]
    val = (sb << 3) + (owner << 2) + new_d1
    out = np.where((lut >= 1) & (lut <= 32), val, lut)
    out[lut == 0] = 0
    return np.asarray(out, dtype=np.uint8)


ENEMY_MIRROR_LUT = _build_enemy_lut()
BULLET_MIRROR_LUT = _build_bullet_lut()


def _flip_direction(channel: np.ndarray, is_bullet: bool) -> np.ndarray:
    """左右翻转方向编码——v3 显式 LUT 查表（dsf A2 / hy E6）。

    布局（obs-schema-v3.plan.md v4.0 §3.2 定稿）：
      敌车 ch7-10 / self ch6：val = (hi<<3) + (d+1)；hi=bonus<<3|tier（敌）/ star(self）
        —— 高位保留、低 3 位方向翻转。
      子弹 ch11（**混合基**，全程加法）：val = (sb<<3) + (owner<<2) + (d+1)；
        sb/owner 翻转不变，仅 d 翻转。
      `mirror(mirror(v))==v` 与「speedBucket 翻转前后不变」由
      tests/test_dataset_mirror.py 全值域断言（敌机 (bonus,tier,d) 全组合、子弹 32 值）。

    历史（v2 修复记录，P0-2）：旧实现只翻 d∈{2,3}，玩家子弹（5..7/0 槽）从不翻转——
    修复改为 slot 解码 + 加法重编码；v3 升 LUT（敌机 hi 位侥幸保 bonus 属巧合，
    不依赖巧合）。
    """
    lut = BULLET_MIRROR_LUT if is_bullet else ENEMY_MIRROR_LUT
    return np.asarray(lut[channel], dtype=np.uint8)


def mirror_x(obs: np.ndarray, scalars: np.ndarray, move_label: int):
    """Return (obs', scalars', move_label') for a left-right reflection."""
    obs = obs.copy()
    obs = obs[:, :, ::-1].copy()  # flip width (copy -> positive strides for torch collate)
    for ch in DIRECTION_CHANNELS:
        obs[ch] = _flip_direction(obs[ch], is_bullet=(ch == CH["bullet"]))
    scalars = scalars.copy()
    for i in SCALAR_X_INDICES:
        scalars[i] = -scalars[i]
    return obs, scalars, int(_MOVE_FLIP[move_label])
