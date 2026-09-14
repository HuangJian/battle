"""I3（roadmap §4-I3）阶梯工厂单测：参数钉死 + 产物不变量 + preflight。

钉死项（总纲 §1 决策宪法 / §4-I3）：
  · D1/D2 tier 划分与命名（cN ⇔ count=N）
  · D3 空场几何（forces 恒 20、四角出生点、无基地）
  · D7 终局标准（max_ticks 线性规则）
  · D9 modern 掉落（无 gates 块）
  · hy R5 剂量公式（wChip = K/承伤基数；c01-c03 无 wChip 项）
  · hy E3 rollout_games（c01-c03 = 600）
  · hy X1 腿矩阵（c06/c07 = 3 腿）
  · N3 wDmg 全阶梯移除
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl import ladder_factory as lf

# ---- 纯函数：tier / max_ticks / 剂量 ----


@pytest.mark.parametrize(
    ("count", "lives"),
    [(1, 1), (7, 1), (8, 2), (14, 2), (15, 3), (20, 3)],
)
def test_tier_lives(count: int, lives: int) -> None:
    assert lf.tier_lives(count) == lives


def test_tier_lives_rejects_out_of_ladder() -> None:
    with pytest.raises(ValueError):
        lf.tier_lives(0)
    with pytest.raises(ValueError):
        lf.tier_lives(21)


def test_max_ticks_fixed_overhead_plus_per_enemy() -> None:
    """D7 立案规则 `600×count + 900`（DECISIONS §2026-09-13-goalnn-max-ticks-rule）。

    斜率 600 沿用 roadmap 原式；固定项 900 修原式在低 count 端的塌缩——原式 c01=600
    实测截断教师 61/200 局（0 击杀），低 count 端不可用。
    """
    assert lf.max_ticks_for(1) == 1500
    assert lf.max_ticks_for(2) == 2100
    assert lf.max_ticks_for(4) == 3300
    assert lf.max_ticks_for(5) == 3900
    assert lf.max_ticks_for(20) == 12900  # ≥ 原式 12000（上界不缩）
    # 相对原式：c01-c03 抬升（修截断），c04+ 同斜率平移
    assert lf.max_ticks_for(1) > 600
    assert all(lf.max_ticks_for(c) - lf.max_ticks_for(c - 1) == 600 for c in range(2, 21))


def test_damage_base_fits_measured_anchors() -> None:
    """线性拟合必须复现实测锚点：c4=150（精确）、c6=218（218 = 150+2×34）。"""
    assert lf.damage_base(4) == 150.0
    assert lf.damage_base(6) == 218.0


def test_wchip_dose_reproduces_validated_conversion() -> None:
    """★ 剂量公式自证：c04 = 0.03（chip03 已验证剂量）；c06 ≈ 0.0206 ≈ 跨关换算
    表预言的 0.02（0.03×150/218）——公式不是新假设，是已验证结论的参数化。"""
    assert lf.wchip_for(4) == 0.03
    assert lf.wchip_for(6) == pytest.approx(0.0206, abs=1e-4)


def test_wchip_absent_for_tiny_levels() -> None:
    """c01-c03 承伤基数极小 ⇒ 不上 wChip（R5）：公式项不出现、params 无键。"""
    assert lf.wchip_for(3) is None
    assert "wChip" not in lf.params_for(3)
    assert "wChip" not in lf.formula_for(3)
    assert "wChip" in lf.formula_for(4)


def test_wdmg_removed_everywhere() -> None:
    """N3：wDmg 全阶梯移除（致死命中归 terminal；星盾命中待事件子类拆分）。"""
    for c in range(1, lf.LEVEL_COUNT + 1):
        assert "wDmg" not in lf.formula_for(c)
        assert "wDmg" not in lf.params_for(c)


def test_forces_len_pinned() -> None:
    """ms F4：forces 长恒 20（循环语义 i%len），源文件违规即 raise。"""
    arena = lf.load_arena_source()
    assert len(arena["forces"]) == 20
    bad = tmp_jsonc({"stages": [{**arena_stage(), "forces": "ab"}]})
    with pytest.raises(ValueError, match="forces 长恒"):
        lf.load_arena_source(bad)


def arena_stage() -> dict:
    return {
        "name": "x",
        "grid": [[6] * 13 for _ in range(13)],
        "forces": "a" * 20,
        "count": 4,
        "player_spawn": {"col": 12, "row": 12},
        "enemy_spawns": [{"col": 2, "row": 2}],
    }


def tmp_jsonc(doc: dict) -> Path:
    import json
    import tempfile

    f = Path(tempfile.mkstemp(suffix=".jsonc")[1])
    f.write_text(json.dumps(doc), encoding="utf-8")
    return f


# ---- 产物不变量 ----


@pytest.fixture
def generated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """生成到 tmp（levels/ 也指到 tmp），并让 resolve_level 能找到（preflight 用）。"""
    levels, courses = tmp_path / "levels", tmp_path / "curricula"
    lf.generate(out_levels=levels, out_courses=courses, plan_path=tmp_path / "ladder" / "plan.jsonc")
    monkeypatch.setattr("rl.config.LEVELS_DIR", levels)
    return courses


def test_generated_course_invariants(generated: Path) -> None:
    from rl.config import load_course

    for c in (1, 4, 7, 8, 14, 15, 20):
        name = lf.level_name(c)
        course = load_course(generated / f"{name}.jsonc")
        assert course.name == name
        assert course.level == name  # level 引用式（环境键归关卡文件）
        assert course.stages[0].count == c  # D2：cN ⇔ count=N
        assert course.player.lives == lf.tier_lives(c)
        assert course.max_ticks == lf.max_ticks_for(c)  # D7
        assert course.seed_rotate == lf.seed_rotate_for(c)  # E3
        assert course.gates is None  # I2：阶梯课程一律不配 gates
        assert "wDmg" not in course.reward.formula  # N3
        assert isinstance(course.stages, list)  # level 引用合并后必为列表
    # c04+ 单关沿用（B 案零改动）：arena forces 原样
    for c in (4, 7, 20):
        course = load_course(generated / f"{lf.level_name(c)}.jsonc")
        assert isinstance(course.stages, list)
        assert len(course.stages) == 1
        assert course.stages[0].forces == "abcdabcdabcdabcdabcd"  # 几何常量（F4 长 20）


def test_early_levels_multi_variant(generated: Path) -> None:
    """B 案：c01-c03 = C(4,1/2/3) = 4/6/4 关（xN 试点语义入厂）；
    c01 单出生点降噪保持（spawn_points_for 原样）。"""
    from rl.config import load_course

    for c, n in ((1, 4), (2, 6), (3, 4)):
        course = load_course(generated / f"{lf.level_name(c)}.jsonc")
        assert isinstance(course.stages, list)
        assert len(course.stages) == n
        assert all(s.count == c for s in course.stages)
        assert all(len(s.forces) == 20 for s in course.stages)
        combos = [s.forces[:c] for s in course.stages]
        assert sorted(combos) == sorted(lf.type_combos(c))
    assert lf.eval_stages_for(1) == "2000-2003"
    assert lf.eval_stages_for(2) == "2000-2005"
    assert lf.eval_stages_for(3) == "2000-2003"
    assert lf.eval_stages_for(4) == "2000-2000"  # 单关沿用


def test_type_combos_cover_all() -> None:
    """C(4,1/2/3) 全覆盖；count≥4 拒绝（单关沿用，不归本函数管）。"""
    assert lf.type_combos(1) == ["a", "b", "c", "d"]
    assert lf.type_combos(2) == ["ab", "ac", "ad", "bc", "bd", "cd"]
    assert lf.type_combos(3) == ["abc", "abd", "acd", "bcd"]
    with pytest.raises(ValueError):
        lf.type_combos(4)
    assert lf.forces_for_combo("ab") == "ab" * 10
    assert lf.forces_for_combo("abc") == "abcabcabcabcabcabcab"
    assert all(len(lf.forces_for_combo(t)) == 20 for t in lf.type_combos(3))


def test_early_reward_is_clean_and_legacy_untouched() -> None:
    """B 案奖励分叉：c01-c03 与 x2/x3-start 逐字同构；c04+ v2 词干逐字不动."""
    assert lf.formula_for(2) == "wKill*kills + wHit*enemyHits + wWin*where(clearTick>=0, 1, 0)"
    assert lf.params_for(2) == {"wKill": 3.0, "wHit": 0.3, "wWin": 2.0}
    assert lf.terminal_for(2) == {"lives_exhausted": -1.0}
    assert lf.terminal_for(1) == {"lives_exhausted": -1.0}
    for banned in ("wTick", "wPickup", "wStuck", "wShot", "wChip", "wDmg"):
        assert banned not in lf.formula_for(3)
        assert banned not in lf.params_for(3)
    # c04+ 沿用（回归锁：B 案不得漂移这 17 级）
    assert "wTick" in lf.formula_for(4) and "wChip" in lf.formula_for(4)
    assert lf.params_for(4)["wShot"] == 0.01
    assert lf.terminal_for(4) == {"stage_clear": 2.0, "lives_exhausted": -1.0, "timeout": -2.0}


def test_ladder_matches_xn_pilot() -> None:
    """B 案核心契约：ladder-c02/c03 关卡与 xN 试点（arena2/arena3）逐关同形。
    （工厂产物纯 JSON 无注释，探针表头仍住 xN 文件；语义等价由本测试钉死。）"""
    from rl.jsonc import load as _load_jsonc

    repo_levels = Path(__file__).resolve().parent.parent / "levels"
    pairs = [("ladder-c02", "arena2"), ("ladder-c03", "arena3")]
    for fac, pilot in pairs:
        f_stages = _load_jsonc(str(repo_levels / f"{fac}.jsonc"))["stages"]
        p_stages = _load_jsonc(str(repo_levels / f"{pilot}.jsonc"))["stages"]
        assert len(f_stages) == len(p_stages) == (6 if fac == "ladder-c02" else 4)
        for fs, ps in zip(f_stages, p_stages, strict=True):
            assert fs["forces"] == ps["forces"] and fs["count"] == ps["count"]
            assert fs["player_spawn"] == ps["player_spawn"]
            assert fs["enemy_spawns"] == ps["enemy_spawns"]
            assert fs["grid"] == ps["grid"]


def test_early_seed_rotate_stays_600() -> None:
    """seed_rotate 保持 600（roadmap 合规；xN 试点的 240 本次不吸收，
    争议见 x3-power.jsonc 批量附录——改这个数另立项）。"""
    assert lf.seed_rotate_for(1) == 600
    assert lf.seed_rotate_for(2) == 600
    assert lf.seed_rotate_for(3) == 600


def test_level_file_geometry_is_constant_empty_arena(generated: Path) -> None:
    """D3：所有级共用空场几何——grid 仅边界钢环、四角出生点、中央 player_spawn。"""
    import json

    lvl20 = json.loads(
        (generated.parent / "levels" / "ladder-c20.jsonc").read_text(encoding="utf-8")
    )
    grid = lvl20["stages"][0]["grid"]
    interior = {v for row in grid[1:-1] for v in row[1:-1]}
    assert interior == {0}  # 内部全空（无掩体）
    assert grid[0] == [6] * 13 and grid[-1] == [6] * 13  # 边界钢环
    assert len(lvl20["stages"][0]["enemy_spawns"]) == 4  # 四角（ms F1）


def test_legs_matrix(tmp_path: Path) -> None:
    """hy X1：攻坚级 c06/c07 = 3 腿（含 BC 重起），其余 2 腿；c01-c04 首腿 = bc。"""
    assert lf.legs_for(6) == {"count": 3, "kinds": ["warm", "bc-restart", "hypothesis"]}
    assert lf.legs_for(7)["count"] == 3
    assert lf.legs_for(2)["kinds"][0] == "bc"  # D5：c01-c04 BC 优先写死
    assert lf.legs_for(10) == {"count": 2, "kinds": ["warm", "hypothesis"]}


def test_plan_doc_invariants(tmp_path: Path) -> None:
    import json

    lf.generate(out_levels=tmp_path / "lv", out_courses=tmp_path / "cu", plan_path=tmp_path / "p.jsonc")
    plan = json.loads((tmp_path / "p.jsonc").read_text(encoding="utf-8"))
    assert plan["gates"] is None  # I2
    assert "modern" in plan["drop_profile"]  # D9
    assert len(plan["levels"]) == 20
    by_name = {lv["level"]: lv for lv in plan["levels"]}
    assert by_name["ladder-c05"]["dose"]["recalibrate_at"] is not None  # R5 重标定点
    assert by_name["ladder-c20"]["max_ticks"] == 12900
    assert by_name["ladder-c01"]["eval_seed_batches"]["grad_round2"] == "200-399"  # 双轮门种子不重叠


def test_generated_files_are_lf(tmp_path: Path) -> None:
    """产物一律 LF 换行（`write_jsonc`）。Windows 文本模式 w/r 会把 \\n 翻成 \\r\\n，
    产物与仓库 LF 惯例不符：每次重生成抖出整文件 diff + CRLF 警告（2026-09-13 实测）。"""
    lf.generate(
        out_levels=tmp_path / "lv", out_courses=tmp_path / "cu", plan_path=tmp_path / "p.jsonc"
    )
    for rel in ("lv/ladder-c01.jsonc", "cu/ladder-c05.jsonc", "p.jsonc"):
        assert b"\r\n" not in (tmp_path / rel).read_bytes(), rel


def test_preflight_green_on_generated(generated: Path, tmp_path: Path) -> None:
    """★ CI 预检：load_course + validate_reward 全绿（20 级，零错误）。"""
    errors = lf.preflight(generated, generated)
    assert errors == []
