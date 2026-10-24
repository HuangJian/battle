"""test_hub_push_dispatch —— hub 中介 push 派发（2026-09-18 用户指令）。

用户口径：push 模式下 hub 按**队列顺序**轮番向**空闲** ppo worker 推送任务；已推送任务
**超时则回落队首并改为推送其它 worker**；离线模式的课程不实时派发（只收回传）。

本文件覆盖三层，全部不依赖 torch / 不 spawn bun：
  ① 纯函数（`push_worker_from_node` / `pick_push_worker`）——登记的判据必须与训练侧
     `_gpu_push_nodes` 同一把尺子，挑选的四道闸（在线/不忙/在飞<并发/不在避让名单）；
  ② `PushWorkers` 登记表——rl-config 热重载（mtime）+ 周期探活（失败即「未知 ⇒ 当它在忙」）；
  ③ `PushDispatcher` 全链路——真 store + 真 hub HTTP 端点 + **假 GPU worker server**
     （实现 `/ping` `/job` `/job/{id}/result` 三件套，不跑任何真实运算）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    AUTH_HEADER,
    CAP_OFFLINE_PUSH,
    ProtocolError,
    normalize_manifest,
    pick_push_worker,
    push_job_wants_hub_push,
    push_worker_from_node,
    push_worker_id_of,
)
from hub.server import _HubQueue, _JobStore, make_server
from remote.push_dispatch import PushDispatcher, PushWorkers
from remote.worker_server import WorkerServerState, make_worker_server
from tests.helpers.push_worker import PAYLOAD, result_of

# ------------------------------------------------------------------ 夹具与工具


def _quiet(_msg: str) -> None:
    pass


def _manifest(jid: str, it: int = 1, *, dispatch: str = "push") -> dict:
    """最小合法 manifest（必填齐全）；`dispatch` 是 hub 认领「这份活由我推」的字段。"""
    m: dict = {
        "proto": 1,
        "runId": "push-run",
        "it": it,
        "job_id": jid,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": '// course jsonc\n{"reward": {"formula": "score"}}',
        "course_fp": "f" * 64,
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 64,
        "epochs": 2,
        "mb": 512,
        "lr": 3e-4,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }
    if dispatch:
        m["dispatch"] = dispatch
    return normalize_manifest(m)


def _hub(tmp_path: Path, courses: list[str]) -> _HubQueue:
    stores = {
        c: _JobStore(tmp_path / c / "remote-jobs", tmp_path / c / "training_log.jsonl")
        for c in courses
    }
    return _HubQueue(stores, order=courses)


def _publish(hub: _HubQueue, course: str, jid: str, it: int = 1, *, dispatch: str = "push") -> dict:
    """发布一份 job（真 store.publish：落 payload + manifest + 账本 job_pending）。

    payload_sha256 必须是**真 sha**：假 worker 也照 worker_server 的契约逐字节对账
    （否则「hub 推了一份坏 body」这类回归会被假 worker 放过去）。
    """
    m = _manifest(jid, it, dispatch=dispatch)
    m["payload_sha256"] = hashlib.sha256(PAYLOAD).hexdigest()
    hub._stores[course].publish(jid, m, PAYLOAD)
    return m


def _wait_until(pred, *, timeout: float = 5.0, step: float = 0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词/状态，超时只当挂起兜底）
        time.sleep(step)
    return bool(pred())


#: 假 worker 的 HTTP 全在本机（正常 <5ms）——超时给足余量是因为门禁拿 xdist -n 12 跑：
#: 满载时一次本机请求也能 >0.5s，过短的探测超时会把**健康** worker 误判为「没答」，
#: 派发器随即把它当忙/离场 ⇒ 没有机器能接活 ⇒ 偶发红（2026-09-18 全量门禁实测）。
_PING_TIMEOUT = 2.0


def _pump(disp: PushDispatcher, pred, *, timeout: float = 20.0, step: float = 0.02) -> bool:
    """持续打拍直到 pred 成立——模拟生产里派发循环（`start()` 的那条线程）。

    测试用显式 `tick()` 而不是起线程：拍与断言在同一个线程里，失败可复现。
    拍本身也是**探活**的驱动（`is_online` 只在 `ping_all()` 里更新），所以「落死判定→
    回落队首→换人重推」这条链必须靠打拍推进。
    """
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        disp.tick()
        # sleep-ok: 轮询步长（打拍节奏：等的是谓词/状态，20s 只当挂起兜底）
        time.sleep(step)
    return bool(pred())


# ------------------------------------------------------------------ ① 纯函数


def test_push_worker_from_node_mirrors_trainer_side_filter() -> None:
    """判据与训练侧 `_gpu_push_nodes` 同尺子：gpu_push + enabled（缺省 true）+ 非空 url。"""
    assert push_worker_from_node({"id": "g1", "url": "https://x/", "gpu_push": True}) == {
        "id": "g1",
        "url": "https://x",
        "key": "",
        "concurrency": 1,
    }
    # 非 push 节点 / 显式停用 / 无 url —— 一律不进登记表
    assert push_worker_from_node({"id": "a", "url": "https://a"}) is None
    assert push_worker_from_node({"id": "a", "url": "https://a", "gpu_push": True, "enabled": False}) is None
    assert push_worker_from_node({"id": "a", "gpu_push": True}) is None
    assert push_worker_from_node("nope") is None
    # id 缺省回落 url、concurrency 显式值生效（缺省 1 = 单 GPU 一次一份）
    w = push_worker_from_node({"url": "https://b", "gpu_push": True, "concurrency": 3})
    assert w is not None and w["id"] == "https://b" and w["concurrency"] == 3


def test_push_workers_capability_filtering(tmp_path: Path) -> None:
    """同一个登记表类服务两条推送腿（plan/push-mode-notebooks §6.6）。

    `capability` 只决定「哪些节点算数」；条目形状不变。两种能力必须互不可见——
    否则 GPU 派发器会把离线节点当 GPU 节点推几十 MB，而离线派发器会把 GPU 机拉进长段训练。
    """
    cfg = tmp_path / "push-config.json"
    _write_cfg(
        cfg,
        [
            {"id": "g1", "url": "https://g1", "gpu_push": True},
            {"id": "o1", "url": "https://o1", "offline_push": True},
            {"id": "both", "url": "https://both", "gpu_push": True, "offline_push": True},
            {"id": "plain", "url": "https://plain"},
        ],
    )
    gpu = PushWorkers(cfg, log=_quiet, ping_timeout=_PING_TIMEOUT)
    assert gpu.reload(force=True) is True
    assert [w["id"] for w in gpu.snapshot()] == ["g1", "both"]
    assert gpu.state()["capability"] == "gpu_push"

    off = PushWorkers(
        cfg, log=_quiet, ping_timeout=_PING_TIMEOUT, capability=CAP_OFFLINE_PUSH
    )
    assert off.reload(force=True) is True
    assert [w["id"] for w in off.snapshot()] == ["o1", "both"]
    assert off.state()["capability"] == "offline_push"

    # 运行时 add 的能力由**登记表**决定（端点不能把自己塞进别的腿）
    off.add({"id": "extra", "url": "https://extra"})
    assert {w["id"] for w in off.snapshot()} == {"o1", "both", "extra"}
    assert {w["id"] for w in gpu.snapshot()} == {"g1", "both"}
    assert set(gpu.state()["workers"][0]) & {"offline_push"} == set()


def test_pick_push_worker_four_gates() -> None:
    """四道闸：在线、不忙、在飞 < 并发、不在避让名单；顺序 = 注册序（稳定可测）。"""
    ws = [
        {"id": "dead", "online": False, "busy": False, "concurrency": 1},
        {"id": "busy", "online": True, "busy": True, "concurrency": 1},
        {"id": "full", "online": True, "busy": False, "concurrency": 1},
        {"id": "ok1", "online": True, "busy": False, "concurrency": 1},
        {"id": "ok2", "online": True, "busy": False, "concurrency": 2},
    ]
    inflight = {"full": 1}
    assert (pick_push_worker(ws, inflight) or {})["id"] == "ok1"
    assert (pick_push_worker(ws, inflight, avoid={"ok1"}) or {})["id"] == "ok2"
    # 从没探活过（online 缺失）同样不选：宁可不推，也不往可能是死的机器上推几十 MB
    assert pick_push_worker([{"id": "x", "busy": False}], {}) is None
    # 并发 > 1 时同一台可接第二份
    assert (pick_push_worker(ws, {"full": 1, "ok1": 1}) or {})["id"] == "ok2"
    assert pick_push_worker(ws, {"full": 1, "ok1": 1, "ok2": 2}) is None


def test_pick_push_worker_prefetch_ahead() -> None:
    """`ahead > 0`：空闲机永远优先；没有空闲机时，忙机按它的**接单队列深度**再接一份。

    预推（2026-10-24）的全部风险都在这一层：把「在跑」当「没空」是旧的保守方向，
    把「队已满」当「还能接」就是往一台已经排满的机器上再传几十 MB。
    """
    idle = {"id": "idle", "online": True, "busy": False, "queued": 0, "concurrency": 1}
    busy = {"id": "busy", "online": True, "busy": True, "queued": 0, "concurrency": 1}
    busy_full = {**busy, "id": "busy-full", "queued": 1}
    deeper = {**busy, "id": "deeper", "queued": 2}
    inflight = {"busy": 1, "busy-full": 1, "deeper": 1}
    # 旧行为（ahead=0，缺省）：忙机一律不接（四道闸的「不忙」原样保留）
    assert pick_push_worker([busy], inflight) is None
    assert pick_push_worker([busy_full, busy], inflight) is None
    # 预推：只要还有空闲机，位置口径不变（忙机排队位不当成空闲位）
    assert (pick_push_worker([busy, idle], inflight, ahead=1) or {})["id"] == "idle"
    # 没有空闲机 ⇒ 只有「队列还没到 ahead」的忙机接
    assert (pick_push_worker([busy_full, busy], inflight, ahead=1) or {})["id"] == "busy"
    assert pick_push_worker([busy_full, deeper], inflight, ahead=1) is None
    # 名额 = concurrency + ahead（忙机已经在跑一份）
    assert pick_push_worker([{**busy, "id": "at-cap"}], {"at-cap": 2}, ahead=1) is None
    # 避让名单对预推同样生效（刚跑死这份活的那台不再收）
    assert pick_push_worker([busy], inflight, avoid={"busy"}, ahead=1) is None


def test_push_want_and_holder_identity() -> None:
    assert push_job_wants_hub_push({"dispatch": "push"}) is True
    assert push_job_wants_hub_push({}) is False
    assert push_job_wants_hub_push({"dispatch": "pull"}) is False
    assert push_worker_id_of("g1") == "push:g1"
    assert push_worker_id_of("push:g1") == "push:g1"  # 幂等


# ------------------------------------------------------------------ ② 登记表 + 探活


def _write_cfg(cfg: Path, nodes: list[dict]) -> None:
    """写 push 登记表，并**显式把 mtime 推到至少前一秒**。

    为什么要挪时间戳：Windows 上 `st_mtime` 的落盘精度被系统时钟节拍卡住（~15.6ms），
    同一拍内的两次写会拿到**相同**的 mtime ⇒ `PushWorkers.reload()` 的 mtime 判定看不见
    这次改动。本用例要测的是「文件变了就重读」，不是「这台机器的时钟分辨率」——全量套件
    + xdist 负载下实测偶发假红（2026-09-21，`test_push_workers_hot_reload_and_probe`）。
    显式递增 = 与平台/负载无关。
    """
    try:
        prev = cfg.stat().st_mtime
    except OSError:
        prev = time.time()
    cfg.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")
    later = max(prev + 1.0, time.time())
    os.utime(cfg, (later, later))


def test_push_workers_hot_reload_and_probe(tmp_path: Path, worker_factory) -> None:
    """rl-config 热重载（mtime）+ 探活写回在线/忙闲；失败未达阈值不算离场但必须「当它在忙」。"""
    cfg = tmp_path / "rl-config.json"
    _write_cfg(cfg, [{"id": "g1", "url": "http://127.0.0.1:1", "gpu_push": True}])
    ws = PushWorkers(cfg, log=_quiet, ping_timeout=_PING_TIMEOUT)
    assert ws.reload(force=True) is True
    assert [w["id"] for w in ws.snapshot()] == ["g1"]
    # 内容没变 ⇒ 不再重读（mtime 判定）
    assert ws.reload() is False
    # 换成一台真活着的 worker：文件变了 ⇒ 拾取；状态从「未知」开始
    live = worker_factory()
    _write_cfg(
        cfg,
        [
            {"id": "g1", "url": "http://127.0.0.1:1", "gpu_push": True},
            {"id": "g2", "url": live.url, "gpu_push": True, "authKey": "k"},
            {"id": "plain", "url": live.url},  # 非 gpu_push：不得进表
        ],
    )
    assert ws.reload() is True
    assert {w["id"] for w in ws.snapshot()} == {"g1", "g2"}
    assert all(w["online"] is False for w in ws.snapshot()), "没探活过 = 未知（不在线）"
    pinged = ws.ping_all()
    assert pinged["g2"] == "ok" and pinged["g1"] == "miss"
    assert ws.is_online("g2") is True and ws.is_online("g1") is False
    snap = {w["id"]: w for w in ws.snapshot()}
    assert snap["g2"]["busy"] is False and snap["g2"]["misses"] == 0
    assert snap["g1"]["misses"] == 1 and snap["g1"]["busy"] is True, "未知状态必须当局忙"
    # 运行时增删（volatile）：add 立刻可派、remove 进删除名单（覆盖面①的配置条目）
    ws.add({"id": "extra", "url": live.url, "authKey": "k"})
    assert {w["id"] for w in ws.snapshot()} == {"g1", "g2", "extra"}
    assert ws.remove("g1") is True
    assert {w["id"] for w in ws.snapshot()} == {"g2", "extra"}
    assert ws.remove("nope") is False
    with pytest.raises(ProtocolError):
        ws.add({"id": "bad"})  # 无 url：响亮拒绝


# ------------------------------------------------------------------ ③ 派发器全链路


def _dispatcher(
    hub: _HubQueue,
    ws: PushWorkers,
    *,
    poll_sec: float = 0.02,
    timeout_sec: float = 5.0,
    push_attempts: int = 3,
) -> PushDispatcher:
    return PushDispatcher(
        hub,
        ws,
        "sekret",
        poll_sec=poll_sec,
        timeout_sec=timeout_sec,
        push_attempts=push_attempts,
        log=_quiet,
    )


def _workers_with(tmp_path: Path, factory, *specs) -> PushWorkers:
    """登记表用**测试自己的**配置路径：绝不能落回仓库的 rl-config.json
    （那会让用例依赖开发机上的真实节点列表——本机有 gpu_push 节点时行为就变了）。"""
    ws = PushWorkers(
        tmp_path / "push-config.json", log=_quiet, ping_sec=0.0, ping_timeout=_PING_TIMEOUT
    )
    for i, (w, kw) in enumerate(specs):
        ws.add({"id": kw.pop("id", f"g{i}"), "url": w.url, "authKey": "sekret", **kw})
    return ws


def test_dispatch_pushes_to_idle_worker_and_ingests_result(tmp_path: Path, worker_factory) -> None:
    """happy path：队首 push job → 空闲 worker → 结果按契约入账 → hub 端点能读到。"""
    w1 = worker_factory()
    hub = _hub(tmp_path, ["x2"])
    m = _publish(hub, "x2", "j" * 16)
    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _dispatcher(hub, ws)

    disp.tick()
    assert _wait_until(lambda: hub._stores["x2"].get_result("j" * 16) is not None), "结果应落盘"
    assert w1.received == ["j" * 16]
    # 真 hub 端点读它（训练侧 wait_job 走的就是这条）：
    srv = make_server(hub, 0, "sekret", host="127.0.0.1")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        status, body = _get(base, "/jobs/" + "j" * 16 + "/status")
        assert status == 200 and body["state"] == "done"
        status, body = _get(base, "/jobs/" + "j" * 16 + "/result")
        assert status == 200 and body["agg"]["policy"] == 0.1
        assert body["data_fp"] == m["data_fp"] and body["init_weights_fp"] == m["init_weights_fp"]
        # 传输账：hub 代发的实测字节（训练侧 iteration 行的 wire.up_bytes 来源）
        wire = hub._stores["x2"].wire_stats("j" * 16)
        assert wire["payload_bytes"] == len(PAYLOAD) and wire["body_bytes"] > 0
        assert wire["upload_sec"] >= 0.0
        # 派发器状态：在途已清、无避让残留
        st = disp.state()
        assert st["pushed"] == 1 and st["inflight"] == [] and st["requeued"] == 0
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)

    # 有结果的 job 不再可领（首写锁定）——不会二次推送
    assert hub.claimable_job_ids("x2") == []


def test_busy_worker_is_skipped_and_held_course_is_not_dispatched(
    tmp_path: Path, worker_factory
) -> None:
    """忙的 worker 不接活（推给另一台）；**被接管的课一份都不推**（它只跑离线段）。

    ★M4b：旧用例钉的是「离线课不推」——模式退役后同一道闸换成 **hold**（role_blocked
    的第三层，它吃所有 kind）。
    """
    busy = worker_factory(busy=True)
    idle = worker_factory()
    hub = _hub(tmp_path, ["on", "off"])
    _publish(hub, "on", "a" * 16)
    _publish(hub, "off", "b" * 16)
    hub.note_hold("off", worker_id="cloud-1", token="tok")
    ws = _workers_with(tmp_path, worker_factory, (busy, {"id": "g1"}), (idle, {"id": "g2"}))
    disp = _dispatcher(hub, ws)

    disp.tick()
    assert _wait_until(lambda: idle.received == ["a" * 16])
    assert busy.received == []
    # 被接管的课：队列里躺着也没人推（job 仍在池里等接管解除/掉线）
    assert hub.claimable_job_ids("off") == ["b" * 16]
    assert hub.role_blocked("b" * 16, "online").startswith("held:")
    assert hub._stores["off"].get_result("b" * 16) is None
    # 在线课推完即出结果
    assert _wait_until(lambda: hub._stores["on"].get_result("a" * 16) is not None)


def test_bc_job_is_never_backed_up_by_the_push_leg(
    tmp_path: Path, worker_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★M5（需求 7）：BC 「领取后独占」——push 腿连**试都不试**（与 ppo 的备份腿对照）。

    真的闸在 store（`_claim_locked` 当面拒：`no_backup`，见 `tests/hub/test_hold.py`）；
    这里钉的是派发面那层「不值当推」的过滤：bc 在飞 ⇒ 不发备份（否则每拍都白试一次）。
    """
    from common.protocol import CLAIM_MODE_BACKUP, PRIORITY_LOW

    w1 = worker_factory()
    hub = _hub(tmp_path, ["bc-c", "ppo-c"])
    # bc 那份手写发布：kind 决定它是不是双角色作业（`_publish` 的夹具不带 kind）
    bc_m = _manifest("b" * 16)
    bc_m["kind"] = "bc"
    bc_m["payload_sha256"] = hashlib.sha256(PAYLOAD).hexdigest()
    hub._stores["bc-c"].publish("b" * 16, bc_m, PAYLOAD)
    _publish(hub, "ppo-c", "p" * 16)
    tried: list[tuple[str, str]] = []

    def spy(jid: str, **kw: Any) -> str | None:
        tried.append((jid, str(kw.get("mode"))))
        return None  # 一律不发租约：本用例只看「试没试哪一份」

    monkeypatch.setattr(hub, "claim", spy)
    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _dispatcher(hub, ws)
    # 预置两条「主副本在飞」的派发账（真推送不在本用例范围：`_run_one` 不该被走到）
    for course, jid in (("bc-c", "b" * 16), ("ppo-c", "p" * 16)):
        disp._inflight[jid] = {
            "job_id": jid,
            "slot": jid,
            "course": course,
            "worker": "g3",
            "lease": "L",
            "mode": "exclusive",
            "url": "",
            "key": "",
            "state": "upload",
            "started": 0.0,
        }
    monkeypatch.setattr(disp, "_job_priority_of", lambda jid: PRIORITY_LOW)
    disp.tick()
    assert tried == [("p" * 16, CLAIM_MODE_BACKUP)], tried


