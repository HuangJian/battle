"""M1（plan/ppo-job-lifecycle-ownership §2）：远端 PPO 的 **lost 看门狗** + 等待打点。

被测 = `TrainingRemoteDrive._remote_ppo_step` 在「已发布、未就绪、可让位」那一支上的行为：

1. hub 侧持续答「不认识这个 jid」（`/jobs/{id}/status` 404）超过宽限 ⇒ 撤单，
   下一拍**重发同一 jid**（幂等键不含 code/时间——「重发」就是同一份活的复活）；
2. hub 说在跑/排队（200）⇒ 不重发，且丢失观察撤表；
3. hub 答不上来（网络/5xx）⇒ **绝不**重发（判错的代价是一次完整重发 + 重算）；
4. 连败预算与远端失败**同一份**：3 次即 ABORT 停腿（不新造第二条命）；
5. 等待期每 `WAIT_REPORT_SEC` 一行打点（细粒度路径唯一的现场可见性）。

时钟策略：**不** mock 全局 `time`——直接操纵 `RemotePpoJob` 上的三个时间戳字段
（`t_ppo` / `lost_since` / `last_lost_probe`），每一步都是确定性图景（跨平台、无 sleep）。
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

import remote.hub_http as hub_http
import trainer.loop_remote_drive as drive_mod
from trainer.loop_remote_drive import LOST_GRACE_SEC, LOST_PROBE_SEC
from trainer.loop_steps import TrainingSteps
from worker.loop_round import RemotePpoJob, RoundContext

JID = "j" * 16


def _sess(it: int = 7) -> RemotePpoJob:
    """与生产发布同形状的会话（hub_url 假域名；本文件不打真网络——liveness/_request 都被替掉）。"""
    now = time.time()
    return RemotePpoJob(
        it=it,
        jid=JID,
        manifest={"job_id": JID, "it": it, "runId": "r-current"},
        transport="hub",
        hub_url="http://hub.invalid",
        hub_token="t",
        job_root="",
        timeout_sec=60.0,
        t_ppo=now,
        last_wait_report=now,  # 打点已最新 ⇒ 默认测试不触发它（专测另有开关）
    )


def _stub(obj: object, name: str, fn: object) -> None:
    """把相位方法换成假件（与 `tests/worker/test_remote_ppo_phases.py` 同一形状）。"""
    setattr(obj, name, fn)


class _Rig:
    """只记调用的三相假件 + 被替换的看门狗副作用（撤单/连败/打点都进列表）。"""

    def __init__(self, liveness: str, *, stub_report: bool = True) -> None:
        self.step = TrainingSteps()
        self.step.args = types.SimpleNamespace()
        self.step._remote_fail = 0
        self.step._leg_abort = False
        self.liveness = liveness
        self.calls: list[str] = []
        self.cancelled: list[str] = []
        self.bumps: list[str] = []
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

        def bump(it: int, what: str) -> bool:
            self.bumps.append(what)
            return False

        _stub(self.step, "_remote_ppo_publish", publish)
        _stub(self.step, "_remote_ppo_probe", probe)
        _stub(self.step, "_remote_ppo_fetch", lambda _s: {"agg": {}})
        _stub(self.step, "_remote_ppo_land", lambda _s, r: r)
        _stub(self.step, "_cancel_remote_job", cancel)
        _stub(self.step, "_bump_remote_fail", bump)
        if stub_report:
            _stub(self.step, "_remote_wait_report", lambda sess, now: None)


def _patch_liveness(monkeypatch, rig: _Rig) -> None:
    monkeypatch.setattr(hub_http, "job_liveness", lambda *a, **k: rig.liveness)


def _due(sess: RemotePpoJob) -> None:
    """把探针节拍拨到「该问一次了」（宽限不动）。"""
    sess.last_lost_probe = time.time() - (LOST_PROBE_SEC + 1.0)


# ---------------------------------------------------------------- 判 lost（重发）


def test_lost_after_grace_republishes_the_same_jid(monkeypatch) -> None:
    """★ 主判据：404 持续 ≥ 宽限 ⇒ 撤单；下一拍重发**同一 jid**、同一 it。"""
    rig = _Rig(hub_http.JOB_UNKNOWN)
    _patch_liveness(monkeypatch, rig)
    ctx = RoundContext(it=7, resumable=True)

    # 第一拍：发布 + 探针未就绪 + 第一次 status 询问 → 起表，不撤。
    first = rig.step._remote_ppo_step(ctx)
    assert first is not None and first.is_wait
    assert rig.cancelled == [] and rig.calls == ["publish", "probe"]
    sess = ctx.remote
    assert sess is not None and sess.lost_since > 0.0

    # 宽限内的第二次询问：仍不撤（宽限是必须的——publish→hub 扫到目录有秒级窗口）。
    _due(sess)
    sess.lost_since = time.time() - (LOST_GRACE_SEC - 1.0)
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == []

    # 宽限过后：判 lost ⇒ 撤单 + 丢会话。
    _due(sess)
    sess.lost_since = time.time() - (LOST_GRACE_SEC + 1.0)
    out = rig.step._remote_ppo_step(ctx)
    assert out is not None and out.is_wait and out.reason  # 让位理由非空（措辞不钉）
    assert rig.cancelled == [JID]
    assert len(rig.bumps) == 1  # 判 lost 走**同一份**连败预算（一条，不多不少）
    assert ctx.remote is None

    # 下一拍：正常发布路径重发同一 jid（幂等键不含 code/时间）。
    assert rig.step._remote_ppo_step(ctx) is not None
    assert [s.jid for s in rig.published] == [JID, JID]
    assert [s.it for s in rig.published] == [7, 7]
    assert rig.calls.count("publish") == 2


def test_alive_status_clears_the_observation_and_never_republishes(monkeypatch) -> None:
    """反向：hub 认识它（排队/在跑/完成任意 state）⇒ 不重发，且清掉丢失观察。"""
    rig = _Rig(hub_http.JOB_ALIVE)
    _patch_liveness(monkeypatch, rig)
    ctx = RoundContext(it=7, resumable=True)
    rig.step._remote_ppo_step(ctx)
    sess = ctx.remote
    assert sess is not None
    _due(sess)
    sess.lost_since = time.time() - (LOST_GRACE_SEC + 100.0)
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == []
    assert sess.lost_since == 0.0  # 恢复可见 ⇒ 撤表


def test_unreachable_status_never_republishes(monkeypatch) -> None:
    """反向：hub 答不上来（网络/5xx）⇒ 不重发；表**不清也不据此判 lost**。"""
    rig = _Rig(hub_http.JOB_UNREACHABLE)
    _patch_liveness(monkeypatch, rig)
    ctx = RoundContext(it=7, resumable=True)
    rig.step._remote_ppo_step(ctx)
    sess = ctx.remote
    assert sess is not None
    _due(sess)
    sess.lost_since = time.time() - (LOST_GRACE_SEC + 100.0)
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == []
    assert sess.lost_since > 0.0  # 不动表
    assert rig.calls.count("publish") == 1


def test_probe_throttle_limits_status_queries(monkeypatch) -> None:
    """节流：两次询问之间不足 `LOST_PROBE_SEC` ⇒ 不再问 hub（不把它变成轮询热点）。"""
    rig = _Rig(hub_http.JOB_UNKNOWN)
    queried: list[str] = []

    def liveness(_base: str, _token: str, jid: str, **_kw: object) -> str:
        queried.append(jid)
        return rig.liveness

    monkeypatch.setattr(hub_http, "job_liveness", liveness)
    ctx = RoundContext(it=7, resumable=True)
    rig.step._remote_ppo_step(ctx)  # 第一问
    assert len(queried) == 1
    for _ in range(5):  # 连续 5 拍（同一发布轮）：节流吃掉
        rig.step._remote_ppo_step(ctx)
    assert len(queried) == 1


def test_push_transport_is_untouched(monkeypatch) -> None:
    """push 腿（直推节点）状态面不同——本刀不动它（Todo 写明；不得假装覆盖）。"""
    rig = _Rig(hub_http.JOB_UNKNOWN)
    queried: list[str] = []

    def liveness(_base: str, _token: str, jid: str, **_kw: object) -> str:
        queried.append(jid)
        return rig.liveness

    monkeypatch.setattr(hub_http, "job_liveness", liveness)
    ctx = RoundContext(it=7, resumable=True)
    rig.step._remote_ppo_step(ctx)
    sess = ctx.remote
    assert sess is not None
    sess.transport = "push"
    _due(sess)
    sess.lost_since = time.time() - (LOST_GRACE_SEC + 100.0)
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == []
    assert queried == [JID]  # 只在 transport=="hub" 时问过一次


# ---------------------------------------------------------------- 连败预算（共用）


def test_bump_remote_fail_aborts_at_three_and_writes_verdict(tmp_path: Path) -> None:
    """★ 3 次即 ABORT（与远端失败同一份阈值/判决）；不再有第二条 `LOST_REPUBLISH_MAX`。

    用**真**策略方法（不经 rig 的假件）——这条判据的全部价值就在「只有一份阈值」。
    """
    jsonl = tmp_path / "training_log.jsonl"
    obj = TrainingSteps()
    obj.args = types.SimpleNamespace()
    obj._jsonl_path = jsonl
    obj._remote_fail = 0
    obj._leg_abort = False
    assert obj._bump_remote_fail(7, "第一次丢失") is False
    assert obj._bump_remote_fail(7, "第二次丢失") is False
    assert obj._leg_abort is False
    assert obj._bump_remote_fail(7, "第三次丢失") is True
    assert obj._leg_abort is True
    events = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").strip().splitlines()]
    assert any(e.get("event") == "gate_verdict" and e.get("verdict") == "ABORT" for e in events)


# ---------------------------------------------------------------- 等待打点


def test_wait_report_line_when_period_elapses(monkeypatch) -> None:
    """细粒度路径等满一个 `WAIT_REPORT_SEC` ⇒ 必有一行「在等什么」（可 grep 的现场可见性）。"""
    rig = _Rig(hub_http.JOB_ALIVE, stub_report=False)
    _patch_liveness(monkeypatch, rig)
    monkeypatch.setattr(drive_mod, "log", rig.logs.append)
    # hub 侧观测走 _request：这里全部失败（best-effort —— 打点不允许依赖网络成败）。
    monkeypatch.setattr(hub_http, "_request", lambda *a, **k: (_ for _ in ()).throw(OSError("no net")))

    ctx = RoundContext(it=7, resumable=True)
    rig.step._remote_ppo_step(ctx)
    sess = ctx.remote
    assert sess is not None
    assert rig.logs == []  # 第一拍不打（节流从 t_ppo 起算）
    sess.last_wait_report = time.time() - (hub_http.WAIT_REPORT_SEC + 1.0)
    rig.step._remote_ppo_step(ctx)
    assert len(rig.logs) == 1
    # 打点行就是交付物（操作员唯一的现场读数）：场次名 + 已等时长必须在同一行上。
    assert rig.logs[0].startswith(f"[run_rl] job {JID} ") and "已等" in rig.logs[0]
    # 再拍：节流内不再打。
    rig.step._remote_ppo_step(ctx)
    assert len(rig.logs) == 1


# ---------------------------------------------------------------- 复活行 → 重新可领


def test_republished_job_is_claimable_again(tmp_path: Path) -> None:
    """★「撤单 + 重发同 jid」的落点判据：账本净态回到 pending ⇒ hub 认领闸重新放行。

    （六轮 F1 的净态对账是既有机制；本用例把它与 M1 的重发语义钉在一起——没有它，
    「账本写出去了、hub 仍然不派」的旧形状会原样回来。）
    """
    from hub.store import _JobStore

    job_root = tmp_path / "remote-jobs"
    jsonl = tmp_path / "training_log.jsonl"
    jd = job_root / JID
    jd.mkdir(parents=True)
    (jd / "manifest.json").write_text(
        json.dumps({"job_id": JID, "it": 7, "runId": "r-current"}), encoding="utf-8"
    )
    (jd / "payload.tar.xz").write_bytes(b"x")
    rows = [
        {"event": "job_pending", "job_id": JID, "runId": "r-current", "it": 7, "ts": 1.0},
        {"event": "job_cancelled", "job_id": JID, "it": 7, "reason": "lost", "ts": 2.0},
    ]
    jsonl.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    store = _JobStore(job_root, jsonl)
    assert store.claimable_job_ids() == []  # 撤销后：不可领

    # 重发（publish_job 的复活行）——同 jid、晚于 job_cancelled 的 job_pending。
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"event": "job_pending", "job_id": JID, "runId": "r-current", "it": 7, "ts": 3.0}
            )
            + "\n"
        )
    assert store.claimable_job_ids() == [JID]  # 净态回到 pending ⇒ 重新可领
