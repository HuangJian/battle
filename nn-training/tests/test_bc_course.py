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


def test_bc_corpus_identity_fp_covers_obs_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ schema 必须在语料身份里（2026-09-13 修复的回归锁）。

    为什么致命：agent 结果缓存键 = `iterId:mode:kind:stage:seed`（dist_common.py:813，
    不含 codehash），而 BC 的 iterId = `bc-it{it}-{fp12}`（bc_dispatch.py:109）**既不含
    课程名也不含 runId**。身份里漏掉 schema ⇒ v2→v3 的 MAJOR bump 后重跑同一课程 fp 逐字
    不变 ⇒ 同一 iterId ⇒ 节点直接回放旧 era 的 shard，v3 编码器一次都跑不到。
    """
    import schema

    c = load_bc_course("bc-c4")
    fp = bc_corpus_identity_fp(c)

    # ① 旧（不含 schema）时代的 fp —— 就是会被回放的那个 iterId 分量。修复后必须不同。
    assert not fp.startswith("f60406b20e78"), "fp 仍是旧 era 值 ⇒ 旧缓存会被回放"

    # ② 语料参数相同 ⇒ 同一身份（bc-c4-v3 就是 bc-c4 在 v3 上的重跑，不是新语料）。
    # 必须在 monkeypatch **之前**算：补丁一改指纹，两边算出的 fp 就不同了。
    assert bc_corpus_identity_fp(load_bc_course("bc-c4-v3")) == fp

    # ③ schema 的两个分量各自都在 payload 里（改任一个，身份必须变）。
    monkeypatch.setattr(schema, "OBS_SCHEMA_MAJOR", schema.OBS_SCHEMA_MAJOR + 1)
    assert bc_corpus_identity_fp(c) != fp
    monkeypatch.setattr(schema, "SCHEMA_FINGERPRINT", "deadbeef")
    assert bc_corpus_identity_fp(c) != fp


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


# ------------------------------------------------------------------ 节点熔断


def _loss_skipped_manifest(stage: int, seed: int):
    """合法的 wins-only 败局容器（validate_result 的 loss-skip 分支）。"""
    from dist_common import BC_COLLECTOR, BC_WVER

    return {
        "wver": BC_WVER,
        "collector": BC_COLLECTOR,
        "kept": False,
        "stage": stage,
        "seed": seed,
        "outcome": "loss",
        "nSamples": 0,
    }


def test_bc_dispatch_trips_broken_node_and_requeues(tmp_path: Path, monkeypatch) -> None:
    """2026-09-14 mac 事故回归：单节点连续真失败 → 本轮熔断，剩余任务改派健康节点。"""
    from rl import bc_dispatch as D

    calls: dict[str, int] = {"bad": 0, "good": 0}

    def fake_fetch(url: str, _auth: str, **kw):
        nid = "bad" if "bad" in url else "good"
        calls[nid] += 1
        if nid == "bad":
            raise D.dist_common.DistError(
                0, "TypeError: undefined is not an object (evaluating 's.obs')"
            )
        return _loss_skipped_manifest(int(kw["stage"]), int(kw["seed"])), {}

    monkeypatch.setattr(D, "fetch_task", fake_fetch)
    msgs: list[str] = []
    tasks = [(2000, s) for s in range(1, 13)]
    nodes = [
        {"id": "bad", "url": "http://bad", "concurrency": 2, "enabled": True},
        {"id": "good", "url": "http://good", "concurrency": 2, "enabled": True},
    ]
    stats = D.dispatch_bc_corpus(
        tasks=tasks,
        out_dir=tmp_path,
        it=1,
        corpus_fp="cfp",
        course_fp="xfp",
        difficulty="hard",
        max_ticks=100,
        nodes=nodes,
        node_fail_limit=3,
        log=msgs.append,
    )
    # 熔断：坏节点被摘掉并在统计里点名
    assert stats["failed_nodes"] == ["bad"]
    assert any("熔断" in m for m in msgs)
    # 全部任务有归宿（games + failed == 任务数），没有静默漏采
    assert stats["games"] + stats["failed"] == len(tasks)
    # 熔断生效：坏节点最多吃掉「阈值 × 槽位」个任务（每次 2 attempt），不再霸占整轮
    assert calls["bad"] <= 3 * 2 * 2, f"熔断太晚：bad 被调用 {calls['bad']} 次"
    assert calls["bad"] < len(tasks) * 2, "熔断后坏节点仍在霸占任务"
    # 全部任务有归宿（成功 + 失败 == 任务数），没有静默漏采
    assert stats["games"] + stats["failed"] == len(tasks)
    # 熔断后的任务改派给了健康节点
    assert calls["good"] == stats["games"]
    assert stats["games"] >= len(tasks) - calls["bad"]


def test_bc_dispatch_failfast_disabled_keeps_old_behavior(tmp_path: Path, monkeypatch) -> None:
    """node_fail_limit=0：关闭熔断（所有节点都试，行为与旧版一致）。"""
    from rl import bc_dispatch as D

    seen: list[str] = []

    def fake_fetch(url: str, _auth: str, **kw):
        seen.append(url)
        raise D.dist_common.DistError(0, "boom")

    monkeypatch.setattr(D, "fetch_task", fake_fetch)
    stats = D.dispatch_bc_corpus(
        tasks=[(2000, 1)],
        out_dir=tmp_path,
        it=1,
        corpus_fp="cfp",
        course_fp="xfp",
        difficulty="hard",
        max_ticks=100,
        nodes=[{"id": "bad", "url": "http://bad", "concurrency": 1, "enabled": True}],
        node_fail_limit=0,
        log=lambda _m: None,
    )
    assert stats["failed"] == 1
    assert stats["failed_nodes"] == []
    assert len(seen) == 2  # attempt 1 + 2


def test_bc_dispatch_busy_is_not_a_node_fault(tmp_path: Path, monkeypatch) -> None:
    """busy（并发槽满）不计入失败 streak —— 否则健康节点会被误熔断。"""
    from rl import bc_dispatch as D

    calls = {"n": 0}

    def fake_fetch(_url: str, _auth: str, **kw):
        calls["n"] += 1
        if int(kw["seed"]) == 1:  # seed1 一直 busy；其余正常
            raise D.dist_common.DistError(0, "busy")
        return _loss_skipped_manifest(int(kw["stage"]), int(kw["seed"])), {}

    monkeypatch.setattr(D, "fetch_task", fake_fetch)
    stats = D.dispatch_bc_corpus(
        tasks=[(2000, 1), (2000, 2), (2000, 3)],
        out_dir=tmp_path,
        it=1,
        corpus_fp="cfp",
        course_fp="xfp",
        difficulty="hard",
        max_ticks=100,
        nodes=[{"id": "n1", "url": "http://n1", "concurrency": 1, "enabled": True}],
        node_fail_limit=2,
        busy_retry_limit=2,  # 背压上限（测试提速）
        busy_backoff_sec=0.01,
        log=lambda _m: None,
    )
    # busy 不计入节点故障 ⇒ 不熔断（否则并发槽满的健康节点会被误摘）
    assert stats["failed_nodes"] == []
    assert stats["failed"] == 1  # 背压上限用尽后仍 busy 才算失败
    assert stats["games"] == 2


def test_bc_dispatch_busy_backpressure_then_success(tmp_path: Path, monkeypatch) -> None:
    """背压回归（2026-09-14）：短暂槽满 → 退避重排后成功，绝不记 failed。

    事故形态：40 局瞬间推送，节点并发槽占满，溢出任务两次「立刻重试」都撞 busy
    ⇒ 直接计 failed（实测 7 局）⇒ BcDispatchError 把整轮训练打死。
    """
    from rl import bc_dispatch as D

    state = {"busy": 3}

    def fake_fetch(_url: str, _auth: str, **kw):
        if state["busy"] > 0:
            state["busy"] -= 1
            raise D.dist_common.DistError(0, "busy")
        return _loss_skipped_manifest(int(kw["stage"]), int(kw["seed"])), {}

    monkeypatch.setattr(D, "fetch_task", fake_fetch)
    msgs: list[str] = []
    stats = D.dispatch_bc_corpus(
        tasks=[(2000, 1), (2000, 2)],
        out_dir=tmp_path,
        it=1,
        corpus_fp="cfp",
        course_fp="xfp",
        difficulty="hard",
        max_ticks=100,
        nodes=[{"id": "n1", "url": "http://n1", "concurrency": 1, "enabled": True}],
        node_fail_limit=2,
        busy_retry_limit=6,
        busy_backoff_sec=0.01,
        log=msgs.append,
    )
    assert stats["failed"] == 0
    assert stats["games"] == 2
    assert stats["failed_nodes"] == []
    assert any("背压" in m for m in msgs)


def test_run_bc_finish_all_rounds_writes_run_complete(tmp_path: Path) -> None:
    """2026-09-14 回归：BC 全轮完成的收尾必须同时做两件事 ——

    ① 打含 `ALL DONE` 的**尾行**：console exit-watchdog 用 `tailNormalCompletion`
      （日志尾行 includes('ALL DONE')，大小写敏感）判「正常完成」；原来只打小写
      `all rounds done` ⇒ BC 正常跑完被标「TrainingLoop 意外退出」（实测 07:32）。
    ② 落 `run_complete` 账本事件：console「✅ 训练已完成」横幅的派生源。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import run_bc

    j = tmp_path / "training_log.jsonl"
    msgs: list[str] = []
    run_bc._finish_all_rounds(j, 3, log=msgs.append)
    assert any("ALL DONE" in m for m in msgs)
    last = [ln for ln in j.read_text(encoding="utf-8").splitlines() if ln.strip()][-1]
    e = json.loads(last)
    assert e["event"] == "run_complete"
    assert e["iter"] == 3 and e["iters"] == 3
    assert "BC" in e["reason"]
