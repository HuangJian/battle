"""tests/test_offline_eval_wiring.py —— 离线三件套的「接线」守卫（第 3、4、5 条指令）。

  * 云机评估的**装配与段末收线**（`_setup_cloud_eval` / `_maybe_cloud_eval` / `_close_eval`）：
    开着且课程配了语料 ⇒ 台账里有后台执行者、到点提交、段末 drain；没配语料 ⇒ 不假装评。
  * notebook → `run_loop` 的 argv 形状（`--eval-on-cloud` / `--eval-slots` / `--resume-dir`）
    与续跑锚点的**拉取**（三件不齐就放弃整个锚点，绝不半套）。
  * 人工导入那条回程：`deliver-<课>.zip` 里的 `eval_log.jsonl` 并进课程账本。
  * 多课程（`CFG.course` 列表）的解析口径。

为什么单独一个文件：这些点全是「两个模块之间的缝」，任一缝断开都**不会**让任何单元用例变红
（云机照跑、控制台照显示），只会让「云上评了但读数没回来」或「重领从头再来」这类事
静默发生——正是要钉的地方。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from platform_utils import cpu_worker_slots
from remote import offline_boot, offline_eval
from remote.artifacts import ArtifactStore
from remote.game_watch import DEFAULT_GAME_TIMEOUT_SEC
from remote.hub_server import _HubQueue, _JobStore
from remote.run_loop import (
    _close_eval,
    _maybe_cloud_eval,
    _setup_cloud_eval,
    open_run_context,
    with_rollout_workers,
)

COURSE = "c5-gae"


def _quiet(_msg: str) -> None:
    pass


def _course(**over: object) -> SimpleNamespace:
    base: dict = {
        "eval_stages": "0-1",
        "eval_games_per_stage": 2,
        "eval_every": 2,
        "difficulty": "hard",
        "max_ticks": 900,
        "stage_ids": [0, 1],
        "player": SimpleNamespace(lives=1, level=None),
        "stage_json": lambda _s: None,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ─────────────────────────── 云机评估的装配 / 提交 / 收线 ───────────────────────────


def _run_ctx(
    tmp_path: Path,
    *,
    eval_on_cloud: bool = False,
    course: object = None,
    plan_workers: int = 0,
    eval_slots: int = 0,
    rollout_workers: int = 0,
) -> tuple[Any, list[str]]:
    (tmp_path / "job").mkdir(parents=True, exist_ok=True)
    (tmp_path / "job" / "init_weights.json").write_bytes(b'{"w":0}')
    logs: list[str] = []
    ctx = open_run_context(
        plan={"start_it": 1, "end_it": 5, "pair_args": {}, "workers": plan_workers},
        plan_sha256="x" * 64,
        manifest={"runId": "run-eval", "it": 1, "commit": "c" * 40, "course_fp": "f" * 16},
        job_dir=tmp_path / "job",
        work_dir=tmp_path / "work",
        artifacts_dir=tmp_path / "art",
        eval_on_cloud=eval_on_cloud,
        eval_slots=eval_slots,
        rollout_workers=rollout_workers,
        course=course,
        log=logs.append,
    )
    return ctx, logs


def test_cloud_eval_is_wired_as_a_background_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict] = []

    def fake(**job: object) -> dict:
        calls.append(dict(job))
        return {"ran": True, "it": job["it"]}

    monkeypatch.setattr(offline_eval, "run_cloud_eval", fake)
    course = _course()
    ctx, logs = _run_ctx(tmp_path, eval_on_cloud=True, course=course)
    try:
        _setup_cloud_eval(ctx)
        assert ctx.eval_runner is not None, "开着却没装配执行者 = 静默不评"
        assert any("与下一轮 PPO 并行" in m for m in logs), "启用日志要说清它与 PPO 并行"
        # 不到点不提交；到点才提交（eval_every=2 ⇒ it2、it4）
        _maybe_cloud_eval(ctx, 1)
        _maybe_cloud_eval(ctx, 2)
        _maybe_cloud_eval(ctx, 3)
        assert ctx.eval_runner.wait_idle(10.0), "后台线程没跑完"
        assert [c["it"] for c in calls] == [2]
        assert calls[0]["course"] is course
        assert calls[0]["eval_jsonl"] == ctx.store.root / ArtifactStore.EVAL_LOG_NAME
        assert calls[0]["course_fp"] == "f" * 16
        # 0 = **原样传「没指定」**（不在这里提前解析）：由 `run_cloud_eval` 按 game_watch 解析成
        # 「首次尝试 5s（用户口径：单局 >5s 肯定不正常）、重试 ×4」——提前解析就丢掉「显式 vs
        # 兜底」这个区别，而它决定重试要不要放宽。旧值 900s 会让一个卡住的局占着 slot 15 分钟
        # （本轮 drain 只有 600s ⇒ 整轮读数丢），2026-09-22 收掉。
        assert calls[0]["game_timeout_sec"] == 0.0
        assert DEFAULT_GAME_TIMEOUT_SEC == 5.0
        # 缺省并发在装配时解析成**真实数字**（与 rollout 同一口径），日志里报的也是它
        assert ctx.eval_slots == offline_eval.default_slots() > 0
        assert calls[0]["slots"] == ctx.eval_slots
        assert all("缺省" not in m for m in logs)
        # 该轮的权重快照是**不可变**的 it-NNN/weights.json（不是活指针）
        assert Path(str(calls[0]["weights_path"])).name == "weights.json"
    finally:
        _close_eval(ctx)


def test_default_slots_does_not_reserve_for_the_planned_rollout_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """计划里的 rollout 并行度**不进**缺省公式：两者交替跑，按对方扣一次等于两笔账扣同一份钱

    用户 2026-09-22：「不应该为 eval 保留 CPU 核数，两者都使用 max(cores − 4, cores × 0.8)」。
    """
    monkeypatch.setattr(offline_eval.os, "cpu_count", lambda: 40)
    monkeypatch.setattr(
        offline_eval, "run_cloud_eval", lambda **job: {"ran": True, "it": job["it"]}
    )
    ctx, logs = _run_ctx(
        tmp_path, eval_on_cloud=True, course=_course(), plan_workers=8
    )
    _setup_cloud_eval(ctx)
    assert ctx.eval_slots == cpu_worker_slots(40) == 36, "40 核 → 36（与 rollout 同一口径）"
    # rollout 的并行度仍然报出来（只是不再从公式里扣）：日志是排障时的第一手读数
    assert any(f"rollout 并行 {ctx.rollout_workers}" in m for m in logs)
    _close_eval(ctx)
    # 显式配置优先：给了正数就完全按它（不夹取、不重算）
    ctx2, _ = _run_ctx(tmp_path / "explicit", eval_on_cloud=True, course=_course(), eval_slots=12)
    _setup_cloud_eval(ctx2)
    assert ctx2.eval_slots == 12
    _close_eval(ctx2)


def test_rollout_workers_default_is_the_same_formula_as_eval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rollout 并行度缺省 = `max(cores − 4, cores × 0.8)`（与云机 eval **同一口径**）。

    为什么不能沿用计划里钉着的 `plan.workers`：那是**导出机**的规模（常在 8~16 核的本机导出），
    而整段是在云机（Kaggle TPU 会话 ~96 vCPU）上跑的；两侧还交替跑，没理由互相预留。
    """
    monkeypatch.setattr(offline_eval.os, "cpu_count", lambda: 96)
    ctx, _ = _run_ctx(tmp_path / "auto", plan_workers=8)
    assert ctx.rollout_workers == cpu_worker_slots(96) == 92, "计划里的 8 不参与缺省"
    # 显式给数就完全按它（不夹取、不重算）
    ctx2, _ = _run_ctx(tmp_path / "explicit", plan_workers=8, rollout_workers=7)
    assert ctx2.rollout_workers == 7


