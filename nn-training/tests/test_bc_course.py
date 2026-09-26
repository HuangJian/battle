"""rl/bc_config.py + rl/bc_loop.py + rl/bc_ledger.py 纯函数 — BC 课程与编排器测试
（plan/bc-cloud-integration.plan.md §2/§6；R3-4 后编排体归 `rl/bc_loop`）。
"""

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

    # ② 语料参数不同 ⇒ 身份必须不同。2026-09-14 前 bc-c4-v3 与 bc-c4 参数逐字相同
    #    （同一语料在 v3 上重跑 ⇒ 同 fp）；此后 bc-c4-v3 按「资源充裕」放大到
    #    300 局 / 150 epoch / auto fire_pos_weight —— 已是**另一份语料**，fp 必须变。
    #    （若将来把两者参数改回全同，本断言应改回 `==`。）
    assert bc_corpus_identity_fp(load_bc_course("bc-c4-v3")) != fp

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
    (shard / "manifest.json").write_text(json.dumps({"stage": 2000, "seed": 7}), encoding="utf-8")
    (d / "bc_s2000_seed8").mkdir()  # 缺 manifest → 不算落盘
    assert landed_pairs(d) == {(2000, 7)}


def test_bc_ledger_completed_rounds(tmp_path: Path) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rl import bc_ledger

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
    assert bc_ledger.completed_rounds(j) == {1}
    assert bc_ledger.completed_rounds(tmp_path / "missing.jsonl") == set()


def test_bc_loop_smoke_overrides() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rl import bc_loop

    c = load_bc_course("bc-c4")
    ov = bc_loop.smoke_overrides(c)
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


def test_bc_loop_finish_all_rounds_writes_run_complete(tmp_path: Path) -> None:
    """2026-09-14 回归：BC 全轮完成的收尾必须同时做两件事 ——

    ① 打含 `ALL DONE` 的**尾行**：console exit-watchdog 用 `tailNormalCompletion`
      （日志尾行 includes('ALL DONE')，大小写敏感）判「正常完成」；原来只打小写
      `all rounds done` ⇒ BC 正常跑完被标「TrainingLoop 意外退出」（实测 07:32）。
    ② 落 `run_complete` 账本事件：console「✅ 训练已完成」横幅的派生源。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rl import bc_loop

    j = tmp_path / "training_log.jsonl"
    msgs: list[str] = []
    bc_loop.finish_all_rounds(j, 3, log=msgs.append)
    assert any("ALL DONE" in m for m in msgs)
    last = [ln for ln in j.read_text(encoding="utf-8").splitlines() if ln.strip()][-1]
    e = json.loads(last)
    assert e["event"] == "run_complete"
    assert e["iter"] == 3 and e["iters"] == 3
    assert "BC" in e["reason"]
    # 2026-09-14：收尾必须提示「云机可释放」（pull 架构下不给无 job 的 worker 发假停机令）
    assert any("云机可释放" in m for m in msgs)


# ------------------------------------------------------- 2026-09-14 可行动项


def test_wait_bc_round_zero_wait_sec_means_unlimited(tmp_path: Path, monkeypatch) -> None:
    """`--wait-sec 0` = 无上限（2026-09-14 用户定案）：排队中的 job 不能被"0 秒"判死。

    背景：BC 时长不可预测（语料 × epochs），而超时中断要重发 job ⇒ bc-resume 按 jid
    存 ⇒ 拿不到旧进度 ⇒ 从头训。所以默认改成无上限，用「无进展告警」兜底。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from remote import hub_client
    from rl import bc_loop

    calls = {"n": 0}

    def fake_request(_base: str, _token: str, path: str, timeout: float = 0.0):
        if path.endswith("/result"):
            calls["n"] += 1
            if calls["n"] < 3:  # 头两轮：仍在排队（404）
                return 404, b"{}"
            return 200, json.dumps({"job_id": "j1", "metrics": {}}).encode("utf-8")
        return 404, b"{}"

    monkeypatch.setattr(hub_client, "_request", fake_request)
    monkeypatch.setattr(bc_loop.time, "sleep", lambda _s: None)  # 免真等 poll_sec

    out = bc_loop.wait_bc_round(
        hub_url="http://hub",
        token="t",
        jid="j1",
        jsonl_path=tmp_path / "training_log.jsonl",
        it=1,
        course=load_bc_course("bc-c4-v3"),
        cfg=None,
        wait_sec=0.0,
        log=lambda _m: None,
    )
    assert out["job_id"] == "j1"
    assert calls["n"] == 3


