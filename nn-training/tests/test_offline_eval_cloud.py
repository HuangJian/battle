"""tests/test_offline_eval_cloud.py —— 云机 A 层评估（`remote/offline_eval.py`）与断点续跑锚点。

用户指令（2026-09-22）三条，本文件各钉一半：

  1. 「battle.offline.ipynb 增加配置项支持是否在云机跑 eval」+「A 层同口径（每 eval_every 轮）」
     ⇒ 语料/行 schema/`wver` 定义都必须与 in-loop **逐位同源**（不是「差不多」）：
     本文件用 in-loop 的同一批纯函数对照（`a_eval_seed_list` / `eval_row` /
     `settle_eval_summary`），并断言账本可配对。
  2. 「云机跑 in-loop eval 时，要像 trainer 一样与下一轮 PPO 并行执行」
     ⇒ `CloudEvalRunner.submit` 必须**立刻返回**（后台线程跑局）、同一时刻只评一轮、
     段末 `drain` 有界收线。这是本文件最要紧的一条：`test_submit_does_not_block_ppo`
     量的是「提交耗时」，一旦哪天有人把它改回同步，这个用例立刻红。
  3. 「再领任务时应传递权重/opt/指标给云机」⇒ hub 递回的锚点由
     `remote.run_loop.apply_resume_overlay` 采纳进产物目录（同轮齐全才认；更旧/指纹不符忽略）。

配合文件：`tests/test_offline_resume_anchor.py`（hub 侧的选轮 + 端点）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import game_watch
from platform_utils import cpu_worker_slots
from remote import offline_eval, serve_pool
from remote.artifacts import ArtifactStore, sha256_bytes, sha256_file
from remote.offline_deliver import OfflineDeliverer
from remote.offline_eval import (
    CLOUD_NODE,
    CloudEvalRunner,
    eval_pairs,
    eval_plan_of,
    run_cloud_eval,
)
from rl.eval_local import a_eval_seed_list


@pytest.fixture(autouse=True)
def _no_serve_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """本文件的用例都把 `run_local_eval_game` 换成了假执行器 ⇒ **谢绝建池**。

    池由 `run_cloud_eval` 建（真进程），而这一层的关切是「语料/落账/收线」，不是执行面；
    不关的话每个用例会起几个真 bun 进程去跑一个假导出器。**池接线本身**由
    `tests/test_offline_eval_pool.py` 专门验（那里用假池，不起进程）。
    """
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")


def _quiet(_msg: str) -> None:
    pass


def _logs() -> tuple[list[str], Callable[[str], None]]:
    out: list[str] = []
    log: Callable[[str], None] = out.append
    return out, log


def _course(**over: object) -> SimpleNamespace:
    base: dict = {
        "eval_stages": "0-1",
        "eval_games_per_stage": 4,
        "eval_every": 2,
        "difficulty": "hard",
        "max_ticks": 900,
        "stage_ids": [0, 1],
        "player": SimpleNamespace(lives=1, level=None),
        "stage_json": lambda _s: None,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ─────────────────────────── 口径：与 in-loop 同源 ───────────────────────────


def test_default_slots_is_the_same_formula_as_rollout(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省并发就是 rollout 的口径 `max(cores − 4, cores × 0.8)`——**不为 rollout 预留**。

    用户 2026-09-22：「rollout 和 eval 是交替进行的，所以不应该为 eval 保留 CPU 核数，
    两者都使用 max(cores − 4, cores × 0.8)；只要留两三个核给数据回传任务就够了」。
    老口径（先扣 `plan.workers` 再卡 64）在 96 核云机上只给 64 = 白扔三成。
    """
    for cores, want in ((96, 92), (40, 36), (16, 12), (8, 6), (4, 3), (1, 1)):
        monkeypatch.setattr(offline_eval.os, "cpu_count", lambda c=cores: c)
        assert offline_eval.default_slots() == want, f"{cores} 核 → {want}"
        assert offline_eval.default_slots() == cpu_worker_slots(cores), "与 rollout 同一口径"
    monkeypatch.setattr(offline_eval.os, "cpu_count", lambda: None)
    assert offline_eval.default_slots() == 1, "读不到核数也要能跑"


