"""test_eval_seed_pool —— A 层评估语料池（固定池外）+ summary 落账 / 缺口细分（2026-10-22）。

覆盖（不起真 loop、不发网络）：
  - 池在 rollout 抽签区间之外（`2**30 < base < 2**31`）⇒ 评估种子与训练种子永不相交。
  - `a_eval_seed_list` 与 `it` / `baseline` 无关：同一关每轮同一批种子 ⇒ it 之间、同 level
    的不同课程之间胜率/击杀/耗时逐点可比（这就是双轨轮换退场的唯一目的）。
  - 日常档 `eval_games_per_stage: 50` → 100 局/关（用户口径：与今天双轨总量持平）。
  - `settle_eval_summary` 不再落 `anchor_wr` / `rotor_wr` / `overfit_gap_pp`。
  - 续跑：固定池下同 wver 重派零新局；新 wver 重评**同一批**局。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.eval_local import (
    EVAL_SEEDS,
    a_eval_seed_list,
    eval_done_keys,
    release_local_gate_if_starved,
    settle_eval_summary,
)
from worker.eval_track import EVAL_DAILY_SEEDS, EVAL_SEEDS_BASE, ROLLOUT_SEED_MAX


def test_release_local_gate_if_starved() -> None:
    """2026-09-15 x3-power it30：无远端节点时立刻开闸，否则 local 空等到 deadline。"""
    gate = threading.Event()
    assert release_local_gate_if_starved(gate, []) is True
    assert gate.is_set()
    gate2 = threading.Event()
    assert release_local_gate_if_starved(gate2, [{"id": "self"}]) is False
    assert not gate2.is_set()
    assert release_local_gate_if_starved(None, []) is False


def test_pool_lives_outside_rollout_seed_space() -> None:
    """池基址在 rollout 抽签区间之外：`build_pairs` 抽 `rng.integers(1, 2**30)` ⇒ 永不相交。

    这是「评估读数与训练读数独立」的前提；池若落在 [1, 2**30) 里，某条腿的训练局会撞上同一
    (stage, seed)——同一局既算训练又算评估。上界 2**31 是给 32 位有符号假设留的安全带
    （仿真侧 RNG 是 `seed >>> 0` 的 mulberry32，硬上界 2**32）。
    """
    assert EVAL_SEEDS_BASE > ROLLOUT_SEED_MAX == 2**30
    assert EVAL_SEEDS[0] == EVAL_SEEDS_BASE and len(EVAL_SEEDS) == 200
    assert EVAL_SEEDS[-1] < 2**31, "池尾仍要在 32 位安全带内"
    assert all(s > ROLLOUT_SEED_MAX for s in EVAL_SEEDS)
    assert tuple(sorted(set(EVAL_SEEDS))) == EVAL_SEEDS, "池连续严格递增（切片即成员集）"


def test_a_eval_seed_list_is_it_invariant() -> None:
    """evalA / in-loop / 云侧共用：语料只由 `n_seeds` 决定，与 it / baseline 无关。

    同一关每轮评同一批局 ⇒ 跨轮、跨课（同 level）的胜率/击杀/耗时可以直接对位比较；
    这是双轨（锚点 50 + 轮转 50，每轮换段）退场后唯一要守住的性质。
    """
    for n, want in ((200, 200), (120, 120), (8, 8)):
        first = a_eval_seed_list(0, n, baseline=True)
        assert first == EVAL_SEEDS[:want]
        for it in (1, 2, 3, 4, 5, 40):
            assert a_eval_seed_list(it, n) == first, f"it{it} 的语料漂了（n={n}）"


def test_daily_config_50_maps_to_100_games() -> None:
    """课程 `eval_games_per_stage: 50`（双轨时代的锚点那一半）⇒ 日常档 100 局/关。

    用户口径 2026-10-22：「100，与今天双轨总量持平」——退场是去掉轮换，不是砍掉一半读数。
    翻译只发生在 50 这一个值上，其余一律照配置切片。
    """
    assert EVAL_DAILY_SEEDS == 100
    for it in (0, 1, 7):
        daily = a_eval_seed_list(it, 50)
        assert daily == EVAL_SEEDS[:100] and len(daily) == 100
        assert a_eval_seed_list(it, 50, baseline=True) == daily, "it0 基线同语料（否则首末不可比）"
    # 只翻译 50：120/200/8 原样切片
    assert a_eval_seed_list(3, 120) == EVAL_SEEDS[:120]
    assert a_eval_seed_list(3, 200) == EVAL_SEEDS[:200]
    assert a_eval_seed_list(3, 8) == EVAL_SEEDS[:8]

WVER = "b" * 16


def _write_eval_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as jf:
        for r in rows:
            jf.write(json.dumps(r) + "\n")


def _eval_row(it: int, stage: int, seed: int, win: int) -> dict:
    return {
        "event": "eval",
        "iter": it,
        "wver": WVER,
        "stage": stage,
        "seed": seed,
        "node": "local",
        "win": win,
        "cleared": win,
        "outcome": "stage_clear" if win else "timeout",
    }


def test_settle_summary_drops_dual_track_fields(tmp_path: Path) -> None:
    """双轨退场（2026-10-22）：summary 不再拆锚点/轮转轨，也没有过拟合 gap。"""
    elog = tmp_path / "eval_log.jsonl"
    _write_eval_rows(
        elog,
        [
            _eval_row(1, 2000, EVAL_SEEDS[0], 1),
            _eval_row(1, 2000, EVAL_SEEDS[1], 1),
            _eval_row(1, 2000, EVAL_SEEDS[2], 0),
            _eval_row(1, 2000, EVAL_SEEDS[3], 0),
        ],
    )
    settle_eval_summary(
        eval_jsonl=elog,
        key16=WVER,
        it=1,
        pairs=[(2000, s) for s in EVAL_SEEDS[:4]],
        total=4,
        landed={(2000, s) for s in EVAL_SEEDS[:4]},
        wins=[2],
        cleared_total=[2],
        outcomes={},
        node_games={},
        jsonl_lock=threading.Lock(),
        t_eval_start=0.0,
        rollout_winrate=None,
        course_fp="fpA",
    )
    rows = [
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    summ = next(r for r in rows if r.get("event") == "eval_summary")
    assert summ["games"] == 4 and summ["wins"] == 2
    assert summ["winRate"] == 0.5 and summ["clearRate"] == 0.5
    for gone in ("anchor_wr", "rotor_wr", "overfit_gap_pp"):
        assert gone not in summ, f"{gone} 已随双轨退场，旧读者不该再看到它"


def test_settle_summary_ignores_bc_evalboard_rows(tmp_path: Path) -> None:
    """P2-7：B/C evalboard 行（同为 `event:"eval"`）不得混进 A 层胜率。

    病根：台账过滤只有 (event, wver, iter)，而 `trainer/batch_eval.py` 的
    `BatchEvalRunner._run.record` 落的 B/C 行同样满足这三条（畸形批 iter=0 能撞上同
    (iter,wver)）⇒ 混进来抬高胜率分母，A 层读数就不是同一门课的读数了。
    """
    elog = tmp_path / "eval_log.jsonl"
    _write_eval_rows(
        elog,
        [
            _eval_row(3, 2000, 860001, 1),
            _eval_row(3, 2000, 860002, 1),
            # B 层行：同 iter / 同 wver，但带 source 字段（且是败局，会拉低读数）
            {**_eval_row(3, 2000, 860001, 0), "source": "B"},
            {**_eval_row(3, 2000, 860002, 0), "source": "C"},
        ],
    )
    settle_eval_summary(
        eval_jsonl=elog,
        key16=WVER,
        it=3,
        pairs=[(2000, 860001), (2000, 860002)],
        total=2,
        landed={(2000, 860001), (2000, 860002)},
        wins=[2],
        cleared_total=[2],
        outcomes={},
        node_games={},
        jsonl_lock=threading.Lock(),
        t_eval_start=0.0,
        rollout_winrate=None,
    )
    summ = next(
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if '"eval_summary"' in ln
    )
    assert summ["games"] == 2, "B/C 行不得计入分母"
    assert summ["wins"] == 2, "B/C 的败局不得污染胜场"
    assert summ["winRate"] == 1.0


def test_eval_a_once_reused_summary_has_no_dual_track_fields(tmp_path: Path) -> None:
    """手动 evalA 的「同权重已在别轮评完」补 summary（`_write_summary_for_wver`）同口径：
    行里只有 `winRate`/`clearRate`，没有锚点/轮转轨的残影。"""
    from trainer.eval_a_once import _write_summary_for_wver

    elog = tmp_path / "eval_log.jsonl"
    _write_eval_rows(
        elog, [_eval_row(2, 0, EVAL_SEEDS[0], 1), _eval_row(2, 0, EVAL_SEEDS[1], 0)]
    )
    assert _write_summary_for_wver(elog, WVER, 2, 0.0) == 2
    summ = next(
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if '"eval_summary"' in ln
    )
    assert summ["games"] == 2 and summ["winRate"] == 0.5
    for gone in ("anchor_wr", "rotor_wr", "overfit_gap_pp"):
        assert gone not in summ


def test_resume_dedup_fixed_pool(tmp_path: Path) -> None:
    """固定池续跑：同 (stage,seed) 去重 ⇒ 半途重派只补缺的局（没有「换段」这回事）。"""
    elog = tmp_path / "eval_log.jsonl"
    corpus = a_eval_seed_list(1, 50)
    _write_eval_rows(elog, [_eval_row(1, 2000, s, 1) for s in corpus[:3]])
    done = eval_done_keys(elog, WVER, min_iter=1)
    assert len([(2000, s) for s in corpus if (2000, s) not in done]) == 100 - 3
    # 下一轮的语料逐字节相同 ⇒ 已评的 3 局仍是「评过」，不会因为 it 变化重跑
    assert a_eval_seed_list(2, 50) == corpus


# ── 缺口细分（plan/eval-baseline-undispatched §2 P0-2/P0-3；评审 F1/F2）─────────────
# 铁律：`dropped` 的**数值**一字不动（控制台「缺N」/门控趋势行的历史可比性），
# 新增的 `never_dispatched` / `lost` / `carried` 只解释它由什么组成。


def _settle_summary(
    elog: Path,
    *,
    it: int = 0,
    pairs: list[tuple[int, int]],
    total: int,
    landed: set[tuple[int, int]],
    left_pending: int = 0,
    left_pairs: set[tuple[int, int]] | None = None,
    close_reason: str | None = None,
) -> dict:
    """跑一次 `settle_eval_summary` 并把**本次落的那条** summary 读回来。"""
    settle_eval_summary(
        eval_jsonl=elog,
        key16=WVER,
        it=it,
        pairs=pairs,
        total=total,
        landed=landed,
        wins=[0],
        cleared_total=[0],
        outcomes={},
        node_games={},
        jsonl_lock=threading.Lock(),
        t_eval_start=0.0,
        rollout_winrate=None,
        left_pending=left_pending,
        left_pairs=left_pairs,
        close_reason=close_reason,
    )
    last = [
        json.loads(ln)
        for ln in elog.read_text(encoding="utf-8").splitlines()
        if '"eval_summary"' in ln
    ][-1]
    assert isinstance(last, dict)
    return last


def test_settle_summary_never_dispatched_vs_lost_split(tmp_path: Path) -> None:
    """it0 现场形状（200 派 105）：`dropped` 同值（硬性），细分指出「没派」不是「丢了」。"""
    elog = tmp_path / "eval_log.jsonl"
    pairs = [(2003, 860001 + i) for i in range(200)]
    landed = set(pairs[:105])
    _write_eval_rows(elog, [_eval_row(0, s, sd, 0) for (s, sd) in sorted(landed)])
    summ = _settle_summary(
        elog,
        pairs=pairs,
        total=200,
        landed=landed,
        left_pending=95,
        left_pairs=set(pairs[105:]),
        close_reason="workers-gone",
    )
    assert summ["dropped"] == 95, "dropped 与旧实现同值是硬性断言（历史可比性）"
    assert summ["games"] == 105
    assert summ["never_dispatched"] == 95
    assert summ["lost"] == 0
    assert summ["carried"] == 0
    assert summ["left_pending"] == 95
    assert summ["close_reason"] == "workers-gone"
    assert {m["reason"] for m in summ["missing"]} == {"never-dispatched"}
    assert len(summ["missing"]) == 20, "缺口清单仍有界（MISSING_MAX）"


def test_settle_summary_lost_only(tmp_path: Path) -> None:
    """全派出、只缺 1 局（it155 那种）：`never_dispatched=0`、`lost=1`。"""
    elog = tmp_path / "eval_log.jsonl"
    pairs = [(2003, 860001 + i) for i in range(400)]
    landed = set(pairs[:399])
    _write_eval_rows(elog, [_eval_row(1, s, sd, 0) for (s, sd) in sorted(landed)])
    summ = _settle_summary(
        elog, it=1, pairs=pairs, total=400, landed=landed, left_pending=0, close_reason="window"
    )
    assert summ["dropped"] == 1
    assert summ["never_dispatched"] == 0
    assert summ["lost"] == 1
    assert summ["carried"] == 0


def test_settle_summary_carried_covers_other_iters(tmp_path: Path) -> None:
    """评审 F2 的幻数现场：续跑轮（it5 只派 20 局，另 180 局在 it3 已评）不得报 `lost=180`。"""
    elog = tmp_path / "eval_log.jsonl"
    pairs = [(2003, 860001 + i) for i in range(200)]
    todo = pairs[180:]
    _write_eval_rows(elog, [_eval_row(5, s, sd, 1) for (s, sd) in todo])
    summ = _settle_summary(elog, it=5, pairs=pairs, total=20, landed=set(todo), left_pending=0)
    assert summ["dropped"] == 180, "第二项（语料全集 − 本轮台账）历史读数不变"
    assert summ["never_dispatched"] == 0
    assert summ["lost"] == 0, "一局都没丢：`lost = dropped - never` 的旧写法在这里会报 180"
    assert summ["carried"] == 180


def test_settle_summary_mixed_pending_partition(tmp_path: Path) -> None:
    """评审 F1：`pending` 里混有 attempts=0（从未派出）与 attempts>0（重投回队）——
    收工快照 `left_pairs` 只装前者（`never_dispatched` 只数它），后者落进 `lost`。"""
    elog = tmp_path / "eval_log.jsonl"
    pairs = [(2000, 860001 + i) for i in range(4)]
    summ = _settle_summary(
        elog,
        pairs=pairs,
        total=4,
        landed=set(),
        left_pending=4,  # 队列余量：1 个从未派出 + 3 个派过没落盘
        left_pairs={pairs[1]},
    )
    assert summ["dropped"] == 4
    assert summ["never_dispatched"] == 1, "只有 attempts==0 的那一局算「从未派出」"
    assert summ["lost"] == 3, "派过没落盘（含重投回队）一律进 lost"
    assert summ["carried"] == 0
    assert summ["dropped"] == summ["never_dispatched"] + summ["lost"] + summ["carried"]
    reasons = {m["seed"]: m["reason"] for m in summ["missing"]}
    assert reasons[860002] == "never-dispatched"
    assert set(reasons.values()) == {"never-dispatched", "undispatched"}


def test_settle_summary_reason_undispatched_when_meta_ok_but_no_row(tmp_path: Path) -> None:
    """P0-3：真·从未派出 = `never-dispatched`；meta 有 `ok=True` 却无台账行的罕怪态仍叫
    `undispatched`（两个成因各占一个值，不再混——这是本刀要消灭的那个病）。"""
    elog = tmp_path / "eval_log.jsonl"
    pairs = [(2000, 860001), (2000, 860002)]
    (tmp_path / "dist-agent-meta.jsonl").write_text(
        json.dumps(
            {"node": "a97", "mode": "eval", "it": 0, "stage": 2000, "seed": 860002, "ok": True}
        )
        + "\n",
        encoding="utf-8",
    )
    summ = _settle_summary(elog, pairs=pairs, total=2, landed=set(), left_pending=1, left_pairs={pairs[0]})
    assert {m["seed"]: m["reason"] for m in summ["missing"]} == {
        860001: "never-dispatched",
        860002: "undispatched",
    }


def test_settle_summary_defaults_keep_cloud_caller_shape(tmp_path: Path) -> None:
    """云机调用点（`remote/offline_eval.py`，位置参数、不传新参）的形状：
    `never_dispatched=0`、`lost` = 派出去没落盘、`carried` 只反映全集口径残留。"""
    elog = tmp_path / "eval_log.jsonl"
    pairs = [(2000, 860001 + i) for i in range(6)]
    landed = set(pairs[:4])
    _write_eval_rows(elog, [_eval_row(1, s, sd, 1) for (s, sd) in sorted(landed)])
    summ = _settle_summary(elog, it=1, pairs=pairs, total=6, landed=landed)
    assert summ["never_dispatched"] == 0
    assert summ["lost"] == 2
    assert summ["carried"] == 0
    assert summ["left_pending"] == 0
    assert summ["close_reason"] is None, "旧调用点不传 ⇒ unknown（不是编一个值出来）"


def test_dispatcher_daily_corpus_100_fixed_offpool(tmp_path: Path, monkeypatch) -> None:
    """本地 runner 注入：`eval_games_per_stage=50` → 100 局/关，且语料固定不轮换。

    · 同 wver 重派 ⇒ 零新局（固定池下已评的 (stage,seed) 全部命中）；
    · 新 wver（权重文件变了）⇒ 重评**同一批** 100 局（以前双轨会换掉轮转那 50 局，
      于是 it 之间读数不在同一局上，跨轮不可比）。
    """
    import types

    import trainer.eval_dispatch as ed

    work = tmp_path / "dt"
    work.mkdir()
    rl = work / "w.json"
    rl.write_text('{"arch":{}}', encoding="utf-8")

    calls: list[tuple[int, int]] = []

    def fake_runner(
        bun,
        snap,
        stage,
        seed,
        out_dir,
        max_ticks,
        difficulty,
        timeout_sec,
        wver,
        stage_json="",
        lives_override=None,
        player_level=None,
        **kw,
    ):
        calls.append((stage, seed))
        win = 1 if seed in EVAL_SEEDS[:50] else 0
        return {
            "stage": stage,
            "seed": seed,
            "outcome": "stage_clear" if win else "timeout",
            "ticks": 10,
            "win": win,
            "score": 0.1,
            "quality": 0.2,
            "dims": {},
            "elapsedSec": 0.001,
            "wver": wver,
            "mode": "eval",
        }

    monkeypatch.setattr(ed, "run_local_eval_game", fake_runner)
    args = types.SimpleNamespace(
        eval_games_per_stage=50,
        total_stages=1,
        eval_stages="2000",
        eval_window_sec=60,
        max_ticks=10,
        difficulty="hard",
    )
    cfg = {"nodes": [], "policy": {"evalLocalSlots": 4}}
    gate = threading.Event()
    gate.set()
    traj_a = work / "trajA"
    ed.dispatch_eval_round("bun", str(rl), traj_a, args, cfg, "rid.dt", 1, local_gate=gate)
    assert len(calls) == 100, f"日常档 50 配置 → 100 局/关 (got {len(calls)})"
    expect = {(2000, s) for s in EVAL_SEEDS[:100]}
    assert set(calls) == expect
    rows = [
        json.loads(ln)
        for ln in (work / "eval_log.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    summ = [r for r in rows if r.get("event") == "eval_summary"][-1]
    assert summ["games"] == 100 and summ["wins"] == 50
    assert summ["winRate"] == 0.5
    assert "anchor_wr" not in summ
    # 续跑：同 wver 再派 → already evaluated
    n_before = len(calls)
    ed.dispatch_eval_round("bun", str(rl), traj_a, args, cfg, "rid.dt", 1, local_gate=gate)
    assert len(calls) == n_before
    # 新权重（新 wver）→ 重评同一批 100 局（不再有轮转段轮换）
    rl.write_text('{"arch":{"v":2}}', encoding="utf-8")
    ed.dispatch_eval_round(
        "bun", str(rl), work / "trajB", args, cfg, "rid.dt2", 2, local_gate=gate
    )
    assert len(calls) == n_before + 100
    assert {c[1] for c in calls[n_before:]} == {s for _, s in expect}


def test_dispatcher_prefix_when_not_daily_n(tmp_path: Path, monkeypatch) -> None:
    """n_seeds=2（单测/冒烟）仍走前缀切片，不被翻成日常档 100。"""
    import types

    import trainer.eval_dispatch as ed

    work = tmp_path / "pre"
    work.mkdir()
    rl = work / "w.json"
    rl.write_text('{"arch":{"n":2}}', encoding="utf-8")
    calls: list[tuple[int, int]] = []

    def fake_runner(bun, snap, stage, seed, out_dir, max_ticks, difficulty, timeout_sec, wver, **kw):
        calls.append((stage, seed))
        return {
            "stage": stage,
            "seed": seed,
            "outcome": "timeout",
            "ticks": 1,
            "win": 0,
            "score": 0,
            "quality": 0,
            "dims": {},
            "elapsedSec": 0.001,
            "wver": wver,
            "mode": "eval",
        }

    monkeypatch.setattr(ed, "run_local_eval_game", fake_runner)
    args = types.SimpleNamespace(
        eval_games_per_stage=2,
        total_stages=1,
        eval_stages="2000",
        eval_window_sec=30,
        max_ticks=10,
        difficulty="hard",
    )
    cfg = {"nodes": [], "policy": {"evalLocalSlots": 2}}
    gate = threading.Event()
    gate.set()
    ed.dispatch_eval_round(
        "bun", str(rl), work / "traj", args, cfg, "rid.pre", 3, local_gate=gate
    )
    assert set(calls) == {(2000, EVAL_SEEDS[0]), (2000, EVAL_SEEDS[1])}
