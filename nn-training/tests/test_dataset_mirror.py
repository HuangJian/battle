"""mirrorX 数据增强回归（P0-2 起家，v3 布局重写：dsf A1/A2 + hy E6）。

**背景**：mirror_x 只翻转方向编码里 `d∈{2,3}` 的格子。历史事故两次：
  · v2 P0-2：玩家子弹编码 `d+1+4`（slot 5..7/0），旧实现恒不命中 ⇒ 玩家子弹
    方向从不翻转，~半数 BC 样本 obs 与 move 标签自相矛盾；
  · v3 布局（obs-schema-v3.plan.md v4.0）：敌车加 bonus<<6 位、子弹改混合基
    `(sb<<3)+(owner<<2)+(d+1)`——hy E6 指出旧重编码 `newd+1+4` 会把 speedBucket
    清零（50% 样本弹速档恒 0）；owner 判定必须 `rest>=5`（`rest>>2` 在 rest=4 误判）。

本文件把「mirror 是自洽的双射」变成可执行断言：
  1. 全通道 round-trip：`mirror_x(mirror_x(x)) == x`（方向、标量、标签逐位相等）；
  2. 显式方向表：8 方向 × 敌/我子弹 × 弹速档 × (bonus,tier) 全组合；
  3. speedBucket 翻转前后不变（hy E6）；
  4. 编码唯一性（v3.2 A1/A2）：(bonus,tier,d) 全组合与子弹 32 值无碰撞。
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
from schema import CH, DIRECTION_CHANNELS, SCALAR_DIM, SCALAR_X_INDICES

TANK_CHANNELS = sorted(DIRECTION_CHANNELS - {CH["bullet"]})
BULLET_CH = CH["bullet"]
N_CH = 16

# dirIdx 顺序（schema.DIR_INDEX）：up=0, down=1, left=2, right=3
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
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = BULLET_VAL[("enemy", sb, _d_flip(d))]
    assert int(out[BULLET_CH, 10, 15]) == want, f"敌弹 d={d} sb={sb} 翻转错误"


@pytest.mark.parametrize("sb", range(4))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_player_bullet_direction_flip(d: int, sb: int) -> None:
    """玩家子弹 (sb<<3)+4+(d+1)：sb 保留（hy E6——旧实现会清零弹速档）。"""
    obs = _mk_obs(BULLET_CH, BULLET_VAL[("player", sb, d)])
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = BULLET_VAL[("player", sb, _d_flip(d))]
    assert int(out[BULLET_CH, 10, 15]) == want, f"玩家子弹 d={d} sb={sb} 翻转错误"


@pytest.mark.parametrize("bonus", [0, 1])
@pytest.mark.parametrize("tier", range(5))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_enemy_tank_direction_flip_preserves_hi(bonus: int, tier: int, d: int) -> None:
    """敌车通道：left↔right 翻转且 bonus/tier 高位保留（A1 bit6 布局）。"""
    val = (bonus << 6) + (tier << 3) + (d + 1)
    obs = _mk_obs(CH["enemy_basic"], val)
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = (bonus << 6) + (tier << 3) + (_d_flip(d) + 1)
    assert int(out[CH["enemy_basic"], 10, 15]) == want


@pytest.mark.parametrize("star", range(4))
@pytest.mark.parametrize("d", [0, 1, 2, 3])
def test_self_direction_flip_preserves_star(star: int, d: int) -> None:
    """self 通道：left↔right 翻转且 star 高位保留。"""
    val = (star << 3) + (d + 1)
    obs = _mk_obs(CH["self"], val)
    out, _sc, _mv = mirror_x(obs, _mk_scalars(), 0)
    want = (star << 3) + (_d_flip(d) + 1)
    assert int(out[CH["self"], 10, 15]) == want


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
    """SCALAR_X_INDICES（15/18/29）分量翻转符号，非 x 分量不变。"""
    _o, out, _mv = mirror_x(_mk_obs(BULLET_CH, 1), _mk_scalars(), 0)
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
        mv = int(rng.integers(0, 5))
        o2, s2, mv2 = mirror_x(obs, sc, mv)
        o4, s4, mv4 = mirror_x(o2, s2, mv2)
        np.testing.assert_array_equal(o4, obs, err_msg="round-trip obs 不一致")
        np.testing.assert_array_equal(s4, sc, err_msg="round-trip scalars 不一致")
        assert mv4 == mv, f"round-trip move_label {mv} -> {mv4}"


def test_roundtrip_player_right_bullet_with_speed_is_exact() -> None:
    """玩家 right + 高速档（val = (3<<3)+4+4 = 32）——v2 时代盲区 + v3 新增 sb，单独锚定。"""
    obs = _mk_obs(BULLET_CH, 32)
    o2, s2, mv2 = mirror_x(obs, _mk_scalars(), 3)  # 标签 left(3)
    o4, s4, mv4 = mirror_x(o2, s2, mv2)
    assert int(o4[BULLET_CH, 10, 10]) == 32  # 双镜像后宽度翻回原位、sb 保留
    assert mv4 == 3