def test_eval_plan_reads_the_course_and_gates_on_eval_every() -> None:
    plan = eval_plan_of(_course())
    assert plan.stages == (0, 1) and plan.n_seeds == 4 and plan.eval_every == 2
    assert plan.difficulty == "hard" and plan.max_ticks == 900 and plan.lives == 1
    assert plan.enabled
    assert [plan.due(it) for it in (0, 1, 2, 3, 4)] == [False, False, True, False, True]
    # 没配语料 = 不评（course 缺席同理）——静默「评了 0 局」比不评更坏
    assert not eval_plan_of(_course(eval_games_per_stage=0)).enabled
    assert not eval_plan_of(_course(eval_stages="", stage_ids=[])).enabled
    assert not eval_plan_of(None).enabled
    # `eval_stages` 空 = 与 in-loop 同义：评真实关（课程 stage_ids）
    assert eval_plan_of(_course(eval_stages="")).stages == (0, 1)


def test_eval_pairs_are_the_in_loop_corpus_including_dual_track() -> None:
    """语料必须是 in-loop 的 `a_eval_seed_list`（含双轨锚点+轮转），且逐局同序。"""
    plan = eval_plan_of(_course(eval_stages="0-1", eval_games_per_stage=50))
    for it in (1, 2, 3, 4):
        want = [(s, sd) for s in (0, 1) for sd in a_eval_seed_list(it, 50)]
        assert eval_pairs(plan, it) == want
    # 双轨：it 变化 ⇒ 轮转轨跟着变（锚点那 50 个种子不变）——云机与 in-loop 同一口径
    p1, p2 = {sd for _, sd in eval_pairs(plan, 1)}, {sd for _, sd in eval_pairs(plan, 2)}
    assert p1 != p2
    assert len(p1) == 100, "n_seeds==50 时双轨 = 锚点 50 + 轮转 50"


# ─────────────────────────── 逐局执行（含失败容忍 / 去重） ───────────────────────────


def _ts_root(tmp_path: Path) -> Path:
    ts = tmp_path / "ts"
    (ts / "tools" / "sim").mkdir(parents=True, exist_ok=True)
    (ts / "tools" / "sim" / "export-eval-game.ts").write_text("// fake\n", encoding="utf-8")
    return ts


def _weights(tmp_path: Path, blob: bytes = b'{"w":1}') -> Path:
    p = tmp_path / "weights.json"
    p.write_bytes(blob)
    return p


def test_run_cloud_eval_records_rows_and_settles_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, int]] = []

    def fake_runner(bun, weights, stage, seed, out_dir, max_ticks, difficulty, timeout, wver, **kw):
        calls.append((stage, seed))
        assert kw["cwd"] == str(_ts_root(tmp_path)), "云机评估必须跑在 TS 树根上"
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {
            "win": stage == 0,
            "cleared": True,
            "outcome": "win" if stage == 0 else "loss",
            "kills": 2,
            "ticks": 100,
            "elapsedSec": 0.01,
            "stage": stage,
            "seed": seed,
        }

    monkeypatch.setattr(offline_eval, "run_local_eval_game", fake_runner)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")
    plan = eval_plan_of(_course(eval_stages="0-1", eval_games_per_stage=3, eval_every=1))
    eval_jsonl = tmp_path / "eval_log.jsonl"
    w = _weights(tmp_path)
    logs, log = _logs()
    out = run_cloud_eval(
        plan=plan,
        it=1,
        weights_path=w,
        eval_jsonl=eval_jsonl,
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=_course(eval_stages="0-1", eval_games_per_stage=3, eval_every=1),
        bun="bun",
        slots=4,
        log=log,
    )
    assert out["ran"] and out["games"] == 6 and out["settled"] == 6 and out["failed"] == 0
    assert len(calls) == 6 and len(set(calls)) == 6
    key16 = sha256_file(w)[:16]
    rows = [json.loads(ln) for ln in eval_jsonl.read_text(encoding="utf-8").splitlines()]
    game_rows = [r for r in rows if r["event"] == "eval"]
    summary = [r for r in rows if r["event"] == "eval_summary"]
    assert len(game_rows) == 6 and len(summary) == 1
    assert {r["node"] for r in game_rows} == {CLOUD_NODE}
    assert {r["wver"] for r in game_rows} == {key16}, "wver 必须是权重字节的 sha256（与 in-loop 同定义）"
    assert summary[0]["games"] == 6 and summary[0]["wins"] == 3
    assert summary[0]["nodes"] == {CLOUD_NODE: 6}
    assert summary[0]["iter"] == 1 and summary[0]["wver"] == key16

    # 幂等：同一 wver 再来一次 ⇒ 一局不重跑（账本去重）
    again = run_cloud_eval(
        plan=plan,
        it=1,
        weights_path=w,
        eval_jsonl=eval_jsonl,
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=_course(eval_stages="0-1", eval_games_per_stage=3, eval_every=1),
        bun="bun",
        slots=4,
        log=log,
    )
    assert again["games"] == 6 and again["settled"] == 0 and again["skipped"] == 6