def test_finish_all_rounds_issues_cloud_halt(tmp_path: Path, monkeypatch) -> None:
    """任务完成后执行停机操作（2026-09-14 用户定案）：向本课 hub 下发 halt=True。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from remote import hub_client
    from rl import bc_loop

    seen: dict = {}

    def fake_halt(base_url: str, token: str, halt: bool, timeout: float = 15.0, log=None) -> bool:
        seen.update(base=base_url, token=token, halt=halt)
        return True

    monkeypatch.setattr(hub_client, "set_cloud_halt", fake_halt)
    msgs: list[str] = []
    bc_loop.finish_all_rounds(
        tmp_path / "training_log.jsonl", 1, hub_url="http://hub", token="t", log=msgs.append
    )
    assert seen == {"base": "http://hub", "token": "t", "halt": True}
    assert any("停机操作已执行" in m for m in msgs)
    assert any("云机可释放" in m for m in msgs)

    # 非 hub 传输（local/push）不得假装停机
    msgs2: list[str] = []
    bc_loop.finish_all_rounds(tmp_path / "l2.jsonl", 1, log=msgs2.append)
    assert any("停机操作跳过" in m for m in msgs2)


def test_bc_run_start_event_is_segmentation_anchor() -> None:
    """2026-09-14 混轮修复：`run_start` 是 console 的**分段锚**。

    同一份 training_log.jsonl 被多轮复用（traj 不变、只换语料口径）⇒ 两轮 bc_epoch
    同挂 it=1 ⇒ console 面板把它们拼成一条曲线（实测上一轮 59 行 + 本轮 150 行）。
    console 侧靠本事件的 `event` 定位最后一段，故字段不能随意改名。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rl import bc_loop

    c = load_bc_course("bc-c4-v3")
    e = bc_loop.bc_run_start_event(c, "bc-c4-v3", run_id="bc-unit-test")
    assert e["event"] == "run_start"
    assert e["runId"] == "bc-unit-test"
    assert e["course"] == "bc-c4-v3"
    assert e["epochs"] == int(c.train.epochs)
    assert e["seed"] == int(c.train.seed)  # R1 两臂的同一口径锚
    assert e["fire_pos_weight"] == c.train.fire_pos_weight
    assert isinstance(e["ts"], float)


def test_bc_job_extra_keeps_auto_fire_pos_weight() -> None:
    """2026-09-14 事故回归：job `extra` 必须**原值直传**课程配置。

    `fire_pos_weight` 曾被写成 `float(course.train.fire_pos_weight)`，而课程值允许
    `"auto"` ⇒ `float("auto")` 抛 ValueError ⇒ BC 采完语料 publish 时直接崩。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rl import bc_loop

    c = load_bc_course("bc-c4-v3")
    ex = bc_loop.bc_job_extra(c, 2)
    assert ex["fire_pos_weight"] == "auto"  # 原值，不被 float 化
    assert ex["train_seed"] == int(c.train.seed)
    assert ex["arch"] == str(c.train.arch)
    assert ex["notes"].endswith("it=2 smoke=False")
    # 数字型配置同样原样透传
    c2 = c.model_copy(update={"train": c.train.model_copy(update={"fire_pos_weight": 3.5})})
    assert bc_loop.bc_job_extra(c2, 1)["fire_pos_weight"] == 3.5


def test_resolve_bc_seed_prefers_course_seed() -> None:
    """R1 前置：BC job 的 `train_seed`（课程 train.seed）必须优先于 per-job 种子。

    旧行为下云端 BC 用 `job_seed(runId, it, init_weights_fp)`，runId 每轮都变 ⇒
    ① 同课程重跑不可复现；② v2/v3 两臂 seed 不同 ⇒ val 划分不同 ⇒ val_loss 不可比。
    键名用 `train_seed`：manifest 里 `seed` 已有 hex per-job 占位的历史口径
    （tests/test_bc_epoch_e2e.py 的 fixture），不复用避免歧义。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from remote.worker import resolve_bc_seed

    base = {"runId": "bc-abc", "it": 1, "init_weights_fp": "bc", "seed": "1a2b3c4d"}
    per_job, src1 = resolve_bc_seed(dict(base))
    assert src1 == "per-job" and isinstance(per_job, int)
    # 同 runId 重发 → 同一种子（D5 幂等语义保持不变）
    assert resolve_bc_seed(dict(base)) == (per_job, "per-job")
    # 显式课程 seed 优先且稳定
    with_seed = dict(base, train_seed=1234)
    assert resolve_bc_seed(with_seed) == (1234, "course")
    assert resolve_bc_seed(dict(with_seed)) == (1234, "course")
    # seed=0 也要被当作"显式指定"（0 是合法种子，不能用 falsy 判断吞掉）
    assert resolve_bc_seed(dict(base, train_seed=0)) == (0, "course")


