"""tests/worker/test_iter_topup.py —— 云机侧**有界多批补差**（plan/rollout-stage-balance §4.4）。

钉住四件事（每条都是一个「静默失败」形态的对应抗体）：

  1. **逐位对账没被放宽**：每一批的 `data_fp(实产) == iter_expected_data_fp(该批 spec)`
     ——补差批有**自己的**声明集（评审 P0-3 的替代方案：不放宽 `verify_shards`）；
  2. **同一条连续流续抽**：首批 ∪ 补差批 = 该关连续流的前缀 `[0, K_s)`（不重抽、不漏号），
     `--out` 全局续号且两批不相交（`data_fp` 的条目含目录名，重号就是假账）；
  3. **每关模板取自本关**：`--stage-json` 不被跨关覆盖/删除（自定义关课程）；
  4. **缺口/浪费分开记账**：触 `game_cap` 与批数用尽仍有缺口 ⇒ `volumeTopup` 里可见，不静默；
  5. **补差批按机器宽度铺满**（`plan/rollout-topup-fastpath.plan.md` §3.3，2026-10-11）：一批
     = `width` 局均分（不再是缺口方程），批与机器宽度同量级；宽度的来路 = 调用方给的
     首批实际 workers → `spec["workers"]` → 首批 argv 条数。

假 `run_fn` 替身（不跑 bun）：按 `--out` 造 `rl_s{stage}_seed{seed}/manifest.json`，样本数由
用例给定（**产出集**口径）。`iter_rollout._exec_argv` 只把 `--out`/`--weights` 绝化成
「传入 job_dir 之下」⇒ 补差批跑在 `job_dir/topup{k}/` 就够了（权重文件由驱动拷进去）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.errors import ProtocolError
from common.protocol import data_fp, iter_expected_data_fp
from worker.iter_topup import (
    _flag_value,
    _resolve_width,
    merge_iter_reports,
    next_batch_spec,
    topup_rollout,
)
from worker.plan import build_plan, iter_spec, pairs_for
from worker.volume_quota import stage_seeds
from worker.volume_waves import initial_wave_pairs_by_stage, volume_block

STAGES = [2000, 2001]
TARGET = 4000
QUOTA = 2000  # ceil(4000/2)
ESTS = {2000: 200, 2001: 100}
#: 局均样本（替身用）：与 est 相同 ⇒ 实测均值 == 估计 ⇒ 期望值可以手算。
SAMPLES = {2000: 200, 2001: 100}
ROTATE = 20261012
#: 协议白名单里的导出器脚本（自造 spec 必须用它，否则 `validate_rollout_spec` 拒收）。
SCRIPT = "tools/sim/export-rl-rollout.ts"


def _args(**over: object) -> SimpleNamespace:
    """与 tests/worker/test_volume_plan_block.py 同形的最小 args。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "2000-2001",
        "seed_rotate": 150,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
        "mode": "per-tick",
        "target_transitions": TARGET,
        "est_samples_per_game": 150,
        "max_games_per_stage": 0,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _FakeRunner:
    """替身 `run_iter_rollout`：按 argv 造 shard 目录 + 轮报（够驱动用）。"""

    def __init__(self, samples: dict[int, int] = SAMPLES) -> None:
        self.samples = dict(samples)
        self.calls: list[dict] = []

    def __call__(
        self, job_dir, spec, *, ts_dir=None, log=lambda msg: None, ctx=None
    ) -> dict:
        self.calls.append(
            {
                "job_dir": str(job_dir),
                "spec": spec,
                "rows": [list(r) for r in spec["argv"]],
                "ctx": ctx,
            }
        )
        dirs: list[str] = []
        per_game: list[dict] = []
        total = 0
        for row in spec["argv"]:
            stage = int(_flag_value(list(row), "--stages"))
            seed = int(_flag_value(list(row), "--seeds"))
            out = _flag_value(list(row), "--out")
            n = int(self.samples[stage])
            # 逐局画像：字段与磁盘单局 manifest **逐字同名**（`stage`/`nSamples`）——§3.4 之后
            # 补差的账就吃它（不再扫盘读同一批 manifest），所以替身也得照真形状给。
            per_game.append({"stage": stage, "seed": seed, "nSamples": n})
            d = Path(job_dir) / out / f"rl_s{stage}_seed{seed}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text(
                json.dumps(
                    {
                        "stage": stage,
                        "seed": seed,
                        "nSamples": n,
                        "wver": str(spec.get("wver") or ""),
                        "course_fp": "cfp",
                        "corpus_fp": "xfp",
                    }
                ),
                encoding="utf-8",
            )
            dirs.append(str(d))
            total += n
        report = {
            "games": len(spec["argv"]),
            "winRate": 0.5,
            "outcomes": {"stage_clear": len(spec["argv"])},
            "totalSamples": total,
            "totalTicks": total * 3,
            "scoreList": [0.1] * len(spec["argv"]),
            "dimLists": {},
            "shards": len(dirs),
            "elapsedSec": 0.5,
            "perGame": per_game,
            "perGameSecs": [0.5] * len(dirs),
            "rolloutSrc": "node",
        }
        return {
            "report": report,
            "shard_dirs": dirs,
            "rollout_sec": 0.5,
            "bun_version": "1.0.0-fake",
            "game_secs": [0.5] * len(dirs),
            "bun": "bun",
            "workers": 1,
            "serve_pool": None,
        }