def test_run_cloud_eval_survives_single_game_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单局失败只放弃那一局（响亮记一笔），其余照落账——评估是旁路，绝不拖垮训练。"""

    def flaky(bun, weights, stage, seed, out_dir, max_ticks, difficulty, timeout, wver, **kw):
        if stage == 1:
            raise RuntimeError("rc=1 (boom)")
        return {"win": True, "cleared": True, "outcome": "win", "elapsedSec": 0.01}

    monkeypatch.setattr(offline_eval, "run_local_eval_game", flaky)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")
    course = _course(eval_stages="0-1", eval_games_per_stage=2, eval_every=1)
    eval_jsonl = tmp_path / "eval_log.jsonl"
    logs, log = _logs()
    out = run_cloud_eval(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=eval_jsonl,
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=3,
        log=log,
    )
    assert out["failed"] == 2 and out["settled"] == 2
    assert any("失败" in m for m in logs)
    assert eval_jsonl.exists()


def test_run_cloud_eval_watchdog_caps_and_retries_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """单局看门狗（与 rollout 同一口径）：首次卡住 ⇒ 按 5s 杀、原地重跑（上限 ×4）。

    `game_timeout_sec=0`（= 没配）⇒ 首次 5s / 重试 20s；显式给了 120 ⇒ 每次都是 120。
    """
    timeouts: list[float] = []
    attempts: dict[tuple[int, int], int] = {}

    def flaky(bun, weights, stage, seed, out_dir, max_ticks, difficulty, timeout, wver, **kw):
        timeouts.append(float(timeout))
        key = (stage, seed)
        attempts[key] = attempts.get(key, 0) + 1
        if attempts[key] == 1:  # 第一把卡死（被看门狗杀了）⇒ 原地重跑
            raise subprocess.TimeoutExpired(["bun", "export-eval-game.ts"], timeout)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {"win": True, "cleared": True, "outcome": "win", "elapsedSec": 0.01}

    monkeypatch.setattr(offline_eval, "run_local_eval_game", flaky)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")
    course = _course(eval_stages="0", eval_games_per_stage=1, eval_every=1)
    logs, log = _logs()
    out = run_cloud_eval(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=1,
        game_timeout_sec=0.0,
        log=log,
    )
    assert out["settled"] == 1 and out["failed"] == 0, "重跑成功 ⇒ 这局算落账，不是失败"
    assert timeouts == [game_watch.DEFAULT_GAME_TIMEOUT_SEC, 20.0], timeouts
    assert any("单局重试 2/3" in m and "本次上限 20s" in m for m in logs), logs
    assert any("单局耗时" in m for m in logs), logs
    rows = [json.loads(ln) for ln in (tmp_path / "eval_log.jsonl").read_text().splitlines()]
    assert next(r for r in rows if r["event"] == "eval")["wallSec"] is not None, "两腿同字段可比"

    # 显式给上限 ⇒ 每次尝试都用它（不对重试放大）
    timeouts.clear()
    attempts.clear()
    run_cloud_eval(
        plan=eval_plan_of(course),
        it=2,
        weights_path=_weights(tmp_path, b'{"w":2}'),
        eval_jsonl=tmp_path / "eval_log2.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work2",
        course=course,
        bun="bun",
        slots=1,
        game_timeout_sec=120.0,
        log=log,
    )
    assert timeouts == [120.0, 120.0], timeouts


def test_run_cloud_eval_skips_loudly_when_prerequisites_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有 bun / 没有 TS 树 / 权重不在盘上：记一笔原因、返回 ran=False，**不抛**。"""
    course = _course(eval_stages="0-1", eval_games_per_stage=1, eval_every=1)
    plan = eval_plan_of(course)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "")
    logs, log = _logs()
    out = run_cloud_eval(
        plan=plan,
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        log=log,
    )
    assert not out["ran"] and any("bun" in m for m in logs)
    missing_w = run_cloud_eval(
        plan=plan,
        it=1,
        weights_path=tmp_path / "nope.json",
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        log=log,
    )
    assert not missing_w["ran"] and any("权重不在盘上" in m for m in logs)