def test_resolve_fire_pos_weight() -> None:
    """fire 头正例权重解析：auto = 训练集 neg/pos；数字直用；0/None 关闭。

    从 `train.bc_core` 取（2026-09-26）：那是这个纯函数的**家**，`train/bc.py` 只是再导出；
    从 `train.bc` 取会把整个训练器的 torch 拖进本用例（本文件其余用例都不需要 torch）。
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from train.bc_core import resolve_fire_pos_weight

    counts = {"move": {}, "fire": {0: 5969, 1: 473}}
    assert abs(resolve_fire_pos_weight("auto", counts) - 5969 / 473) < 1e-9
    assert resolve_fire_pos_weight("auto", counts) == resolve_fire_pos_weight("AUTO", counts)
    assert resolve_fire_pos_weight(3.5, counts) == 3.5
    # 本机 CLI 路径把配置值 str() 后传参 ⇒ 字符串数字必须能用（"0.0"/"3.5"）
    assert resolve_fire_pos_weight("3.5", counts) == 3.5
    assert resolve_fire_pos_weight("0.0", counts) == 0.0
    assert resolve_fire_pos_weight(0, counts) == 0.0
    assert resolve_fire_pos_weight(None, counts) == 0.0
    with pytest.raises(ValueError):
        resolve_fire_pos_weight("nope", counts)
    # 没有正例时不炸（权重退化为 0 = 关闭）
    assert resolve_fire_pos_weight("auto", {"fire": {0: 10}}) == 0.0


def test_bc_c4_v3_corpus_scaled_up() -> None:
    """2026-09-14：bc-c4-v3 课程按用户指令放大语料与轮次（CPU/GPU 资源充裕）。

    原口径 40 局 × 60 epoch ⇒ kept 29 局 / 6442 样本，70k 参数学生 11 轮进平台。
    """
    c = load_bc_course("bc-c4-v3")
    assert c.corpus.games_per_stage >= 300
    assert c.corpus.seed_rotate >= c.corpus.games_per_stage  # 多轮种子不重叠
    assert c.train.epochs >= 150
    assert str(c.train.fire_pos_weight) == "auto"
    assert c.eval.games_per_stage >= 30
    assert c.train.seed == 1234  # R1 两臂统一口径的锚


# --------------------------------------------------
# 课程 `eval` 块（2026-09-13 BC 每-epoch 评估；2026-09-26 自 test_bc_epoch_resume 分家）
# --------------------------------------------------


def test_bc_course_eval_block_multi_level() -> None:
    from rl.bc_config import load_bc_course

    c = load_bc_course("bc-c4")
    assert c.eval.enabled is True
    assert c.eval.every_epochs == 10
    assert c.eval.games_per_stage == 10
    assert c.eval.levels == ["arena4", "arena6"]  # 多地图

def test_bc_course_eval_block_default_off() -> None:
    from rl.bc_config import load_bc_course

    assert load_bc_course("bc-e2e").eval.enabled is False  # 夹具不配 eval