def _plan_and_spec(job_dir: Path, *, samples: dict[int, int] = SAMPLES, **arg_over: object) -> dict:
    """真链路造 plan + 首批 spec：`volume_block` → `build_plan` → `pairs_for` → `iter_spec`。"""
    args = _args(**arg_over)
    block = volume_block(
        args,
        est_samples_per_game=150,
        ests_by_stage=ESTS,
        topup={"max_games_per_stage": int(getattr(args, "max_games_per_stage", 0) or 0)},
    )
    assert block is not None
    plan = build_plan(args, it=6, iters_total=10, rotate_seed=ROTATE, volume=block)
    pairs = pairs_for(plan, 7)
    spec = iter_spec(plan, 7, pairs, wver="wver-x")
    return spec


def _seed_pairs(dirs: list[str]) -> dict[int, list[int]]:
    from common.protocol import parse_shard_name

    out: dict[int, list[int]] = {}
    for d in dirs:
        parsed = parse_shard_name(Path(d).name)
        assert parsed is not None
        stage, seed = parsed
        out.setdefault(int(stage), []).append(int(seed))
    for v in out.values():
        v.sort()
    return out


def test_ctx_is_forwarded_to_every_batch(tmp_path: Path) -> None:
    """`ctx` 必须透传给每一批（plan §3.2）：否则补差批又各自重建壳子（
    探针 / TS 树 / **长驻池 94 个冷启动**）——现场最贵的那一段。

    `None` 也要能跑（老调用逐字不变）。
    """
    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(job)
    sentinel = object()
    run = _FakeRunner()
    info1 = run(job, spec, ts_dir=job)
    topup_rollout(
        job_dir=job,
        spec=spec,
        shard_dirs=list(info1["shard_dirs"]),
        run_fn=run,
        log=lambda m: None,
        width=4,
        ctx=sentinel,
    )
    assert run.calls[1:], "至少跑了一批补差"
    assert all(c["ctx"] is sentinel for c in run.calls[1:]), "每一批都要把 ctx 传下去"


def test_batch_totals_from_the_portrait_equal_the_disk_scan(tmp_path: Path) -> None:
    """§3.4 等价性：逐局画像与磁盘单局 manifest 两条路算出**同一份**账。

    这是这个优化的全部风险面：画像行少一个字段（或少一行）就静默少算样本 ⇒ 补差批会多跑或少跑。
    所以口径不是「画像够用」而是「两者逐位相等」——同一批产出、两条路各自算一遍。
    """
    from worker.iter_rollout import collect_shard_manifests
    from worker.volume_alloc import stage_totals

    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(job)
    info = _FakeRunner()(job, spec, ts_dir=job)

    rows = info["report"]["perGame"]
    disk = collect_shard_manifests([Path(d) for d in info["shard_dirs"]])
    assert stage_totals(rows) == stage_totals(disk)
    # 也钉住「不是两个空字典相等」：真有两关的账。
    assert stage_totals(rows)[1] == {2000: 9, 2001: 18}