# ─────────────────────────── 并行（用户口径的核心） ───────────────────────────


def test_submit_does_not_block_ppo(monkeypatch: pytest.MonkeyPatch) -> None:
    """`submit` 必须立刻返回：后台线程跑局，提交方（下一轮 rollout/PPO）不等它。"""
    started = threading.Event()
    release = threading.Event()

    def slow(**job: object) -> dict:
        started.set()
        release.wait(5)
        return {"ran": True, "it": job.get("it")}

    monkeypatch.setattr(offline_eval, "run_cloud_eval", slow)
    logs, log = _logs()
    runner = CloudEvalRunner(lambda it: {"it": it}, log=log)
    t0 = time.time()
    assert runner.submit(3)
    submit_sec = time.time() - t0
    assert submit_sec < 0.5, f"submit 阻塞了 {submit_sec:.2f}s —— 它就变成了串行 eval"
    assert started.wait(5), "后台线程没有开始跑这一轮"
    assert runner.inflight_it() == 3
    # 在飞时提交下一轮：有界等一小会（这里 handoff 设得很短）后跳过，绝不自旋到死
    runner.handoff_wait_sec = 0.05
    t1 = time.time()
    assert runner.submit(4) is False
    assert time.time() - t1 < 1.0
    assert any("跳过本轮" in m for m in logs)
    release.set()
    assert runner.drain(timeout=5.0) is True
    assert runner.results == [{"ran": True, "it": 3}]


def test_drain_is_bounded_and_reports_overrun(monkeypatch: pytest.MonkeyPatch) -> None:
    """段末收线是**有界**的：跑不完就记一笔 WARN 返回 False（不无限挂住进程）。"""
    release = threading.Event()

    def slow(**_job: object) -> dict:
        release.wait(5)
        return {"ran": True}

    monkeypatch.setattr(offline_eval, "run_cloud_eval", slow)
    logs, log = _logs()
    runner = CloudEvalRunner(lambda it: {"it": it}, log=log)
    assert runner.submit(7)
    t0 = time.time()
    assert runner.drain(timeout=0.2) is False
    assert time.time() - t0 < 2.0
    assert any("WARN" in m and "收线超时" in m for m in logs)
    release.set()
    assert runner.wait_idle(5.0)


