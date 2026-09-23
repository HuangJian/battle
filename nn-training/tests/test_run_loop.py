"""tests/test_run_loop.py —— 半离线自主段（`remote/run_loop.py` + `remote/artifacts.py`）。

用户需求（2026-09-17）：云机从 hub 领到任务（课程 + 初始权重 + 代码）后，**即使 hub 一直
失联**也要能全程自主跑完，并以 Kaggle/Colab 官方方式（工作目录里的产物 zip）交付逐轮权重
与指标。本文件用**注入的 run_job 替身**把整条链钉住（真 run_job 要 torch + 真 rollout，
那部分在 e2e 里）：

  * **交接语义**：本轮（kind=run 的 `it`）已由正常路径跑完（`first_result` = 它的**输出**
    权重），自主段从它接着跑计划里的 it+1..end_it；
  * **逐轮同构**：合成给替身的 job 与 hub 发布的 kind=iter **逐字段同构**（`rollout` 逐局
    argv 与计划对集一一对应、`init_weights_fp` 链、`--wver` = 该轮 init 的 sha），替身还
    自己开 payload 验 `init_weights.json` 的字节——所以「权重真的逐轮传下去了」不是推测；
  * **产物**：逐轮 weights/opt/账本行/state + 收尾 zip，且合并结果**过协议层校验**
    （半离线段在 hub 侧不需要新代码的前提就是这个）；
  * **续跑**：预算到点 / 上限 / 同 job 重领 / 中途失败 —— 四种停机点都能接着跑，且账本
    `it` 唯一（同一轮两行会让曲线彻底读不了）。

另有一条**被本文件抓出来的真缺陷**（`ArtifactStore.start`）：续跑判据曾用调用方传入的
payload 计划 sha，而目录里写盘的是另一份格式 ⇒ 新会话永远认不出旧目录、每次从 `start_it`
重跑。`test_standalone_resume_*` 就是它的回归。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.run_loop as run_loop_mod
from common.protocol import (
    ProtocolError,
    RetryableError,
    encode_opt_tar,
    encode_weights_json,
    normalize_manifest,
    pack_payload,
    unpack_payload,
    validate_result,
)
from remote.artifacts import ArtifactStore, sha256_bytes, sha256_file
from remote.run_loop import (
    _combined,
    open_run_context,
    run_plan_job,
    run_standalone,
    verify_plan_file,
)
from rl.plan import build_plan, dump_plan, pairs_for, planned_iters


def _quiet(_msg: str) -> None:  # 测试日志静音
    """吞掉 run_loop 的日志（只在断言失败时才需要看，那时直接改这里打印）。"""


def _args(**over: object) -> SimpleNamespace:
    """最小 args（与 tests/test_plan.py 同）：默认 rotate 模式，argv 走真 `build_rollout_cmd`。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "0-3",
        "seed_rotate": 0,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _manifest(it: int = 1) -> dict:
    """kind=run 的最小合法 manifest（必填齐全；run 追加项也齐）。"""
    return normalize_manifest(
        {
            "proto": 1,
            "kind": "run",
            "runId": "run-runloop",
            "it": int(it),
            "job_id": "j" * 16,
            "commit": "c" * 40,
            "code_sha256": "z" * 64,
            "course": "// course jsonc\n{}",
            "course_fp": "f" * 64,
            "reward_formula": "score",
            "formula_hash": "h" * 40,
            "metrics_version": 1,
            "gamma": 0.995,
            "lam": 0.95,
            "mode": "per-tick",
            "seed": "s" * 64,
            "epochs": 1,
            "mb": 8,
            "lr": 3e-4,
            "init_weights_fp": "w" * 64,
            "data_fp": "d" * 64,
            "payload_sha256": "p" * 64,
            "ts_code_sha256": "t" * 64,
            "rollout": {
                "argv": [["tools/sim/export-rl-rollout.ts", "--out", "w0"]],
                "wver": "w" * 64,
                "workers": 1,
                "game_timeout_sec": 0.0,
                "bun": "bun",
            },
            "plan_sha256": "q" * 64,
        }
    )


