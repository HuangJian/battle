"""rl/bc_config.py + run_bc 纯函数 — BC 课程与编排器测试（plan/bc-cloud-integration.plan.md §2/§6）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rl.bc_config import (
    bc_corpus_identity_fp,
    load_bc_course,
    resolve_bc_course,
    round_seeds,
)
from rl.bc_dispatch import landed_pairs


def test_load_bc_c4_course_with_level_injection() -> None:
    """真实 BC 课程（bc-c4）：arena4 关卡注入 stages/difficulty/max_ticks/player。"""
    c = load_bc_course("bc-c4")
    assert c.kind == "bc"
    assert c.name == "bc-c4-distill"
    assert c.is_custom_stages  # arena4 内联自定义关 → 2000+i
    assert c.stage_ids == [2000]
    assert c.max_ticks == 2400
    assert c.player.lives == 1
    assert c.corpus.games_per_stage == 40
    assert c.corpus.wins_only is True
    assert c.train.arch == "student"
    assert c.train.value_coef == 0.0


def test_bc_course_rejects_env_keys_alongside_level(tmp_path: Path) -> None:
    """引用 level 后课程侧重复声明环境键 = 配置冲突 raise（与 RL 课程同规）。"""
    p = tmp_path / "bad.bc.jsonc"
    p.write_text(
        json.dumps({"name": "bad", "level": "arena4", "difficulty": "classic"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="difficulty"):
        load_bc_course(str(p))


def test_bc_course_extra_forbid(tmp_path: Path) -> None:
    p = tmp_path / "typo.bc.jsonc"
    p.write_text(json.dumps({"name": "t", "epocs": 5}), encoding="utf-8")
    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # extra=forbid（拼错键响亮报错）
        load_bc_course(str(p))


def test_bc_corpus_identity_fp_semantics() -> None:
    c = load_bc_course("bc-c4")
    fp = bc_corpus_identity_fp(c)
    assert len(fp) == 64
    # train 超参 / 预算 / 路径不构成语料身份（与 D14 分类学一致）
    import copy

    d = json.loads(json.dumps(c.model_dump()))
    d2 = copy.deepcopy(d)
    d2["train"]["epochs"] = 999
    d2["iters"] = 7
    d2["out"] = "tmp/other/weights.json"
    from rl.bc_config import BcCourseConfig

    c2 = BcCourseConfig(**d2)
    assert bc_corpus_identity_fp(c2) == fp
    # 语料参数变化 = 身份变化
    d3 = copy.deepcopy(d)
    d3["corpus"]["games_per_stage"] = 41
    c3 = BcCourseConfig(**d3)
    assert bc_corpus_identity_fp(c3) != fp


def test_round_seeds_rotation() -> None:
    c = load_bc_course("bc-c4")
    r1 = round_seeds(c, 1)
    assert r1[0] == 1 and len(r1) == 40
    r2 = round_seeds(c, 2)
    assert r2[0] == 1 + c.corpus.seed_rotate  # §15.1 语料轮转
    assert not (set(r1) & set(r2))  # seed_rotate=64 > games=40 → 轮间严格不重


def test_resolve_bc_course_missing_fail_loud() -> None:
    with pytest.raises(FileNotFoundError):
        resolve_bc_course("no-such-bc-course-xyz")


def test_landed_pairs_reads_manifest(tmp_path: Path) -> None:
    d = tmp_path / "it1"
    shard = d / "bc_s2000_seed7"
    shard.mkdir(parents=True)
    (shard / "manifest.json").write_text(
        json.dumps({"stage": 2000, "seed": 7}), encoding="utf-8"
    )
    (d / "bc_s2000_seed8").mkdir()  # 缺 manifest → 不算落盘
    assert landed_pairs(d) == {(2000, 7)}


def test_run_bc_completed_rounds_ledger(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_bc

    j = tmp_path / "training_log.jsonl"
    j.write_text(
        "\n".join(
            [
                json.dumps({"event": "job_pending", "job_id": "a", "it": 1}),
                json.dumps({"event": "bc_round_completed", "it": 1}),
                json.dumps({"event": "job_completed", "job_id": "a"}),
                "not json",
                json.dumps({"event": "bc_round_completed", "it": "bad"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert run_bc.completed_rounds(j) == {1}
    assert run_bc.completed_rounds(tmp_path / "missing.jsonl") == set()


def test_run_bc_smoke_overrides() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_bc

    c = load_bc_course("bc-c4")
    ov = run_bc.smoke_overrides(c)
    assert ov["games_per_stage"] == 1
    assert ov["max_ticks"] <= 300
    assert ov["epochs"] == 1