def test_maybe_cloud_eval_only_submits_when_due_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remote.run_loop import _maybe_cloud_eval

    submitted: list[int] = []

    class _Runner:
        def submit(self, it: int) -> bool:
            submitted.append(it)
            return True

    class _Plan:
        def due(self, it: int) -> bool:
            return it % 3 == 0

    ctx: Any = SimpleNamespace(
        eval_on_cloud=True, eval_runner=_Runner(), eval_plan=_Plan(), log=_quiet
    )
    for it in range(1, 7):
        _maybe_cloud_eval(ctx, it)
    assert submitted == [3, 6]

    class _Boom:
        def submit(self, _it: int) -> bool:
            raise RuntimeError("boom")

    ctx.eval_runner = _Boom()
    _maybe_cloud_eval(ctx, 9)  # 不抛

    # 关掉开关 / 没装配 ⇒ 一律不动（不 import、不提交）
    ctx.eval_on_cloud = False
    _maybe_cloud_eval(ctx, 12)


# ─────────────────────────── 断点续跑锚点（采纳侧） ───────────────────────────


def _ctx(tmp_path: Path, *, start_it: int = 1, end_it: int = 6):
    from remote.run_loop import RunContext

    plan = {"start_it": start_it, "end_it": end_it, "pair_args": {}}
    manifest = {"runId": "run-resume", "it": start_it, "commit": "c" * 40}
    store = ArtifactStore(tmp_path / "art", run_id="run-resume", log=_quiet)
    store.start(plan, manifest, plan_sha256="x" * 64)
    logs, log = _logs()
    ctx = RunContext(
        plan=plan,
        plan_sha256="x" * 64,
        manifest=manifest,
        store=store,
        work_dir=tmp_path / "work",
        log=log,
    )
    return ctx, logs


def _overlay(tmp_path: Path, *, it: int = 3, blob: bytes = b'{"w":"resumed"}') -> Path:
    d = tmp_path / "resume"
    (d / f"it-{it:03d}").mkdir(parents=True, exist_ok=True)
    (d / f"it-{it:03d}" / "weights.json").write_bytes(blob)
    (d / f"it-{it:03d}" / "opt.tar").write_bytes(b"opt-resumed")
    (d / f"it-{it:03d}" / "row.json").write_text(
        json.dumps({"it": it, "weights_fp": sha256_bytes(blob), "agg": {"kl": 0.01}}),
        encoding="utf-8",
    )
    (d / "resume.json").write_text(
        json.dumps(
            {
                "it": it,
                "run_id": "run-src",
                "source": "backfeed",
                "weights_fp": sha256_bytes(blob),
                "metrics": {},
            }
        ),
        encoding="utf-8",
    )
    return d


def test_resume_overlay_is_adopted_and_advances_the_artifact(tmp_path: Path) -> None:
    from remote.run_loop import apply_resume_overlay

    ctx, logs = _ctx(tmp_path)
    assert apply_resume_overlay(ctx, _overlay(tmp_path, it=3)) == 3
    assert (ctx.store.read_state() or {})["last_it"] == 3
    assert ctx.store.weights_path(3).read_bytes() == b'{"w":"resumed"}'
    assert ctx.store.opt_path(3).read_bytes() == b"opt-resumed"
    assert ctx.last_opt_sha == sha256_bytes(b"opt-resumed"), "Adam 动量必须接着传下去"
    rows = [json.loads(ln) for ln in (ctx.store.root / "metrics.jsonl").read_text().splitlines()]
    assert any(r["it"] == 3 and r.get("agg", {}).get("kl") == 0.01 for r in rows), "指标行也要过来"
    assert any("续跑锚点已采纳" in m for m in logs)

    # 幂等：同一次重领再铺一遍 ⇒ 不动（不重复记账）
    n_before = len(rows)
    assert apply_resume_overlay(ctx, _overlay(tmp_path, it=3)) == 0
    rows2 = [json.loads(ln) for ln in (ctx.store.root / "metrics.jsonl").read_text().splitlines()]
    assert len(rows2) == n_before


