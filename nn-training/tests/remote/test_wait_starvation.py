"""饥饿响亮：等 PPO job 时**必须**说清在等什么（plan/accident.plan.md §3，2026-09-21）。

单一 PPO 路径之后，训练侧的全部能力就是「发布 → 等 worker 认领」。于是「等」成了唯一的
失败形态：**没有 worker 时，训练什么都不做也不报错**。C 腿事故里这条静默等了 3.5 小时
（约 40 次认领过期循环），日志只有超时——操作员只能靠 cadence 反推。

本文件钉三档（三档必须可区分）：
  * `state=pending`   ⇒ **等待认领中**，并把 hub 的在线 worker 数写进那一行（0 就是病灶本身）；
  * `state=leased`    ⇒ 执行中（带租约剩余/上次心跳），不是饥饿，不该喊「起 worker」；
  * `state=cancelled` ⇒ **已被撤单**（账本净态，2026-10-17 加）：这条活已经没了，
    等待无意义——不许再把人往「起 worker」上引（2026-10-16 事故：这条假 `pending`
    让腿空等了 3h+，见 `plan/export-must-not-cancel-live-job`）。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# 复用现有测试基建（`_boot_server` / `_mini_manifest`）
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import remote.hub_http as hub_http
from common.protocol import JobFailedError, normalize_manifest
from remote.hub_client import HubClientError, JobCancelledError, cancel_job, wait_job
from tests.remote.test_remote_ppo import _boot_server, _mini_manifest  # type: ignore


def _publish_unclaimed(tmp_path: Path):
    """起 hub-server 并发布一个**无人认领**的 job；返回 (base, store, srv, th, jid)。"""
    base, store, srv, th = _boot_server(tmp_path)
    manifest = normalize_manifest(_mini_manifest())
    jid = manifest["job_id"]
    store.publish(jid, manifest, b"PK\x03\x04fake")
    return base, store, srv, th, jid


def test_wait_reports_starvation_with_zero_workers(tmp_path: Path) -> None:
    """零 worker：等待期写出「等待认领中 + 在线 worker = 0」——操作员的下一步动作在行里。

    期望形态（修复前这里一行都不写，只有 25 分钟后的超时）：
        job <id> **等待认领中**（state=pending，已等 Ns；hub 在线 worker = 0）
        ——要算就去控制台起 worker（本机/云机同一认领协议），不要就停腿
    """
    base, _store, srv, th, jid = _publish_unclaimed(tmp_path)
    lines: list[str] = []
    try:
        try:
            wait_job(
                base,
                "sekret",
                jid,
                # timeout 0.6 → 0.3（2026-09-29，§43）：本用例只关心「等待期说过什么」，
                # 超时那一瞬是预期结局 —— 它是本用例的固有开销，0.3s 仍容 6 次探测（0.05s 步长）
                # 与 ≥2 次节流后的报告（report_every_sec=0.1）。
                timeout_sec=0.3,
                poll_sec=0.05,
                report_every_sec=0.1,
                log=lines.append,
            )
        except Exception:
            pass  # 超时是预期结局：本用例只关心**等待期说过什么**
    finally:
        srv.shutdown()
        th.join()

    assert lines, "等待期零日志——这正是本次事故的形态（安静地等）"
    starvation = [ln for ln in lines if "等待认领中" in ln]
    assert starvation, lines
    assert any("在线 worker = 0" in ln for ln in starvation), starvation
    assert any("起 worker" in ln for ln in starvation), starvation
    # 与「执行中」不混：零认领时不该出现 leased 的措辞
    assert not [ln for ln in lines if "执行中" in ln], lines
    # 节流是真的节流（不是每个 poll 一行）：0.3s / 0.05s ≈ 6 次探测，报告应远少于它
    assert len(lines) <= 6, lines


def test_wait_reports_leased_not_starvation_when_claimed(tmp_path: Path) -> None:
    """已被认领（leased）：报「执行中」，**不**喊饥饿——两档必须可区分（否则告警失去意义）。

    注意本用例的收尾方式：leased 在 `wait_job` 里意味着「再给一个完整预算」
    （H3 原语义，会一直等下去），所以不能在主线程里靠小 timeout 退出来
    ——后台线程等着，落一份结果把它变成 ready，再收敛。
    """
    base, store, srv, th, jid = _publish_unclaimed(tmp_path)
    lines: list[str] = []
    err: list[BaseException] = []
    try:
        assert store.claim(jid)  # worker 认领（真租约 ⇒ /status 报 leased）

        def _wait() -> None:
            try:
                wait_job(
                    base,
                    "sekret",
                    jid,
                    timeout_sec=5.0,
                    poll_sec=0.05,
                    report_every_sec=0.05,
                    log=lines.append,
                )
            except BaseException as e:  # 记下来，别吞在后台线程里
                err.append(e)

        worker = threading.Thread(target=_wait, daemon=True)
        worker.start()
        deadline = time.time() + 5.0
        while time.time() < deadline and not any("执行中" in ln for ln in lines):
            # sleep-ok: 轮询步长（等的是日志出现「执行中」这个状态，5s 只当挂起兜底）
            time.sleep(0.02)
        assert store.store_result(jid, {"ok": True})  # 落结果 ⇒ 探针就绪 ⇒ 等待结束
        worker.join(timeout=10.0)
        assert not worker.is_alive(), f"落结果后仍未收兵；日志={lines}"
    finally:
        srv.shutdown()
        th.join()

    assert not err, err
    assert any("执行中" in ln for ln in lines), lines
    assert not [ln for ln in lines if "等待认领中" in ln], lines


def test_wait_stops_when_the_ledger_says_cancelled(tmp_path: Path) -> None:
    """账本已撤 ⇒ 打点说人话，且到点立刻收兵——**可重试**错，不是停腿（2026-10-16 事故）。

    三件事分别钉：
      * `_wait_state_note` 多第三档：`pending` = 饥饿、`leased` = 执行中、`cancelled` = 这条活
        已经没了（等待无意义）；
      * **收兵路径必须是可判别的那一类**：`JobCancelledError`（`HubClientError` 子类）而
        **不是** `JobFailedError`——后者在 `_handle_remote_failure` 里是「节点确定性失败 ⇒
        写 ABORT 停腿」（`trainer/loop_remote_fail.py`），而这次是**我们自己撤的单**：正确处置是
        撤单 + 下一拍重发同 jid（与 M1 lost 同一条路）；
      * 「不把人往起 worker 上引」有**行为面**判据：`cancelled` 档在 `_wait_state_note` 里
        早返回 ⇒ 从头到尾**不问** `/admin/queue`（旧版这里是子串判据，只证明「那行里没那几个字」）。
    """
    base, _store, srv, th, jid = _publish_unclaimed(tmp_path)
    lines: list[str] = []
    asked: list[str] = []
    real_request = hub_http._request

    def _spy(base_url, token, path, *a, **k):
        asked.append(path)
        return real_request(base_url, token, path, *a, **k)

    hub_http._request = _spy  # type: ignore[assignment]
    try:
        # trainer 侧 M2 的撤单行（hub 与 trainer 共读同一份 training_log.jsonl）。
        assert cancel_job(
            tmp_path / "training_log.jsonl",
            jid,
            reason="上一 runId 77a9a90a… 遗留（腿启动清理）",
        )
        with pytest.raises(JobCancelledError) as ei:
            wait_job(
                base,
                "sekret",
                jid,
                timeout_sec=0.3,
                poll_sec=0.05,
                report_every_sec=0.1,
                log=lines.append,
            )
        assert not isinstance(ei.value, JobFailedError)  # 停腿那条路不许被走到
        assert isinstance(ei.value, HubClientError)  # 可重试：与 M1 lost 同一条处置路
        assert asked.count("/admin/queue") == 0  # 早返回：不问在线 worker（那条路只服务 pending）
    finally:
        hub_http._request = real_request  # type: ignore[assignment]
        srv.shutdown()
        th.join()
    cancelled = [ln for ln in lines if "已被撤单" in ln]
    assert cancelled, lines


def test_wait_report_can_be_disabled(tmp_path: Path) -> None:
    """`report_every_sec=0` = 关（调试/单测可静音；默认 300s 节流）。"""
    base, _store, srv, th, jid = _publish_unclaimed(tmp_path)
    lines: list[str] = []
    try:
        try:
            wait_job(
                base,
                "sekret",
                jid,
                timeout_sec=0.3,
                poll_sec=0.05,
                report_every_sec=0.0,
                log=lines.append,
            )
        except Exception:
            pass
    finally:
        srv.shutdown()
        th.join()
    assert lines == [], lines
