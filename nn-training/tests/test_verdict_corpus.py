"""test_verdict_corpus — 判决批（P2「中方案」）单测。

覆盖：语料注册表读取/响亮失败 · plan_verdict_units 展开（ckpts × 关卡、同种子配对、
unit 自带权重）· units_for_batch 分派（verdict vs ladder）· consume_requests 的
kind='verdict' 物化与去重 · _verdict_key_of 顺序敏感。

契约来源：docs/evalboard-phase0-census.md §P2；TS 镜像
dashboard/src/evalboard/corpora.ts（改一侧必须同步另一侧）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.batch_eval import (
    BATCH_STAGE_BASE,
    consume_requests,
    corpora_path,
    corpus_doc,
    load_corpora,
    plan_verdict_units,
    units_for_batch,
)
from rl.jsonc import load as jsonc_load

CKPTS = [
    {"label": "bc", "path": "nn-training/weights/bc.json"},
    {"label": "it30", "path": "nn-training/weights/it30.json"},
]


def _corpus(cid: str = "v-ladder-c03-p400600") -> dict:
    return corpus_doc(cid)


def test_registry_declares_t5_verdict_segment() -> None:
    doc = load_corpora()
    assert isinstance(doc["corpora"], list) and doc["corpora"]
    c = _corpus()
    assert c["level"] == "ladder-c03"
    # 池外段：与训练池 860001-860200 及已用池外段 400000/400200 不相交（§15.1）。
    assert c["seed0"] == 400600
    assert c["games_per_stage"] == 200
    assert corpora_path().name == "corpora.json"


def test_unknown_corpus_and_malformed_registry_fail_loudly(tmp_path: Path, monkeypatch) -> None:
    with pytest.raises(ValueError, match="未知判决语料"):
        _corpus("nope")
    bad = tmp_path / "c.json"
    bad.write_text(json.dumps({"version": "v1", "corpora": {}}), encoding="utf-8")
    monkeypatch.setenv("EVALBOARD_CORPORA", str(bad))
    with pytest.raises(ValueError, match="形态非法"):
        load_corpora()
    # 合法单条可读（EVALBOARD_CORPORA 覆盖对两侧同效）。
    bad.write_text(
        json.dumps(
            {"version": "v1", "corpora": [{"id": "x", "level": "ladder-c03", "seed0": 5, "games_per_stage": 1}]}
        ),
        encoding="utf-8",
    )
    assert corpus_doc("x")["seed0"] == 5


def test_plan_verdict_units_expands_ckpts_times_stages() -> None:
    verdict = plan_verdict_units(_corpus(), CKPTS)
    level = jsonc_load(str(ROOT / "levels" / "ladder-c03.jsonc"))
    stages = level["stages"]
    assert len(verdict) == len(CKPTS) * len(stages) == 2 * 4
    assert verdict[0]["unit_idx"] == 0 and verdict[-1]["unit_idx"] == len(verdict) - 1


def test_verdict_units_carry_weights_and_pairable_seeds() -> None:
    units = plan_verdict_units(_corpus(), CKPTS)
    by_ckpt: dict[str, list[dict]] = {}
    for u in units:
        by_ckpt.setdefault(u["ckpt_label"], []).append(u)
    # 每个 ckpt 覆盖全部关卡 —— 批次级无权重，权重在 unit 上（多 ckpt 同批）。
    assert sorted(by_ckpt) == ["bc", "it30"]
    # 逐局配对：两 ckpt 的同一关种子段逐位相同（§3.5④：不许事后求交集）。
    for a, b in zip(by_ckpt["bc"], by_ckpt["it30"], strict=True):
        assert a["seeds"] == b["seeds"] == [400600 + i for i in range(200)]
        assert a["stageId"] == b["stageId"]
        assert a["stageJsonHash"] == b["stageJsonHash"]
        assert a["ckpt"] != b["ckpt"]


def test_verdict_unit_stagejson_and_stage_id_match_dispatch_contract() -> None:
    units = plan_verdict_units(_corpus(), CKPTS)
    level = jsonc_load(str(ROOT / "levels" / "ladder-c03.jsonc"))
    st0 = level["stages"][0]
    u0 = units[0]
    # 紧凑 separators：与 eval-course-ckpt.ts 的 JSON.stringify(stage) 同形，
    # 否则同一关在 agent 侧会分成两个 stageJsonHash/缓存键。
    sj = json.dumps(st0, separators=(",", ":"), ensure_ascii=False)
    assert u0["stageJson"] == sj
    assert u0["stageJsonHash"] == hashlib.sha256(sj.encode()).hexdigest()[:16]
    assert u0["stageId"] == BATCH_STAGE_BASE
    assert units[1]["stageId"] == BATCH_STAGE_BASE + 1
    assert u0["lives"] == 1 and u0["level"] == 0
    assert u0["difficulty"] == "hard" and u0["maxTicks"] == 2700
    assert u0["rung"].startswith("v-ladder-c03-p400600#")


def test_plan_verdict_units_rejects_empty_ckpts_and_bad_corpus() -> None:
    with pytest.raises(ValueError, match=r"未知判决语料|level"):
        plan_verdict_units({"id": "bad", "seed0": 1, "games_per_stage": 1}, CKPTS)
    with pytest.raises(ValueError, match=r"ckpts\[\]\.path"):
        plan_verdict_units(_corpus(), [{"label": "x"}])
    with pytest.raises(ValueError, match=r"seed0/games_per_stage"):
        plan_verdict_units({**_corpus(), "seed0": 0}, CKPTS)


def test_units_for_batch_dispatches_verdict_vs_ladder() -> None:
    v = units_for_batch({"kind": "verdict", "corpus": "v-ladder-c03-p400600", "ckpts": CKPTS})
    assert len(v) == 8 and "ckpt" in v[0]
    # 旧台账行没有 kind ⇒ 走 ladder.json 分支（不得被当判决批）。
    ladder_units = units_for_batch({"units": {"of": 0, "done": []}})
    assert ladder_units and "ckpt" not in ladder_units[0]
    # 未知语料在展开时响亮失败（调用方 _requeue 退回，不静默跑空）。
    with pytest.raises(ValueError, match="未知判决语料"):
        units_for_batch({"kind": "verdict", "corpus": "nope", "ckpts": CKPTS})


def _wreq(root: Path, *rows: dict) -> None:
    with open(root / "requests.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _vreq(**kw) -> dict:
    d = {
        "req_id": "q-v1",
        "kind": "verdict",
        "ts": "2026-09-19T10:00:00",
        "requester": "cli",
        "corpus": "v-ladder-c03-p400600",
        "ckpts": CKPTS,
        "policy": "nn",
        "iter": 30,
    }
    d.update(kw)
    return d


def test_consume_verdict_request_materializes_verdict_batch(tmp_path: Path) -> None:
    _wreq(tmp_path, _vreq())
    counts = consume_requests(tmp_path)
    assert counts["enqueued"] == 1 and counts["consumed"] == 1
    (batch,) = [
        json.loads(line) for line in (tmp_path / "batches.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert batch["kind"] == "verdict" and batch["corpus"] == "v-ladder-c03-p400600"
    assert batch["ckpts"] == CKPTS
    assert batch["trigger"] == "verdict"
    # 判决批不占用 ladder 键空间：course/rung/ckpt 不出现在行上（靠 kind 判别）。
    assert "course" not in batch and "rung_from" not in batch
    # of 由 plan 展平后回写；consume 只声明 0。
    assert batch["units"]["of"] == 0


def test_consume_verdict_dedupes_pending_and_materialized(tmp_path: Path) -> None:
    _wreq(tmp_path, _vreq())
    consume_requests(tmp_path)
    n1 = len((tmp_path / "batches.jsonl").read_text(encoding="utf-8").splitlines())
    # 同键再入队（同一 requests.jsonl 未清）⇒ skipped，不建第二批。
    counts = consume_requests(tmp_path)
    assert counts["enqueued"] == 0
    n2 = len((tmp_path / "batches.jsonl").read_text(encoding="utf-8").splitlines())
    assert n2 == n1 == 1


def test_consume_verdict_skips_malformed_rows(tmp_path: Path) -> None:
    _wreq(tmp_path, _vreq(req_id="q-bad1", corpus=""), _vreq(req_id="q-bad2", ckpts=[{"label": "x"}]))
    counts = consume_requests(tmp_path)
    assert counts["enqueued"] == 0 and counts["skipped"] == 2