def test_resume_overlay_ignores_older_or_broken_anchors(tmp_path: Path) -> None:
    from remote.run_loop import apply_resume_overlay

    # ① 更旧：产物已到 it3，锚点 it1 ⇒ 忽略
    ctx, logs = _ctx(tmp_path)
    ctx.store.checkpoint(3, weights_json=b'{"w":3}', opt_tar=b"o", row={"it": 3})
    assert apply_resume_overlay(ctx, _overlay(tmp_path, it=1)) == 0
    assert (ctx.store.read_state() or {})["last_it"] == 3
    assert any("不新于产物当前进度" in m for m in logs)

    # ② 指纹不符（传输损坏）⇒ 忽略，且**不**把坏字节写进产物
    ctx2, logs2 = _ctx(tmp_path / "b")
    bad = _overlay(tmp_path / "b", it=4, blob=b'{"w":"tampered"}')
    meta = json.loads((bad / "resume.json").read_text(encoding="utf-8"))
    meta["weights_fp"] = "0" * 64
    (bad / "resume.json").write_text(json.dumps(meta), encoding="utf-8")
    assert apply_resume_overlay(ctx2, bad) == 0
    assert not ctx2.store.weights_path(4).exists()
    assert any("指纹不符" in m for m in logs2)

    # ③ 缺 resume.json / 缺权重 ⇒ 忽略（不抛）
    ctx3, logs3 = _ctx(tmp_path / "c")
    assert apply_resume_overlay(ctx3, tmp_path / "c" / "nothing") == 0
    empty = tmp_path / "c" / "resume"
    empty.mkdir(parents=True)
    (empty / "resume.json").write_text(json.dumps({"it": 5, "weights_fp": ""}), encoding="utf-8")
    assert apply_resume_overlay(ctx3, empty) == 0
    assert apply_resume_overlay(ctx3, None) == 0
    assert (ctx3.store.read_state() or {})["last_it"] == 1


# ─────────────────────────── 读数回程：artifacts zip / 补传体 ───────────────────────────


def test_artifacts_zip_carries_the_cloud_eval_ledger(tmp_path: Path) -> None:
    """`eval_log.jsonl` 必须进 artifacts.zip ——「云上评的读数怎么回来」只有这一条路。"""
    ctx, _ = _ctx(tmp_path)
    ctx.store.checkpoint(1, weights_json=b'{"w":1}', opt_tar=b"o", row={"it": 1})
    (ctx.store.root / ArtifactStore.EVAL_LOG_NAME).write_text(
        json.dumps({"event": "eval", "iter": 1, "wver": "a" * 16, "stage": 0, "seed": 1}) + "\n",
        encoding="utf-8",
    )
    ctx.store.finalize(state="complete")
    import zipfile

    with zipfile.ZipFile(ctx.store.root / ArtifactStore.ALL_ZIP) as z:
        assert ArtifactStore.EVAL_LOG_NAME in z.namelist()


def test_backfeed_body_carries_only_this_rounds_eval_rows(tmp_path: Path) -> None:
    d = OfflineDeliverer(
        base_url="", token="", run_id="run-x", artifacts_dir=tmp_path, log=_quiet
    )
    rows = [
        {"event": "eval", "iter": 2, "wver": "a" * 16, "stage": 0, "seed": 1},
        {"event": "eval", "iter": 3, "wver": "a" * 16, "stage": 0, "seed": 1},
        {"event": "eval", "iter": 2, "wver": "a" * 16, "stage": 0, "seed": 2, "source": "batcheval"},
        {"event": "eval_summary", "iter": 2, "wver": "a" * 16},
    ]
    (tmp_path / ArtifactStore.EVAL_LOG_NAME).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    got = d._eval_rows_for(2)
    assert [r["seed"] for r in got] == [1], "只发自家的、本轮的逐局行（source 行与 summary 不随行）"
    assert d._eval_rows_for(9) == []

    # 上界：超出只发前 N 条（宁少不错——体超限会让整趟补传被拒，连权重一起丢）
    many = [
        {"event": "eval", "iter": 5, "wver": "a" * 16, "stage": 0, "seed": i}
        for i in range(d.EVAL_ROWS_CAP + 3)
    ]
    (tmp_path / ArtifactStore.EVAL_LOG_NAME).write_text(
        "\n".join(json.dumps(r) for r in many) + "\n", encoding="utf-8"
    )
    assert len(d._eval_rows_for(5)) == d.EVAL_ROWS_CAP
