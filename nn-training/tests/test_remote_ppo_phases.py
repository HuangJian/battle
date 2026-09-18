"""R2c-3：远端 PPO 三相驱动（`TrainingSteps._remote_ppo_step`）的让位语义。

拆相的全部价值都在这几条性质上，逐条钉住：

1. **未就绪 + 可续跑 ⇒ 让位**（`wait_for`），且**不取结果、不落位**；
2. **会话不重建**：再次进入同一步时不能再发布一遍（否则每次让位都会重传几十 MB
   payload、并在云上留下第二份 job）；
3. **不可续跑 ⇒ 就地阻塞取结果**（组合路径语义：没人接手，让位等于永远不回来）；
4. **失败走同一份判决**（`_handle_remote_failure`）：`True` ⇒ 上抛（本轮失败），
   `False` ⇒ 已降级给本机（调用方接着跑本机 PPO）。

`_remote_ppo_step` 是 `TrainingSteps` 的普通方法（不碰 torch / 网络 / 盘），所以这里直接
实例化 mixin 并把四个相位方法换成假件——被测的正是「三相怎么串」这件事本身。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from remote.protocol import JobFailedError
from rl.loop_round import RemotePpoJob, RoundContext
from rl.loop_steps import TrainingSteps


def _stub(obj: object, name: str, fn: object) -> None:
    setattr(obj, name, fn)


def _sess(it: int = 1) -> RemotePpoJob:
    return RemotePpoJob(
        it=it,
        jid=f"job-{it}",
        manifest={"job_id": f"job-{it}"},
        transport="hub",
        hub_url="http://hub.invalid",
        hub_token="t",
        timeout_sec=60.0,
    )


class _Rig:
    """搭一台「只记调用」的三相假件，把相位方法替换进真 mixin 实例。"""

    def __init__(self, *, probe_result: dict | None, policy_raises: bool = True) -> None:
        self.step = TrainingSteps()
        self.step.args = types.SimpleNamespace(ppo="remote", remote_degrade_after=0)
        self.step._remote_fail = 0
        self.step._remote_degraded = False
        self.calls: list[str] = []
        self.published: list[RemotePpoJob] = []
        self.landed: list[dict] = []
        self.policy_excs: list[BaseException] = []
        #: 策略返回 True = 上抛；False = 已降级
        self.policy_says_raise = policy_raises
        self.probe_result = probe_result

        def publish(it: int) -> RemotePpoJob:
            self.calls.append("publish")
            sess = _sess(it)
            self.published.append(sess)
            return sess

        def probe(sess: RemotePpoJob) -> dict | None:
            self.calls.append("probe")
            return self.probe_result

        def fetch(sess: RemotePpoJob) -> dict:
            self.calls.append("fetch")
            return {"agg": {}, "job": sess.jid}

        def land(sess: RemotePpoJob, result: dict) -> dict:
            self.calls.append("land")
            self.landed.append(result)
            return result

        def policy(it: int, e: BaseException) -> bool:
            self.calls.append("policy")
            self.policy_excs.append(e)
            if not self.policy_says_raise:
                # 模拟真策略的降级副作用（`args.ppo` 置 local + 标记）
                self.step.args.ppo = "local"
                self.step._remote_degraded = True
            return self.policy_says_raise

        _stub(self.step, "_remote_ppo_publish", publish)
        _stub(self.step, "_remote_ppo_probe", probe)
        _stub(self.step, "_remote_ppo_fetch", fetch)
        _stub(self.step, "_remote_ppo_land", land)
        _stub(self.step, "_handle_remote_failure", policy)


def test_not_ready_and_resumable_yields_without_fetch() -> None:
    """★ 让位点的形状：已发布、未就绪 ⇒ 只留一句 wait（不取结果、不落位）。"""
    rig = _Rig(probe_result=None)
    ctx = RoundContext(it=1, resumable=True)
    res = rig.step._remote_ppo_step(ctx)
    assert res is not None and res.is_wait
    assert "job-1" in res.reason  # 在等谁，写进理由（读面/事故考古靠它）
    assert rig.calls == ["publish", "probe"]
    assert ctx.remote is not None and ctx.remote.jid == "job-1"


def test_reentry_reuses_the_session_instead_of_republishing() -> None:
    """★ 让位后回来：**不能**再发布一遍（重传几十 MB + 云上多一份 job）。"""
    rig = _Rig(probe_result=None)
    ctx = RoundContext(it=1, resumable=True)
    rig.step._remote_ppo_step(ctx)
    rig.calls.clear()
    rig.probe_result = {"agg": {"kl": 0.0}}  # 「云机回传了」
    res = rig.step._remote_ppo_step(ctx)
    assert res is None
    assert rig.calls == ["probe", "land"]  # 没有第二次 publish / fetch
    assert len(rig.published) == 1
    assert ctx.remote is None  # 收口后置空（下一轮重新发布）


def test_not_resumable_blocks_and_lands() -> None:
    """组合语义（`resumable=False`）：就地阻塞取结果——没人接手，让位等于不回来。"""
    rig = _Rig(probe_result=None)
    ctx = RoundContext(it=1)  # 默认不可续跑
    assert rig.step._remote_ppo_step(ctx) is None
    assert rig.calls == ["publish", "probe", "fetch", "land"]
    assert rig.landed[0]["job"] == "job-1"


def test_ready_probe_lands_without_fetch() -> None:
    rig = _Rig(probe_result={"agg": {}, "job": "job-1"})
    ctx = RoundContext(it=1, resumable=True)
    assert rig.step._remote_ppo_step(ctx) is None
    assert rig.calls == ["publish", "probe", "land"]
    assert rig.step._remote_fail == 0  # 成功即复位连败（下一段失败从头计）


def test_probe_terminal_failure_goes_through_the_policy_and_reraises() -> None:
    """410（终局）不是「还没好」：进策略，策略说上抛就上抛（→ 本轮失败/停腿）。"""
    rig = _Rig(probe_result=None)

    def boom(_sess: RemotePpoJob) -> dict | None:
        raise JobFailedError("job-1 失败: bun 未安装", kind="ProtocolError")

    _stub(rig.step, "_remote_ppo_probe", boom)
    with pytest.raises(JobFailedError):
        rig.step._remote_ppo_step(RoundContext(it=1, resumable=True))
    assert rig.calls[-1] == "policy"
    assert isinstance(rig.policy_excs[0], JobFailedError)


def test_publish_failure_can_degrade_to_local() -> None:
    """发布失败也走**同一份**判决：策略说降级 ⇒ 返回 None 且 `args.ppo` 已置 local。"""
    rig = _Rig(probe_result=None, policy_raises=False)

    def boom(_it: int) -> RemotePpoJob:
        raise JobFailedError("job 失败: 节点干不了", kind="ProtocolError")

    _stub(rig.step, "_remote_ppo_publish", boom)
    ctx = RoundContext(it=1, resumable=True)
    assert rig.step._remote_ppo_step(ctx) is None
    assert rig.step.args.ppo == "local"  # 调用方据此接着跑本机 PPO
    assert rig.step._remote_degraded is True
    assert ctx.remote is None  # 没发布成功 ⇒ 不留半个会话


def test_real_policy_is_wired_into_the_phase_driver(
    tmp_path: Path,
) -> None:
    """真策略（不是假件）连进三相驱动：连败达阈值 ⇒ 降级到本机并可继续跑。

    这条用例的目的是钉**接线**：三相里的任何一段失败都必须走进 `_handle_remote_failure`
    的那套计数/停腿/降级判决（`tests/test_remote_degrade.py` 已验证判决本身）。
    """
    from remote.hub_client import HubClientError

    st = TrainingSteps()
    st.args = types.SimpleNamespace(ppo="remote", remote_degrade_after=1)
    st._remote_fail = 1  # 已经连败一次 ⇒ 本次失败即达阈值
    st._jsonl_path = tmp_path / "training_log.jsonl"
    st._remote_degraded = False
    _stub(st, "_ensure_local_ppo_stack", lambda: None)  # 不真建 torch 栈

    def boom(_it: int) -> RemotePpoJob:
        # 502 = 瞬时错误（不是 401/403 那类「重试无意义」），所以会走到阈值判定
        raise HubClientError("probe_job_result: HTTP 502: bad gateway")

    _stub(st, "_remote_ppo_publish", boom)
    ctx = RoundContext(it=3, resumable=True)
    assert st._remote_ppo_step(ctx) is None  # 降级 ⇒ 调用方接着跑本机 PPO
    assert st.args.ppo == "local"
    assert st._remote_degraded is True
    assert st._remote_fail == 2  # 失败已计账


def test_land_failure_goes_through_the_policy() -> None:
    """落位失败（校验不过/写盘失败）同样是远端失败——不能自成一套重试口径。"""
    rig = _Rig(probe_result={"agg": {}})

    def boom(_sess: RemotePpoJob, _result: dict) -> dict:
        raise JobFailedError("job-1 失败: init 指纹不符", kind="ProtocolError")

    _stub(rig.step, "_remote_ppo_land", boom)
    with pytest.raises(JobFailedError):
        rig.step._remote_ppo_step(RoundContext(it=1, resumable=True))
    assert rig.calls[-1] == "policy"
