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

    def __init__(self, liveness: str, *, ledger: Path, stub_report: bool = True) -> None:
        self.step = TrainingSteps()
        self.step.args = types.SimpleNamespace()
        self.step._remote_fail = 0
        self.step._leg_abort = False
        #: `_jsonl_path` 是这条腿的**声明依赖**（`TrainingRemoteFail` 里就声明了它）；看门狗的
        #: 本地账本那一支（2026-10-17）读它。夹具必须给——不给就是夹具不完整（不许静默降级）。
        #: 传 `tmp_path / "training_log.jsonl"`（不创建）就等价于「账本里没有任何行」。
        self.step._jsonl_path = ledger
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


def test_lost_after_grace_republishes_the_same_jid(tmp_path: Path, monkeypatch) -> None:
    """★ 主判据：404 持续 ≥ 宽限 ⇒ 撤单；下一拍重发**同一 jid**、同一 it。"""
    rig = _Rig(hub_http.JOB_UNKNOWN, ledger=tmp_path / "training_log.jsonl")
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


def test_alive_status_clears_the_observation_and_never_republishes(
    tmp_path: Path, monkeypatch
) -> None:
    """反向：hub 认识它（排队/在跑/完成任意 state）⇒ 不重发，且清掉丢失观察。"""
    rig = _Rig(hub_http.JOB_ALIVE, ledger=tmp_path / "training_log.jsonl")
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


def test_unreachable_status_never_republishes(tmp_path: Path, monkeypatch) -> None:
    """反向：hub 答不上来（网络/5xx）⇒ 不重发；表**不清也不据此判 lost**。"""
    rig = _Rig(hub_http.JOB_UNREACHABLE, ledger=tmp_path / "training_log.jsonl")
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


def test_probe_throttle_limits_status_queries(tmp_path: Path, monkeypatch) -> None:
    """节流：两次询问之间不足 `LOST_PROBE_SEC` ⇒ 不再问 hub（不把它变成轮询热点）。"""
    rig = _Rig(hub_http.JOB_UNKNOWN, ledger=tmp_path / "training_log.jsonl")
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


def test_push_transport_is_untouched(tmp_path: Path, monkeypatch) -> None:
    """push 腿（直推节点）状态面不同——本刀不动它（Todo 写明；不得假装覆盖）。"""
    rig = _Rig(hub_http.JOB_UNKNOWN, ledger=tmp_path / "training_log.jsonl")
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


def test_wait_report_line_when_period_elapses(tmp_path: Path, monkeypatch) -> None:
    """细粒度路径等满一个 `WAIT_REPORT_SEC` ⇒ 必有一行「在等什么」（可 grep 的现场可见性）。"""
    rig = _Rig(hub_http.JOB_ALIVE, ledger=tmp_path / "training_log.jsonl", stub_report=False)
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


# ---------------------------------------------------------------- 本地账本净态（B）
#
# 2026-10-16 事故（plan/export-must-not-cancel-live-job）：导出进程跑 M2 启动清理，把在线腿
# 正在等的那份 job 写成 `job_cancelled`（写进 hub 与 trainer **共读**的那一份 training_log.jsonl），
# 而 hub 的 `/status` 一路答 200/pending ⇒ M1 的 404 判据全程沉默，腿等 3h+。
#
# 两条钉死的边界：① 只对**本进程该自愈的那一类**撤单重发（M2 残留；白名单按 reason）；
# ② 判据必须是**净态**——`scan_jobs` 的 terminal 是「曾经终局」集合，用它会在重发后
#   一拍一撤（30s 一拍 ⇒ ~90s 内 3 连败 ABORT 停腿）。


def _pending_then_m2_cancel(tmp_path: Path) -> Path:
    """造出事故现场的那两行：在线腿的 pending + **另一个进程**（导出腿）的 M2 清理写的 cancel。

    时序就是事故时序：在线腿以 `r-current` 发布（pending）⇒ 导出腿以自己的 runId（`r-export`）
    跑 M2，把它判成「上一代遗留」作废。撤单行由**生产写入面**落
    （reader 与 writer 不是各写一份文案）。
    """
    from remote.hub_client import cancel_jobs_of_previous_runs

    jsonl = tmp_path / "training_log.jsonl"
    jsonl.write_text(
        json.dumps({"event": "job_pending", "job_id": JID, "runId": "r-current", "it": 7, "ts": 1.0})
        + "\n",
        encoding="utf-8",
    )
    out = cancel_jobs_of_previous_runs(
        jsonl, tmp_path / "remote-jobs", "r-export", log=lambda _m: None
    )
    assert out == [JID]  # 撤单行由**生产写入面**落（reader 与 writer 不许各写一份文案）
    return jsonl