def test_with_rollout_workers_replaces_only_the_workers_field() -> None:
    """换并行度不得动摇声明集：`workers` 不进 `data_fp`（它只算 argv 的 stage/seed）。"""
    from remote.protocol import iter_expected_data_fp

    spec = {
        "argv": [["bun", "export.ts", "--stages", "3", "--seeds", "11"]],
        "wver": "w" * 16,
        "workers": 8,
        "game_timeout_sec": 900.0,
        "bun": "bun",
    }
    before = iter_expected_data_fp(spec)
    got = with_rollout_workers(spec, 92)
    assert got["workers"] == 92
    assert got["argv"] == spec["argv"] and got["wver"] == spec["wver"]
    assert iter_expected_data_fp(got) == before, "data_fp 不因并行度而变"
    assert spec["workers"] == 8, "不改原对象（调用方可能还拿着它）"
    # 0/负数 = 不动（online/节点腿沿用计划值）；与计划相同也不重造对象
    assert with_rollout_workers(spec, 0) is spec
    assert with_rollout_workers(spec, 8) is spec


def test_eval_landing_triggers_a_repost_of_that_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评估落账 ⇒ 重投该轮（否则逐局行只能等段末 artifacts zip）。"""
    monkeypatch.setattr(
        offline_eval, "run_cloud_eval", lambda **job: {"ran": True, "it": job["it"], "settled": 2}
    )

    class _Deliverer:
        def __init__(self) -> None:
            self.reposts: list[int] = []

        def submit_eval_round(self, it: int) -> None:
            self.reposts.append(int(it))

    ctx, logs = _run_ctx(tmp_path, eval_on_cloud=True, course=_course(eval_every=1))
    deliv = _Deliverer()
    ctx.deliverer = deliv  # type: ignore[assignment]
    _setup_cloud_eval(ctx)
    _maybe_cloud_eval(ctx, 2)
    assert ctx.eval_runner is not None and ctx.eval_runner.wait_idle(10.0)
    assert deliv.reposts == [2] and any("重投" in m for m in logs)


def test_close_eval_drains_and_is_safe_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = __import__("threading").Event()

    def slow(**_job: object) -> dict:
        release.wait(10)
        return {"ran": True}

    monkeypatch.setattr(offline_eval, "run_cloud_eval", slow)
    ctx, logs = _run_ctx(tmp_path / "on", eval_on_cloud=True, course=_course(eval_every=1))
    _setup_cloud_eval(ctx)
    _maybe_cloud_eval(ctx, 2)
    release.set()
    _close_eval(ctx)
    assert ctx.eval_runner is not None and ctx.eval_runner.results == [{"ran": True}]
    assert any("段末收线" in m for m in logs)

    # 没开评估 / 没装配 ⇒ 空操作（不抛）
    ctx_off, _ = _run_ctx(tmp_path / "off")
    _close_eval(ctx_off)


def test_cloud_eval_not_assembled_without_corpus(tmp_path: Path) -> None:
    ctx, logs = _run_ctx(
        tmp_path / "nocorpus", eval_on_cloud=True, course=_course(eval_games_per_stage=0)
    )
    _setup_cloud_eval(ctx)
    assert ctx.eval_runner is None
    assert any("不会真评" in m for m in logs)


# ─────────────────────────── notebook → run_loop 的 argv ───────────────────────────


def _argv(cfg: dict, tmp_path: Path, resume: Path | None = None) -> list[str]:
    return offline_boot.build_run_argv(
        cfg, tmp_path / "task-c5-gae.zip", tmp_path / "run", "https://hub.example", "tok", resume
    )


def test_argv_carries_cloud_eval_switches(tmp_path: Path) -> None:
    argv = _argv(
        {"course": COURSE, "eval_on_cloud": True, "eval_slots": 6, "eval_game_timeout_sec": 120},
        tmp_path,
    )
    assert "--eval-on-cloud" in argv
    assert argv[argv.index("--eval-slots") + 1] == "6"
    assert argv[argv.index("--eval-game-timeout-sec") + 1] == "120.0"
    # 关着就不带（缺省 = 不评：不能悄悄替用户打开一个要花算力的开关）
    off = _argv({"course": COURSE}, tmp_path)
    assert "--eval-on-cloud" not in off and "--eval-slots" not in off


def test_argv_carries_the_resume_dir_only_when_it_exists(tmp_path: Path) -> None:
    d = tmp_path / "resume"
    d.mkdir()
    argv = _argv({"course": COURSE}, tmp_path, d)
    assert argv[argv.index("--resume-dir") + 1] == str(d)
    assert "--resume-dir" not in _argv({"course": COURSE}, tmp_path, tmp_path / "missing")


def test_fetch_resume_pulls_all_three_parts_or_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三件不齐 ⇒ 放弃整个锚点（不是「有两件也凑合」）：半套锚点比没有更危险。"""
    meta = {
        "course": COURSE,
        "resume": {
            "it": 7,
            "run_id": "run-a",
            "source": "backfeed",
            "weights_fp": "a" * 64,
            "opt_bytes": 3,
        },
    }
    seen: list[str] = []

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def fake_open(req: object, timeout: float = 0) -> _Resp:
        url = getattr(req, "full_url", "")
        seen.append(url)
        if "name=" not in url:
            return _Resp(json.dumps(meta).encode("utf-8"))
        if "name=opt.tar" in url:
            raise RuntimeError("boom")  # 第二件拿不到 ⇒ 整个锚点作废
        return _Resp(b'{"w":7}')

    monkeypatch.setattr(offline_boot, "_build_opener", lambda: SimpleNamespace(open=fake_open))
    logs: list[str] = []
    assert offline_boot.fetch_resume("https://h", "t", COURSE, tmp_path / "r", logs.append) is None
    assert any("放弃该锚点" in m for m in logs)
    assert len(seen) == 3 and "name=weights.json" in seen[1]

    # 顺利路径：三件落盘 + resume.json（含来源，供 run_loop 采纳时打日志）
    def ok_open(req: object, timeout: float = 0) -> _Resp:
        url = getattr(req, "full_url", "")
        if "name=" not in url:
            return _Resp(json.dumps(meta).encode("utf-8"))
        name = url.split("name=")[1]
        return _Resp(b'{"w":7}' if name == "weights.json" else b"x")

    monkeypatch.setattr(offline_boot, "_build_opener", lambda: SimpleNamespace(open=ok_open))
    got = offline_boot.fetch_resume("https://h", "t", COURSE, tmp_path / "ok", logs.append)
    assert got == tmp_path / "ok" and (got / "resume.json").exists()
    assert (got / "it-007" / "weights.json").read_bytes() == b'{"w":7}'
    assert (got / "it-007" / "opt.tar").exists() and (got / "it-007" / "row.json").exists()

    # hub 说没有 ⇒ None（正常应答，不是失败）
    monkeypatch.setattr(
        offline_boot,
        "_build_opener",
        lambda: SimpleNamespace(open=lambda req, timeout=0: _Resp(b'{"course": "c", "resume": null}')),
    )
    assert offline_boot.fetch_resume("https://h", "t", COURSE, tmp_path / "none", logs.append) is None