def _report(games: int = 4, win: float = 0.5) -> dict:
    """与真 `combine_reports` 同形状的一轮采集报告（含 dimMeans/scoreStats）。"""
    return {
        "games": games,
        "shards": games,
        "winRate": win,
        "totalSamples": games * 10,
        "totalTicks": games * 100,
        "elapsedSec": 1.5,
        "outcomes": {"win": int(games * win), "loss": games - int(games * win)},
        # 控制台那几列（kills/accuracy/loot 与 score）的数据源。产物行必须带上它们：
        # 不带 ⇒ 导入后那张表在云机腿上恒空（与「本机腿」同一张表逐列不可比）。
        "dimMeans": {"progress": 0.4, "accuracy": 0.12, "loot": 0.3},
        "scoreStats": {"mean": 0.87, "std": 0.05},
    }


def _iter_shaped_result(m: dict, *, weights: bytes, opt: bytes, win: float, marker: str = "") -> dict:
    """与真 worker 同形状的一轮结果（`validate_result` 必须过）。"""
    return {
        "job_id": m["job_id"],
        "data_fp": m["data_fp"],
        "init_weights_fp": m["init_weights_fp"],
        "weights_json": encode_weights_json(weights),
        "opt_tar_b64": encode_opt_tar(opt),
        "agg": {
            "policy": 0.1,
            "value": 0.2,
            "entropy": 0.3,
            "kl": 0.01,
            "mean_ret": float(win),
            "steps": 100,
            "chunks": 2,
        },
        "report": _report(win=win),
        "commit_echo": m["commit"],
        "ppo_sec": 1.25,
        "wire": {"rollout_sec": 0.75, "rollout_bytes": 0, "marker": marker},
    }


class _FakeRunJob:
    """`run_job` 替身：**开 payload 验 init 权重**、记录每次调用、产出确定性结果。

    开 payload 这一步是刻意的：链条最容易错的地方就是「下一轮的 init_weights 到底是谁」。
    替身把它从 preloaded payload 里解出来再对指纹，所以断言的是真字节而不是自报值。
    """

    def __init__(self, tmp_path: Path, *, fail_at: int | None = None) -> None:
        self.tmp = tmp_path
        self.fail_at = fail_at
        self.calls: list[dict] = []

    def __call__(self, base_url: str, token: str, job: dict, **kw: object) -> dict:
        m = normalize_manifest(job["manifest"])
        payload = kw["preloaded"]
        assert isinstance(payload, dict)
        raw = payload["payload_zip"]
        assert isinstance(raw, bytes)
        zip_path = self.tmp / f"payload-{len(self.calls)}.tar.xz"
        zip_path.write_bytes(raw)
        dest = self.tmp / f"unpack-{len(self.calls)}"
        dest.mkdir(parents=True, exist_ok=True)
        unpack_payload(zip_path, dest)
        init = (dest / "init_weights.json").read_bytes()
        assert sha256_bytes(init) == m["init_weights_fp"], "payload 的 init 权重与清单不符"
        if self.fail_at is not None and int(m["it"]) == self.fail_at:
            self.calls.append({"manifest": m, "init": init, "weights": b""})
            raise RetryableError(f"fake: it{m['it']} 单局超时（模拟瞬时失败）")
        weights = json.dumps({"it": m["it"], "w": len(self.calls)}).encode("utf-8")
        self.calls.append({"manifest": m, "init": init, "weights": weights})
        return _iter_shaped_result(
            m, weights=weights, opt=f"opt-{m['it']}".encode(), win=0.5 + 0.01 * int(m["it"])
        )


ANCHOR_IN = b'{"anchor":true}'  # job payload 里那份（= 本轮 it 的**输入**权重）
ANCHOR_OUT = b'{"it":1,"w":0}'  # 本轮 it 的**输出**权重（first_result）


