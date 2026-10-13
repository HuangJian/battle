"""M3（plan/ppo-job-lifecycle-ownership §4）：「重发本轮 PPO」意图的**消费语义**（细粒度路径）。

被测 = `TrainingRemoteDrive._maybe_consume_redo` 接在 `_remote_ppo_step` 那一拍上的行为，
意图文件 = 控制台写的 `tmp/<课>/redo-ppo.json`（形状 `{version,it,at}`，契约住
`trainer/loop_control.py`，控制台半边由 `dashboard/tests/redo-ppo.test.ts` 钉）。

四条性质（每条都是「不做就会静默地不对」的那类）：

1. **命中即撤 + 同 jid 重发**：撤单只是「不再等这份活」——下一拍走正常发布路径，
   幂等键不含 code/时间 ⇒ **同一 jid** 目录重建（同一份活复活，不是第二份）；
2. **按 `at` 去重**：意图文件留在盘上（人不会去删它），没有去重键就会**每一拍**再撤一次
   刚重发的活 ⇒ 无限重发（比不修更坏）；
3. **`it` 对不上就原地等**：正数 `it` 只对那一轮生效（不抢跑到别的轮）；
4. **坏文件只喊一次、绝不动作**（保守方向 = 继续训练，与 `ControlApplier` 同口径）。
"""

from __future__ import annotations

import json
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import trainer.loop_remote_drive as drive_mod
from trainer.loop_control import read_redo_applied
from trainer.loop_steps import TrainingSteps
from worker.loop_round import RemotePpoJob, RoundContext

JID = "j" * 16
IT = 7


def _sess(it: int = IT) -> RemotePpoJob:
    return RemotePpoJob(
        it=it,
        jid=JID,
        manifest={"job_id": JID, "it": it, "runId": "r-current"},
        transport="hub",
        hub_url="http://hub.invalid",
        hub_token="t",
        job_root="",
        timeout_sec=60.0,
        t_ppo=time.time(),
        last_wait_report=time.time(),
    )


def _stub(obj: object, name: str, fn: object) -> None:
    """把相位方法换成假件（与 `tests/worker/test_remote_ppo_phases.py` 同一形状）。"""
    setattr(obj, name, fn)


class _Rig:
    """三相假件 + 真 `_maybe_consume_redo`（被测的就是「意图怎么被消费」这件事本身）。"""

    def __init__(self, traj_root: Path) -> None:
        self.step = TrainingSteps()
        self.step.args = types.SimpleNamespace()
        self.step._remote_fail = 0
        self.step._leg_abort = False
        self.step._traj_root = traj_root  # 课程级目录：意图文件就住这里（不是 it<N>/）
        self.step._redo_err_note = ""  # TrainingLoop.__init__ 的槽位（声明式；rig 自己填）
        self.calls: list[str] = []
        self.cancelled: list[str] = []
        self.published: list[RemotePpoJob] = []
        self.logs: list[str] = []

        def publish(it: int) -> RemotePpoJob:
            self.calls.append("publish")
            s = _sess(it=it)
            self.published.append(s)
            return s

        def probe(_sess: RemotePpoJob) -> dict | None:
            self.calls.append("probe")
            return None

        def cancel(sess: RemotePpoJob, *, it: int, reason: str) -> None:
            self.cancelled.append(sess.jid)

        def never_lost(sess: RemotePpoJob, it: int) -> bool:
            # 看门狗在「hub 侧不认识」时才动作；本文件的假 probe 恒 None ⇒ 让它永不判 lost
            # （否则三拍之后 lost 宽限会插进来，把 M3 的行为淹掉）。
            return False

        _stub(self.step, "_remote_ppo_publish", publish)
        _stub(self.step, "_remote_ppo_probe", probe)
        _stub(self.step, "_remote_ppo_fetch", lambda _s: {"agg": {}})
        _stub(self.step, "_remote_ppo_land", lambda _s, r: r)
        _stub(self.step, "_cancel_remote_job", cancel)
        _stub(self.step, "_remote_wait_report", lambda sess, now: None)
        _stub(self.step, "_remote_ppo_lost", never_lost)