def _in_flight_primary(disp: PushDispatcher, jid: str, course: str, *, worker: str, url: str) -> None:
    """预置一条「主副本在飞」的派发账（真推送不在这些用例的范围，只摆状态）。"""
    disp._inflight[jid] = {
        "job_id": jid,
        "slot": jid,
        "course": course,
        "worker": worker,
        "lease": "L",
        "mode": "exclusive",
        "url": url,
        "key": "sekret",
        "state": "waiting",
        "started": 0.0,
    }


def test_backup_claim_of_settled_job_is_refused_by_the_store_gate(
    tmp_path: Path, worker_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-10-11 满载 flake 回归（`e2e/…` 的 `pushed == 3`）—— **store 闸**这一半。

    现场的交错形状（确定性摆出，不撞时序）：主副本落地后，派发器仍认为「这份活值得开备份」
    （优先表说 low）——本用例把在飞账挂在**另一台** worker id 上（`gX`），于是登记表里那台
    真机器（`g1`）看着空闲、派发器会真去 `claim`。代价不是免费的：被推的机器会**真的从头算
    一遍**（白烧一轮 GPU），其回传随后被首写锁定 409 丢掉。

    权威判据住 `_claim_locked`（当面拒：`landed`，直接用例见 `tests/hub/test_hub_leases.py`）
    ——本用例证的是它真能挡住派发器这条腿。
    """
    from common.protocol import PRIORITY_LOW

    w1 = worker_factory()
    hub = _hub(tmp_path, ["c1"])
    jid = "j" * 16
    _publish(hub, "c1", jid)
    # 主副本赢下首写（result/ 落盘）——它此刻可能还没 pop 在飞记录
    assert hub._stores["c1"].store_result(jid, {"job_id": jid, "agg": {}}) is True
    assert hub.claimable_job_ids("c1") == [], "已落盘 ⇒ 出池（这一半是既有语义）"

    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _dispatcher(hub, ws)
    _in_flight_primary(disp, jid, "c1", worker="gX", url="http://127.0.0.1:1")
    monkeypatch.setattr(disp, "_job_priority_of", lambda _jid: PRIORITY_LOW)

    tick = disp.tick()
    assert tick["started"] == [], tick
    assert not any(str(s).endswith("#b0") for s in disp._inflight), disp._inflight
    # 给了推送线程足够时间真把 POST 发出去（红的时候就是这么抓到它的）
    if _wait_until(lambda: w1.received != [], timeout=2.0):
        raise AssertionError(f"已结算的活被推给了机器（白烧一轮 GPU）：{w1.received}")
    assert disp.state()["pushed"] == 0


def test_dispatch_reads_its_own_state_once_per_tick(
    tmp_path: Path, worker_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-10-11（二）：挑活挑人**只用一份自有状态快照**——类的消除，不是逐处补闸。

    本用例把交错**确定性地**摆在两次读数之间：主副本的线程恰好在派发器读完「主副本在飞」
    之后、挑人之前落地并 pop 掉在飞记录（`_job_priority_of` 被钩上即执行这一下）。
    旧实现（两次读）此时拿到的在飞视图已经是**空的** ⇒ 刚跑完这份活的 `g1` 被当成空闲 ⇒
    第三份推送；新实现（`_snapshot`）挑人用的仍是同一份快照 ⇒ 快照里 `g1` 正在飞 ⇒ 挑不到人。

    `hub.claim` 被让开（返回租约）是**故意的**：本用例要证的是快照，不是 store 那道闸
    （后者由 `test_backup_claim_of_settled_job_is_refused_by_the_store_gate` 单独钉）。
    """
    from common.protocol import PRIORITY_LOW

    w1 = worker_factory()
    hub = _hub(tmp_path, ["c1"])
    jid = "j" * 16
    _publish(hub, "c1", jid)
    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _dispatcher(hub, ws)
    _in_flight_primary(disp, jid, "c1", worker="g1", url=w1.url)
    popped = {"n": 0}

    def _priority_and_pop(_jid: str) -> str:
        popped["n"] += 1
        disp._inflight.pop(jid, None)  # 主副本线程恰在此刻落地并 pop 掉在飞记录
        return PRIORITY_LOW

    monkeypatch.setattr(disp, "_job_priority_of", _priority_and_pop)
    monkeypatch.setattr(hub, "claim", lambda jid, **kw: "")  # 让开 store 闸：只证快照

    tick = disp.tick()
    assert popped["n"] == 1, "夹具没走到「备份值不值得开」那一步"
    assert tick["started"] == [], tick
    if _wait_until(lambda: w1.received != [], timeout=2.0):
        raise AssertionError(f"同一份活被推给刚跑完它的机器：{w1.received}")
    assert disp.state()["pushed"] == 0


def test_dead_worker_requeues_to_queue_head_and_another_worker_takes_it(
    tmp_path: Path, worker_factory
) -> None:
    """超时/失联 → **回落队首** + 避开那台 → 改推其它 worker（用户口径的正面用例）。"""
    dying = worker_factory(complete=False)
    rescue = worker_factory()
    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "j" * 16)
    ws = _workers_with(tmp_path, worker_factory, (dying, {"id": "g1"}), (rescue, {"id": "g2"}))
    disp = _dispatcher(hub, ws)

    disp.tick()  # ping 全绿 → 派给 g1（队首/注册序）
    assert _wait_until(lambda: dying.received == ["j" * 16])

    dying.fail_ping = True  # 推出去之后这台挂了（结果永远出不来）
    disp.timeout_sec = 60.0  # 把兜底上限拉远：本次要验证的是「探活判死」而不是超时
    assert _pump(disp, lambda: disp.requeued >= 1), "失联必须触发回落"
    assert _pump(disp, lambda: hub._stores["x2"].get_result("j" * 16) is not None)
    assert rescue.received == ["j" * 16], "回落队首后必须改推另一台"
    assert dying.received == ["j" * 16], "跑死它的那台不得再拿同一份活"
    assert disp.state()["avoid"]["j" * 16] == ["g1"]
    assert hub.claimable_job_ids("x2") == []  # 结果已落盘 ⇒ 出池


