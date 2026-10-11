"""tests/remote/test_round_ctx.py —— 轮级资源跨批复用（`plan/rollout-topup-fastpath.plan.md` §3.2）。

现场（§1.3–§1.4）：补差批复用的是同一个 `run_iter_rollout`，于是**每一批**都重付一遍
「与批内局数无关」的壳子——落点探针（8MB+fsync）、TS 树安装、**长驻池建池 + 94 个 cold start**。
批越小占比越高：it34 补差2 只有 **1 局**，却付了 48.9s。本文件钉住三件事：

  1. **探针一轮一次**：`scratch.open_scratch` 计数 == 1（首批），补差批 0（回退档同样只探一次）；
  2. **TS 树一轮装一次**：`scratch.install_tree` 计数 == 1；
  3. **池的所有权随 ctx 转移**：`ctx=None` ⇒ 本轮自建自收（逐字今天）；传 ctx ⇒ 本批**不**收，
     由调用方 `RoundContext.close()` 收（补差跑完、PPO 之前）。

`ctx=None` 的逐字行为由 `tests/remote/test_remote_iter.py` + `test_rollout_scratch.py` 守
（那两个文件一个字都不用改）。夹具（假 bun + 桩导出器 + 假本地盘）复用 `test_rollout_scratch`。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.scratch as scratch
import worker.iter_rollout as iter_rollout
from common.protocol import INIT_WEIGHTS_NAME
from tests.remote.test_rollout_scratch import (  # type: ignore[import-not-found]
    _job_dir,
    _local_root,
    _only_candidate,
    _stub_spec,
    _watchdog,
)
from worker.iter_rollout import RoundContext, run_iter_rollout


class _FakePool:
    """池的替身：只记「建了几次 / 收了几次」（不启 bun）。"""

    def __init__(self) -> None:
        self.started = 0
        self.closed = 0
        # 下面几个是 `pool.summary()` / `serve_pool` 读数行要的属性（值与用例无关）
        self.served = 0
        self.spawned = 0
        self.killed = 0
        self.unready = 0
        self.fallback = 0
        self.fallback_reasons: dict[str, int] = {}

    def start(self) -> int:
        self.started += 1
        return 1  # 真就绪 1 个（返回 0 会走「池起不来」那条分支）

    def try_pool(self, *a: Any, **kw: Any) -> None:
        """`None` = 本局没被池接走 ⇒ 走一次性 spawn（真池的准入守卫这里不需要重造）。"""
        return None

    def close(self) -> None:
        self.closed += 1

    def summary(self) -> str:
        return "serve_pool: fake"


def _count_probe_and_tree(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """把探针/TS 树换成计数包装（真实现照跑）。"""
    counts = {"probe": 0, "tree": 0}
    real_probe, real_tree = scratch.open_scratch, scratch.install_tree

    def probe(*a: Any, **kw: Any) -> Any:
        counts["probe"] += 1
        return real_probe(*a, **kw)

    def tree(*a: Any, **kw: Any) -> Any:
        counts["tree"] += 1
        return real_tree(*a, **kw)

    monkeypatch.setattr(scratch, "open_scratch", probe)
    monkeypatch.setattr(scratch, "install_tree", tree)
    return counts


def _second_batch_dir(job: Path, tmp_path: Path) -> Path:
    """补差形态的第二批：自己的子目录（`topup1/`）+ 自己的权重镜像源。"""
    sub = job / "topup1"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / INIT_WEIGHTS_NAME).write_text('{"w": 1}', encoding="utf-8")
    return sub


def test_round_ctx_reuses_probe_tree_and_pool_across_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """两批复用同一个 ctx ⇒ 探针/TS 树各只跑一次，池**只建一次**（生命周期 = 轮）。

    池用替身数「建了几次」而不是真起 bun：桩导出器（本进程 python）进不了真池的准入
    （那是 `serve_pool` 自己的守卫，见 `rollout-serve-pool-readiness`）——而判据是同一条：
    **一轮只建一次池、只冷启动一次**。真池的行为由 `test_remote_iter` / `serve_pool` 自己的用例守。
    """
    _watchdog(monkeypatch)
    _only_candidate(monkeypatch, _local_root(tmp_path))
    counts = _count_probe_and_tree(monkeypatch)
    pools: list[_FakePool] = []

    def make_pool(*a: Any, **kw: Any) -> _FakePool:
        pools.append(_FakePool())
        return pools[-1]

    monkeypatch.setattr(iter_rollout, "_make_pool", make_pool)
    job = _job_dir(tmp_path)
    rctx = RoundContext()
    msgs: list[str] = []
    run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)]), log=msgs.append, ctx=rctx)
    assert counts == {"probe": 1, "tree": 1}
    assert len(pools) == 1 and rctx.pool is pools[0], "首批建池并挂到轮级 ctx 上"
    assert rctx.root is not None and rctx.workers >= 1

    sub = _second_batch_dir(job, tmp_path)
    second = run_iter_rollout(sub, _stub_spec(tmp_path, [(1, 1)]), log=msgs.append, ctx=rctx)
    assert counts == {"probe": 1, "tree": 1}, "补差批不许重跑探针 / 重装 TS 树"
    assert len(pools) == 1 and rctx.pool is pools[0], "池不许重建（生命周期 = 轮）"
    assert (pools[0].started, pools[0].closed) == (1, 0), "只冷启动一次，且本批不收（还要给补差批用）"
    assert second["report"]["shards"] == 1
    # ★ 批级产出根 = 轮 scratch 目录下**镜像**该批相对位置的那个子目录（§3.5）
    round_dir = scratch.layout_for(_local_root(tmp_path), job).job
    assert (round_dir / "topup1" / "r" / "w0" / "rl_s1_seed1" / "manifest.json").exists()
    assert not (sub / "w0").exists(), "§3.5：产出不许落在真 job 目录"
    # 两批**同一个**装载根（PPO 只收一个 root；递归扫拿到全部批）——这是 §3.5/P0-1 的承重面
    assert second["shards_root"] == str(round_dir)
    rctx.close()
    assert pools[0].closed == 1 and rctx.pool is None
    # 轮末回收（PPO 之后由调用方叫）：清掉轮 scratch 目录，ts 家不动
    rctx.cleanup()
    assert not list(round_dir.iterdir()), "轮末由 ctx.cleanup() 回收本地盘占用"
    assert list(_local_root(tmp_path).glob(f"{scratch.SCRATCH_TS_PREFIX}*")), "ts 家永不清"


def test_pool_ownership_moves_to_the_ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ctx=None` ⇒ 轮末自收（逐字今天）；传 ctx ⇒ 本批不收，由 `ctx.close()` 收。"""
    _watchdog(monkeypatch)
    _only_candidate(monkeypatch, _local_root(tmp_path))
    job = _job_dir(tmp_path)

    own = _FakePool()
    monkeypatch.setattr(iter_rollout, "_make_pool", lambda *a, **kw: own)
    run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)]), log=lambda _m: None)
    assert (own.started, own.closed) == (1, 1), "老调用（无 ctx）必须自建自收"

    shared = _FakePool()
    monkeypatch.setattr(iter_rollout, "_make_pool", lambda *a, **kw: shared)
    rctx = RoundContext()
    job2 = tmp_path / "job2"
    job2.mkdir()
    (job2 / INIT_WEIGHTS_NAME).write_text('{"w": 1}', encoding="utf-8")
    run_iter_rollout(job2, _stub_spec(tmp_path, [(0, 0)]), log=lambda _m: None, ctx=rctx)
    assert (shared.started, shared.closed) == (1, 0), "传 ctx 时本批不许收池（补差批还要用它）"
    rctx.close()
    assert shared.closed == 1
    rctx.close()
    assert shared.closed == 1, "close() 幂等"


def test_round_ctx_fallback_probes_once_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """回退档（候选链空）同样只探一次：抄 `SCRATCH_CANDIDATES=()` 时不该每批重跑一遍落点链。"""
    _watchdog(monkeypatch)
    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", ())
    counts = _count_probe_and_tree(monkeypatch)
    job = _job_dir(tmp_path)
    rctx = RoundContext()
    run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)]), log=lambda _m: None, ctx=rctx)
    assert (rctx.root, rctx.ts_home) == (None, None)
    sub = _second_batch_dir(job, tmp_path)
    run_iter_rollout(sub, _stub_spec(tmp_path, [(1, 1)]), log=lambda _m: None, ctx=rctx)
    assert counts == {"probe": 1, "tree": 0}, "回退档不装树；探针也只跑一次"
    assert (sub / "w0" / "rl_s1_seed1" / "manifest.json").exists()
    rctx.cleanup()  # 回退档没有 scratch 要清（幂等且不抛）
    rctx.cleanup()
    assert (sub / "w0" / "rl_s1_seed1" / "manifest.json").exists(), "回退档的产出住在真 job 目录里"
