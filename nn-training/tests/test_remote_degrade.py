"""tests/test_remote_degrade.py —— R9 远端失败自动降级（plan/feasibility-map.md §12）。

c6 it50 事故：单个 job 三次 1800s 超时、进程最终死在 eval 中途——云端在关键路径上
却没有任何退路。本文件锁死三档处置与**退避**行为：

  * 成功 → 失败计数复位；
  * 连败未达阈值 → 原样抛出（交给 loop 原地重试，既有语义不变）；
  * 连败达阈值 → `args.ppo` 改 local + `remote_degrade` 事件 + 本轮继续（训练活着）；
  * `--remote-degrade-after 0`（禁降级）→ 连败 3 次写 `gate_verdict: ABORT` 并停腿；
  * `wait_job` 轮询：网络错误/5xx 指数退避，404（正常排队）不退避。

免 torch：只构造 stub 承载 `TrainingSteps._remote_ppo_or_degrade`，不碰训练后端。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote.hub_client import wait_job
from rl.loop_steps import TrainingSteps


class _Stub(TrainingSteps):
    """承载被测方法的最小宿主：继承真 mixin 取 `_remote_ppo_or_degrade`，只覆盖
    `_remote_ppo`（真远端要 hub/隧道/打包，这里不需要）。"""

    def __init__(self, tmp_path: Path, fail_times: int = 0, **args_over: object) -> None:
        base = {"ppo": "remote", "remote_degrade_after": 3}
        base.update(args_over)
        self.args = SimpleNamespace(**base)  # type: ignore[arg-type]
        self._jsonl_path = tmp_path / "training_log.jsonl"
        self._jsonl_path.write_text("", encoding="utf-8")
        self._remote_fail = 0
        self._remote_degraded = False
        self._leg_abort = False
        self.remote_calls: list[int] = []
        self.fail_times = fail_times

    def _remote_ppo(self, it: int) -> None:
        self.remote_calls.append(it)
        if len(self.remote_calls) <= self.fail_times:
            raise TimeoutError(f"wait_job 超时（>{1800}s）")

    def events(self) -> list[dict]:
        return [
            json.loads(ln)
            for ln in self._jsonl_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]


def test_success_resets_failure_counter(tmp_path: Path) -> None:
    """远端一旦成功 → 连败计数复位（否则一次成功后的首次失败会误触发降级）。"""
    st = _Stub(tmp_path)  # fail_times=0：本轮远端成功
    st._remote_fail = 2
    assert st._remote_ppo_or_degrade(7) is True
    assert st._remote_fail == 0
    assert st.events() == []  # 成功不写任何事件


def test_below_threshold_retries_same_iteration(tmp_path: Path) -> None:
    """未达阈值：原样抛出 → loop 的「原地重试同一 iter」语义不变。"""
    st = _Stub(tmp_path, fail_times=5)
    with pytest.raises(TimeoutError):
        st._remote_ppo_or_degrade(1)
    with pytest.raises(TimeoutError):
        st._remote_ppo_or_degrade(1)
    assert st._remote_fail == 2
    assert st.args.ppo == "remote"  # 还没降级
    assert st.events() == []


def test_degrade_to_local_after_threshold(tmp_path: Path) -> None:
    """达阈值：args.ppo → local + remote_degrade 事件 + 本轮继续（训练不死）。"""
    st = _Stub(tmp_path, fail_times=3)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            st._remote_ppo_or_degrade(5)
    assert st._remote_ppo_or_degrade(5) is False  # 第 3 次 → 降级
    assert st.args.ppo == "local"
    ev = st.events()
    assert [e["event"] for e in ev] == ["remote_degrade"]
    assert ev[0]["iter"] == 5 and ev[0]["after_failures"] == 3
    # 降级后方法不再调远端（调用方已走本地路径）
    assert len(st.remote_calls) == 3


def test_degrade_disabled_writes_abort(tmp_path: Path) -> None:
    """`--remote-degrade-after 0`：连败 3 次写 gate_verdict ABORT + 停腿标记。"""
    st = _Stub(tmp_path, remote_degrade_after=0, fail_times=3)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            st._remote_ppo_or_degrade(9)
    assert st.events() == []  # 未达 3 次不写判决
    with pytest.raises(TimeoutError):  # 第 3 次：写完判决后仍抛出（loop 决定停腿）
        st._remote_ppo_or_degrade(9)
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT"
    assert ev[0]["iter"] == 9
    assert st._leg_abort is True
    assert st.args.ppo == "remote"  # 禁降级时不许偷偷改


def test_wait_job_poll_backoff_on_network_errors() -> None:
    """R9 退避：连续网络错误按 2 的幂退避，成功/404 复位（不猛敲不可达边缘）。"""
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_request(base: str, token: str, path: str, timeout: float = 30.0, **kw: object):
        calls["n"] += 1
        if calls["n"] <= 3:
            raise OSError("tunnel i/o timeout")
        return 200, b'{"ok": 1}'

    import remote.hub_client as hc

    orig_req, orig_sleep = hc._request, hc.time.sleep
    hc._request = fake_request  # type: ignore[assignment]
    try:
        hc.time.sleep = lambda s: sleeps.append(float(s))  # type: ignore[assignment]
        out = wait_job("http://x", "t", "j1", timeout_sec=10, poll_sec=5.0, log=lambda m: None)
    finally:
        hc._request, hc.time.sleep = orig_req, orig_sleep  # type: ignore[assignment]
    assert out == {"ok": 1}
    assert sleeps == [5.0, 10.0, 20.0]  # 5 × 2^k，封顶 60


def test_wait_job_404_does_not_backoff() -> None:
    """404 = job 还在排队（正常等待）→ 固定 poll_sec，不触发退避。"""
    sleeps: list[float] = []
    seq = [404, 404, 200]
    it = iter(seq)

    def fake_request(base: str, token: str, path: str, timeout: float = 30.0, **kw: object):
        code = next(it)
        return code, b"{}" if code == 404 else b'{"ok": 1}'

    import remote.hub_client as hc

    orig_req, orig_sleep = hc._request, hc.time.sleep
    hc._request = fake_request  # type: ignore[assignment]
    try:
        hc.time.sleep = lambda s: sleeps.append(float(s))  # type: ignore[assignment]
        wait_job("http://x", "t", "j2", timeout_sec=10, poll_sec=5.0, log=lambda m: None)
    finally:
        hc._request, hc.time.sleep = orig_req, orig_sleep  # type: ignore[assignment]
    assert sleeps == [5.0, 5.0]