def test_push_timeout_requeues(tmp_path: Path, worker_factory) -> None:
    """兜底上限：worker 一直吊着（202）⇒ 到点回落队首换人（不等训练侧 30min 超时）。"""
    slow = worker_factory(complete=False)
    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "j" * 16)
    ws = _workers_with(tmp_path, worker_factory, (slow, {"id": "g1"}))
    disp = _dispatcher(hub, ws, timeout_sec=0.2)

    disp.tick()
    assert _pump(disp, lambda: disp.requeued >= 1)
    assert hub._stores["x2"].get_result("j" * 16) is None
    assert disp.state()["pushed"] == 1 and slow.received == ["j" * 16]
    # 回落 = 租约已放掉：job 立刻回到可领池（换 worker 的下一次 tick 会重新挑）
    assert hub.claimable_job_ids("x2") == ["j" * 16]


def test_worker_refusing_job_requeues_to_another(tmp_path: Path, worker_factory) -> None:
    """worker 拒收（409 队满）不是终局：回落队首换一台，训练侧完全无感。"""
    refusing = worker_factory(refuse=True)
    ok = worker_factory()
    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "j" * 16)
    ws = _workers_with(tmp_path, worker_factory, (refusing, {"id": "g1"}), (ok, {"id": "g2"}))
    # attempts=1：本用例测的是**调度层**「409 ⇒ 回落队首换一台」，不是 push_client 的
    # 瞬时重试梯子（2s/4s 退避白等 6s，2026-09-20 耗时预算揭出）。梯子本身由下面
    # test_submit_job_transient_retry_ladder 直接钉住（快、不打真 sleep）。
    disp = _dispatcher(hub, ws, push_attempts=1)

    disp.tick()
    assert _pump(disp, lambda: hub._stores["x2"].get_result("j" * 16) is not None)
    assert refusing.received == [] and ok.received == ["j" * 16]
    assert disp.state()["requeued"] >= 1


