"""test_offline_eval_pool.py — 云机离线 eval 的长驻池接线（`docs/nn/runtime-opt.md` §22.7）。

两段各自独立：

  * **接线**（`remote/offline_eval.run_cloud_eval`）：每轮**建一个池**、把同一个池交给每一局、
    轮末关掉并把计数放进本地返回字典（不进 wire）。这里把 `run_local_eval_game` 换成**假**执行器
    ⇒ 断言的正是「池有没有被建/传/关」，不依赖真 bun。
  * **执行面**（`biz/eval_local.run_local_eval_game`）：池在则**先试池**（用它的输出行长出一个
    `CompletedProcess`），池说不行则**回退**一次性 `run_eval_runner_capture`，两条路的返回值
    形状完全一致（同一个 `_eval_report.json` → manifest + `mode="eval"` + `elapsedSec`）。

真 bun + 真导出器的池逐位对拍在 `tests/worker/test_remote_iter_real_bun.py`（rollout 腿，同一份协议）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.offline_eval as offline_eval
import worker.eval_local as eval_local
import worker.serve_pool as serve_pool
from remote.artifacts import sha256_file
from remote.cloud_pool import CLOUD_EVAL_POOL
from remote.offline_eval import CLOUD_NODE, eval_plan_of, run_cloud_eval


def _course(eval_stages: str = "0", n_seeds: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        eval_stages=eval_stages,
        eval_games_per_stage=n_seeds,
        eval_every=1,
        difficulty="hard",
        max_ticks=1200,
        player=SimpleNamespace(lives=1, level=0),
        stage_ids=(0,),
        stage_json=lambda _s: "",
    )


def _ts_root(tmp_path: Path) -> Path:
    ts = tmp_path / "ts"
    (ts / "tools" / "sim").mkdir(parents=True, exist_ok=True)
    (ts / "tools" / "sim" / serve_pool.EVAL_SCRIPT.split("/")[-1]).write_text(
        "// fake\n", encoding="utf-8"
    )
    return ts


def _weights(tmp_path: Path) -> Path:
    p = tmp_path / "weights.json"
    p.write_bytes(b'{"w":1}')
    return p


def _logs() -> tuple[list[str], object]:
    msgs: list[str] = []
    return msgs, msgs.append


class FakePool:
    """假池：只记「谁用了它」——真实行为（复用/回退）由 `test_remote_serve_pool.py` 覆盖。"""

    def __init__(self, **over: object) -> None:
        self.started = 0
        self.closed = 0
        self.calls: list[list[str]] = []
        self.served = 0
        self.spawned = 1  # 缺省「就绪一个」；`spawned=0` 用来演「池起不来」
        self.killed = 0
        self.fallback = 0
        self.fallback_reasons: dict[str, int] = {}
        # 跨轮 keeper 需要的最小形状（真池由 ServePool 提供）。
        self.max_workers = 4
        self.ts_dir = None
        self.begin_n = 0
        for k, v in over.items():
            setattr(self, k, v)

    def begin_round(self) -> None:
        self.begin_n += 1
        self.served = 0
        self.spawned = 0
        self.killed = 0
        self.fallback = 0
        self.fallback_reasons = {}

    def start(self) -> int:
        self.started += 1
        return int(self.spawned)

    def close(self) -> None:
        self.closed += 1

    def summary(self) -> str:
        return f"serve_pool: served={self.served} spawned={self.spawned} killed=0 fallback=0（-）"

    def try_capture(self, argv: list[str], timeout: float, **_kw: object) -> tuple[float, list[str]]:
        self.calls.append(list(argv))
        self.served += 1
        return 0.01, ["stub out"]


def test_cloud_eval_builds_one_pool_and_hands_it_to_every_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一轮 = 一个池：建一次、每局都拿到同一个、轮末关闭 + 汇一行统计。"""
    pool = FakePool(spawned=2)
    built: list[tuple] = []

    def fake_make(bun: str, ts_dir: Path, workers: int, log: object, **kw: object):
        built.append((bun, str(ts_dir), workers, kw.get("for_script")))
        return pool

    monkeypatch.setattr(offline_eval.serve_pool, "make_pool", fake_make)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")
    seen: list[object] = []

    def fake_runner(_bun, _w, stage, seed, out_dir, *_a, **kw):
        seen.append(kw.get("pool"))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {"win": True, "cleared": True, "outcome": "win", "elapsedSec": 0.01,
                "stage": stage, "seed": seed}

    monkeypatch.setattr(offline_eval, "run_local_eval_game", fake_runner)
    course = _course(n_seeds=2)
    ts = _ts_root(tmp_path)
    msgs, log = _logs()
    out = run_cloud_eval(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=ts,
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=3,
        log=log,
    )
    assert out["ran"] and out["settled"] == 2
    # 建池一次，参数是（bun, TS 树, min(slots, 本轮局数)）—— 多的槽位不预热；
    # `for_script` 只是准入门槛（池本身是同质入口，不再按脚本建）
    assert built == [("bun", str(ts), 2, serve_pool.EVAL_SCRIPT)], built
    assert pool.started == 1 and pool.closed >= 1, "池必须起一次、关掉"
    assert seen == [pool, pool], "每一局都要拿到同一个池"
    assert any(
        f"长驻 worker 池：2/2 就绪（同质入口 {serve_pool.SERVE_ANY_SCRIPT}，mode=eval）" in m
        for m in msgs
    ), msgs
    assert any("serve_pool: served=" in m for m in msgs), msgs
    # 计数只进本地返回字典（wire 不认识这个键）
    assert set(out["servePool"]) == {"served", "spawned", "killed", "fallback", "reasons"}