# ─────────────────────────── 人工导入那条回程 ───────────────────────────


def test_deliver_import_merges_carried_eval_rows_into_the_course_ledger(tmp_path: Path) -> None:
    import zipfile

    from remote.deliver_zip import import_deliver_zip

    src = tmp_path / "deliver-c5-gae.zip"
    rows = [
        {"event": "eval", "iter": 3, "wver": "a" * 16, "stage": 0, "seed": 860001, "node": "cloud"},
        {"event": "eval", "iter": 3, "wver": "a" * 16, "stage": 0, "seed": 860002, "node": "cloud"},
        # summary 也要并：控制台的 eval 列只读它（2026-09-23 修）
        {"event": "eval_summary", "iter": 3, "wver": "a" * 16, "games": 400, "wins": 30},
    ]
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("plan.json", "{}")
        z.writestr("manifest.json", "{}")
        z.writestr("state.json", json.dumps({"run_id": "run-x"}))
        z.writestr(f"{ArtifactStore.IT_PREFIX}003/weights.json", '{"w":3}')
        z.writestr(ArtifactStore.EVAL_LOG_NAME, "\n".join(json.dumps(r) for r in rows) + "\n")

    course_dir = tmp_path / "tmp" / COURSE
    got = import_deliver_zip(src, course_dir / "deliver", course=COURSE, log=_quiet)
    assert (got["eval_rows"], got["eval_summaries"]) == (2, 1), (
        "逐局行与 summary 都要并进课程账本（后者是控制台 eval 列的唯一数据源）"
    )
    ledger = course_dir / "eval_log.jsonl"
    merged = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines()]
    assert [r.get("seed") for r in merged] == [860001, 860002, None]
    assert merged[-1]["event"] == "eval_summary" and merged[-1]["games"] == 400

    # 再导一次（同一个包）⇒ 去重，两类都不会再写
    got2 = import_deliver_zip(src, course_dir / "deliver", course=COURSE, log=_quiet)
    assert (got2["eval_rows"], got2["eval_summaries"]) == (0, 0)
    assert len(ledger.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_deliver_zip_without_eval_log_is_unaffected(tmp_path: Path) -> None:
    import zipfile

    from remote.deliver_zip import import_deliver_zip

    src = tmp_path / "deliver-c5-gae.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr(f"{ArtifactStore.IT_PREFIX}001/weights.json", '{"w":1}')
    course_dir = tmp_path / "tmp" / COURSE
    got = import_deliver_zip(src, course_dir / "deliver", course=COURSE, log=_quiet)
    assert got["eval_rows"] == 0 and not (course_dir / "eval_log.jsonl").exists()


# ─────────────────────────── 多课程（第 4 条） ───────────────────────────


def test_courses_of_accepts_string_list_and_comma_forms() -> None:
    assert offline_boot.courses_of({"course": "c5-gae"}) == ["c5-gae"]
    assert offline_boot.courses_of({"course": ["c5-gae", "c6-gae"]}) == ["c5-gae", "c6-gae"]
    assert offline_boot.courses_of({"course": "c5-gae, c6-gae"}) == ["c5-gae", "c6-gae"]
    # 去重保序（列表即执行顺序）；空白/逗号混写也认
    assert offline_boot.courses_of({"course": ["b", " a", "a", "b"]}) == ["b", "a"]
    for bad_cfg in ({}, {"course": []}, {"course": "  "}):  # type: ignore[var-annotated]
        with pytest.raises(SystemExit):
            offline_boot.courses_of(bad_cfg)  # type: ignore[arg-type]
    for bad in ({"course": "a/b"}, {"course": ["../x"]}, {"course": "a" * 65}):
        with pytest.raises(SystemExit, match="非法"):
            offline_boot.courses_of(bad)


def test_multi_course_gets_its_own_work_dir_per_course(tmp_path: Path) -> None:
    """多课时逐课一层子目录：两门课共用 `<work>/run/` 会让第二门把第一门的产物当续跑点。"""
    cfg = {"work_dir": str(tmp_path / "wd")}
    assert offline_boot.course_work_dir(cfg, "a", multi=False) == tmp_path / "wd"
    assert offline_boot.course_work_dir(cfg, "b", multi=True) == tmp_path / "wd" / "b"
    # 缺省（没给显式 work_dir）逐课一层：单课与多课一致
    plain = {"download_dir": str(tmp_path / "dl")}
    assert offline_boot.course_work_dir(plain, "a", multi=False) == tmp_path / "dl" / "battle-offline" / "a"


# ─────────────────────────── 补传体带上本轮的评估行（一次性接线守卫） ───────────────────────────


def test_backfeed_body_includes_eval_rows_for_the_round(tmp_path: Path) -> None:
    """`_post_artifact` 的体里必须有 `eval_rows` —— 少了它，云上评的读数只能等整段结束。"""
    from remote.offline_deliver import OfflineDeliverer

    art = tmp_path / "art"
    store = ArtifactStore(art, run_id="run-x", log=_quiet)
    store.start({"start_it": 1, "end_it": 3, "pair_args": {}}, {"runId": "run-x"}, plan_sha256="s" * 64)
    store.checkpoint(2, weights_json=b'{"w":2}', opt_tar=b"o", row={"it": 2})
    (art / ArtifactStore.EVAL_LOG_NAME).write_text(
        "\n".join(
            json.dumps(r)
            for r in (
                {"event": "eval", "iter": 2, "wver": "a" * 16, "stage": 0, "seed": 1},
                # summary 一并随体（重投那次才有的那份）：控制台的 eval 列只认它
                {"event": "eval_summary", "iter": 2, "wver": "a" * 16, "games": 4, "wins": 1},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    sent: list[dict] = []

    def opener(_url: str, raw: bytes, _headers: dict, _timeout: float) -> tuple[int, bytes]:
        sent.append(json.loads(raw.decode("utf-8")))
        return 200, b"{}"

    d = OfflineDeliverer(
        base_url="https://h",
        token="t",
        run_id="run-x",
        artifacts_dir=art,
        opener=opener,
        now_fn=time.time,
        log=_quiet,
    )
    assert d._post_artifact(2)
    assert sent and [r["event"] for r in sent[0]["eval_rows"]] == ["eval", "eval_summary"]
    assert sent[0]["it"] == 2 and sent[0]["weights_json"]


def test_deliverer_reposts_a_delivered_round_for_late_eval_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已投递的轮次仍可被**重投**（评估后到的读数）——hub 幂等但会并账。"""
    import threading

    from remote.offline_deliver import OfflineDeliverer

    art = tmp_path / "art"
    store = ArtifactStore(art, run_id="run-x", log=_quiet)
    store.start({"start_it": 1, "end_it": 3, "pair_args": {}}, {"runId": "run-x"}, plan_sha256="s" * 64)
    store.checkpoint(1, weights_json=b'{"w":1}', opt_tar=b"o", row={"it": 1})
    posted: list[int] = []

    def opener(_url: str, raw: bytes, _h: dict, _t: float) -> tuple[int, bytes]:
        posted.append(int(json.loads(raw.decode("utf-8"))["it"]))
        return 200, b"{}"

    def probe(force: bool = False) -> bool:
        return True

    d = OfflineDeliverer(
        base_url="https://h",
        token="t",
        run_id="run-x",
        artifacts_dir=art,
        background=True,
        opener=opener,
        log=_quiet,
    )
    # 探活不是本用例的被测面（不真打网络）；`_thread` 只为了过 `submit_eval_round` 的守卫
    # （「有后台线程才重投」——那条守卫本身由 `test_deliverer_*` 系列覆盖）。
    monkeypatch.setattr(d, "probe", probe)
    monkeypatch.setattr(d, "_thread", threading.current_thread())
    assert d._sync() == 1 and posted == [1], "首投按常规积压走"
    assert d._sync() == 0 and posted == [1], "投过了就不再重复"
    d.submit_eval_round(1)
    assert d._sync() == 1 and posted == [1, 1], "评估落账后必须重投该轮（否则读数等段末）"
    assert d._sync() == 0 and posted == [1, 1], "重投只补一次（幂等，不刷屏）"