def test_submit_job_transient_retry_ladder(monkeypatch) -> None:
    """push_client.submit_job：409「队满」等瞬时拒绝按 2s/4s 退避重试，试满仍失败抛
    `RetryableError`（零墙钟：把 sleep 换成记录）。

    这是**生产**行为（节点轻微拥挤时先等一等，不要立刻整队回落）；此前只在
    test_worker_refusing_job_requeues_to_another 里被顺带跑成 6s 真·sleep（2+4）——
    那既不是该用例的判据，也把单测墙钟拖到耗时预算线。现在两件事各回本位：调度层
    断言用 `push_attempts=1` 快跑，梯子本身在这里逐位钉住。
    """
    import types as _types

    import remote.push_client as pc

    # 缓存探间全命中，把 httpx 面收窄到 /job 这一跳。
    monkeypatch.setattr(pc, "code_cached_on_node", lambda *a, **k: True)
    monkeypatch.setattr(pc, "ts_code_cached_on_node", lambda *a, **k: True)
    monkeypatch.setattr(pc, "blob_cached_on_node", lambda *a, **k: True)

    calls = {"n": 0}
    slept: list[float] = []

    def fake_request(base_url, token, path, **_kw):
        calls["n"] += 1
        return 409, b"busy"

    monkeypatch.setattr(pc, "_request", fake_request)
    # 只替 pc 命名空间里的 time：不碰全局 time.sleep（其它线程/插件还要用）。
    monkeypatch.setattr(
        pc, "time", _types.SimpleNamespace(sleep=lambda s: slept.append(s), time=time.time)
    )

    manifest = {"job_id": "j" * 16, "code_sha256": "c" * 8}
    with pytest.raises(pc.RetryableError):
        pc.submit_job(
            "http://node.local", "tok", manifest, b"payload", None, attempts=3, log=lambda _m: None
        )
    assert calls["n"] == 3, calls
    assert slept == [2, 4], f"退避梯子应为 2s/4s（实测 {slept}）"