def _prepare(tmp_path: Path, *, iters: int = 4, start_it: int = 1):
    """造一个 kind=run 的交接现场：job 目录（payload 权重 + plan.json）+ 本轮结果。"""
    plan = build_plan(
        _args(), it=start_it, iters_total=iters - 1 + start_it, rotate_seed=4242, log=_quiet
    )
    m = _manifest(it=start_it)
    m["plan_sha256"] = sha256_bytes(dump_plan(plan))
    m = normalize_manifest(m)
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "init_weights.json").write_bytes(ANCHOR_IN)
    (job_dir / "plan.json").write_bytes(dump_plan(plan))
    first = _iter_shaped_result(
        m, weights=ANCHOR_OUT, opt=b"opt-anchor", win=0.4, marker="anchor"
    )
    return plan, m, job_dir, first


def _run(
    tmp_path: Path,
    *,
    iters: int = 4,
    start_it: int = 1,
    max_iters: int = 0,
    budget_sec: float = 0.0,
):
    plan, m, job_dir, first = _prepare(tmp_path, iters=iters, start_it=start_it)
    fake = _FakeRunJob(tmp_path)
    result = run_plan_job(
        job_id=m["job_id"],
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp_path / "work",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        artifacts_dir=tmp_path / "art",
        run_job_fn=fake,
        max_iters=max_iters,
        budget_sec=budget_sec,
        log=_quiet,
    )
    return plan, m, fake, result, tmp_path / "art"


def _warm_code_cache(tmp_path: Path) -> Path:
    """预热内容寻址代码缓存（`code_cache/<manifest.code_sha256>/`）。

    standalone 入口要求「代码可用」——要么产物里有 code.zip（全离线任务包），要么缓存已命中
    （同一台机器上先跑过一段）。这些用例只关心段/续跑语义，给它暖缓存即可（manifest 的
    code_sha256 是 `"z"*64`）。
    """
    root = tmp_path / "code_cache"
    (root / ("z" * 64)).mkdir(parents=True, exist_ok=True)
    return root


def _ledger(art: Path) -> list[dict]:
    p = art / ArtifactStore.METRICS_NAME
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ────────────────────────── ★2026-09-22 离线整段 course 接线回归 ──────────────────────────

_COURSE_FIXTURE = (
    '// 课程快照夹具（与 import_bundle 落盘的 course.jsonc 同形状）\n'
    '{\n'
    '  "name": "x20-fixture",\n'
    '  "stages": "0-3",\n'
    '  "max_ticks": 700,\n'
    '  "reward": {"formula": "score"}\n'
    '}\n'
)


