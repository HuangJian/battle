"""关卡抽离（DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25）：level 引用 + corpus_fp 语义身份。

覆盖四件事：
① `load_course` 的 level 合并（stages/difficulty/max_ticks/player 归关卡文件唯一持有，
   课程侧内联重复声明 = raise）；
② 内联 stages 旧用法逐字节兼容（在跑课程不迁移也照常加载）；
③ `corpus_identity_fp` 分类学：预算/测量/路径字段（iters/eval_*/out/traj）**不**进语料
   身份（mid-run 编辑不得触发 D14 拒收），env+reward 改动**必须**进；
④ 内联 stages 与 level 引用同形 ⇒ 同 corpus_fp。
"""

import json
from pathlib import Path

import pytest

from rl.config import corpus_identity_fp, load_course

REPO = Path(__file__).resolve().parents[2]
C6_DMGFIX = REPO / "nn-training" / "curricula" / "c6-dmgfix.jsonc"
C6_CHIP = REPO / "nn-training" / "curricula" / "c6-chip.jsonc"
ARENA6 = REPO / "nn-training" / "levels" / "arena6.jsonc"


def test_level_merge_resolves_env_from_level_file() -> None:
    """level 引用：环境语义（stages/命/difficulty/max_ticks）全部来自关卡文件。"""
    c = load_course(C6_DMGFIX)
    assert c.level == "arena6"
    assert len(c.stages) == 1 and c.stages[0].count == 6
    assert c.player.lives == 1 and c.player.level == 0
    assert c.difficulty == "hard" and c.max_ticks == 2400
    # 课程自身的训练变量原样保留
    assert c.reward.params["wChip"] == 0.005
    assert "wDmg" not in c.reward.params


def test_level_inline_conflict_raises(tmp_path: Path) -> None:
    """level 引用后课程侧内联 stages = 配置冲突（环境语义单一来源）。"""
    d = _course_from(C6_DMGFIX)
    d["stages"] = _course_from(ARENA6)["stages"]
    p = tmp_path / "conflict.jsonc"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError):
        load_course(p)


def test_level_file_missing_raises() -> None:
    """引用不存在的关卡 = FileNotFoundError（启动期硬失败）。"""
    d = _course_from(C6_DMGFIX)
    d["level"] = "no-such-level"
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jsonc", delete=False) as f:
        f.write(json.dumps(d).encode("utf-8"))
        p = Path(f.name)
    with pytest.raises(FileNotFoundError):
        load_course(p)
    p.unlink()


def _course_from(path: Path) -> dict:
    from rl.jsonc import load as _load_jsonc

    return _load_jsonc(str(path))


def _write_course(tmp_path: Path, d: dict) -> Path:
    p = tmp_path / "course.jsonc"
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def test_corpus_fp_covers_obs_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """★ schema 必须在 RL 语料身份里（2026-09-13 补，与 BC 侧同一坑的对称回归锁）。

    D14 混训分流按 corpus_fp 判定：身份里漏掉 schema ⇒ v2(14ch) 与 v3(16ch) 语料被
    判为**同一身份**，D14 放行 ⇒ 形状不同的 shard 混进同一次训练（加载侧
    data.npyio.verify_shard_schema 只能事后 raise，那时已经在崩）。
    """
    import schema

    base = _course_from(C6_DMGFIX)
    c = load_course(_write_course(tmp_path, base))
    fp = corpus_identity_fp(c)

    monkeypatch.setattr(schema, "OBS_SCHEMA_MAJOR", schema.OBS_SCHEMA_MAJOR + 1)
    assert corpus_identity_fp(c) != fp
    monkeypatch.setattr(schema, "SCHEMA_FINGERPRINT", "deadbeef")
    assert corpus_identity_fp(c) != fp


def test_corpus_fp_ignores_budget_fields(tmp_path: Path) -> None:
    """iters/eval_*/out/traj 编辑不改 corpus_fp（C 类：随时可改、不破坏血缘）。"""
    base = _course_from(C6_DMGFIX)
    a = load_course(_write_course(tmp_path, base))
    mutated = dict(base)
    mutated["iters"] = 999
    mutated["eval_games_per_stage"] = 7
    mutated["out"] = "tmp/other/weights.json"
    mutated["traj"] = "tmp/other-traj"
    mutated["max_hours"] = 3.0
    b = load_course(_write_course(tmp_path, mutated))
    assert corpus_identity_fp(a) == corpus_identity_fp(b)


def test_corpus_fp_tracks_reward_and_stage(tmp_path: Path) -> None:
    """reward 参数（wChip）与 stage（count）改动必须改变 corpus_fp（A 类：语料身份）。"""
    base = _course_from(C6_DMGFIX)
    fp0 = corpus_identity_fp(load_course(_write_course(tmp_path, base)))

    reward_mutation = json.loads(json.dumps(base))
    reward_mutation["reward"]["params"]["wChip"] = 0.03
    fp_r = corpus_identity_fp(load_course(_write_course(tmp_path, reward_mutation)))
    assert fp_r != fp0

    # stage 突变在内联形上做（level 引用本身不携带 stages）
    stage_mutation = json.loads(json.dumps(base))
    stage_mutation.pop("level")
    lvl = _course_from(ARENA6)
    for k in ("stages", "difficulty", "max_ticks", "player"):
        stage_mutation[k] = lvl[k]
    stage_mutation["stages"][0]["count"] = 4
    fp_s = corpus_identity_fp(load_course(_write_course(tmp_path, stage_mutation)))
    assert fp_s != fp0


def test_corpus_fp_inline_equals_level_ref(tmp_path: Path) -> None:
    """同 env+reward：内联 stages 与 level 引用同形同指纹（哈希解析后的值）。"""
    ref = _course_from(C6_DMGFIX)
    inline = dict(ref)
    inline.pop("level")
    lvl = _course_from(ARENA6)
    for k in ("stages", "difficulty", "max_ticks", "player"):
        inline[k] = lvl[k]
    a = load_course(_write_course(tmp_path, ref))
    b = load_course(_write_course(tmp_path, inline))
    assert corpus_identity_fp(a) == corpus_identity_fp(b)


def test_inline_stages_course_still_loads() -> None:
    """旧用法（内联 stages，如仍在跑的 c6-chip）逐字节兼容。"""
    c = load_course(C6_CHIP)
    assert c.level == ""
    assert len(c.stages) == 1 and c.stages[0].count == 6
    assert c.reward.params["wChip"] == 0.03
