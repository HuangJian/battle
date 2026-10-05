"""mirrorX 数据增强回归（P0-2 起家，v3 布局重写：dsf A1/A2 + hy E6；v4 扩 extra）。

**背景**：mirror_x 只翻转方向编码里 `d∈{2,3}` 的格子。历史事故两次：
  · v2 P0-2：玩家子弹编码 `d+1+4`（slot 5..7/0），旧实现恒不命中 ⇒ 玩家子弹
    方向从不翻转，~半数 BC 样本 obs 与 move 标签自相矛盾；
  · v3 布局（obs-schema-v3.plan.md v4.0）：敌车加 bonus<<6 位、子弹改混合基
    `(sb<<3)+(owner<<2)+(d+1)`——hy E6 指出旧重编码 `newd+1+4` 会把 speedBucket
    清零（50% 样本弹速档恒 0）；owner 判定必须 `rest>=5`（`rest>>2` 在 rest=4 误判）。

v4（plan/policy-spatial-head.plan.md §4-S0b 第 5 条）：POLICY_EXTRA(9) 一并镜像——
左右两对语义维**互换**（威胁计数 [2]↔[3]、命中距离 [6]↔[7]；前/后与包夹度不变）。

本文件把「mirror 是自洽的双射」变成可执行断言：
  1. 全通道 round-trip：`mirror_x(mirror_x(x)) == x`（方向、标量、extra、标签逐位相等）；
  2. 显式方向表：8 方向 × 敌/我子弹 × 弹速档 × (bonus,tier) 全组合；
  3. speedBucket 翻转前后不变（hy E6）；
  4. 编码唯一性（v3.2 A1/A2）：(bonus,tier,d) 全组合与子弹 32 值无碰撞；
  5. extra 9 维：8 向翻转 + 包夹度不变（v4）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.schema import (
    CH,
    DIRECTION_CHANNELS,
    EXTRA_MIRROR_SWAPS,
    POLICY_EXTRA_DIM,
    SCALAR_DIM,
    SCALAR_X_INDICES,
)
from worker.data.mirror import mirror_x  # 纯 numpy 模块：本文件不需要 torch

TANK_CHANNELS = sorted(DIRECTION_CHANNELS - {CH["bullet"]})
BULLET_CH = CH["bullet"]
N_CH = 16

# dirIdx 顺序（common.schema.DIR_INDEX）：up=0, down=1, left=2, right=3
# 子弹混合基：val = (sb<<3) + (owner<<2) + (d+1)；敌 owner=0、玩家 owner=1、sb=0。
BULLET_VAL = {
    ("enemy", sb, d): (sb << 3) + (0 << 2) + (d + 1) for sb in range(4) for d in range(4)
}
BULLET_VAL.update(
    {("player", sb, d): (sb << 3) + (1 << 2) + (d + 1) for sb in range(4) for d in range(4)}
)


def _mk_obs(ch: int, val: int) -> np.ndarray:
    obs = np.zeros((N_CH, 26, 26), dtype=np.uint8)
    obs[ch, 10, 10] = val
    return obs


def _mk_scalars() -> np.ndarray:
    sc = np.zeros(SCALAR_DIM, dtype=np.float32)
    sc[15], sc[18], sc[29] = 0.7, -0.3, 0.4  # SCALAR_X_INDICES（左右翻转符号）
    sc[0] = 0.5  # 非 x 分量不变
    return sc


def _d_flip(d: int) -> int:
    return 3 if d == 2 else (2 if d == 3 else d)


# ---- 1. 显式方向表 ----
@pytest.mark.parametrize("sb", range(4))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_enemy_bullet_direction_flip(d: int, sb: int) -> None:
    """敌弹 (sb<<3)+(d+1)：left(2)↔right(3)，up/down 与 sb 不变。"""
    obs = _mk_obs(BULLET_CH, BULLET_VAL[("enemy", sb, d)])
    out, _sc, _mv, _ex = mirror_x(obs, _mk_scalars(), 0)
    want = BULLET_VAL[("enemy", sb, _d_flip(d))]
    assert int(out[BULLET_CH, 10, 15]) == want, f"敌弹 d={d} sb={sb} 翻转错误"


@pytest.mark.parametrize("sb", range(4))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_player_bullet_direction_flip(d: int, sb: int) -> None:
    """玩家子弹 (sb<<3)+4+(d+1)：sb 保留（hy E6——旧实现会清零弹速档）。"""
    obs = _mk_obs(BULLET_CH, BULLET_VAL[("player", sb, d)])
    out, _sc, _mv, _ex = mirror_x(obs, _mk_scalars(), 0)
    want = BULLET_VAL[("player", sb, _d_flip(d))]
    assert int(out[BULLET_CH, 10, 15]) == want, f"玩家子弹 d={d} sb={sb} 翻转错误"


@pytest.mark.parametrize("bonus", [0, 1])
@pytest.mark.parametrize("tier", range(5))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_enemy_tank_direction_flip_preserves_hi(bonus: int, tier: int, d: int) -> None:
    """敌车通道：left↔right 翻转且 bonus/tier 高位保留（A1 bit6 布局）。"""
    val = (bonus << 6) + (tier << 3) + (d + 1)
    obs = _mk_obs(CH["enemy_basic"], val)
    out, _sc, _mv, _ex = mirror_x(obs, _mk_scalars(), 0)
    want = (bonus << 6) + (tier << 3) + (_d_flip(d) + 1)
    assert int(out[CH["enemy_basic"], 10, 15]) == want


@pytest.mark.parametrize("star", range(4))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_self_direction_flip_preserves_star(star: int, d: int) -> None:
    """self 通道：left↔right 翻转且 star 高位保留。"""
    val = (star << 3) + (d + 1)
    obs = _mk_obs(CH["self"], val)
    out, _sc, _mv, _ex = mirror_x(obs, _mk_scalars(), 0)
    want = (star << 3) + (_d_flip(d) + 1)
    assert int(out[CH["self"], 10, 15]) == want


def test_zero_cells_untouched() -> None:
    """零值格子保持零（无方向信息可翻）。"""
    obs = _mk_obs(BULLET_CH, 0)
    out, _sc, _mv, _ex = mirror_x(obs, _mk_scalars(), 0)
    assert int(out[BULLET_CH, 10, 15]) == 0


def test_move_label_flip() -> None:
    """move 标签（none,up,down,left,right = 0..4）：left(3)↔right(4)，其余不变。"""
    for mv, want in [(0, 0), (1, 1), (2, 2), (3, 4), (4, 3)]:
        _o, _s, out, _ex = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), mv)
        assert out == want, f"move_label {mv} -> {out}, 期望 {want}"


def test_scalar_x_flip_sign() -> None:
    """SCALAR_X_INDICES（15/18/29）分量翻转符号，非 x 分量不变。"""
    _o, out, _mv, _ex = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), 0)
    assert out[15] == -0.7 and out[18] == 0.3 and out[29] == -0.4
    assert out[0] == 0.5


# ---- 2. 编码唯一性（v3.2 A1/A2）----


def test_bullet_encoding_uniqueness() -> None:
    """32 个合法子弹值（4 sb × 2 owner × 4 d）两两不同（dsf A2：加法记法前提）。"""
    vals = [BULLET_VAL[(owner, sb, d)] for owner in ("enemy", "player") for sb in range(4) for d in range(4)]
    assert len(set(vals)) == 32
    assert min(vals) == 1 and max(vals) == 32


def test_enemy_encoding_uniqueness_full_combo() -> None:
    """(bonus, tier, d) 全组合通道值唯一（dsf A1：bit4 布局的碰撞不复现）。"""
    vals = {(b << 6) + (t << 3) + (d + 1): (b, t, d) for b in (0, 1) for t in range(5) for d in range(4)}
    assert len(vals) == 40  # 2×5×4 全组合无碰撞
    assert max(vals) == (1 << 6) + (4 << 3) + 4 == 100


# ---- 3. 全通道 round-trip：镜像的镜像 = 恒等 ----
def test_roundtrip_is_identity() -> None:
    """对随机合法编码的全通道观测：mirror(mirror(x)) == x 逐位相等。"""
    rng = np.random.default_rng(20260902)
    for _ in range(50):
        obs = np.zeros((N_CH, 26, 26), dtype=np.uint8)
        for ch in TANK_CHANNELS:
            b = int(rng.integers(0, 2))
            t = int(rng.integers(0, 5))
            d = int(rng.integers(0, 4))
            obs[ch, rng.integers(0, 26), rng.integers(0, 26)] = (b << 6) + (t << 3) + (d + 1)
        for owner, sb, d in [
            (o, s, d) for o in ("enemy", "player") for s in range(4) for d in range(4)
        ]:
            obs[BULLET_CH, rng.integers(0, 26), rng.integers(0, 26)] = BULLET_VAL[(owner, sb, d)]
        sc = rng.standard_normal(SCALAR_DIM).astype(np.float32)
        ex = rng.standard_normal(POLICY_EXTRA_DIM).astype(np.float32)
        mv = int(rng.integers(0, 5))
        o2, s2, mv2, e2 = mirror_x(obs, sc, mv, ex)
        o4, s4, mv4, e4 = mirror_x(o2, s2, mv2, e2)
        np.testing.assert_array_equal(o4, obs, err_msg="round-trip obs 不一致")
        np.testing.assert_array_equal(s4, sc, err_msg="round-trip scalars 不一致")
        np.testing.assert_array_equal(e4, ex, err_msg="round-trip extra 不一致")
        assert mv4 == mv, f"round-trip move_label {mv} -> {mv4}"


def test_roundtrip_player_right_bullet_with_speed_is_exact() -> None:
    """玩家 right + 高速档（val = (3<<3)+4+4 = 32）——v2 时代盲区 + v3 新增 sb，单独锚定。"""
    obs = _mk_obs(BULLET_CH, 32)
    o2, s2, mv2, e2 = mirror_x(obs, _mk_scalars(), 3, np.zeros(POLICY_EXTRA_DIM))  # 标签 left(3)
    o4, s4, mv4, e4 = mirror_x(o2, s2, mv2, e2)
    assert int(o4[BULLET_CH, 10, 10]) == 32  # 双镜像后宽度翻回原位、sb 保留
    assert mv4 == 3


# ---- 5. POLICY_EXTRA(9) 镜像（v4，plan §4-S0b 第 5 条）----


def test_extra_mirror_swaps_left_right_pairs() -> None:
    """8 向翻转：两对左右语义维互换（[2]↔[3]、[6]↔[7]），其余维逐值不变。"""
    ex = np.arange(1, POLICY_EXTRA_DIM + 1, dtype=np.float32)  # 1..9，全维唯一
    _o, _s, _mv, out = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), 0, ex)
    want = ex.copy()
    for a, b in EXTRA_MIRROR_SWAPS:
        want[a], want[b] = ex[b], ex[a]
    np.testing.assert_array_equal(out, want)
    # 前/后（0/1）与包夹度（8）不变
    assert out[0] == ex[0] and out[1] == ex[1] and out[8] == ex[8]


def test_extra_mirror_pincer_invariance() -> None:
    """包夹度维（min(左,右)）左右互换后不变（对称量）；左右两维按互换语义取值。"""
    ex = np.zeros(POLICY_EXTRA_DIM, dtype=np.float32)
    ex[2], ex[3], ex[8] = 3, 2, 2  # 左 3 / 右 2 / 包夹 2/4
    _o, _s, _mv, out = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), 0, ex)
    assert out[2] == ex[3] and out[3] == ex[2]  # 左右互换
    assert out[8] == ex[8]  # 包夹度不变（min 对称）


def test_extra_none_returns_none() -> None:
    """extra=None（旧调用/无 extra 语料）⇒ 第 4 返回同为 None（旧调用方零破坏）。"""
    _o, _s, _mv, out = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), 0)
    assert out is None