def test_run_standalone_loads_course_snapshot_into_iter_spec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★2026-09-22 事故回归：`run_standalone`（全离线腿）必须把产物 `course.jsonc` 快照
    加载成 `CourseConfig` 传进 `iter_spec`。

    此前它从不传 course ⇒ `iter_spec` 里 `stage_json_of(course, stage)` 拿不到自定义关
    （ladder 2000+）⇒ `retarget_argv` 把计划里的 `--stage-json` 整对删掉 ⇒ 导出器解析
    stage 失败 → **空局**（0 samples、rc=0、零 shard）→ 整段 rollout 被误报成环境/写盘
    问题（半离线 worker 侧已传 course：worker.py run_plan_job(course=course)，只有这条漏）。
    """
    plan, m, job_dir, first = _prepare(tmp_path, iters=3, start_it=1)
    art = tmp_path / "art"
    # 先跑一段（anchor + 1 轮），让产物目录成形；再续跑就轮到 run_standalone 接管
    run_plan_job(
        job_id=m["job_id"],
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp_path / "work",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        artifacts_dir=art,
        run_job_fn=_FakeRunJob(tmp_path),
        max_iters=1,
        log=_quiet,
    )
    # 产物目录放课程快照（与 import_bundle 落盘同名同路径）
    (art / "course.jsonc").write_text(_COURSE_FIXTURE, encoding="utf-8")
    seen: dict[str, Any] = {"calls": 0}
    real_iter_spec = run_loop_mod.iter_spec

    def spy(*a: Any, **kw: Any) -> Any:
        seen["calls"] = int(seen["calls"]) + 1
        seen["course"] = kw.get("course")
        return real_iter_spec(*a, **kw)

    monkeypatch.setattr(run_loop_mod, "iter_spec", spy)
    run_standalone(
        artifacts_dir=art,
        max_iters=0,
        code_cache_dir=_warm_code_cache(tmp_path),
        run_job_fn=_FakeRunJob(tmp_path),
        log=_quiet,
    )
    assert (
        seen["course"] is not None
    ), f"iter_spec 必须拿到 CourseConfig（calls={seen['calls']}，course.jsonc 存在={(art / 'course.jsonc').exists()}）——缺它 --stage-json 被删、出空局"
    assert getattr(seen["course"], "name", None) == "x20-fixture"


# ────────────────────────── 链条 + 产物 ──────────────────────────


def test_chain_runs_whole_plan_and_writes_artifacts(tmp_path: Path) -> None:
    """计划区间全跑完：逐轮同构 job（指纹链 + 逐局 argv）、逐轮产物、合并结果过协议校验。"""
    plan, m, fake, result, art = _run(tmp_path)
    want = planned_iters(plan)
    assert [c["manifest"]["it"] for c in fake.calls] == want
    # 逐轮 manifest 与 kind=iter 同构：kind / argv 条数 / 逐局 stage+seed / wver
    for c in fake.calls:
        mm = c["manifest"]
        pairs = pairs_for(plan, mm["it"])
        assert mm["kind"] == "iter"
        assert len(mm["rollout"]["argv"]) == len(pairs)
        assert mm["rollout"]["wver"] == mm["init_weights_fp"]
        stages = [int(a[a.index("--stages") + 1]) for a in mm["rollout"]["argv"]]
        seeds = [int(a[a.index("--seeds") + 1]) for a in mm["rollout"]["argv"]]
        assert list(zip(stages, seeds, strict=True)) == [(int(s), int(d)) for s, d in pairs]
        assert all(a[0] == "tools/sim/export-rl-rollout.ts" for a in mm["rollout"]["argv"])
    # 权重链：第 1 轮的 init = 本轮（job 自己的 it）输出；此后逐轮 = 上一轮输出
    assert fake.calls[0]["init"] == ANCHOR_OUT
    for prev, cur in zip(fake.calls, fake.calls[1:], strict=False):
        assert cur["init"] == prev["weights"]
        assert cur["manifest"]["init_weights_fp"] == sha256_bytes(prev["weights"])
    # 产物：逐轮 weights/opt + 账本 + state + README/LATEST/全量包
    store = ArtifactStore(art, run_id="run-runloop")
    for it in [plan["start_it"], *want]:
        assert store.weights_path(it).exists() and store.opt_path(it).exists()
    assert store.weights_path(plan["start_it"]).read_bytes() == ANCHOR_OUT  # 被本轮输出覆盖
    rows = _ledger(art)
    assert [r["it"] for r in rows] == [plan["start_it"], *want]  # 账本 it 唯一
    assert rows[0]["phase"] == "anchor"  # 起点行（来自本轮结果）
    # 逐维度/分数统计要进产物行（2026-09-22）：控制台的 kills/accuracy/loot 与 score 列读它，
    # 而导入的离线产物腿要靠这行把同一批列点起来（不带就恒空）。
    assert rows[1]["report"]["dimMeans"] == {"progress": 0.4, "accuracy": 0.12, "loot": 0.3}
    assert rows[1]["report"]["scoreStats"]["mean"] == 0.87
    assert store.dir_for(2).name == "it-002"  # 人肉翻看友好
    st = json.loads((art / ArtifactStore.STATE_NAME).read_text(encoding="utf-8"))
    assert (st["state"], st["last_it"]) == ("complete", want[-1])
    assert st["plan_sha256"] == sha256_file(art / ArtifactStore.PLAN_NAME)  # 自描述（续跑判据）
    for name in (
        ArtifactStore.PLAN_NAME,
        ArtifactStore.MANIFEST_NAME,
        ArtifactStore.README_NAME,
        ArtifactStore.ALL_ZIP,
        ArtifactStore.LATEST_ZIP,
    ):
        assert (art / name).exists(), name
    # 合并结果：末轮形状 + 明细；**过协议层校验**是它能直接走 hub 落位链的前提
    assert result["it_end"] == want[-1]
    # `iters` = 本段覆盖的**每一轮**，含本 job 自己那一轮（它也是一次真采集，明细必须带上）
    assert [r["it"] for r in result["iters"]] == [plan["start_it"], *want]
    assert result["iters"][0]["report"]["winRate"] == pytest.approx(0.4)  # 本轮的采集口径
    assert result["iters"][-1]["weights_fp"] == sha256_file(store.weights_path(want[-1]))
    assert result["agg"]["mean_ret"] == pytest.approx(0.5 + 0.01 * want[-1])
    assert result["report"]["games"] == 4
    assert result["run_state"] == "complete"
    assert result["artifacts"]["dir"] == str(art)
    validate_result(result, m, commit_echo_must_match=False)


def test_iter_shaped_job_passes_protocol_validation(tmp_path: Path) -> None:
    """合成给节点的 job 自己能过 `normalize_manifest`（argv 白名单/相对路径/逐局 stage+seed）。"""
    _plan, _m, fake, _r, _art = _run(tmp_path, iters=3)
    for c in fake.calls:
        mm = normalize_manifest(c["manifest"])  # validate_rollout_spec 在这里面
        assert mm["rollout"]["bun"] and mm["rollout"]["argv"]


def test_combined_rejects_result_without_any_report(tmp_path: Path) -> None:
    """没有一轮真采集报告 = 不是成功结果（`iters` 不得用占位凑数）。"""
    _plan, m, job_dir, _first = _prepare(tmp_path, iters=2)
    store = ArtifactStore(tmp_path / "art2", run_id="run-runloop")
    store.start(
        build_plan(_args(), it=1, iters_total=2, rotate_seed=1, log=_quiet),
        m,
        plan_sha256=m["plan_sha256"],
    )
    ctx = open_run_context(
        plan=build_plan(_args(), it=1, iters_total=2, rotate_seed=1, log=_quiet),
        plan_sha256=m["plan_sha256"],
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp_path / "w2",
        artifacts_dir=tmp_path / "art2",
        log=_quiet,
    )
    with pytest.raises(ProtocolError) as ei:
        _combined(ctx, last_it=1, session=[], state="noop")
    assert "iters 为空" in str(ei.value)


# ────────────────────────── 预算 / 上限 / 续跑 ──────────────────────────


def test_budget_stops_before_starting_next_iteration(tmp_path: Path) -> None:
    """预算到点：在下一轮**开始之前**干净停机（产物自洽），再跑一次接着跑完。"""
    plan, _m, fake, result, art = _run(tmp_path, budget_sec=1e-9)
    assert fake.calls == []
    st = json.loads((art / ArtifactStore.STATE_NAME).read_text(encoding="utf-8"))
    assert (st["state"], st["last_it"]) == ("budget", plan["start_it"])
    assert result["run_state"] == "budget"
    assert [r["it"] for r in result["iters"]] == [plan["start_it"]]  # 只有本轮（锚点）
    fake2 = _FakeRunJob(tmp_path)
    res2 = run_standalone(
        artifacts_dir=art,
        run_job_fn=fake2,
        device="cpu",
        code_cache_dir=_warm_code_cache(tmp_path),
        log=_quiet,
    )
    assert res2["it_end"] == plan["end_it"]
    assert [c["manifest"]["it"] for c in fake2.calls] == planned_iters(plan)
    assert fake2.calls[0]["init"] == ANCHOR_OUT  # 起点权重 = 本轮输出（不是重跑本轮）


def test_max_iters_caps_single_invocation(tmp_path: Path) -> None:
    """`--run-max-iters`：本次只跑 N 轮（计划仍写到末尾，下次接着跑）。"""
    plan, _m, fake, result, art = _run(tmp_path, max_iters=2)
    want = planned_iters(plan)
    assert [c["manifest"]["it"] for c in fake.calls] == want[:2]
    assert result["it_end"] == want[1]
    assert json.loads((art / ArtifactStore.STATE_NAME).read_text())["last_it"] == want[1]


def test_standalone_resume_continues_without_duplicate_rows(tmp_path: Path) -> None:
    """**续跑（无 hub）**：从产物接上，不重跑也不重复记账（这条就是 start() 判据的回归）。"""
    plan, _m, _fake, _result, art = _run(tmp_path, max_iters=2)
    before = _ledger(art)
    fake2 = _FakeRunJob(tmp_path)
    res = run_standalone(
        artifacts_dir=art,
        run_job_fn=fake2,
        device="cpu",
        code_cache_dir=_warm_code_cache(tmp_path),
        log=_quiet,
    )
    assert [c["manifest"]["it"] for c in fake2.calls] == planned_iters(plan)[2:]
    after = _ledger(art)
    assert [r["it"] for r in after] == [plan["start_it"], *planned_iters(plan)]
    assert len(after) == len(before) + len(planned_iters(plan)) - 2
    assert res["it_end"] == plan["end_it"]
    # 续跑后状态仍在 complete（不是"新段"）
    assert json.loads((art / ArtifactStore.STATE_NAME).read_text())["state"] == "complete"


def test_reclaim_skips_iterations_already_in_artifacts(tmp_path: Path) -> None:
    """同 job 重领（产物跑到一半被回收）：从产物接上，锚点轮不重复记账。"""
    plan, m, _fake, _r, art = _run(tmp_path, max_iters=2)
    done = planned_iters(plan)[:2]
    job_dir = tmp_path / "job"
    first = _iter_shaped_result(
        m, weights=(art / f"it-{done[-1]:03d}" / "weights.json").read_bytes(), opt=b"opt-anchor", win=0.4
    )
    fake2 = _FakeRunJob(tmp_path)
    res = run_plan_job(
        job_id=m["job_id"],
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp_path / "work",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        artifacts_dir=art,
        run_job_fn=fake2,
        log=_quiet,
    )
    assert [c["manifest"]["it"] for c in fake2.calls] == planned_iters(plan)[len(done) :]
    assert [r["it"] for r in _ledger(art)] == [plan["start_it"], *planned_iters(plan)]
    assert res["it_end"] == plan["end_it"]


def test_iteration_failure_finalizes_artifacts_and_stays_resumable(tmp_path: Path) -> None:
    """中途失败：产物收尾（state=failed）+ 已跑完的轮次都在 —— 修好后接着跑，不重来。"""
    plan, m, job_dir, first = _prepare(tmp_path, iters=4)
    art = tmp_path / "artf"
    bad = _FakeRunJob(tmp_path, fail_at=3)
    with pytest.raises(RetryableError):
        run_plan_job(
            job_id=m["job_id"],
            manifest=m,
            job_dir=job_dir,
            work_dir=tmp_path / "work",
            plan=plan,
            plan_sha256=m["plan_sha256"],
            first_result=first,
            artifacts_dir=art,
            run_job_fn=bad,
            log=_quiet,
        )
    assert [c["manifest"]["it"] for c in bad.calls] == [2, 3, 3, 3]  # it2 一次 + it3 重试 3 次
    st = json.loads((art / ArtifactStore.STATE_NAME).read_text(encoding="utf-8"))
    assert st["state"] == "failed" and st["last_it"] == 2
    assert (art / ArtifactStore.ALL_ZIP).exists()  # 失败也收尾（否则人拿不到中间产物）
    assert ArtifactStore(art, run_id="run-runloop").weights_path(2).exists()
    good = _FakeRunJob(tmp_path)
    res = run_standalone(
        artifacts_dir=art,
        run_job_fn=good,
        device="cpu",
        code_cache_dir=_warm_code_cache(tmp_path),
        log=_quiet,
    )
    assert [c["manifest"]["it"] for c in good.calls] == [3, 4]
    assert res["it_end"] == 4
    assert [r["it"] for r in _ledger(art)] == [1, 2, 3, 4]


# ────────────────────────── 计划交接的三道门 ──────────────────────────


def test_verify_plan_file_rejects_tampered_or_mismatched(tmp_path: Path) -> None:
    """sha / pairs_fp / start_it 三道具名成立：任一不符都在**跑第一局之前**响。"""
    plan = build_plan(_args(), it=2, iters_total=5, rotate_seed=77, log=_quiet)
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True, exist_ok=True)
    raw = dump_plan(plan)
    (job_dir / "plan.json").write_bytes(raw)
    good = _manifest(it=2)
    good["plan_sha256"] = sha256_bytes(raw)
    good = normalize_manifest(good)
    got, sha = verify_plan_file(job_dir, good, log=_quiet)
    assert got["end_it"] == plan["end_it"] and sha == sha256_bytes(raw)

    # ① sha 不符（payload 损坏/串包）
    with pytest.raises(ProtocolError) as ei:
        verify_plan_file(job_dir, {**good, "plan_sha256": "0" * 64}, log=_quiet)
    assert "plan.json sha256" in str(ei.value)

    # ② 全段对集指纹不符（pair_args 与 hub 侧不一致 / build_pairs 漂移）
    tampered = {**plan, "pairs_fp": "1" * 64}
    (job_dir / "plan.json").write_bytes(dump_plan(tampered))
    with pytest.raises(ProtocolError) as ei2:
        verify_plan_file(
            job_dir, {**good, "plan_sha256": sha256_bytes(dump_plan(tampered))}, log=_quiet
        )
    assert "对集指纹不符" in str(ei2.value)

    # ③ 计划与 job 不是同一轮的交接
    (job_dir / "plan.json").write_bytes(raw)
    with pytest.raises(ProtocolError) as ei3:
        verify_plan_file(job_dir, {**good, "it": 3}, log=_quiet)
    assert "start_it" in str(ei3.value)

    # ④ 缺计划文件（手工递送时最容易漏的一样）
    (job_dir / "plan.json").unlink()
    with pytest.raises(ProtocolError) as ei4:
        verify_plan_file(job_dir, good, log=_quiet)
    assert "plan.json" in str(ei4.value)


def test_seed_checkpoint_requires_payload_weights(tmp_path: Path) -> None:
    """缺起点权重 ⇒ 拒收（产物目录不能从「不存在的起点」上自洽）。"""
    plan, m, job_dir, _first = _prepare(tmp_path, iters=2)
    (job_dir / "init_weights.json").unlink()
    with pytest.raises(ProtocolError) as ei:
        open_run_context(
            plan=plan,
            plan_sha256=m["plan_sha256"],
            manifest=m,
            job_dir=job_dir,
            work_dir=tmp_path / "w3",
            artifacts_dir=tmp_path / "art3",
            log=_quiet,
        )
    assert "起点权重" in str(ei.value)


def test_pack_payload_with_extra_files_roundtrip(tmp_path: Path) -> None:
    """`pack_payload(extra_files=…)`：额外文件落归档**根**（逐轮 payload 靠它带 init 权重）。"""
    p = tmp_path / "init_weights.json"
    p.write_bytes(ANCHOR_IN)
    zip_path = tmp_path / "payload.tar.xz"
    pack_payload([], {}, zip_path, extra_files=[p])
    dest = tmp_path / "dest"
    dest.mkdir()
    _m, shards = unpack_payload(zip_path, dest)
    assert shards == []
    assert (dest / "init_weights.json").read_bytes() == ANCHOR_IN