def _events(jsonl: Path) -> list[str]:
    return [
        str(json.loads(x)["event"])
        for x in jsonl.read_text(encoding="utf-8").strip().splitlines()
        if x.strip()
    ]


def test_previous_run_cancel_in_the_local_ledger_self_heals(tmp_path: Path, monkeypatch) -> None:
    """★ 主判据：本地账本已撤（M2 残留）+ hub 说「还在排队」⇒ 判 lost、撤单、下拍重发同 jid。

    hub 一路答 ALIVE（事故现场形状）——只信 `/status` 的看门狗在这里什么都看不到。
    """
    jsonl = _pending_then_m2_cancel(tmp_path)
    rig = _Rig(hub_http.JOB_ALIVE, ledger=jsonl)
    _patch_liveness(monkeypatch, rig)

    def publish(it: int) -> RemotePpoJob:
        rig.calls.append("publish")
        s = _sess(it=it)
        # 重发的本地那一半（`publish_job` 无条件追加 job_pending）——净态随之回 pending。
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {"event": "job_pending", "job_id": JID, "runId": "r-current", "it": it, "ts": 9.0}
                )
                + "\n"
            )
        rig.published.append(s)
        return s

    _stub(rig.step, "_remote_ppo_publish", publish)
    # 第一拍：会话已在（本腿此前已发布），看门狗必须从**本地账本**读出「已撤」。
    ctx = RoundContext(it=7, resumable=True)
    ctx.remote = _sess()

    out = rig.step._remote_ppo_step(ctx)
    assert out is not None and out.is_wait
    assert rig.cancelled == [JID]  # 撤单（连败预算走同一份）
    assert len(rig.bumps) == 1
    assert ctx.remote is None  # 丢会话 ⇒ 下一拍走正常发布路径

    assert rig.step._remote_ppo_step(ctx) is not None  # 重发同 jid
    assert [s.jid for s in rig.published] == [JID]  # 重发的是**同一份活**（幂等键不含 code/时间）
    assert _events(jsonl) == ["job_pending", "job_cancelled", "job_pending"]

    # ★ 再拍（探针拨到期）：净态已回 pending ⇒ 绝不能再判 lost。
    #   用 `scan_jobs` 的 terminal（曾经终局）实现时，这一步会再撤一次 ⇒ 90s 三连败停腿。
    sess = ctx.remote
    assert sess is not None
    _due(sess)
    assert rig.step._remote_ppo_step(ctx) is not None
    assert rig.cancelled == [JID]
    assert len(rig.bumps) == 1
    assert rig.calls.count("publish") == 1  # 净态回 pending ⇒ 不再重发


def test_operator_cancel_is_not_reverted_by_the_leg(tmp_path: Path, monkeypatch) -> None:
    """反向：人工/批量撤单（不是 M2 残留）⇒ **不自愈**。

    三类写者往同一份账本落 `job_cancelled`：M2 腿启动清理 / 人工 `POST /admin/cancel-job` /
    `cancel_unsettled_jobs`。无差别自愈会替操作员反悔（还顺带 `unfreeze_job` 解掉人工冻结）
    ——白名单只认 M2 残留那一类，其余继续等，由 `/status` 的打点喊人处置。
    """
    for reason in ("admin cancel-job", "mode-switch"):
        jsonl = tmp_path / f"{reason.replace(' ', '-')}.jsonl"
        jsonl.write_text(
            "".join(
                json.dumps(r) + "\n"
                for r in (
                    {"event": "job_pending", "job_id": JID, "runId": "r-current", "it": 7, "ts": 1.0},
                    {"event": "job_cancelled", "job_id": JID, "reason": reason, "ts": 2.0},
                )
            ),
            encoding="utf-8",
        )
        rig = _Rig(hub_http.JOB_ALIVE, ledger=jsonl)
        _patch_liveness(monkeypatch, rig)
        ctx = RoundContext(it=7, resumable=True)
        assert rig.step._remote_ppo_step(ctx) is not None
        assert rig.cancelled == [], reason
        assert rig.bumps == [], reason
        assert ctx.remote is not None, reason  # 会话保留：等待不静默变成重发
        assert rig.calls.count("publish") == 1, reason