def test_tampered_result_is_rejected_not_stored(tmp_path: Path, worker_factory) -> None:
    """结果对不上账（data_fp 漂）⇒ **拒收**、不落盘、换一台重跑。

    这条也钉住「两条腿共用 `accept_result`」：云机 POST 上来那条路一直有这道校验，
    hub 代发的这条腿若漏掉，错的结果会被静默写成一轮看起来正常的训练。
    """
    bad = worker_factory(tamper=True)
    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "j" * 16)
    ws = _workers_with(tmp_path, worker_factory, (bad, {"id": "g1"}))
    disp = _dispatcher(hub, ws)

    disp.tick()
    assert _pump(disp, lambda: disp.requeued >= 1)
    assert hub._stores["x2"].get_result("j" * 16) is None, "对不上账的结果绝不能落盘"
    assert not (tmp_path / "x2" / "remote-jobs" / ("j" * 16) / "result").exists()
    assert hub.claimable_job_ids("x2") == ["j" * 16]


def test_pull_job_head_is_not_pushed(tmp_path: Path, worker_factory) -> None:
    """队首不归 hub 推（`dispatch` 缺席 = pull/本机）⇒ 本拍一份都不推，且不越过它。"""
    w1 = worker_factory()
    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "p" * 16, dispatch="")  # 队首：pull 活
    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _dispatcher(hub, ws)

    for _ in range(3):
        disp.tick()
    assert w1.received == []
    assert disp.state()["pushed"] == 0
    assert hub.claimable_job_ids("x2") == ["p" * 16]


