"""远端失败判决（原 tests/test_remote_degrade.py 的去降级版，2026-09-21 §3）。

★ 单一 PPO 路径（plan/accident.plan.md §3）：**没有**"下沉到本机算"这一档 ——
`_handle_remote_failure` 恒返回 True（调用方上抛 = 本轮失败），连败 3 次写
`gate_verdict: ABORT` 停腿。要本机算，操作员在控制台起本机 worker（与云机走同一
认领协议），而不是训练进程偷偷把 job 算在自己身上（C 腿事故的根）。

本文件锁死各档处置：

  * `JobFailedError`（节点已回报原因）→ **第一次**就带原因 ABORT，不消耗连败配额、不重试；
  * 401/403/400（鉴权/闭锁，重试无意义）→ 第一次就 ABORT，不消耗配额；
  * 5xx/网络抖动（可重试）→ 按连败计数，满 3 次 ABORT 停腿；
  * `wait_job` 轮询：网络错误/5xx 指数退避，404（正常排队）不退避。

免 torch：stub 直接把真 mixin 的 `_handle_remote_failure` 绑到最小宿主上。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote.hub_client import wait_job
from remote.protocol import JobFailedError
from rl.loop_steps import TrainingSteps


class _Stub(TrainingSteps):
    """最小宿主：继承真 mixin，拿真 `_handle_remote_failure` / `_abort_node_failure`。"""

    def __init__(self, tmp_path: Path, **args_over: object) -> None:
        self.args = SimpleNamespace(**args_over)
        self._jsonl_path = tmp_path / "training_log.jsonl"
        self._jsonl_path.write_text("", encoding="utf-8")
        self._remote_fail = 0
        self._leg_abort = False

    def events(self) -> list[dict]:
        return [
            json.loads(ln)
            for ln in self._jsonl_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]


def test_retryable_failures_abort_after_three(tmp_path: Path) -> None:
    """可重试失败：未满 3 次不写判决；第 3 次写 ABORT 并停腿（**不**下沉本机）。"""
    st = _Stub(tmp_path)
    for _ in range(2):
        assert st._handle_remote_failure(2, TimeoutError("wait_job 超时")) is True
    assert st.events() == [] and st._leg_abort is False
    assert st._handle_remote_failure(2, TimeoutError("wait_job 超时")) is True
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT" and ev[0]["iter"] == 2
    assert "无本机降级" in ev[0]["reason"]
    assert st._leg_abort is True


def test_success_resets_counter_elsewhere(tmp_path: Path) -> None:
    """连败计数的复位点在**成功路径**（`_remote_ppo_step` 落位后置 0）——这里钉住契约。"""
    st = _Stub(tmp_path)
    st._remote_fail = 2
    assert st._handle_remote_failure(1, TimeoutError("boom")) is True
    assert st._remote_fail == 3  # 判决只加不减；复位由成功路径负责


def test_fatal_remote_http_aborts_on_first_failure(tmp_path: Path) -> None:
    """401/403/400 = 鉴权/闭锁类失败：**第一次**就写 ABORT 停腿，不走连败计数。

    2026-09-16 x3-step 事故：hub 把回环 IP 封掉后每次 `wait_job` 都是 403，而
    `HubClientError` 原先不在捕获白名单里 ⇒ 冒泡到 `loop_core` 的通用兜底，白烧
    5×30s 重试后才被杀进程。
    """
    from remote.hub_client import HubClientError

    st = _Stub(tmp_path)
    st._handle_remote_failure(3, HubClientError('wait_job: HTTP 403: {"error": "ip blocked"}'))
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT" and ev[0]["iter"] == 3
    assert "HTTP 403" in ev[0]["reason"]
    assert st._leg_abort is True
    assert st._remote_fail == 0  # 确定性失败不消耗连败配额


def test_retryable_http_still_counts_consecutive(tmp_path: Path) -> None:
    """5xx 属可重试：不写「立即 ABORT」，仍按连败计数。"""
    from remote.hub_client import HubClientError

    st = _Stub(tmp_path)
    st._handle_remote_failure(4, HubClientError("wait_job: HTTP 500: boom"))
    st._handle_remote_failure(4, HubClientError("wait_job: HTTP 500: boom"))
    assert st.events() == []  # 未达 3 次：不写判决
    st._handle_remote_failure(4, HubClientError("wait_job: HTTP 500: boom"))
    assert st._remote_fail == 3
    assert st._leg_abort is True


def test_node_failure_in_remote_retryable_set() -> None:
    """`JobFailedError` 必须在捕获集合里。

    不在集合里就会冒泡到 `loop_core` 的通用兜底（连败即杀进程），专为远端失败写的
    停腿判决一行不写 —— x3-step 事故（`HubClientError` 缺席）的同一个坑。
    """
    from rl.loop_steps import remote_retryable_exceptions

    assert JobFailedError in remote_retryable_exceptions()


def test_node_failure_aborts_on_first_failure_with_reason(tmp_path: Path) -> None:
    """节点确定性失败（已回报原因）：第一次就带原因 ABORT 停腿，不重试也不下沉本机。

    2026-09-17：`bun` 装不上 / TS 运行时取不到这类失败过去在训练侧只表现为
    `wait_job` 25 分钟超时（原因留在云机日志里），每次重试再白烧一个超时窗口。
    现在原因随 `JobFailedError` 到达 ⇒ 判决里写的是**真原因**，一眼能修。
    """
    st = _Stub(tmp_path)
    st._handle_remote_failure(
        6,
        JobFailedError(
            "job j 失败: bun 未安装（PATH 里没有 bun）[kind=ProtocolError]",
            kind="ProtocolError",
        ),
    )
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT" and ev[0]["iter"] == 6
    assert "bun 未安装" in ev[0]["reason"]
    assert st._leg_abort is True
    assert st._remote_fail == 0  # 确定性失败不消耗连败配额


def test_wait_job_poll_backoff_on_network_errors() -> None:
    """退避：连续网络错误按 2 的幂退避，成功即复位（不猛敲不可达边缘）。"""
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
