"""tests/test_remote_degrade.py —— R9 远端失败降级（plan/feasibility-map.md §12 + T7）。

2026-09-15 T7：**默认不再自动降级**（`remote_degrade_after` 默认 0）。原默认 3 会
在 remote 模式撞上 `ppo_backend=None`（x3-power it1 `None.load_episodes`）。
控制台启动弹窗 opt-in 打开后，降级前必须 `_ensure_local_ppo_stack()`。

本文件锁死三档处置与**退避**行为：

  * 成功 → 失败计数复位；
  * 连败未达阈值 → 原样抛出（交给 loop 原地重试，既有语义不变）；
  * 连败达阈值（N>0 opt-in）→ 先建本机栈 + `args.ppo` 改 local + `remote_degrade` 事件；
  * 默认 N=0 → 连败 3 次写 `gate_verdict: ABORT` 并停腿（不切本机）；
  * `wait_job` 轮询：网络错误/5xx 指数退避，404（正常排队）不退避。

免 torch：只构造 stub 承载 `TrainingSteps._remote_ppo_or_degrade`，不碰训练后端。
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
    """承载被测方法的最小宿主：继承真 mixin 取 `_remote_ppo_or_degrade`，只覆盖
    `_remote_ppo`（真远端要 hub/隧道/打包，这里不需要）。"""

    def __init__(
        self,
        tmp_path: Path,
        fail_times: int = 0,
        exc: BaseException | None = None,
        **args_over: object,
    ) -> None:
        # 默认 remote_degrade_after=0：与 CLI 新默认一致（不自动降级）。
        # 测 N>0 路径时由用例显式传入。
        base = {"ppo": "remote", "remote_degrade_after": 0}
        base.update(args_over)
        self.args = SimpleNamespace(**base)  # type: ignore[arg-type]
        self._jsonl_path = tmp_path / "training_log.jsonl"
        self._jsonl_path.write_text("", encoding="utf-8")
        self._remote_fail = 0
        self._remote_degraded = False
        self._leg_abort = False
        self.remote_calls: list[int] = []
        self.fail_times = fail_times
        #: 抛出的异常（默认 TimeoutError；测 4xx 立即停腿时传 HubClientError）
        self.exc = exc
        self.ensure_local_calls = 0
        # 模拟 remote 模式 D2：栈为空（降级前不得直接 load_episodes）。
        self.ppo_backend = None
        self._model = None

    def _ensure_local_ppo_stack(self) -> None:
        """测试替身：只记调用次数（真身在 loop_core，需 torch）。"""
        self.ensure_local_calls += 1
        self.ppo_backend = object()  # type: ignore[assignment]
        self._model = object()

    def _remote_ppo(self, it: int, rollout_spec: dict | None = None) -> dict:
        """测试替身：签名必须与真身一致（真身 M3 后多一个 rollout_spec 形参并返回 result）。"""
        self.remote_calls.append(it)
        if len(self.remote_calls) <= self.fail_times:
            if self.exc is not None:
                raise self.exc
            raise TimeoutError(f"wait_job 超时（>{1800}s）")
        return {}

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
    st = _Stub(tmp_path, fail_times=5, remote_degrade_after=3)
    with pytest.raises(TimeoutError):
        st._remote_ppo_or_degrade(1)
    with pytest.raises(TimeoutError):
        st._remote_ppo_or_degrade(1)
    assert st._remote_fail == 2
    assert st.args.ppo == "remote"  # 还没降级
    assert st.events() == []


def test_degrade_to_local_after_threshold(tmp_path: Path) -> None:
    """达阈值（opt-in N>0）：先建本机栈 + args.ppo → local + remote_degrade 事件。"""
    st = _Stub(tmp_path, fail_times=3, remote_degrade_after=3)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            st._remote_ppo_or_degrade(5)
    assert st._remote_ppo_or_degrade(5) is False  # 第 3 次 → 降级
    assert st.args.ppo == "local"
    # T7：降级前必须懒加载本机 PPO 栈（否则 None.load_episodes）。
    assert st.ensure_local_calls == 1
    assert st.ppo_backend is not None
    ev = st.events()
    assert [e["event"] for e in ev] == ["remote_degrade"]
    assert ev[0]["iter"] == 5 and ev[0]["after_failures"] == 3
    # 降级后方法不再调远端（调用方已走本地路径）
    assert len(st.remote_calls) == 3


def test_default_is_no_auto_degrade(tmp_path: Path) -> None:
    """T7：默认 remote_degrade_after=0 → 连败 3 次 ABORT，绝不静默切本机。"""
    st = _Stub(tmp_path, fail_times=3)  # 使用默认（0）
    assert st.args.remote_degrade_after == 0
    for _ in range(2):
        with pytest.raises(TimeoutError):
            st._remote_ppo_or_degrade(2)
    with pytest.raises(TimeoutError):
        st._remote_ppo_or_degrade(2)
    assert st.args.ppo == "remote"
    assert st.ensure_local_calls == 0  # 默认路径永不拉起本机栈
    assert st._leg_abort is True
    assert [e["event"] for e in st.events()] == ["gate_verdict"]


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
    assert st.ensure_local_calls == 0


def test_fatal_remote_http_aborts_on_first_failure(tmp_path: Path) -> None:
    """401/403/400 = 鉴权/闭锁类失败：**第一次**就写 ABORT 停腿，不走连败计数。

    2026-09-16 x3-step 事故：hub 把回环 IP 封掉后每次 `wait_job` 都是 403，而
    `HubClientError` 原先不在本方法的捕获白名单里 ⇒ 冒泡到 `loop_core` 的通用兜底，
    白烧 5×30s 重试（每次还重新 publish 同一 job）后才被杀进程。
    """
    from remote.hub_client import HubClientError

    st = _Stub(
        tmp_path,
        fail_times=9,
        exc=HubClientError('wait_job: HTTP 403: {"error": "ip blocked"}'),
    )
    with pytest.raises(HubClientError):
        st._remote_ppo_or_degrade(3)
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT" and ev[0]["iter"] == 3
    assert "HTTP 403" in ev[0]["reason"]
    assert st._leg_abort is True
    assert st.args.ppo == "remote" and st.ensure_local_calls == 0
    assert len(st.remote_calls) == 1  # 没有重试


def test_retryable_http_still_counts_consecutive(tmp_path: Path) -> None:
    """5xx 属可重试：不写「立即 ABORT」，仍按连败计数（默认 0 → 3 次后停腿）。"""
    from remote.hub_client import HubClientError

    st = _Stub(tmp_path, fail_times=9, exc=HubClientError("wait_job: HTTP 500: boom"))
    for _ in range(2):
        with pytest.raises(HubClientError):
            st._remote_ppo_or_degrade(4)
    assert st.events() == []  # 未达 3 次：不写判决
    with pytest.raises(HubClientError):
        st._remote_ppo_or_degrade(4)
    assert st._remote_fail == 3
    assert st._leg_abort is True


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


def test_node_failure_in_remote_retryable_set() -> None:
    """`JobFailedError` 必须在捕获集合里。

    不在集合里就会冒泡到 `loop_core` 的通用兜底（连败即杀进程），专为远端失败写的
    停腿判决一行不写——x3-step 事故（`HubClientError` 缺席）的同一个坑。
    """
    from rl.loop_steps import remote_retryable_exceptions

    assert JobFailedError in remote_retryable_exceptions()


def test_node_failure_aborts_on_first_failure_with_reason(tmp_path: Path) -> None:
    """节点确定性失败（已回报原因）：第一次就带原因 ABORT 停腿，不重试也不降级。

    2026-09-17：`bun` 装不上 / TS 运行时取不到这类失败过去在训练侧只表现为
    `wait_job` 25 分钟超时（原因留在云机日志里），而每次重试再白烧一个超时窗口。
    现在原因随 `JobFailedError` 到达 ⇒ 判决里写的是**真原因**，一眼能修。
    """
    st = _Stub(
        tmp_path,
        fail_times=9,
        exc=JobFailedError(
            "job j 失败: bun 未安装（PATH 里没有 bun）[kind=ProtocolError]",
            kind="ProtocolError",
        ),
    )
    with pytest.raises(JobFailedError):
        st._remote_ppo_or_degrade(6)
    ev = st.events()
    assert [e["event"] for e in ev] == ["gate_verdict"]
    assert ev[0]["verdict"] == "ABORT" and ev[0]["iter"] == 6
    assert "bun 未安装" in ev[0]["reason"]
    assert st._leg_abort is True
    assert st._remote_fail == 0  # 确定性失败不消耗连败配额
    assert len(st.remote_calls) == 1  # 不重试（重试只会再撞同一堵墙）
    assert st.args.ppo == "remote" and st.ensure_local_calls == 0  # 也不静默降级