def test_no_idle_worker_leaves_job_at_head(tmp_path: Path, worker_factory) -> None:
    """没有空闲 worker ⇒ 什么都不推（等下一拍），job 留在队首、无租约残留。"""
    hub = _hub(tmp_path, ["x2"])
    _publish(hub, "x2", "j" * 16)
    ws = PushWorkers(
        tmp_path / "push-config.json", log=_quiet, ping_sec=0.0, ping_timeout=_PING_TIMEOUT
    )
    disp = _dispatcher(hub, ws)

    disp.tick()
    assert disp.state()["started" if "started" in disp.state() else "pushed"] == 0
    assert hub.claimable_job_ids("x2") == ["j" * 16]
    assert disp.state()["inflight"] == []


# ------------------------------------------------------------------ ⑤ 预推（2026-10-24）


def _queue_worker(tmp_path: Path, started: list[str]):
    """真 `worker_server`（真队列/真 HTTP 面），starter 换成只记账的假执行器。

    预推的正确性一半在 worker 侧：`submit` 忙时入队、`/result` 对排队单回 202 `queued`、
    `kick` 在上一份结束后拉起队首。这些都不能用假 handler 手搓——抄错的假体就是下一个 bug。
    """
    state = WorkerServerState(tmp_path / "queue-work")
    srv = make_worker_server(state, 0, "sekret")
    # 构造时注入的是真 starter（会跑 run_job/PPO）——在任何 POST 之前换掉。
    state.set_starter(lambda jid, item: started.append(jid))
    # `_manifest` 的 code_sha256 恒为 "z"*64：在节点侧造一个「已缓存」标记，
    # 否则 `submit_job` 会因「节点无 code 缓存且本次未携带 code.zip」直接回落
    # （那是另一条腿的契约，不该陪跑预推用例）。
    cache = state.work_dir / "code_cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ("z" * 64)).write_bytes(b"fake-code")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, state, th


