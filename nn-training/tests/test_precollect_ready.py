"""R2c-3：预采让位判据（`rl/rollout_phase.precollect_ready`）。

为什么单独立一组用例：这是「长等待真让位」三处里**唯一已装**的一处，而它的正确性全靠
两条性质，两条都不是「看起来对」能保证的：

1. **非阻塞** —— 调度器问它一次就必须立刻有答案（真阻塞的话，让位点反而成了卡点；
   预采的等待窗口在旧实现里能轮询到 1 小时）；
2. **与步骤内部循环同源** —— `join_precollect_child` 改成调同一个判据，所以不可能出现
   「调度器说可以开训、步骤进去还在轮询」这种极难排查的分叉。

`completed_pairs`（只读盘）与 `precollect_snapshot_wver` 在这里被替换成常量，于是用例
既不碰真 traj 目录，也不依赖真权重文件。
"""

from __future__ import annotations

import subprocess
import sys
import time
import types
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import dist_common
import rl.rollout_phase as rp


class _Child:
    """`subprocess.Popen` 的最小替身：只需要 `poll()` / `returncode`。"""

    def __init__(self, code: int | None) -> None:
        self._code = code

    def poll(self) -> int | None:
        return self._code

    @property
    def returncode(self) -> int | None:
        return self._code

    def terminate(self) -> None:  # join 的超时分支会调它
        self._code = -15


def _as_popen(child: _Child) -> subprocess.Popen[bytes]:
    """替身 → 声明的签名（生产代码只调 `poll`/`returncode`/`terminate`）。"""
    return cast("subprocess.Popen[bytes]", child)


@pytest.fixture(autouse=True)
def _fake_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """把「盘上事实」钉成常量：半波阈值 = 4（`streamWaveGames=8`）。"""

    monkeypatch.setattr(dist_common, "load_dist_config", lambda: {"policy": {"streamWaveGames": 8}})
    monkeypatch.setattr(dist_common, "weights_fingerprint", lambda _p: "w0")
    monkeypatch.setattr(rp, "precollect_snapshot_wver", lambda _p, _it: "w0-extra")


def test_min_wave_is_half_the_stream_wave() -> None:
    assert rp.precollect_min_wave() == 4  # max(4, 8 // 2)


def _args(tmp_path: Path) -> types.SimpleNamespace:
    return types.SimpleNamespace(out=tmp_path / "weights.json")


def _shards(n: int) -> set[tuple[int, int]]:
    return {(1, i) for i in range(n)}


def test_no_child_is_never_a_wait(tmp_path: Path) -> None:
    """句柄为空 = 本轮没有预采这件事：不构成等待（否则会凭空卡住一条腿）。"""
    assert rp.precollect_ready(None, tmp_path, 3, _args(tmp_path)) is True


def test_alive_child_below_min_wave_is_not_ready(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(rp, "completed_pairs", lambda *a, **kw: _shards(3))
    assert rp.precollect_ready(_as_popen(_Child(None)), tmp_path, 3, _args(tmp_path)) is False


def test_alive_child_at_min_wave_is_ready(tmp_path: Path, monkeypatch) -> None:
    """半波即开训（不必等子进程退出）——与 join 循环的「proceeding before exit」同口径。"""
    monkeypatch.setattr(rp, "completed_pairs", lambda *a, **kw: _shards(4))
    assert rp.precollect_ready(_as_popen(_Child(None)), tmp_path, 3, _args(tmp_path)) is True


def test_exited_child_is_ready_even_with_zero_shards(tmp_path: Path, monkeypatch) -> None:
    """子进程已退出 = 不会再有产出：立刻判就绪（步骤进去也只是读盘 + 打日志）。"""
    monkeypatch.setattr(rp, "completed_pairs", lambda *a, **kw: set())
    assert rp.precollect_ready(_as_popen(_Child(0)), tmp_path, 3, _args(tmp_path)) is True


def test_predicate_is_non_blocking(tmp_path: Path, monkeypatch) -> None:
    """★ 让位判据必须**立刻**回答：未就绪时也不能自己在那里轮询。"""
    monkeypatch.setattr(rp, "completed_pairs", lambda *a, **kw: set())
    t0 = time.monotonic()
    assert rp.precollect_ready(_as_popen(_Child(None)), tmp_path, 3, _args(tmp_path)) is False
    assert time.monotonic() - t0 < 0.5


def test_join_uses_the_same_predicate_and_returns_at_once_when_ready(
    tmp_path: Path, monkeypatch
) -> None:
    """同源证据：就绪的子进程交给 `join_precollect_child`，它必须当场返回（不再轮询）。"""
    monkeypatch.setattr(rp, "completed_pairs", lambda *a, **kw: _shards(5))
    child = _Child(None)
    t0 = time.monotonic()
    assert rp.join_precollect_child(_as_popen(child), tmp_path, 3, _args(tmp_path)) is None
    assert time.monotonic() - t0 < 0.5
    assert child.poll() is None  # 就绪开训 ≠ terminate 子进程：它继续产剩下的 shard


def test_join_timeout_still_terminates(tmp_path: Path, monkeypatch) -> None:
    """兜底语义不变：一直不就绪（时钟推过 1 小时）⇒ terminate + 回合自己采。"""
    monkeypatch.setattr(rp, "completed_pairs", lambda *a, **kw: set())
    clock = {"t": 0.0}
    monkeypatch.setattr(rp.time, "time", lambda: clock["t"])
    monkeypatch.setattr(rp.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    child = _Child(None)
    assert rp.join_precollect_child(_as_popen(child), tmp_path, 3, _args(tmp_path)) is None
    assert child.poll() == -15  # 超时 terminate
