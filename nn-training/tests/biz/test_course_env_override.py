"""课程侧环境覆盖（docs/nn/training-stack.md §49）：挑关 / 敌数 / 命 / 星 / max_ticks。

守的是 2026-10-28 之前那条硬规则的反面：**引用 level 后仍允许这五个键**，
但它们必须被 `load_course` **消费**（写进 stages/player/max_ticks），不能在课程里
留下第二份事实；同时 `stages` / `difficulty` / `player` 三个仍必须拒收。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from biz.corpus_fp import corpus_identity_fp
from biz.course_resolve import load_course
from biz.course_spec import CourseConfig

BASE = {
    "name": "t-env",
    "mode": "per-tick",
    "level": "classic35",
    "reward": {
        "formula": "wKill*kills",
        "params": {"wKill": 3.0},
        "terminal": {"timeout": -35.0, "lives_exhausted": -35.0,
                     "base_destroyed": -35.0, "stage_clear": 0.0},
        "scheme": "toy",
    },
}


def _course(tmp_path: Path, **over) -> str:
    doc = dict(BASE)
    doc.update(over)
    p = tmp_path / f"{doc.get('name', 't')}.jsonc"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(p)


def _stage(c: CourseConfig, sid: int) -> dict:
    """stage_id → 下发的 stageJson（dict）；缺失/超限时测试直接失败。"""
    raw = c.stage_json(sid)
    assert raw is not None
    data = json.loads(raw)
    assert isinstance(data, dict)
    return data


def test_level_defaults_untouched(tmp_path: Path) -> None:
    """不带覆盖 ⇒ 与关卡文件逐字段一致（老行为零变化）。"""
    c = load_course(_course(tmp_path))
    assert len(c.stage_ids) == 35
    assert c.max_ticks == 36000
    assert c.player.lives == 3 and c.player.level == 0
    assert c.stage_select == "" and c.enemies is None


def test_stage_select_single_keeps_identity(tmp_path: Path) -> None:
    """挑单关：名字对得上、id 从 2000 重新编号（不是 classic35 里的 2022）。"""
    c = load_course(_course(tmp_path, stage_select="23"))
    assert c.stage_ids == [2000]
    assert _stage(c, 2000)["name"] == "Ramparts"


def test_stage_select_multi_preserves_order(tmp_path: Path) -> None:
    """多关：**顺序即新关序**（写 23,9,18,10 就按这个顺序发 id）。"""
    c = load_course(_course(tmp_path, stage_select="23,9,18,10"))
    names = [_stage(c, i)["name"] for i in c.stage_ids]
    assert names == ["Ramparts", "Twin Towers", "Gridlock", "Gauntlet"]
    assert c.stage_ids == [2000, 2001, 2002, 2003]


def test_stage_select_range(tmp_path: Path) -> None:
    c = load_course(_course(tmp_path, stage_select="1-3"))
    assert len(c.stage_ids) == 3
    assert _stage(c, 2000)["name"] == "Outpost"


def test_stage_select_out_of_range_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="越界"):
        load_course(_course(tmp_path, stage_select="36"))


def test_enemies_override_writes_count(tmp_path: Path) -> None:
    """敌数覆盖写回每个 stage 的 `count`（forces 串不动，长度仍是 20）。"""
    c = load_course(_course(tmp_path, stage_select="23,9", enemies=6))
    for sid in c.stage_ids:
        st = _stage(c, sid)
        assert st["count"] == 6
        assert len(st["forces"]) == 20


def test_enemies_over_forces_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="超过"):
        load_course(_course(tmp_path, enemies=21))


def test_lives_and_star_override_player(tmp_path: Path) -> None:
    """命/星写进 player 块 ⇒ `flat_overrides()` 的 lives_override / player_level 跟着走。"""
    c = load_course(_course(tmp_path, lives=1, star=1))
    assert c.player.lives == 1 and c.player.level == 1
    ov = c.flat_overrides()
    assert ov["lives_override"] == 1 and ov["player_level"] == 1


def test_max_ticks_override_wins(tmp_path: Path) -> None:
    c = load_course(_course(tmp_path, max_ticks=12000))
    assert c.max_ticks == 12000


def test_stages_still_forbidden_with_level(tmp_path: Path) -> None:
    """`stages` 仍然拒收（那是另一份事实）；错误信息要指向正确的键。"""
    with pytest.raises(ValueError, match="stage_select"):
        load_course(_course(tmp_path, stages="2000-2003"))


def test_player_block_still_forbidden(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="player"):
        load_course(_course(tmp_path, player={"lives": 1}))


def test_overrides_without_level_raise(tmp_path: Path) -> None:
    """没有 level 却写覆盖键 ⇒ 响亮报错（它们是「从关卡取环境」的修饰语，不是独立事实）。"""
    doc: dict[str, object] = dict(BASE)
    doc.pop("level")
    doc["stages"] = [{"name": "x", "grid": [[0] * 13] * 13}]
    doc["stage_select"] = "1"
    p = tmp_path / "no-level.jsonc"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="没有 `level`"):
        load_course(str(p))


def test_stage_select_accepts_bare_number(tmp_path: Path) -> None:
    """JSON 里写数值（`"stage_select": 23`）也认——解析前就 `str()` 归一。"""
    c = load_course(_course(tmp_path, stage_select=23))
    assert c.stage_ids == [2000]
    assert _stage(c, 2000)["name"] == "Ramparts"


def test_stage_select_on_range_string_level_raises(tmp_path: Path) -> None:
    """关卡的 stages 是范围串（不是内联关列表）⇒ 错误信息要说清「只能挑 list 型」。"""
    lvl = tmp_path / "range-level.jsonc"
    lvl.write_text(
        json.dumps({"stages": "0-1", "difficulty": "hard", "player": {"lives": 3}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="内联关列表"):
        load_course(_course(tmp_path, level=str(lvl), stage_select="1"))


@pytest.mark.parametrize(
    ("over", "match"),
    [
        ({"lives": 0}, "lives=0"),
        ({"star": -1}, "star=-1"),
        ({"enemies": 0}, "enemies=0"),
        ({"enemies": "many"}, "enemies='many'"),
    ],
)
def test_dirty_override_values_rejected(tmp_path: Path, over: dict, match: str) -> None:
    """覆盖键在模型构造前就被消费 ⇒ `Field(ge=...)` 拦不住，必须自己响亮拒（`lives: 0` = 0 命）。"""
    with pytest.raises(ValueError, match=match):
        load_course(_course(tmp_path, **over))


def test_override_keys_do_not_leak_into_flat_overrides(tmp_path: Path) -> None:
    """覆盖键没有 argparse dest ⇒ 不得漏进 flat_overrides（漏了就是 setattr 一个无名属性）。"""
    c = load_course(_course(tmp_path, stage_select="23", enemies=4, lives=2, star=1))
    ov = c.flat_overrides()
    assert not ({"stage_select", "enemies", "lives", "star"} & set(ov))
    assert ov["lives_override"] == 2 and ov["player_level"] == 1


def test_overrides_change_corpus_identity(tmp_path: Path) -> None:
    """四个覆盖 + max_ticks 都是「一个样本是什么」的一部分 ⇒ 必须改变 corpus_identity_fp。"""
    base = corpus_identity_fp(load_course(_course(tmp_path)))
    for over in (
        {"stage_select": "23"},
        {"enemies": 6},
        {"lives": 1},
        {"star": 1},
        {"max_ticks": 12000},
    ):
        assert corpus_identity_fp(load_course(_course(tmp_path, **over))) != base, over
    # 预算/路径类键照旧不进身份（D14 分类学不变）
    assert corpus_identity_fp(load_course(_course(tmp_path, iters=5))) == base


def test_duplicate_positions_are_kept(tmp_path: Path) -> None:
    """重复位置按写入算（不静默去重）：写 `2,2` = 同一关占两个 id → 采样权重 ×2。"""
    c = load_course(_course(tmp_path, stage_select="2,2"))
    assert c.stage_ids == [2000, 2001]
    assert _stage(c, 2000)["name"] == _stage(c, 2001)["name"]