def _two_course_hub(tmp_path: Path) -> tuple[_HubQueue, dict[str, dict]]:
    hub = _hub(tmp_path, ["a", "b"])
    return hub, {"a": _publish(hub, "a", "a" * 16), "b": _publish(hub, "b", "b" * 16)}


def _worker_registry(tmp_path: Path, url: str) -> PushWorkers:
    ws = PushWorkers(
        tmp_path / "push-config.json", log=_quiet, ping_sec=0.0, ping_timeout=_PING_TIMEOUT
    )
    ws.add({"id": "g1", "url": url, "authKey": "sekret"})
    return ws


def test_push_ahead_queues_the_next_job_and_defers_start_marker(tmp_path: Path) -> None:
    """★ 预推：worker 在跑时也接下一份，传输叠到当前 PPO 上（不再「传完才开算」）。

    三件事一起钉（它们任意一条错，预推就从「省空转」变成「烧 GPU」）：
      ① 第二份真的进了 worker 的有界 FIFO（还没开算）；
      ② `computing_at` 在排队期间为空（否则 `job_priority` 会把排队读成「掉队」并开备份）；
      ③ 排队时间不烧「推送后无结果」预算（假时钟：总龄 13s > timeout 10s，开算才 5s）。
    """
    # 假时钟从**真实时刻**起跳（`tick` 的「到点探活」拿派发器时钟与登记表的 last_ping 比，
    # 从 0 起跳会让探活永不 due，预推永远等不到「忙」这个事实）。
    clock = [time.time()]
    started: list[str] = []
    srv, state, th = _queue_worker(tmp_path, started)
    hub, ms = _two_course_hub(tmp_path)
    disp = PushDispatcher(
        hub,
        _worker_registry(tmp_path, f"http://127.0.0.1:{srv.server_address[1]}"),
        "sekret",
        poll_sec=0.01,
        timeout_sec=10.0,
        push_ahead=1,
        now_fn=lambda: clock[0],
        log=_quiet,
    )
    try:
        # `ping=True`：假时钟是**冻结**的，靠它判「到点探活」永远不 due（那需要墙钟推进）。
        disp.tick(ping=True)
        assert _wait_until(lambda: len(started) == 1), disp.state()
        first = started[0]
        first_course = "a" if first == "a" * 16 else "b"
        second_course = "b" if first_course == "a" else "a"
        second = second_course * 16

        disp.tick(ping=True)  # 忙机 + 还有接单位 ⇒ 预推第二份
        assert _wait_until(
            lambda: (state.get(second) or {}).get("state") == "queued"
        ), disp.state()
        assert state.queued() == 1
        assert hub._stores[second_course].scheduling_facts(second)["computing_at"] is None

        clock[0] += 8.0  # 排队 8s（< 10s 队龄上限）
        state.set_result(first, result_of(ms[first_course]))
        state.kick()
        # 先把第一份收尾干净再跳钟（否则假时钟越过 a 的 10s 预算、而 hub 还没收到它的
        # 结果时，会被「无结果」回落——那是测试自己挤出来的竞态，不是被测语义）。
        assert _wait_until(lambda: hub._stores[first_course].get_result(first) is not None)
        assert _wait_until(lambda: second in started), (started, disp.state())
        clock[0] += 5.0  # 总龄 13s > timeout 10s，但开算后才 5s
        assert _wait_until(
            lambda: hub._stores[second_course].scheduling_facts(second)["computing_at"]
            is not None
        ), disp.state()
        state.set_result(second, result_of(ms[second_course]))
        assert _wait_until(
            lambda: hub._stores[second_course].get_result(second) is not None
        ), disp.state()
        assert disp.state()["requeued"] == 0
    finally:
        disp.stop()
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_push_ahead_zero_keeps_the_busy_worker_untouched(tmp_path: Path) -> None:
    """`--push-ahead 0` = 真旧行为：忙机一份都不多收，第二份留在队首（回退档）。"""
    started: list[str] = []
    srv, state, th = _queue_worker(tmp_path, started)
    hub, _ms = _two_course_hub(tmp_path)
    disp = PushDispatcher(
        hub,
        _worker_registry(tmp_path, f"http://127.0.0.1:{srv.server_address[1]}"),
        "sekret",
        poll_sec=0.01,
        timeout_sec=10.0,
        push_ahead=0,
        log=_quiet,
    )
    try:
        disp.tick()
        assert _wait_until(lambda: len(started) == 1), disp.state()
        for _ in range(3):
            disp.tick()
            # sleep-ok: 轮询步长（等的是「忙机没有被选」这个状态，不是墙钟）
            time.sleep(0.05)
        remaining_course = "b" if started[0] == "a" * 16 else "a"
        remaining = remaining_course * 16
        assert state.get(remaining) is None  # 忙机没有多收任何一份
        assert state.queued() == 0
        assert hub.claimable_job_ids(remaining_course) == [remaining]
        assert disp.state()["push_ahead"] == 0
    finally:
        disp.stop()
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_trainer_publish_with_dispatch_flag_flows_through_hub_push(
    tmp_path: Path, worker_factory
) -> None:
    """训练侧**真发布**（`publish_job(dispatch="push")`）→ hub 推送 → 结果可读。

    这条把「训练侧 push 改走 hub」钉在真发布链上：manifest 落盘的 `dispatch` 字面量与
    hub 的认领判据（`push_job_wants_hub_push`）必须是同一个值——写错一处就是 job 永远
    躺在队首（训练侧一直等、hub 一直不动，而两边日志都很安静）。
    """
    from remote.hub_client import publish_job

    w1 = worker_factory()
    wfile = tmp_path / "init_weights.json"
    wfile.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    jsonl = tmp_path / "training_log.jsonl"
    jsonl.write_text("", encoding="utf-8")
    store = _JobStore(tmp_path / "jobs", jsonl)
    hub = _HubQueue({"x2": store}, order=["x2"])
    m = publish_job(
        job_root=tmp_path / "jobs",
        jsonl_path=jsonl,
        run_id="r",
        it=1,
        traj_dir=tmp_path / "traj",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='{"reward":{"formula":"score"}}',
        course_fp="f" * 64,
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        init_weights_path=str(wfile),
        dispatch="push",
        log=_quiet,
    )
    jid = m["job_id"]
    assert m["dispatch"] == "push" and push_job_wants_hub_push(m)

    ws = _workers_with(tmp_path, worker_factory, (w1, {"id": "g1"}))
    disp = _dispatcher(hub, ws)
    disp.tick()
    assert _wait_until(lambda: store.get_result(jid) is not None)
    assert w1.received == [jid]
    got = store.get_result(jid)
    assert got is not None and got["data_fp"] == m["data_fp"]


