"""test_job_fail_report.py — 节点**确定性**失败的原因回传（`POST /jobs/{id}/fail`）。

plan/remote-wire-remediation.plan.md §5.3 缺口（2026-09-17）：`bun` 装不上 / TS 运行时
取不到这类失败此前只落在**云机日志**里——

  * pull 侧 worker 走 `except ProtocolError` 静默 skip（不回传、不还租约），训练侧只能
    等 `wait_job` 25 分钟超时：读到的是「超时」，不是「bun 缺失」；
  * push 侧节点服务用 **500** 报失败，而 500 在 `push_client.wait_result` 里被当**瞬时
    错误**重试到预算耗尽。

两条路都把「确定性能力缺失」伪装成了「网络/排队问题」。本文件钉死修复后的契约：

  * 回报端点：首写锁定、租约鉴权、有结果不收失败、账本 `job_failed` 留痕；
  * 失败即终局：`GET /jobs/{id}/result` → **410 + 原因**，`/status` → `failed + 原因`，
    job 不再回池（不会换个节点再演一遍）；
  * `wait_job` / `push_client.wait_result` **立刻**抛 `JobFailedError`（带 reason/kind/
    detail），而不是等满超时预算；
  * 逐轮重试 = 重发同一个 job（同幂等键 → 同 job_id）→ `publish_job` 清掉失败标记
    （否则重试会被「已失败」永久钉死）。

时间一律量真实秒（本文件不睡长钟：快速失败是「秒级」这一事实本身就是要断言的东西）。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

# 复用现有测试基建（`_boot_server` / `_http` / `_mini_manifest` / `_write_shard`）
sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from test_remote_ppo import _boot_server, _http, _mini_manifest, _write_shard  # type: ignore

from common.protocol import FAIL_NAME, JobFailedError, normalize_manifest
from remote.hub_client import publish_job, report_job_failure, wait_job
from remote.hub_server import _JobStore

_QUIET = lambda _m: None  # noqa: E731 — 测试日志静音


def _publish_min(tmp_path: Path):
    """起 hub-server 并发布一个最小 job；返回 (base, store, srv, th, jid)。"""
    base, store, srv, th = _boot_server(tmp_path)
    manifest = normalize_manifest(_mini_manifest())
    jid = manifest["job_id"]
    store.publish(jid, manifest, b"PK\x03\x04fake")
    return base, store, srv, th, jid


def _post_fail(base: str, jid: str, payload: dict, lease: str = "") -> tuple[int, dict]:
    status, body = _http(
        base,
        "sekret",
        f"/jobs/{jid}/fail",
        "POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        extra_headers={"X-Lease-Token": lease} if lease else None,
    )
    return int(status), dict(body)


# ────────────────────────── 回报端点 ──────────────────────────


def test_post_fail_records_reason_and_makes_result_terminal(tmp_path: Path) -> None:
    """回报后：池里剔除、/result 从「还没回来」变「不会回来 + 原因」、/status 终局留痕。"""
    base, store, srv, th, jid = _publish_min(tmp_path)
    try:
        assert store.claimable_job_ids() == [jid]
        token = store.claim(jid)
        assert token

        st, body = _post_fail(
            base,
            jid,
            {
                "reason": "bun 未安装：notebook 未跑 setup 单元格",
                "kind": "ProtocolError",
                "detail": "Traceback (most recent call last): ... FileNotFoundError: bun",
                "worker": "kaggle-1:42",
            },
            lease=token,
        )
        assert (st, body["status"]) == (200, "failed-recorded"), body

        # 确定性失败不重演：不再回池（换节点也一样跑不成）
        assert store.claimable_job_ids() == []

        # /result：410 = 不会有结果（不是 404「还没回来」，也不是 5xx「瞬时错误」）
        st2, body2 = _http(base, "sekret", f"/jobs/{jid}/result")
        assert st2 == 410, body2
        assert body2["failed"] is True
        assert "bun" in body2["error"]
        assert body2["fail_kind"] == "ProtocolError"

        # /status：终局 + 原因（控制台与 wait_job 收尾二次确认都读它）
        st3, body3 = _http(base, "sekret", f"/jobs/{jid}/status")
        assert st3 == 200 and body3["state"] == "failed", body3
        assert "bun" in body3["reason"] and body3["fail_kind"] == "ProtocolError"

        # 账本留痕：复盘看得到「哪台机器、为什么」，而不是一行超时
        ledger = [
            json.loads(ln)
            for ln in (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        failed = [e for e in ledger if e["event"] == "job_failed"]
        assert len(failed) == 1, ledger
        assert failed[0]["worker"] == "kaggle-1:42" and failed[0]["kind"] == "ProtocolError"
    finally:
        srv.shutdown()
        th.join()


def test_post_fail_first_write_wins_and_result_wins(tmp_path: Path) -> None:
    """首写锁定：训练侧读到的是**第一个**报上来的原因；已落结果则拒收失败。"""
    base, store, srv, th, jid = _publish_min(tmp_path)
    try:
        st1, body1 = _post_fail(base, jid, {"reason": "第一个节点：bun 未安装", "worker": "a:1"})
        assert (st1, body1["status"]) == (200, "failed-recorded")
        st2, body2 = _post_fail(base, jid, {"reason": "第二个节点：别的原因", "worker": "b:2"})
        assert (st2, body2["status"]) == (200, "already-recorded")
        assert store.job_failure(jid)["reason"] == "第一个节点：bun 未安装"
        # 后到的失败不改写训练侧已读到的那条
        st3, body3 = _http(base, "sekret", f"/jobs/{jid}/result")
        assert st3 == 410 and body3["error"] == "第一个节点：bun 未安装"
    finally:
        srv.shutdown()
        th.join()

    # 有结果 → 准时失败回报不覆盖成功产物（与 store_result 首写锁定同向）
    store2 = _JobStore(tmp_path / "jobs2", tmp_path / "log2.jsonl")
    manifest = normalize_manifest(_mini_manifest(job_id="k" * 16))
    jid2 = manifest["job_id"]
    store2.publish(jid2, manifest, b"PK\x03\x04fake")
    assert store2.store_result(jid2, {"a": 1}) is True
    assert store2.store_job_failure(jid2, {"reason": "迟到的失败"}) is False
    assert store2.job_failure(jid2) is None


def test_post_fail_rejects_bad_body_and_foreign_lease(tmp_path: Path) -> None:
    """缺 reason / 坏 JSON / 非租约持有人 / 未发布 job —— 一律响亮拒绝，不落假失败。"""
    base, store, srv, th, jid = _publish_min(tmp_path)
    try:
        token = store.claim(jid)
        assert token
        assert _post_fail(base, jid, {}, lease=token)[0] == 400  # reason 必填
        assert _post_fail(base, jid, {"reason": ""}, lease=token)[0] == 400
        st_bad, _ = _http(
            base,
            "sekret",
            f"/jobs/{jid}/fail",
            "POST",
            data=b"{not json",
            extra_headers={"X-Lease-Token": token},
        )
        assert st_bad == 400
        # 活租约须持有人（H2）：别台机器不能把别人在跑的 job 判死
        assert _post_fail(base, jid, {"reason": "越权"}, lease="wrong")[0] == 403
        assert store.job_failure(jid) is None  # 被拒的都没落盘
        assert _post_fail(base, "no-such-job" * 2, {"reason": "x"})[0] == 404
    finally:
        srv.shutdown()
        th.join()


# ────────────────────────── 训练侧：立刻带原因收兵 ──────────────────────────


def test_wait_job_fails_fast_with_reason_instead_of_timeout(tmp_path: Path) -> None:
    """25 分钟预算内**立刻**抛 JobFailedError（原因/类名/现场随行）——本文件的核心断言。"""
    base, store, srv, th, jid = _publish_min(tmp_path)
    try:
        token = store.claim(jid)
        assert token
        ok = report_job_failure(
            base,
            "sekret",
            jid,
            "bun 未安装（PATH 里没有 bun，node_modules 缺失）",
            kind="ProtocolError",
            detail="Traceback… FileNotFoundError: bun",
            worker="kaggle-1:42",
            lease_token=token,
            log=_QUIET,
        )
        assert ok is True

        t0 = time.time()
        with pytest.raises(JobFailedError) as ei:
            # 与生产同参：预算 25 分钟、轮询 5s —— 修复前这里要等到超时才返回
            wait_job(base, "sekret", jid, timeout_sec=25 * 60, poll_sec=5.0, log=_QUIET)
        elapsed = time.time() - t0
        assert elapsed < 10.0, f"应立刻收兵，实测 {elapsed:.1f}s（修复前是 1500s）"
        assert "bun" in str(ei.value)
        assert ei.value.kind == "ProtocolError"
        assert "FileNotFoundError" in ei.value.detail  # 现场随行（人一眼能修）
    finally:
        srv.shutdown()
        th.join()


def test_wait_job_timeout_confirm_recognizes_failure(tmp_path: Path) -> None:
    """收尾二次确认（H3）也要认失败：超时那一刻若已 failed → 抛 JobFailedError 而非超时。"""
    base, store, srv, th, jid = _publish_min(tmp_path)
    try:
        token = store.claim(jid)
        assert token
        assert _post_fail(base, jid, {"reason": "TS 运行时取不到（ts_code 404）"}, lease=token)[0] == 200
        with pytest.raises(JobFailedError) as ei:
            # timeout_sec 极小 → 直奔收尾确认分支（status → failed）
            wait_job(base, "sekret", jid, timeout_sec=0.001, poll_sec=0.01, log=_QUIET)
        assert "TS 运行时" in str(ei.value)
    finally:
        srv.shutdown()
        th.join()


def test_worker_report_job_failure_reaches_hub(tmp_path: Path) -> None:
    """pull 侧 worker 的回报端点（worker 自带 transport）：原因 + 机器身份都到位。"""
    from remote.worker import report_job_failure as worker_report
    from remote.worker import worker_tag

    base, store, srv, th, jid = _publish_min(tmp_path)
    try:
        token = store.claim(jid)
        assert token
        ok = worker_report(
            base,
            "sekret",
            jid,
            "bun 未安装",
            kind="ProtocolError",
            detail="tb",
            lease_token=token,
            log=_QUIET,
        )
        assert ok is True
        rec = store.job_failure(jid)
        assert rec is not None
        assert rec["reason"] == "bun 未安装" and rec["kind"] == "ProtocolError"
        assert rec["worker"] == worker_tag()  # 多机共用 token 时定位现场的唯一线索
    finally:
        srv.shutdown()
        th.join()


def test_republish_same_job_clears_failure_marker(tmp_path: Path) -> None:
    """逐轮重试 = 重发同一个 job（同幂等键 → 同 job_id）→ 必须清掉失败标记。

    不清的后果是「重试被永久钉死」：job 仍被判已失败 → 无人认领 + wait_job 立刻 410，
    整条腿再也跑不起来。这也是「失败即终局」这条设计与既有重试语义共存的**唯一**
    接口，故必须钉死。
    """
    jroot = tmp_path / "jobs"
    shard = tmp_path / "shard"
    _write_shard(shard, 0, 1)
    kwargs = dict(
        job_root=jroot,
        jsonl_path=tmp_path / "log.jsonl",
        run_id="r",
        it=1,
        traj_dir=str(tmp_path / "traj"),
        shard_dirs=[shard],
        commit="c" * 40,
        code_sha256="z" * 64,
        course="{}",
        course_fp="f" * 64,
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=8,
        lr=3e-4,
        log=_QUIET,
    )
    m = publish_job(**kwargs)  # type: ignore[arg-type]
    jid = str(m["job_id"])
    jd = jroot / jid
    (jd / FAIL_NAME).write_text(json.dumps({"reason": "bun 未安装"}), encoding="utf-8")
    store = _JobStore(jroot, tmp_path / "log.jsonl")
    assert store.claimable_job_ids() == []  # 已失败 → 不回池

    m2 = publish_job(**kwargs)  # type: ignore[arg-type]
    assert str(m2["job_id"]) == jid, "同幂等键必须得到同 job_id（否则测的不是重试路径）"
    assert not (jd / FAIL_NAME).exists()
    assert store.claimable_job_ids() == [jid]  # 重发即重试


def test_push_round_promotes_node_failure_over_retryable() -> None:
    """push：节点 410（JobFailedError）不得被包成 RetryableError（那会重试 3 次）。"""
    from types import SimpleNamespace

    import rl.loop_steps as ls

    def _boom(*_a: object, **_k: object) -> dict:
        raise JobFailedError("job j 失败: bun 未安装 [kind=ProtocolError]", kind="ProtocolError")

    orig_submit, orig_wait = ls._push_submit, ls._push_wait_result
    ls._push_submit = lambda *a, **k: {}  # type: ignore[assignment]
    ls._push_wait_result = _boom  # type: ignore[assignment]
    try:
        nodes = [{"url": "http://a", "authKey": ""}, {"url": "http://b", "authKey": ""}]
        with pytest.raises(JobFailedError) as ei:
            ls._push_job_round(
                nodes,
                {},
                "j",
                b"",
                b"",
                SimpleNamespace(smoke=False),
                60.0,
                _QUIET,
            )
        assert "bun" in str(ei.value)  # 原因原样上浮，不被 RetryableError 吃掉
    finally:
        ls._push_submit, ls._push_wait_result = orig_submit, orig_wait  # type: ignore[assignment]


def test_push_node_failure_is_410_and_fails_fast(tmp_path: Path) -> None:
    """push：节点把 failed 从 500 改成 410 —— wait_result 立刻抛，不再重试到预算耗尽。"""
    from remote.push_client import wait_result
    from remote.worker_server import WorkerServerState, make_worker_server

    state = WorkerServerState(tmp_path / "work")
    srv = make_worker_server(state, 0, "tok")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        state.set_state("j1", "running")
        state.set_error("j1", "ProtocolError: bun 未安装", kind="ProtocolError")
        t0 = time.time()
        with pytest.raises(JobFailedError) as ei:
            wait_result(base, "tok", "j1", timeout_sec=1800.0, poll_sec=0.01, log=_QUIET)
        assert time.time() - t0 < 10.0  # 修复前：500 → 重试到 1800s 预算耗尽
        assert "bun" in str(ei.value) and ei.value.kind == "ProtocolError"
    finally:
        srv.shutdown()
        th.join()