def _write_intent(traj: Path, *, it: int, at: float) -> None:
    """控制台那一侧写意图（形状与 `dashboard/src/server/actions/redo-ppo.ts` 逐字同）。"""
    traj.mkdir(parents=True, exist_ok=True)
    (traj / "redo-ppo.json").write_text(
        json.dumps({"version": 1, "it": it, "at": at}), encoding="utf-8"
    )


def test_redo_cancels_then_republishes_the_same_jid_once(tmp_path: Path) -> None:
    """★ 主判据：撤单 + **同 jid** 重发 + 写回执（去重键 = 意图的 at）。"""
    rig = _Rig(tmp_path)
    at = 1758000000.5
    _write_intent(tmp_path, it=0, at=at)
    ctx = RoundContext(it=IT, resumable=True)

    # 第一拍：发布（还没有会话可撤——意图在下一拍才消费，因为它必须先有一个在飞会话）。
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == []

    # 第二拍：命中意图 ⇒ 撤当前 sess、丢它、同拍重新发布。
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == [JID]
    assert [s.jid for s in rig.published] == [JID, JID]
    assert [s.it for s in rig.published] == [IT, IT]
    # 回执：`at` = 被消费的意图时间戳（去重键原样回写），`it` = 消费它的那一轮。
    assert read_redo_applied(tmp_path) == (IT, at)

    # 第三拍：同一份意图还在盘上，但回执的 at 与它相等 ⇒ **不得**再撤（否则无限重发）。
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == [JID]
    assert rig.calls.count("publish") == 2


def test_redo_targeting_another_iter_waits_without_action(tmp_path: Path) -> None:
    """`it` 指未来/别的轮 ⇒ 不动作、不写回执（等对上的那一轮，不抢跑）。"""
    rig = _Rig(tmp_path)
    _write_intent(tmp_path, it=IT + 5, at=1758000000.5)
    ctx = RoundContext(it=IT, resumable=True)
    rig.step._remote_ppo_step(ctx)
    rig.step._remote_ppo_step(ctx)
    assert rig.cancelled == []
    assert rig.calls.count("publish") == 1
    assert read_redo_applied(tmp_path) == (0, 0.0)


def test_redo_current_iter_is_consumed_by_any_iter_when_it_is_zero(tmp_path: Path) -> None:
    """`it<=0` = 对**当前轮**生效：切到别的轮也照常命中（控制台缺省语义）。"""
    rig = _Rig(tmp_path)
    at = 1758000001.0
    _write_intent(tmp_path, it=0, at=at)
    ctx = RoundContext(it=IT + 3, resumable=True)  # 训练已经走到下一轮
    rig.step._remote_ppo_step(ctx)  # 发布
    rig.step._remote_ppo_step(ctx)  # 消费
    assert rig.cancelled == [JID]
    assert read_redo_applied(tmp_path)[1] == at


def test_broken_intent_file_is_ignored_and_warned_once(tmp_path: Path, monkeypatch) -> None:
    """★ 坏文件：一次都不动作 + **只喊一次**（每拍重放同一行 = 把日志刷成噪声）。"""
    rig = _Rig(tmp_path)
    monkeypatch.setattr(drive_mod, "log", rig.logs.append)
    (tmp_path / "redo-ppo.json").write_text("{ not json", encoding="utf-8")
    ctx = RoundContext(it=IT, resumable=True)
    rig.step._remote_ppo_step(ctx)
    rig.step._remote_ppo_step(ctx)
    rig.step._remote_ppo_step(ctx)
    assert rig.cancelled == []
    assert len(rig.logs) == 1  # 只喊一次（措辞不钉：钉的是「喊了没、喊了几次」）
    assert read_redo_applied(tmp_path) == (0, 0.0)


def test_no_intent_file_is_a_plain_steady_state(tmp_path: Path, monkeypatch) -> None:
    """没有意图（正常态）：零动作、零日志——这条路径每拍都过，不能有任何噪声。"""
    rig = _Rig(tmp_path)
    monkeypatch.setattr(drive_mod, "log", rig.logs.append)
    ctx = RoundContext(it=IT, resumable=True)
    rig.step._remote_ppo_step(ctx)
    rig.step._remote_ppo_step(ctx)
    assert rig.cancelled == [] and rig.logs == []
    assert rig.calls == ["publish", "probe", "probe"]