def test_push_workers_admin_endpoint(tmp_path: Path, worker_factory) -> None:
    """`GET/POST /admin/push-workers`：看登记表与派发器状态；运行时增删；未启用时 409。"""
    hub = _hub(tmp_path, ["x2"])
    ws = _workers_with(tmp_path, worker_factory)
    disp = _dispatcher(hub, ws)
    srv = make_server(hub, 0, "sekret", host="127.0.0.1", push=disp)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        status, body = _get(base, "/admin/push-workers")
        assert status == 200
        assert set(body) == {"dispatcher", "registry"}
        assert body["registry"]["workers"] == []
        # 运行时登记（volatile）——控制台/冒烟不想等 mtime 热重载时走这条路
        live = worker_factory()
        status, body = _post(
            base, "/admin/push-workers", {"action": "add", "id": "g9", "url": live.url}
        )
        assert status == 200 and body["worker"]["id"] == "g9"
        assert [w["id"] for w in _get(base, "/admin/push-workers")[1]["registry"]["workers"]] == [
            "g9"
        ]
        assert _post(base, "/admin/push-workers", {"action": "remove", "id": "g9"})[0] == 200
        assert _get(base, "/admin/push-workers")[1]["registry"]["workers"] == []
        # 非法体 / 未知 action 响亮 400，不改现状
        assert _post(base, "/admin/push-workers", {"action": "bogus"})[0] == 400
        assert _post(base, "/admin/push-workers", {"action": "add"})[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)

    # 未启用 push 派发的 hub：端点 409（响亮好过默默什么都没发生）
    srv2 = make_server(hub, 0, "sekret", host="127.0.0.1")
    th2 = threading.Thread(target=srv2.serve_forever, daemon=True)
    th2.start()
    try:
        base2 = f"http://127.0.0.1:{srv2.server_address[1]}"
        assert _get(base2, "/admin/push-workers")[0] == 409
        assert _post(base2, "/admin/push-workers", {"action": "reload"})[0] == 409
    finally:
        srv2.shutdown()
        srv2.server_close()
        th2.join(timeout=5)


# ------------------------------------------------------------------ hub HTTP 小工具


def _get(base: str, path: str) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, headers={AUTH_HEADER: "Bearer sekret"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        headers={AUTH_HEADER: "Bearer sekret", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except ValueError:
            return e.code, {}