def test_cloud_eval_closes_the_pool_even_when_a_game_explodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """执行面出意外（执行器抛）也不得漏掉关池——否则段末留下常驻进程。"""
    pool = FakePool(spawned=2)

    monkeypatch.setattr(offline_eval.serve_pool, "make_pool", lambda *_a, **_k: pool)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")

    def boom(*_a, **_k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(offline_eval, "run_local_eval_game", boom)
    course = _course(n_seeds=1)
    out = run_cloud_eval(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=2,
        log=lambda _m: None,
    )
    # 池现在由内层 finally 关一次（不存 keeper：本用例没给 ts_code_sha256）；外层 pool 已置 None。
    assert out["failed"] == 1 and pool.closed == 1, "异常路径也必须关池（且只关一次）"


def test_cloud_eval_skips_pooling_when_the_pool_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """池起不来（服务不可用/协议漂移）⇒ 不传池、整轮照跑（只慢不错）。"""
    pool = FakePool(spawned=0)

    monkeypatch.setattr(offline_eval.serve_pool, "make_pool", lambda *_a, **_k: pool)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")
    seen: list[object] = []

    def fake_runner(_bun, _w, stage, seed, out_dir, *_a, **kw):
        seen.append(kw.get("pool"))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {"win": True, "cleared": True, "outcome": "win", "elapsedSec": 0.0}

    monkeypatch.setattr(offline_eval, "run_local_eval_game", fake_runner)
    course = _course(n_seeds=1)
    msgs, log = _logs()
    out = run_cloud_eval(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=2,
        log=log,
    )
    assert out["settled"] == 1, "池起不来不耽误读数"
    assert seen == [None], "没就绪就不该把池交出去"
    assert out["servePool"] is None
    assert any("起不来" in m for m in msgs), msgs


def test_cloud_eval_reuses_the_keeper_pool_for_the_same_ts_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0b：同 ts sha 的第二个 eval 取回暖池（spawned=0），不再 make/start。"""
    pool = FakePool(spawned=2, max_workers=4)
    built: list[int] = []

    def fake_make(*_a, **_k):
        built.append(1)
        return pool

    monkeypatch.setattr(offline_eval.serve_pool, "make_pool", fake_make)
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")

    def fake_runner(_bun, _w, stage, seed, out_dir, *_a, **_kw):
        p = _kw.get("pool")
        if p is not None:
            p.try_capture([], 1.0)  # 模拟执行面真的用了池（served 才不是 0）
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {"win": True, "cleared": True, "outcome": "win", "elapsedSec": 0.0}

    monkeypatch.setattr(offline_eval, "run_local_eval_game", fake_runner)
    sha = "a" * 64
    course = _course(n_seeds=1)
    kw: dict[str, Any] = dict(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=2,
        ts_code_sha256=sha,
        log=lambda _m: None,
    )
    CLOUD_EVAL_POOL.close_all()
    try:
        first = run_cloud_eval(**kw)
        assert built == [1] and first["servePool"]["spawned"] == 2
        # 同一份权重会让第二点被 `eval_done_keys` 全跳过（连池都不建）；换一份权重
        # 只为让第二点有 todo，TS sha 仍然是同一个（keeper key 不变）。
        w2 = tmp_path / "weights2.json"
        w2.write_bytes(b'{"w":2}')
        second = run_cloud_eval(
            **{
                **kw,
                "it": 2,
                "weights_path": w2,
                # 独立账本：否则同 (stage,seed) 的已评行会让第二点提前返回（连池都不建）。
                "eval_jsonl": tmp_path / "eval_log2.jsonl",
            }
        )
        assert built == [1], "第二个同 sha eval 不该再 make_pool"
        assert second["servePool"]["spawned"] == 0
        assert second["servePool"]["served"] == 1
        assert pool.begin_n == 1 and pool.started == 1, "跨轮只 begin 一次，且不重复 start"
    finally:
        CLOUD_EVAL_POOL.close_all()


def test_cloud_eval_env_switch_disables_pooling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`NN_SERVE_POOL=0` ⇒ 连 `make_pool` 都不建（本批之前的逐局 spawn 行为）。"""
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")
    monkeypatch.setattr(offline_eval, "find_bun", lambda *_a, **_k: "bun")
    seen: list[object] = []

    def fake_runner(_bun, _w, stage, seed, out_dir, *_a, **kw):
        seen.append(kw.get("pool"))
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {"win": True, "cleared": True, "outcome": "win", "elapsedSec": 0.0}

    monkeypatch.setattr(offline_eval, "run_local_eval_game", fake_runner)
    course = _course(n_seeds=1)
    out = run_cloud_eval(
        plan=eval_plan_of(course),
        it=1,
        weights_path=_weights(tmp_path),
        eval_jsonl=tmp_path / "eval_log.jsonl",
        ts_root=_ts_root(tmp_path),
        work_dir=tmp_path / "work",
        course=course,
        bun="bun",
        slots=2,
        log=lambda _m: None,
    )
    assert out["settled"] == 1 and out["servePool"] is None and seen == [None]


# ─────────────────────────── 执行面：run_local_eval_game 的池优先/回退 ───────────────────────────


def _report(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_eval_report.json").write_text(
        json.dumps({"win": True, "cleared": True, "outcome": "win", "ticks": 10}),
        encoding="utf-8",
    )


class CapturingPool:
    """只实现 `try_capture` 的假池（执行面测试用）。"""

    def __init__(self, result: tuple[float, list[str]] | None) -> None:
        self.result = result
        self.calls: list[tuple[list[str], float]] = []

    def try_capture(self, argv, timeout, **_kw):
        self.calls.append((list(argv), float(timeout)))
        return self.result


def test_run_local_eval_game_prefers_the_pool(tmp_path: Path, monkeypatch) -> None:
    """池成功 ⇒ **绝不** spawn 一次性进程，manifest 与一次性路径同形。"""
    pool = CapturingPool((0.12, ["[stub] game ok"]))
    out_dir = tmp_path / "eval-s0-d0"
    _report(out_dir)

    def explode(*_a, **_k):  # 一次性路径被调用 = 失败
        raise AssertionError("池可用时不该走 run_eval_runner_capture")

    monkeypatch.setattr(eval_local, "run_eval_runner_capture", explode)
    man = eval_local.run_local_eval_game(
        sys.executable,
        str(tmp_path / "weights.json"),
        0,
        0,
        out_dir,
        1200,
        "hard",
        5.0,
        "W" * 16,
        pool=pool,
    )
    assert man["outcome"] == "win" and man["mode"] == "eval"
    assert man["wver"] == "W" * 16
    assert man["elapsedSec"] >= 0.0
    (argv, timeout) = pool.calls[0]
    # 送进池的 argv 与一次性路径同一份：脚本名 + **绝对**的 --out/--weights
    assert argv[0] == serve_pool.EVAL_SCRIPT
    assert argv[argv.index("--out") + 1] == str(out_dir)
    assert timeout == 5.0


def test_run_local_eval_game_falls_back_when_the_pool_declines(
    tmp_path: Path, monkeypatch
) -> None:
    """池说不行（超时/ERR/worker 死掉）⇒ 回退一次性 runner，返回值形状不变。"""
    import subprocess

    pool = CapturingPool(None)
    out_dir = tmp_path / "eval-s0-d1"
    _report(out_dir)
    used: list[list[str]] = []

    def fake_capture(cmd, timeout, **kw):
        used.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    monkeypatch.setattr(eval_local, "run_eval_runner_capture", fake_capture)
    man = eval_local.run_local_eval_game(
        sys.executable,
        str(tmp_path / "weights.json"),
        0,
        1,
        out_dir,
        1200,
        "hard",
        5.0,
        "W" * 16,
        pool=pool,
    )
    assert man["win"] is True and man["mode"] == "eval"
    assert pool.calls and len(used) == 1, "池试过一次 + 一次性兜底一次"
    assert used[0][1] == serve_pool.EVAL_SCRIPT


def test_run_local_eval_game_without_pool_never_touches_it(
    tmp_path: Path, monkeypatch
) -> None:
    """不传 `pool`（本机/控制台路径）⇒ 行为与加池前逐字节相同：直接一次性 runner。"""
    import subprocess

    out_dir = tmp_path / "eval-s0-d2"
    _report(out_dir)
    called: list[int] = []

    def fake_capture(cmd, timeout, **kw):
        called.append(1)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(eval_local, "run_eval_runner_capture", fake_capture)
    man = eval_local.run_local_eval_game(
        sys.executable, str(tmp_path / "weights.json"), 0, 2, out_dir, 1200, "hard", 5.0, "W" * 16
    )
    assert len(called) == 1 and man["mode"] == "eval"


def test_eval_manifest_wver_is_the_weights_sha(tmp_path: Path) -> None:
    """`wver` 口径不因池化而变：仍是权重字节的 sha256 前 16 位（与 in-loop 可配对的前提）。

    导出器自己也会在报告里回显 wver；`run_local_eval_game` 的 `setdefault` 只负责在报告
    缺该字段时把它补齐 —— 两种情形下的取值必须都是调用方给的那个（= 权重指纹）。
    """
    w = _weights(tmp_path)
    key16 = sha256_file(w)[:16]
    for seed, with_field in ((4, False), (5, True)):
        out_dir = tmp_path / f"eval-s0-d{seed}"
        _report(out_dir)
        rep = json.loads((out_dir / "_eval_report.json").read_text(encoding="utf-8"))
        if with_field:
            rep["wver"] = key16
            (out_dir / "_eval_report.json").write_text(json.dumps(rep), encoding="utf-8")
        man = eval_local.run_local_eval_game(
            sys.executable, str(w), 0, seed, out_dir, 1200, "hard", 5.0, key16,
            pool=CapturingPool((0.01, [])),
        )
        assert man["wver"] == key16
    assert CLOUD_NODE == "cloud"  # 云机局在账本里的 node 名（口径锁）
