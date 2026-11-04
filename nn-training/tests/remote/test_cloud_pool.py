"""test_cloud_pool.py — 跨轮 serve pool 的归属/取回/关闭（`remote/cloud_pool.py`）。

这里不启 bun：池是假对象，只验证「谁拥有池、什么时候关、什么 key 能复用」。
真实池的 `begin_round` 计数在 `tests/worker/test_remote_serve_pool.py` 覆盖。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.cloud_pool as cloud_pool


class FakePool:
    def __init__(self, *, width: int = 2, ts_dir: Path | None = None, closed: bool = False) -> None:
        self.max_workers = int(width)
        self.ts_dir = ts_dir
        self.closed = closed
        self.close_n = 0
        self.begin_n = 0
        self.served = 3
        self.spawned = 2
        self.killed = 0

    def begin_round(self) -> None:
        self.begin_n += 1

    def close(self) -> None:
        self.close_n += 1
        self.closed = True


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(cloud_pool.ENV_KEEP, "1")
    monkeypatch.setattr(cloud_pool, "_EVAL_BUSY", cloud_pool.threading.Event())


def test_take_empty_is_none_and_put_then_take_same_object(tmp_path: Path) -> None:
    k = cloud_pool.CloudPoolKeeper("rollout")
    assert k.take("a" * 64) is None
    p = FakePool(ts_dir=tmp_path)
    k.put("a" * 64, p)
    got = k.take("a" * 64)
    assert got is p and got.begin_n == 1, "take 必须调用 begin_round（轮读数清零）"
    assert k.take("a" * 64) is None, "取走之后槽位为空"


def test_key_change_closes_old_pool_and_returns_none(tmp_path: Path) -> None:
    k = cloud_pool.CloudPoolKeeper("rollout")
    old = FakePool(ts_dir=tmp_path)
    k.put("a" * 64, old)
    assert k.take("b" * 64) is None
    assert old.close_n == 1 and old.closed


def test_narrow_pool_and_pruned_ts_dir_are_closed(tmp_path: Path) -> None:
    k = cloud_pool.CloudPoolKeeper("rollout")
    narrow = FakePool(width=2, ts_dir=tmp_path)
    k.put("a" * 64, narrow)
    assert k.take("a" * 64, min_workers=3) is None
    assert narrow.close_n == 1

    gone = tmp_path / "gone"
    pruned = FakePool(width=8, ts_dir=gone)
    k.put("a" * 64, pruned)
    assert k.take("a" * 64) is None
    assert pruned.close_n == 1


def test_closed_pool_is_not_reused(tmp_path: Path) -> None:
    k = cloud_pool.CloudPoolKeeper("rollout")
    p = FakePool(ts_dir=tmp_path, closed=True)
    k.put("a" * 64, p)
    assert k.take("a" * 64) is None
    assert p.close_n == 1


def test_put_replaces_and_closes_old(tmp_path: Path) -> None:
    k = cloud_pool.CloudPoolKeeper("rollout")
    old, new = FakePool(ts_dir=tmp_path), FakePool(ts_dir=tmp_path)
    k.put("a" * 64, old)
    k.put("a" * 64, new)
    assert old.close_n == 1 and old.closed
    assert k.take("a" * 64) is new


def test_env_switch_closes_on_take_and_put(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    k = cloud_pool.CloudPoolKeeper("rollout")
    p = FakePool(ts_dir=tmp_path)
    k.put("a" * 64, p)
    monkeypatch.setenv(cloud_pool.ENV_KEEP, "0")
    assert k.take("a" * 64) is None
    assert p.close_n == 1
    q = FakePool(ts_dir=tmp_path)
    k.put("a" * 64, q)
    assert q.close_n == 1


def test_close_all_is_idempotent_and_eval_busy_skips(tmp_path: Path) -> None:
    eval_k = cloud_pool.CloudPoolKeeper("eval")
    p = FakePool(ts_dir=tmp_path)
    eval_k.put("a" * 64, p)
    cloud_pool.set_eval_busy(True)
    eval_k.close_all()
    assert p.close_n == 0, "评估在飞时不许硬杀池"
    cloud_pool.set_eval_busy(False)
    eval_k.close_all()
    eval_k.close_all()
    assert p.close_n == 1


def test_close_cloud_pools_skips_eval_runner_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    from remote import run_loop

    calls: list[str] = []
    monkeypatch.setattr(run_loop.CLOUD_EVAL_POOL, "close_all", lambda: calls.append("eval"))
    monkeypatch.setattr(run_loop.CLOUD_ROLLOUT_POOL, "close_all", lambda: calls.append("rollout"))

    class Runner:
        def inflight_it(self):
            return 7

        def pending_it(self):
            return None

    run_loop._close_cloud_pools(type("Ctx", (), {"eval_runner": Runner()})(), lambda _m: None)
    assert calls == ["rollout"], "eval 在飞 ⇒ 只收 rollout"
