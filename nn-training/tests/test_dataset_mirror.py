"""mirrorX 数据增强回归（plan/python-refactor.md P0-2）。

**背景**：`data/dataset.py` 的 `mirror_x` 只翻转方向编码里 `d∈{2,3}` 的格子。
TS 侧 `src/nn/obs-encoder.ts:231` 把玩家子弹编码为 `d+1+4`（低 3 位 5..7，
right=8 → slot=0），旧实现恒不命中 → **玩家子弹方向从不翻转**，镜像后 obs 与
move 标签自相矛盾，而 `--mirror-p 0.5` 意味着约半数 BC 训练样本被污染。

本文件把「mirror 是自洽的双射」变成可执行断言：
  1. 全通道 round-trip：`mirror_x(mirror_x(x)) == x`（方向、标量、标签逐位相等）；
  2. 显式方向表：8 方向 × 敌/我子弹 × 坦克 star，逐一断言翻转结果与 hi 保留。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.dataset import mirror_x
from schema import CH, DIRECTION_CHANNELS

TANK_CHANNELS = sorted(DIRECTION_CHANNELS - {CH["bullet"]})
BULLET_CH = CH["bullet"]

# dirIdx 顺序（schema.DIR_INDEX）：up=0, down=1, left=2, right=3
# 坦克/敌车：val = (hi<<3)|(d+1)；敌弹：d+1；玩家子弹：d+1+4
TANK_ENC = {0: 1, 1: 2, 2: 3, 3: 4}  # d -> low3
ENEMY_BULLET_ENC = {0: 1, 1: 2, 2: 3, 3: 4}
PLAYER_BULLET_ENC = {0: 5, 1: 6, 2: 7, 3: 8}


def _mk_obs(ch: int, val: int) -> np.ndarray:
    obs = np.zeros((14, 26, 26), dtype=np.uint8)
    obs[ch, 10, 10] = val
    return obs


def _mk_scalars() -> np.ndarray:
    sc = np.zeros(19, dtype=np.float32)
    sc[15], sc[18] = 0.7, -0.3  # SCALAR_X_INDICES（左右翻转符号）
    sc[0] = 0.5  # 非 x 分量不变
    return sc


# ---- 1. 显式方向表：8 方向 × 敌我子弹 × 坦克 hi ----
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_enemy_bullet_direction_flip(d: int) -> None:
    """敌弹 d+1：left(2)↔right(3)，up/down 不变。"""
    val = ENEMY_BULLET_ENC[d]
    obs = _mk_obs(BULLET_CH, val)
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = ENEMY_BULLET_ENC[3 if d == 2 else (2 if d == 3 else d)]
    assert int(out[BULLET_CH, 10, 15]) == want, f"敌弹 d={d} 翻转错误"


@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_player_bullet_direction_flip(d: int) -> None:
    """玩家子弹 d+1+4：left(7)↔right(8)，up(5)/down(6) 不变。

    修复前的旧实现：玩家子弹低 3 位 ∈ {5,6,7,0}，d=(col&7)-1 ∈ {4,5,6,-1}
    恒 ≠ {2,3} → 从不翻转（right=8 还被原样保留）。本测试即 P0-2 的回归锚点。
    """
    val = PLAYER_BULLET_ENC[d]
    obs = _mk_obs(BULLET_CH, val)
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = PLAYER_BULLET_ENC[3 if d == 2 else (2 if d == 3 else d)]
    assert int(out[BULLET_CH, 10, 15]) == want, f"玩家子弹 d={d} 翻转错误"


@pytest.mark.parametrize("hi", [0, 1, 2, 3])
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_tank_direction_flip_preserves_star(hi: int, d: int) -> None:
    """坦克通道：left↔right 翻转且 hi(star/tier) 位保留。"""
    ch = TANK_CHANNELS[0]
    val = (hi << 3) | TANK_ENC[d]
    obs = _mk_obs(ch, val)
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = (hi << 3) | TANK_ENC[3 if d == 2 else (2 if d == 3 else d)]
    assert int(out[ch, 10, 15]) == want, f"坦克 d={d} hi={hi} 翻转错误"


def test_zero_cells_untouched() -> None:
    """零值格子保持零（无方向信息可翻）。"""
    obs = _mk_obs(BULLET_CH, 0)
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    assert int(out[BULLET_CH, 10, 15]) == 0


def test_move_label_flip() -> None:
    """move 标签（none,up,down,left,right = 0..4）：left(3)↔right(4)，其余不变。"""
    for mv, want in [(0, 0), (1, 1), (2, 2), (3, 4), (4, 3)]:
        _o, _s, out = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), mv)
        assert out == want, f"move_label {mv} -> {out}, 期望 {want}"


def test_scalar_x_flip_sign() -> None:
    """SCALAR_X_INDICES 分量翻转符号，非 x 分量不变。"""
    _o, out, _mv = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), 0)
    assert out[15] == -0.7 and out[18] == 0.3
    assert out[0] == 0.5


# ---- 2. 全通道 round-trip：镜像的镜像 = 恒等 ----
def test_roundtrip_is_identity() -> None:
    """对随机合法编码的全通道观测：mirror(mirror(x)) == x 逐位相等。

    覆盖旧实现漏掉的玩家子弹（right=8 → slot=0）路径：若翻转不对，
    两次镜像后方向位不可能回到原值。
    """
    rng = np.random.default_rng(20260902)
    for _ in range(50):
        obs = np.zeros((14, 26, 26), dtype=np.uint8)
        for ch in TANK_CHANNELS:
            hi = rng.integers(0, 4)
            d = rng.integers(0, 4)
            obs[ch, rng.integers(0, 26), rng.integers(0, 26)] = (hi << 3) | (d + 1)
        # 敌弹 1-4 与玩家子弹 5-8 都撒一些（含 right=8）
        for v in [1, 2, 3, 4, 5, 6, 7, 8]:
            obs[BULLET_CH, rng.integers(0, 26), rng.integers(0, 26)] = v
        sc = rng.standard_normal(19).astype(np.float32)
        mv = int(rng.integers(0, 5))
        o2, s2, mv2 = mirror_x(obs, sc, mv)
        o4, s4, mv4 = mirror_x(o2, s2, mv2)
        np.testing.assert_array_equal(o4, obs, err_msg="round-trip obs 不一致")
        np.testing.assert_array_equal(s4, sc, err_msg="round-trip scalars 不一致")
        assert mv4 == mv, f"round-trip move_label {mv} -> {mv4}"


def test_roundtrip_player_bullet_right_is_exact() -> None:
    """玩家 right 子弹（val=8，slot=0）——旧实现的盲区，单独锚定。"""
    obs = _mk_obs(BULLET_CH, 8)
    o2, s2, mv2 = mirror_x(obs, _mk_scalars(), 3)  # 标签 left(3)
    o4, s4, mv4 = mirror_x(o2, s2, mv2)
    assert int(o4[BULLET_CH, 10, 10]) == 8  # 双镜像后宽度翻回原位
    assert mv4 == 3