def test_topup_batches_do_not_rescan_the_same_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.4 行为面：补差批不再为记账**重扫**一批刚读过的 manifest。

    扫盘次数 = 1（首批基线：调用方手里只有 `shard_dirs`，那一次必须读盘）；
    之后每批都吃该批报告里的逐局画像。（没有画像时才回落扫盘，由上一个用例守等价性。）
    """
    import worker.iter_rollout as iter_rollout

    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(job)
    run = _FakeRunner()
    info1 = run(job, spec, ts_dir=job)

    scans = {"n": 0}
    real = iter_rollout.collect_shard_manifests

    def counting(dirs: object) -> list[dict]:
        scans["n"] += 1
        return real(dirs)  # type: ignore[arg-type]

    monkeypatch.setattr(iter_rollout, "collect_shard_manifests", counting)
    extra = topup_rollout(
        job_dir=job,
        spec=spec,
        shard_dirs=list(info1["shard_dirs"]),
        run_fn=run,
        log=lambda m: None,
        width=6,
    )
    assert extra is not None and extra["volume"]["batches"] >= 1
    assert scans["n"] == 1, "除了首批基线，补差批不该再扫盘读同一批 manifest"


def test_width_source_chain_is_explicit_then_spec_then_argv() -> None:
    """补差批的宽度来路（plan §3.3）：首批**实际用过**的 workers → `spec["workers"]` → argv 条数。

    三者都拿不到 ⇒ `0`（调用方据此**不补差**）——批大小直接决定批次级固定开销摊在几局上，
    猜一个宽度就是拿墙钟赌（宁可不补）。
    """
    spec = {"argv": [["x"], ["y"], ["z"]]}
    assert _resolve_width(7, spec) == 7  # 调用方给的（首批实际 workers）优先
    assert _resolve_width(0, {**spec, "workers": 4}) == 4
    assert _resolve_width(None, {**spec, "workers": 0}) == 3  # 首批 argv 条数
    assert _resolve_width(None, {"argv": []}) == 0  # 拿不到 ⇒ 不猜


def test_topup_is_off_without_volume_block(tmp_path: Path) -> None:
    """没有 `volume` 块 ⇒ 返回 None（老行为逐字节不变：不跑补差、不加字段）。"""
    spec = {"argv": [["x", "--stages", "2000", "--seeds", "1", "--out", "w0"]], "wver": "w"}
    assert topup_rollout(job_dir=tmp_path, spec=spec, shard_dirs=[], run_fn=_FakeRunner()) is None


def test_topup_continues_stream_and_keeps_bitwise_accounting(tmp_path: Path) -> None:
    """核心契约：补差批从首批的后一局续抽、`--out` 续号不重、每批逐位对账成立。"""
    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(job)
    vol = spec["volume"]
    assert vol["est_s_by_stage"] == ESTS
    # 首批：ceil(2000/230)=9 局(2000) + ceil(2000/115)=18 局(2001) = 27 局、各 1800 样本。
    assert vol["games_per_stage_by_stage"] == {2000: 9, 2001: 18}
    run = _FakeRunner()
    info1 = run(job, spec, ts_dir=job)
    assert len(spec["argv"]) == 27
    assert data_fp(info1["shard_dirs"]) == iter_expected_data_fp(spec)

    # ⑥ 满铺：本批 = width 局（不再是缺口方程）。显式给 width = 6（生产里由首批 workers 给）
    # ⇒ 两关轮转均分 3/3；缺口本只需 1+2 局，铺满后过采 500 样本 —— 那是**预期的**（软指标）。
    extra = topup_rollout(
        job_dir=job,
        spec=spec,
        shard_dirs=list(info1["shard_dirs"]),
        run_fn=run,
        log=lambda m: None,
        width=6,
    )
    assert extra is not None
    assert extra["volume"]["batches"] == 1
    assert extra["volume"]["shortfall_by_stage"] == {}
    assert extra["volume"]["wasted_samples"] == 500
    assert extra["volume"]["collected_by_stage"] == {2000: 2400, 2001: 2100}
    assert sum(c["rows"].__len__() for c in run.calls[1:]) == 6, "批大小 == width（满铺）"
    assert extra["volume"]["rule"] == "per-stage-v3"
    # 补差批跑在子目录（隔离 = 逐位对账不失真），且权重被拷进去了。
    assert run.calls[1]["job_dir"].endswith("topup1")
    assert (job / "topup1" / "init_weights.json").exists()
    # 逐位对账对**补差批自己**同样成立（verify_shards 未被放宽的等价物）。
    assert data_fp(extra["shard_dirs"]) == iter_expected_data_fp(
        next_batch_spec(
            spec,
            {2000: 3, 2001: 3},
            start_idx={2000: 9, 2001: 18},
            out_base=27,
            rotate_seed=ROTATE,
            it=7,
        )
    )
    # 两批 `--out` 不相交，且整体 = 各批的并集。
    outs = [_flag_value(r, "--out") for r in spec["argv"]] + [
        _flag_value(r, "--out") for c in run.calls[1:] for r in c["rows"]
    ]
    assert len(outs) == len(set(outs)) == 33
    assert sorted(int(o[1:]) for o in outs) == list(range(33))
    # 种子 = 连续流前缀 `[0, K_s)`（不重抽、不漏局）。
    produced = _seed_pairs([*info1["shard_dirs"], *extra["shard_dirs"]])
    assert produced[2000] == sorted(stage_seeds(ROTATE, 7, 2000, 0, 12))
    assert produced[2001] == sorted(stage_seeds(ROTATE, 7, 2001, 0, 21))
    # 轮报合并：games/样本相加、逐局画像拼接（漏了这一条 = 云机轮账少算一批）。
    merged = merge_iter_reports(dict(info1["report"]), list(extra["reports"]))
    assert merged["games"] == 33
    assert merged["totalSamples"] == 2400 + 2100
    assert merged["shards"] == 33
    assert len(merged["perGame"]) == 33
    assert merged["elapsedSec"] == pytest.approx(1.0)


def test_topup_needs_same_stage_template_and_weights(tmp_path: Path) -> None:
    """缺失的关模板 / 缺失的权重文件 ⇒ 响亮拒收（不能静默跑一批读不到权重的局）。"""
    job = tmp_path / "job"
    job.mkdir()
    spec = _plan_and_spec(job)
    with pytest.raises(ProtocolError, match="init_weights"):
        topup_rollout(job_dir=job, spec=spec, shard_dirs=[], run_fn=_FakeRunner())
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    stripped = {**spec, "argv": [r for r in spec["argv"] if _flag_value(list(r), "--stages") == "2000"]}
    # 满铺下要让两关都进本批（width=2）才能碰到「缺 2001 模板」那条：宽度为 1 时本批只包含
    # stages 序里的第一关，压根不会去取第二关的模板。
    with pytest.raises(ProtocolError, match="模板"):
        topup_rollout(job_dir=job, spec=stripped, shard_dirs=[], run_fn=_FakeRunner(), width=2)


def test_topup_stage_json_is_taken_from_same_stage_row(tmp_path: Path) -> None:
    """跨关取模板会删/带错 `--stage-json`（自定义关课程）——同关取才安全。"""
    def _row(stage: int, seed: int, out: str, *, extra: list[str] | None = None) -> list[str]:
        return [
            SCRIPT,
            "--stages",
            str(stage),
            "--seeds",
            str(seed),
            "--out",
            out,
            "--weights",
            "init_weights.json",
            *(extra or []),
        ]

    spec = {
        "argv": [
            _row(2000, 1, "w0", extra=["--stage-json", '{"a":1}']),
            _row(2001, 2, "w1"),
            _row(2001, 3, "w2"),
        ],
        "wver": "w",
    }
    spec2 = next_batch_spec(
        spec, {2001: 1}, start_idx={2001: 2}, out_base=3, rotate_seed=1, it=1
    )
    row = list(spec2["argv"][0])
    assert _flag_value(row, "--stages") == "2001"
    assert "--stage-json" not in row  # 2001 的行本来就没有 ⇒ 不许从 2000 那边继承
    assert _flag_value(row, "--out") == "w3"
    spec3 = next_batch_spec(
        spec, {2000: 1}, start_idx={2000: 1}, out_base=3, rotate_seed=1, it=1
    )
    assert _flag_value(list(spec3["argv"][0]), "--stage-json") == '{"a":1}'


def test_topup_records_shortfall_when_cap_binds(tmp_path: Path) -> None:
    """`game_cap` 硬顶 + 批数用尽仍有缺口 ⇒ 缺口**可见**（硬指标），循环不空转。"""
    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(
        job,
        stages="2000-2000",
        target_transitions=2000,
        max_games_per_stage=10,
    )
    # 单关课程：quota=2000、est=200 ⇒ 首批 9 局；每局只有 100 样本 ⇒ 一次补差后触 cap。
    run = _FakeRunner(samples={2000: 100})
    info1 = run(job, spec, ts_dir=job)
    extra = topup_rollout(
        job_dir=job, spec=spec, shard_dirs=list(info1["shard_dirs"]), run_fn=run, log=lambda m: None
    )
    assert extra is not None
    assert extra["volume"]["capped_stages"] == [2000]
    assert extra["volume"]["shortfall_by_stage"] == {2000: 1000}
    assert extra["volume"]["games_by_stage"] == {2000: 10}
    # 批数不被 cap 拖成无限（每批都触顶 ⇒ 立即停）。
    assert extra["volume"]["batches"] <= 3


def test_topup_records_waste_and_skips_met_stages(tmp_path: Path) -> None:
    """浪费是**软指标**：过采的关不再补差，`wasted_samples` 如实记账（不要求 == 0）。"""
    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(job, samples={2000: 260, 2001: 100})
    run = _FakeRunner(samples={2000: 260, 2001: 100})
    info1 = run(job, spec, ts_dir=job)
    extra = topup_rollout(
        job_dir=job,
        spec=spec,
        shard_dirs=list(info1["shard_dirs"]),
        run_fn=run,
        log=lambda m: None,
        width=5,
    )
    assert extra is not None
    # 2000：9 局 × 260 = 2340 > 2000（过采 340，已达标 ⇒ 不再进 eligible）；2001 还需补，
    # 满铺给 5 局（width）⇒ collected 2300（比缺口多很多，浪费 640 是**预期的**）。
    assert extra["volume"]["wasted_samples"] == 640
    assert extra["volume"]["collected_by_stage"] == {2000: 2340, 2001: 2300}
    assert extra["volume"]["shortfall_by_stage"] == {}


def test_topup_batches_are_reproducible_across_restarts(tmp_path: Path) -> None:
    """DoD ③（跨重启同 pairs）：同一计划两遍补差 ⇒ 逐批 `argv` 与 shard 名**逐字节相同**。

    为什么必须显式钉住：补差的种子 = `continuous_pairs(rotate_seed, it, start_idx)`，而
    `start_idx` 取的是**盘上账本**（产出集）而不是内存计数——所以「job 被驱逐后按同一份计划
    重放」必须落到同一批 `(stage, seed, out)`。哪一处把账本换成内存态、或让 `--out` 从 0
    重新起数、或 `start_idx` 从 0 重算，这条都会红。
    """
    job = tmp_path / "job"
    job.mkdir()
    (job / "init_weights.json").write_text("{}", encoding="utf-8")
    spec = _plan_and_spec(job)
    run = _FakeRunner()
    info1 = run(job, spec, ts_dir=job)
    first = list(info1["shard_dirs"])

    def _one(where: Path) -> tuple[_FakeRunner, dict]:
        where.mkdir(parents=True, exist_ok=True)
        (where / "init_weights.json").write_text("{}", encoding="utf-8")
        fake = _FakeRunner()
        out = topup_rollout(
            job_dir=where, spec=spec, shard_dirs=list(first), run_fn=fake, log=lambda m: None
        )
        assert out is not None
        return fake, out

    a_run, a = _one(tmp_path / "a")
    b_run, b = _one(tmp_path / "b")
    # 逐批 argv 逐字节相同（stage/seed/out/… 全在行里）。
    assert [c["rows"] for c in a_run.calls] == [c["rows"] for c in b_run.calls]
    # 批子目录名（topup1/topup2…）与 shard 名同样逐字节相同。
    assert [Path(c["job_dir"]).name for c in a_run.calls] == [
        Path(c["job_dir"]).name for c in b_run.calls
    ]
    assert sorted(Path(d).name for d in a["shard_dirs"][len(first) :]) == sorted(
        Path(d).name for d in b["shard_dirs"][len(first) :]
    )
    assert a["volume"] == b["volume"]


def test_initial_wave_pairs_by_stage_is_the_continuous_prefix() -> None:
    """首批就是连续流前缀（补差「接得上」是构造性的，不是约定）。"""
    pairs = initial_wave_pairs_by_stage(ROTATE, 7, games_by_stage={2000: 9, 2001: 18})
    assert pairs == [
        *((2000, s) for s in stage_seeds(ROTATE, 7, 2000, 0, 9)),
        *((2001, s) for s in stage_seeds(ROTATE, 7, 2001, 0, 18)),
    ]
