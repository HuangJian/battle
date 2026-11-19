"""BC eval 继承课程环境（`eval.course_env`，2026-11-12）。

钉三件事：
  ① 课程层：`course_env=true` 与 `levels` 互斥；只支持 list 型 stages；加载后
     stage_ids / lives / max_ticks 与课程环境逐字一致。
  ② dispatch 层：course_env=true 时 tasks 只含课程 4 关，payload 的
     max_ticks / difficulty / lives / player_level / stage_json 全部来自课程。
  ③ 旧路径（course_env=false）逐字不变：仍按 eval.levels 的关卡文件跑，panel.source="levels"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import worker.bc_eval as be
from worker.bc_config import load_bc_course


def _course(tmp_path: Path, **over: object) -> Path:
    doc: dict[str, object] = {
        "kind": "bc",
        "name": "t-course-env",
        "level": "classic35-safe-base",
        "stage_select": "16,22,3,6",
        "enemies": 2,
        "lives": 1,
        "star": 0,
        "max_ticks": 3000,
        "eval": {"every_epochs": 10, "games_per_stage": 5, "course_env": True},
    }
    doc.update(over)
    p = tmp_path / "t.bc.jsonc"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _mock_engine(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_fetch(url: str, key: str, **kw: object):
        calls.append(kw)
        return (
            {
                "win": True,
                "cleared": True,
                "outcome": "stage_clear",
                "ticks": 100,
                "score": 1,
                "kills": 2,
                "playerHits": 1,
                "powerUpsCollected": 1,
                "powerUpsSpawned": 1,
                "cellsVisited": 10,
                "moveHist": [0, 1, 1, 1, 1],
            },
            None,
        )

    monkeypatch.setattr(be, "fetch_task", fake_fetch)
    monkeypatch.setattr(be, "post_weights_cached", lambda *a, **k: None)
    monkeypatch.setattr(be, "validate_eval_result", lambda m, w: "")
    return calls


NODES = [{"id": "self", "url": "http://127.0.0.1:1", "authKey": "", "concurrency": 1}]


def test_course_env_loads_course_environment(tmp_path: Path) -> None:
    c = load_bc_course(str(_course(tmp_path)))
    assert c.eval.course_env is True
    assert c.eval.enabled is True
    assert c.eval.levels == []
    assert c.stage_ids == [2000, 2001, 2002, 2003]
    assert c.max_ticks == 3000
    assert c.player.lives == 1
    assert c.player.level == 0
    stages = c.stages
    assert isinstance(stages, list)
    assert [s.get("count") for s in stages] == [2, 2, 2, 2]


def test_course_env_rejects_levels(tmp_path: Path) -> None:
    p = _course(
        tmp_path,
        eval={
            "every_epochs": 10,
            "games_per_stage": 5,
            "course_env": True,
            "levels": ["arena4"],
        },
    )
    with pytest.raises(ValueError, match="互斥"):
        load_bc_course(str(p))


def test_course_env_rejects_range_stages(tmp_path: Path) -> None:
    p = tmp_path / "range.bc.jsonc"
    p.write_text(
        json.dumps(
            {
                "kind": "bc",
                "name": "range",
                "stages": "0-3",
                "eval": {"every_epochs": 10, "games_per_stage": 5, "course_env": True},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="stages 是 list"):
        load_bc_course(str(p))


def test_dispatch_course_env_inherits_env(monkeypatch: pytest.MonkeyPatch) -> None:
    c = load_bc_course("g0-4s2e")
    calls = _mock_engine(monkeypatch)
    res = be.dispatch_bc_eval(
        weights_bytes=b"x",
        eval_levels=[],
        games_per_stage=2,
        it=1,
        epoch=10,
        nodes=NODES,
        course=c,
        course_env=True,
        log=lambda m: None,
    )
    assert len(calls) == 4 * 2  # 4 关 × 2 seed
    assert {x["stage"] for x in calls} == {2000, 2001, 2002, 2003}
    assert all(x["max_ticks"] == 3000 for x in calls)
    assert all(x["difficulty"] == "hard" for x in calls)
    assert all(x["lives_override"] == 1 for x in calls)
    assert all(x["player_level"] == 0 for x in calls)
    assert all(x["stage_json"] for x in calls)
    assert all(x["mode"] == "eval" and x["policy"] == "nn" for x in calls)

    panel = res["levels"][0]["panel"]
    assert panel["source"] == "course"
    assert panel["stage_ids"] == [2000, 2001, 2002, 2003]
    assert panel["enemies"] == [2]
    assert panel["max_ticks"] == 3000
    assert panel["lives"] == 1
    assert res["sentinel_only"] is True


def test_dispatch_writes_agent_meta_per_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-13 报障：BC 手动 evalA 的局数在节点页「今日贡献」全为 0。

    根因：`dispatch_bc_eval` 只把聚合行写进课程账本，**没写 `dist-agent-meta.jsonl`**
    （节点页唯一数据源）。这里钉住每局一条、带承载节点与 mode=eval。
    """
    c = load_bc_course("g0-4s2e")
    _mock_engine(monkeypatch)
    meta = tmp_path / "dist-agent-meta.jsonl"
    be.dispatch_bc_eval(
        weights_bytes=b"x",
        eval_levels=[],
        games_per_stage=2,
        it=3,
        epoch=10,
        nodes=NODES,
        course=c,
        course_env=True,
        meta_path=meta,
        log=lambda m: None,
    )
    rows = [json.loads(x) for x in meta.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 4 * 2  # 4 关 × 2 seed
    assert {r["node"] for r in rows} == {"self"}
    assert {r["mode"] for r in rows} == {"eval"}
    assert {r["it"] for r in rows} == {3}
    assert {r["stage"] for r in rows} == {2000, 2001, 2002, 2003}
    assert {r["seed"] for r in rows} == {1, 2}
    assert all(r["ok"] is True and r["win"] == 1 for r in rows)


def test_dispatch_records_failed_games_in_agent_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """失败局同样入机器健康账（否则节点页成功率只看得见成功、把坏节点读健康）。"""
    from common.distribution import DistError

    c = load_bc_course("g0-4s2e")

    def fail_fetch(url: str, key: str, **kw: object):
        raise DistError(0, "boom")

    monkeypatch.setattr(be, "fetch_task", fail_fetch)
    monkeypatch.setattr(be, "post_weights_cached", lambda *a, **k: None)
    meta = tmp_path / "dist-agent-meta.jsonl"
    be.dispatch_bc_eval(
        weights_bytes=b"x",
        eval_levels=[],
        games_per_stage=1,
        it=1,
        epoch=10,
        nodes=NODES,
        course=c,
        course_env=True,
        meta_path=meta,
        log=lambda m: None,
    )
    rows = [json.loads(x) for x in meta.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == 4
    assert all(r["ok"] is False for r in rows)
    # 全等而不是子串：文本形态断言的计数护栏只许降（`tests/helpers/text_asserts.py` 口径 A）。
    assert {r["reason"] for r in rows} == {"boom"}
    assert {r["node"] for r in rows} == {"self"}


def test_dispatch_levels_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from worker.config import load_course, resolve_level

    lvl = load_course(str(resolve_level("arena4")))
    n = len(lvl.stage_ids)
    calls = _mock_engine(monkeypatch)
    res = be.dispatch_bc_eval(
        weights_bytes=b"x",
        eval_levels=["arena4"],
        games_per_stage=1,
        it=1,
        epoch=10,
        nodes=NODES,
        course_env=False,
        log=lambda m: None,
    )
    assert len(calls) == n  # arena4 全部关 × 1 seed
    panel = res["levels"][0]["panel"]
    assert panel["source"] == "levels"
    assert panel["stage_ids"] == list(range(2000, 2000 + n))
    assert panel["max_ticks"] == int(lvl.max_ticks)
