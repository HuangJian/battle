"""同族课程产物直取（plan/episode-reuse.plan.md）的判据与接缝。

用户口径（2026-10-22）：**不做缓存**（缓存会让内存/硬盘爆炸）。复用面只限「盘上已经有的
东西」：先找出可复用的课程（同 level/关卡/难度/决策粒度 = `episode_identity_fp` 相同），
再进它们的工作目录找这一局的 rollout shard / eval 行；找到就取，找不到就自采。

四层，各有各的失败模式：

1. **兄弟筛选**：身份不同的课混进来 = 拿别人的关卡读数当自己的；自己算进去 = 自己借自己。
2. **rollout 命中**：只认 `wver` **逐字节相同**的 shard（权重不同就是另一局）；预置奖励
   （`reward.npy`，别的课算的）绝不借；缺报告/缺 metrics ⇒ 不借（半成品不是局）。
3. **D14 改戳**：拷进本课波次目录后血缘必须写成**本课**的，并留下 `reused.from` 出处。
4. **eval 移植**：只搬同 `wver` 同 (stage,seed) 的 `event:"eval"` 行；B/C 批（带 `source`）
   不搬；移植只换本轮身份字段（iter/节点/时间/来源），读数逐字段保留。

不测的：真 bun 导出器与真开课（那是门禁外的实测面）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from biz.corpus_fp import corpus_identity_fp, episode_identity_fp
from biz.course_resolve import load_course
from worker import sibling_reuse as sr

REPO = Path(__file__).resolve().parents[3]
CURRICULA = REPO / "nn-training" / "curricula"

LEG_A = "y30-pickup"
LEG_B = "y30-speed"
#: 身份不同的对照课（不同 level/关卡 ⇒ 不该被当成兄弟）。
OTHER = "x20-clutch"

WVER = "a" * 64
WVER16 = WVER[:16]


@pytest.fixture(autouse=True)
def _reuse_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """本文件测的就是开关打开时的行为；全局隔离夹具把它钉成 0（见 tests/conftest.py）。

    开关本身的语义由 `test_switch_off_disables_every_scan` 显式钉。

    **扫描面钉成本文件用到的三门课**（2026-10-29 提速）：`sibling_courses()` 每次调用都要把
    `curricula/` 下**全部 243 个**课程 `load_course`（jsonc 去注释 + 尾逗号 + 关卡注入 + pydantic
    校验），实测 488 次解析 = 2.6s，而 `test_rollout_source_requires_same_wver` 要调两次
    `rollout_sources` ⇒ 整条用例 2.7s / 2.74s 全花在这上面（扫描本身与命中断言都在毫秒级）。
    收窄**不改任何判据**：本文件只断言这三门课的入选/排除，而 `_wire_trajs` 只给这三门建了
    traj 根 ⇒ 其余 240 门课即便身份相同，`_resolve_dir` 也会返回 `None` 被跳过，扫不扫结果逐字一样
    （`MAX_SOURCES=100` 也远够：映射里的候选最多 1 个）。
    """
    monkeypatch.delenv("NN_REUSE_SIBLINGS", raising=False)
    lib = tmp_path / "curricula"
    lib.mkdir()
    for _name in (LEG_A, LEG_B, OTHER):
        (lib / f"{_name}.jsonc").write_bytes((CURRICULA / f"{_name}.jsonc").read_bytes())
    monkeypatch.setattr(sr, "CURRICULA_DIR", lib)


def _course(name: str):  # type: ignore[no-untyped-def]
    return load_course(CURRICULA / f"{name}.jsonc")


def _wire_trajs(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, Path]) -> None:
    """把课程的 `traj` 串映射到本用例的临时目录（不碰仓库里真实的 tmp/**）。"""

    def _resolve(raw: str) -> Path | None:
        return mapping.get(str(raw))

    monkeypatch.setattr(sr, "_resolve_dir", _resolve)


def _shard(root: Path, stage: int = 2000, seed: int = 7, *, wver: str = WVER) -> Path:
    """造一份导出器同形的单局产物（shard + 该波报告）。"""
    wave = root / "it3" / "w0"
    shard = wave / f"rl_s{stage}_seed{seed}"
    shard.mkdir(parents=True)
    (shard / "manifest.json").write_text(
        json.dumps({"stage": stage, "seed": seed, "wver": wver, "corpus_fp": "src", "nSamples": 30}),
        encoding="utf-8",
    )
    for name in ("obs.npy", "metrics.npy"):
        (shard / name).write_bytes(b"\x00")
    (wave / "_rl_report.json").write_text(
        json.dumps(
            {
                "games": 1,
                "wver": wver,
                "outcomes": {"stage_clear": 1},
                "totalSamples": 30,
                "totalTicks": 900,
                "scoreList": [2.0],
                "dimLists": {},
                "elapsedSec": 8.0,
            }
        ),
        encoding="utf-8",
    )
    return shard


# ------------------------------------------------------------------ 兄弟筛选


def test_siblings_are_identity_matched_and_exclude_self(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同 level/关卡/难度 ⇒ 兄弟；自己与身份不同的课都不算。"""
    a, b, other = _course(LEG_A), _course(LEG_B), _course(OTHER)
    assert episode_identity_fp(a) == episode_identity_fp(b)
    assert corpus_identity_fp(a) != corpus_identity_fp(b)  # 只差 reward 才是要复用的一类
    assert episode_identity_fp(other) != episode_identity_fp(a)
    roots = {a.traj: tmp_path / "a", b.traj: tmp_path / "b", other.traj: tmp_path / "c"}
    for p in roots.values():
        p.mkdir(parents=True)
    _wire_trajs(monkeypatch, roots)
    names = [n for n, _root in sr.sibling_courses(a)]
    assert LEG_B in names, names
    assert LEG_A not in names, "自己不能算自己的兄弟（会借自己、也会自增依赖）"
    assert OTHER not in names, "身份不同的课不得进候选"


def test_sibling_with_missing_workdir_is_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """兄弟课的工作目录不在盘上（没开过课）⇒ 没东西可借，正常跳循环。"""
    a = _course(LEG_A)
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "exists"})
    (tmp_path / "exists").mkdir(parents=True)
    assert sr.sibling_courses(a) == [] or all(n != LEG_A for n, _ in sr.sibling_courses(a))


# ------------------------------------------------------------------ rollout 命中


def test_rollout_source_requires_same_wver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    _shard(root, wver="b" * 64)  # 兄弟课那一局用的是**别的**权重
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    assert sr.rollout_sources([(2000, 7)], WVER, args) == {}
    _shard(root, seed=9, wver=WVER)  # 同权重、但不同 seed 的一局
    found = sr.rollout_sources([(2000, 9)], WVER, args)
    assert (2000, 9) in found and found[(2000, 9)][2] == LEG_B


def test_rollout_source_refuses_precomputed_reward(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R2：`reward.npy` 是别的课算的奖励 —— 借来就是把别人的奖励喂进本课。"""
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    shard = _shard(root)
    (shard / "reward.npy").write_bytes(b"\x00")
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    assert sr.rollout_sources([(2000, 7)], WVER, args) == {}


def test_rollout_reuse_copies_and_restamps(tmp_path: Path) -> None:
    """命中 ⇒ shard 拷进本课波次目录、D14 改戳成本课、报告与出处落地。"""
    src_root = tmp_path / "b"
    shard = _shard(src_root)
    report = shard.parent / "_rl_report.json"
    wdir = tmp_path / "wave"
    wdir.mkdir()
    out = sr.reuse_rollout(
        (shard, report, LEG_B),
        wdir,
        stage=2000,
        seed=7,
        course_fp="course-b",
        corpus_fp="corpus-b",
    )
    assert out is not None
    assert out["reused"]["from"] == LEG_B
    assert out["reusedSourceElapsedSec"] == 8.0
    mm = json.loads((wdir / "rl_s2000_seed7" / "manifest.json").read_text(encoding="utf-8"))
    assert mm["corpus_fp"] == "corpus-b" and mm["course_fp"] == "course-b"
    assert mm["reused"]["from"] == LEG_B
    assert (wdir / "_rl_report.json").is_file()
    assert (wdir / "rollout.log").is_file()


def test_reuse_rollouts_filters_dispatch_queue_and_lands_on_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """派发前过滤：命中局出队、shard 落在本课 traj，未命中局照旧自采。

    聚合不是本函数的职责（它不返回报告）——拷贝进来的 shard 必须能被
    `resumed_manifests` 从盘上收走，否则「出队」就等于丢局。
    """
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    _shard(root)  # 兄弟课有 (2000, 7)
    traj = tmp_path / "a" / "it3"
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    todo = sr.reuse_rollouts([(2000, 7), (2000, 8)], traj, WVER, args)
    assert todo == [(2000, 8)]
    landed = traj / "rw0" / "rl_s2000_seed7"
    mm = json.loads((landed / "manifest.json").read_text(encoding="utf-8"))
    assert mm["corpus_fp"] and mm["course_fp"], "D14 改戳必须落地（否则续跑/聚合都判异血缘）"
    assert not (traj / "rw1").exists(), "没命中就不许造空目录（零额外存储）"
    from worker.resume import resumed_manifests

    got = resumed_manifests(
        traj,
        WVER,
        only={(2000, 7), (2000, 8)},
        course_fp=mm["course_fp"],
        corpus_fp=mm["corpus_fp"],
    )
    assert [int(m["stage"]) for m in got] == [2000], "借来的局必须进本轮聚合"


def test_reuse_rollouts_is_all_or_nothing_on_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """一局都没撞上 ⇒ 任务表逐字不动、不造目录（旧行为逐字节）。"""
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    _shard(root, wver="b" * 64)  # 兄弟课那一局是别的权重
    traj = tmp_path / "a" / "it3"
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    tasks = [(2000, 7), (2000, 8)]
    assert sr.reuse_rollouts(tasks, traj, WVER, args) == tasks
    assert not traj.exists()


def test_state_init_course_never_borrows_rollout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """起始分布课的切点随 (rotateSeed,it,stage,seed) 变 ⇒ 跨课借就是换起跑状态。"""
    a = _course(LEG_A)
    si = a.model_copy(update={"state_init": a.state_init or object()})
    args = SimpleNamespace(course_obj=si, reuse_siblings=1)
    assert sr.rollout_sources([(2000, 7)], WVER, args) == {}


# ------------------------------------------------------------------ eval 移植


def _ledger(root: Path, rows: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "eval_log.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _row(stage: int, seed: int, *, wver: str = WVER16, **extra: object) -> dict:
    base = {
        "event": "eval",
        "iter": 12,
        "wver": wver,
        "time": "2026-10-22 10:00:00",
        "stage": stage,
        "seed": seed,
        "node": "a97",
        "outcome": "stage_clear",
        "win": 1,
        "cleared": 1,
        "score": 2.0,
        "dims": {"kills": 4},
    }
    base.update(extra)
    return base


def test_eval_rows_matched_by_wver_and_pair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    _ledger(
        root,
        [
            _row(2000, 7),  # 命中
            _row(2000, 8, wver="b" * 16),  # 权重不同 ⇒ 不借
            _row(2000, 9, source="evalboard"),  # B/C 批：另一套 harness ⇒ 不借
            _row(2001, 7, outcome="timeout", win=0, cleared=0),  # 另一关、同 seed
        ],
    )
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    got = sr.find_eval_rows([(2000, 7), (2000, 8), (2000, 9), (2001, 7)], WVER16, args)
    assert set(got) == {(2000, 7), (2001, 7)}
    assert got[(2000, 7)][1] == LEG_B


def test_transplant_keeps_readings_and_reidentifies_row() -> None:
    """移植只换本轮身份字段；读数（win/cleared/score/dims…）逐字段保留。"""
    src = _row(2000, 7, elapsedSec=9.5)
    out = sr.transplant_row(src, it=13, source=LEG_B)
    assert out["iter"] == 13
    assert out["node"] == sr.REUSE_NODE
    assert out["reusedFrom"] == LEG_B
    assert out["wallSec"] is None
    for k in ("stage", "seed", "outcome", "win", "cleared", "score", "dims", "elapsedSec"):
        assert out[k] == src[k], k


def test_reuse_eval_round_returns_todo_and_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    _ledger(root, [_row(2000, 7)])
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    todo, reused = sr.reuse_eval_round([(2000, 7), (2000, 8)], key16=WVER16, args=args, it=5)
    assert todo == [(2000, 8)]
    assert set(reused) == {(2000, 7)} and reused[(2000, 7)]["iter"] == 5


# ------------------------------------------------------------------ 开关


def test_switch_off_disables_every_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = _course(LEG_A), _course(LEG_B)
    root = tmp_path / "b"
    _wire_trajs(monkeypatch, {a.traj: tmp_path / "a", b.traj: root})
    _shard(root)
    _ledger(root, [_row(2000, 7)])
    monkeypatch.setenv("NN_REUSE_SIBLINGS", "0")
    args = SimpleNamespace(course_obj=a, reuse_siblings=1)
    assert sr.rollout_sources([(2000, 7)], WVER, args) == {}
    assert sr.find_eval_rows([(2000, 7)], WVER16, args) == {}
    # CLI 开关同理（缺省 = 开；0 = 关）
    monkeypatch.delenv("NN_REUSE_SIBLINGS")
    assert sr.rollout_sources([(2000, 7)], WVER, SimpleNamespace(course_obj=a)) != {}
    assert (
        sr.rollout_sources([(2000, 7)], WVER, SimpleNamespace(course_obj=a, reuse_siblings=0)) == {}
    )


def test_no_course_means_no_scan() -> None:
    """课程之外的调用方（评测脚本/手工命令）没有语料身份可讲 ⇒ 不猜。"""
    assert sr.sibling_courses(None) == []
    assert sr.rollout_sources([(0, 1)], WVER, SimpleNamespace()) == {}
    assert sr.find_eval_rows([(0, 1)], WVER16, SimpleNamespace()) == {}
