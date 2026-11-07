"""BC 败局留用（#6）：配置互斥、ratio 选局、skip marker、fixed 校验。

全部用例只读写几个 npy 形状（不训练、不吃真语料），固定 seed/排序键，无墙钟。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from worker.bc_config import BcCourseConfig, bc_corpus_identity_fp
from worker.bc_corpus import (
    apply_corpus_policy,
    fixed_data_fp,
    load_shards,
    validate_fixed_corpus,
)


def _course(corpus: dict, *, action_v6: bool = False, stages: str = "0-3") -> BcCourseConfig:
    return BcCourseConfig.model_validate(
        {
            "kind": "bc",
            "name": "corpus-test",
            "stages": stages,
            "action_v6": action_v6,
            "corpus": corpus,
        }
    )


def _ratio(**kwargs) -> dict:
    return {
        "source": "collect",
        "outcome_policy": "ratio",
        "wins_only": False,
        "target": {"win": 0.7, "loss": 0.2, "timeout": 0.1},
        **kwargs,
    }


def _shard(root: Path, stage: int, seed: int, outcome: str, *, payload: bool = True) -> Path:
    d = root / f"bc_s{stage}_seed{seed}"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(
        json.dumps({"stage": stage, "seed": seed, "outcome": outcome}),
        encoding="utf-8",
    )
    if payload:
        (d / "obs.npy").write_bytes(b"not-read")
    return d


def _v6_shard(root: Path, name: str, n: int = 2) -> Path:
    d = root / name
    d.mkdir(parents=True)
    np.save(d / "obs.npy", np.zeros((n, 17, 26, 26), dtype=np.uint8))
    np.save(d / "scalars.npy", np.zeros((n, 46), dtype=np.float32))
    np.save(d / "extra.npy", np.zeros((n, 57), dtype=np.float32))
    np.save(d / "a_move.npy", np.zeros((n,), dtype=np.uint8))
    np.save(d / "a_fire.npy", np.zeros((n,), dtype=np.uint8))
    np.save(d / "a_hold.npy", np.zeros((n,), dtype=np.uint8))
    np.save(d / "mask.npy", np.ones((n, 9), dtype=np.uint8))
    (d / "manifest.json").write_text(
        json.dumps({"stage": 0, "seed": 1, "outcome": "stage_clear"}),
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------- 配置面


def test_ratio_config_and_fixed_invariants() -> None:
    c = _course(_ratio())
    assert c.corpus.outcome_policy == "ratio"
    assert c.corpus.target is not None
    assert c.corpus.target.win == pytest.approx(0.7)

    bad = _ratio()
    bad["wins_only"] = True
    with pytest.raises(ValueError, match="wins_only"):
        _course(bad)

    bad = _ratio(target={"win": 0.7, "loss": 0.2, "timeout": 0.2})
    with pytest.raises(ValueError, match="之和"):
        _course(bad)

    bad = _ratio(target={"win": 0.0, "loss": 0.5, "timeout": 0.5})
    with pytest.raises(ValueError, match="win"):
        _course(bad)

    fixed = {
        "source": "fixed",
        "outcome_policy": "all",
        "wins_only": False,
        "games_per_stage": 0,
        "fixed_dir": "tmp/fixed",
    }
    assert _course(fixed).corpus.source == "fixed"
    for key, value in (
        ("wins_only", True),
        ("games_per_stage", 1),
        ("outcome_policy", "ratio"),
    ):
        with pytest.raises(ValueError):
            _course({**fixed, key: value})


def test_fingerprint_only_changes_when_policy_is_activated() -> None:
    base = _course({"games_per_stage": 0})
    fp = bc_corpus_identity_fp(base)
    assert bc_corpus_identity_fp(_course({"games_per_stage": 0})) == fp

    ratio = _course(_ratio(games_per_stage=0))
    assert bc_corpus_identity_fp(ratio) != fp
    global_ratio = _course(_ratio(games_per_stage=0, scope="global"))
    assert bc_corpus_identity_fp(global_ratio) != bc_corpus_identity_fp(ratio)

    fixed = _course(
        {
            "source": "fixed",
            "outcome_policy": "all",
            "wins_only": False,
            "games_per_stage": 0,
            "fixed_dir": "tmp/a",
        }
    )
    fixed_other_path = _course(
        {
            "source": "fixed",
            "outcome_policy": "all",
            "wins_only": False,
            "games_per_stage": 0,
            "fixed_dir": "tmp/b",
        }
    )
    assert bc_corpus_identity_fp(fixed) != fp
    assert bc_corpus_identity_fp(fixed_other_path) == bc_corpus_identity_fp(fixed)


# ---------------------------------------------------------------- ratio


def test_ratio_keeps_all_wins_and_caps_nonwins(tmp_path: Path) -> None:
    for i in range(3):
        _shard(tmp_path, 0, i, "stage_clear")
    for i in range(3, 7):
        _shard(tmp_path, 0, i, "lives_exhausted")
    for i in range(7, 9):
        _shard(tmp_path, 0, i, "timeout")

    event = apply_corpus_policy(
        tmp_path,
        policy="ratio",
        corpus_fp="cfp",
        it=1,
        target={"win": 0.7, "loss": 0.2, "timeout": 0.1},
        log=lambda _m: None,
    )
    assert event["collected"] == {"win": 3, "loss": 4, "timeout": 2}
    # N = 3/0.7 = 4.2857；loss=round(0.857)=1，timeout=round(0.428)=0
    assert event["kept"] == {"win": 3, "loss": 1, "timeout": 0}
    assert event["actual"]["win"] == pytest.approx(0.75)
    assert set(event["infeasible"]) == {"timeout"}
    assert event["invalid_skipped"] == 0

    assert len(load_shards(tmp_path)) == 4
    markers = [p for p in tmp_path.glob("bc_s*_seed*") if (p / "manifest.json").is_file()]
    skipped = [
        p
        for p in markers
        if json.loads((p / "manifest.json").read_text()).get("corpus_skipped")
    ]
    assert len(skipped) == 5
    assert all(not (p / "obs.npy").exists() for p in skipped)
    for p in skipped:
        assert (p / "manifest.json").is_file()


def test_ratio_scope_stage_vs_global(tmp_path: Path) -> None:
    def make(root: Path, stage: int, wins: int, losses: int) -> None:
        for i in range(wins):
            _shard(root, stage, i, "stage_clear")
        for i in range(losses):
            _shard(root, stage, 100 + i, "lives_exhausted")

    make(tmp_path / "stage", 0, 1, 3)
    make(tmp_path / "stage", 1, 1, 0)
    stage = apply_corpus_policy(
        tmp_path / "stage",
        policy="ratio",
        corpus_fp="cfp",
        it=1,
        target={"win": 0.5, "loss": 0.5, "timeout": 0.0},
        scope="stage",
        log=lambda _m: None,
    )
    assert stage["kept"] == {"win": 2, "loss": 1, "timeout": 0}

    make(tmp_path / "global", 0, 1, 3)
    make(tmp_path / "global", 1, 1, 0)
    glob = apply_corpus_policy(
        tmp_path / "global",
        policy="ratio",
        corpus_fp="cfp",
        it=1,
        target={"win": 0.5, "loss": 0.5, "timeout": 0.0},
        scope="global",
        log=lambda _m: None,
    )
    assert glob["kept"] == {"win": 2, "loss": 2, "timeout": 0}


def test_ratio_is_deterministic_and_resume_safe(tmp_path: Path) -> None:
    _shard(tmp_path, 0, 0, "stage_clear")
    for i in range(1, 7):
        _shard(tmp_path, 0, i, "lives_exhausted")
    first = apply_corpus_policy(
        tmp_path,
        policy="ratio",
        corpus_fp="cfp",
        it=1,
        target={"win": 1.0, "loss": 0.0, "timeout": 0.0},
        log=lambda _m: None,
    )
    assert first["kept"]["loss"] == 0
    # 二次调用（模拟断点续跑）不会把已 skip 的局重新选回来，也不会解除 marker。
    second = apply_corpus_policy(
        tmp_path,
        policy="ratio",
        corpus_fp="cfp",
        it=1,
        target={"win": 1.0, "loss": 0.0, "timeout": 0.0},
        log=lambda _m: None,
    )
    assert second["kept"]["loss"] == 0
    skipped = [
        p
        for p in tmp_path.glob("bc_s*_seed*")
        if json.loads((p / "manifest.json").read_text()).get("corpus_skipped")
    ]
    assert len(skipped) == 6


def test_collect_corpus_applies_ratio_after_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from trainer import bc_loop
    from worker import bc_collect

    course = _course(
        _ratio(
            games_per_stage=2,
            target={"win": 0.5, "loss": 0.5, "timeout": 0.0},
        ),
        stages="0",
    )
    out = tmp_path / "bc-data" / "it1"
    seen: list[dict] = []

    def fake_dispatch(**kw):
        seen.append(kw)
        _shard(Path(kw["out_dir"]), 0, 1, "stage_clear")
        _shard(Path(kw["out_dir"]), 0, 2, "lives_exhausted")
        _shard(Path(kw["out_dir"]), 0, 3, "lives_exhausted")
        return {"failed": 0, "failed_nodes": []}

    monkeypatch.setattr(bc_collect, "dispatch_bc_corpus", fake_dispatch)
    ledger = tmp_path / "log.jsonl"
    got = bc_loop.collect_corpus(
        course,
        "cfp",
        "cfp",
        1,
        out,
        smoke=False,
        cfg=None,
        jsonl_path=ledger,
        log=lambda _m: None,
    )
    assert got == out
    assert seen and seen[0]["wins_only"] is False
    assert len(load_shards(out)) == 2
    event = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert event["event"] == "bc_corpus_mix"
    assert event["kept"] == {"win": 1, "loss": 1, "timeout": 0}


def test_all_keeps_valid_outcomes_but_drops_invalid(tmp_path: Path) -> None:
    _shard(tmp_path, 0, 1, "stage_clear")
    _shard(tmp_path, 0, 2, "timeout")
    _shard(tmp_path, 0, 3, "error")
    event = apply_corpus_policy(
        tmp_path,
        policy="all",
        corpus_fp="cfp",
        it=1,
        log=lambda _m: None,
    )
    assert event["kept"] == {"win": 1, "loss": 0, "timeout": 1}
    assert event["invalid_skipped"] == 1
    assert len(load_shards(tmp_path)) == 2


# ---------------------------------------------------------------- fixed


def test_fixed_v6_validation_and_data_fp(tmp_path: Path) -> None:
    root = tmp_path / "fixed"
    _v6_shard(root / "it1", "bc_s0_seed1")
    fp = validate_fixed_corpus(root, action_v6=True)
    assert len(fp) == 64
    assert fixed_data_fp(root) == fp

    (root / "it1" / "bc_s0_seed1" / "mask.npy").unlink()
    with pytest.raises(ValueError, match="缺 mask"):
        validate_fixed_corpus(root, action_v6=True)


def test_fixed_config_skips_dispatch_and_records_data_fp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from trainer import bc_loop
    from worker import bc_collect

    root = tmp_path / "fixed"
    _v6_shard(root, "bc_s0_seed1")
    course = _course(
        {
            "source": "fixed",
            "outcome_policy": "all",
            "wins_only": False,
            "games_per_stage": 0,
            "fixed_dir": str(root),
        },
        action_v6=True,
    )
    monkeypatch.setattr(
        bc_collect, "dispatch_bc_corpus", lambda **_kw: pytest.fail("fixed 不应派发采集")
    )
    ledger = tmp_path / "log.jsonl"
    got = bc_loop.collect_corpus(
        course,
        "cfp",
        "cfp",
        1,
        tmp_path / "bc-data" / "it1",
        smoke=False,
        cfg=None,
        jsonl_path=ledger,
        log=lambda _m: None,
    )
    assert got == root
    event = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert event["source"] == "fixed"
    assert event["policy"] == "all"
    assert event["data_fp"] == fixed_data_fp(root)
