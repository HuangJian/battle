"""节点探活预算 / 并发取小 —— 云机（trycloudflare 隧道）被误判「断连」的三个口子。

## 现场（2026-11-11，kaggle 滚动机 rollout 节点）

- 节点答 `/v1/ping` 要 **0.9~2.7s**，而派发/评估的预算是 **3s**：
  07/08/09 三个小时各 `ping failed — excluded this round` **84 / 97 / 88 次**，
  eval 侧 `连续 9 次瞬时失败（背压/瞬断）— 停派`、`让出在飞 4 槽 ⇒ c=0/4`。
  **§149 更正**：那 0.9~2.7s 不是隧道 RTT，是 `socket.create_connection` **串行**试
  `getaddrinfo` 每个地址、且 timeout 按地址各给一份的 IPv6 税（kaggle 主机名 = 2 个
  IPv6 黑洞 + 2 个 IPv4；tcp 段实测 16.24s，IPv4 只要 0.21s）。当时把预算 3s→8s
  只是把税放大（同批课程 rollout 39~41s → 53~95s），已收回 3s；真修在
  `common.net_http.racing_create_connection`（用例 `tests/common/test_racing_connect.py`）。
- 一次 ping 超时就把该节点的**权重下发账本**摘掉（`forget_weights_node`）⇒ 下一轮
  重新握手；而云机“已缓存”的探针本身也可能超时（5s），于是退化成整份重传：
  日志里 `weights[rollout] -> kaggle (kept, 54.6s)`——**节点明明有这份权重**。
  权重只有 0.38MB ⇒ 50s 不是带宽，是「探针超时 → 白传」。（探针预算同样曾抬到 15s，
  实测变成 30.83s，§149 一并收回 5s。）
- 节点 `/v1/ping` 报 `cpus=2`（`physicalCores()`），而 rl-config 钉 `concurrency=4`、
  笔记本用 `effective_cores()`（4）起 worker ⇒ 4 个重型 native worker 抢 2 个物理核，
  HTTP 事件循环被饿住，反过来放大前两条。

## 本文件钉什么

1. 探活预算有**唯一来源**（`common.distribution.STATUS_TIMEOUT_SEC`），派发/评估/批跑
   缺省都用它；policy 显式给值仍优先（现场那份 rl-config 正是显式 3，所以要能读到）。
2. **ping 超时 ≠ 节点重启**：不摘权重账本；只有 codeHash 不符（真重启）才摘。
3. 并发槽位**显式 config 优先**（2026-11-14 收回取小）：config 4 + cpus 2 ⇒ 4；config 缺省
   回落节点自报池容量 `workers` → `cpus`（`node_slots`）。
"""

from __future__ import annotations

from typing import Any

import pytest

import common.distribution
import trainer.dispatch as disp
from tests.trainer.test_rollout_dispatch_resilience import GOOD_PING, _Harness, _node

# ------------------------------------------------------------------ 1) 预算唯一来源


def test_status_budget_default_is_shared() -> None:
    """缺省 3s（§149 收回 §148 的 8s：真因是串行 connect，抬预算只放大 IPv6 税）。

    policy 显式值仍是权威——「唯一来源」钉的是缺省不许各处再写死字面量，不是不许覆盖。
    """
    assert common.distribution.STATUS_TIMEOUT_SEC == 3.0
    assert common.distribution.status_timeout_of({}) == 3.0
    assert common.distribution.status_timeout_of({"statusTimeoutSec": 8}) == 8.0
    assert common.distribution.status_timeout_of({"statusTimeoutSec": "12"}) == 12.0
    # 坏值不许炸掉整轮探活（沿用「配置写错就当缺省」的既有口径）
    assert common.distribution.status_timeout_of({"statusTimeoutSec": "abc"}) == 3.0


def test_dispatch_probes_with_the_shared_budget(tmp_path, monkeypatch) -> None:
    """policy 不写 `statusTimeoutSec` 时，ping 的实际预算必须是共享常量（不是另写死的字面量）。"""
    seen: list[float] = []

    def _ping(_url: str, _key: str, timeout: float = 0.0) -> dict[str, Any] | None:
        seen.append(float(timeout))
        return dict(GOOD_PING)

    h = _Harness(tmp_path, monkeypatch, 1, nodes=[_node("a97", 1)], ping_fn=_ping)
    h.run(lambda *a, **k: h.manifest())
    assert seen and set(seen) == {common.distribution.STATUS_TIMEOUT_SEC}, seen


# ------------------------------------------------------------------ 2) 超时不摘权重账本


def _wver_of(h: _Harness) -> str:
    return common.distribution.weights_fingerprint(str(h.weights))


def test_ping_timeout_keeps_the_weights_ledger(tmp_path, monkeypatch) -> None:
    """超时的节点**这轮不派**，但它「已收过这份权重」的事实要留着（否则下轮白传 0.38MB）。"""
    # 账本由 `_Harness.run()` 清零；本用例要预置一条，故先换成 no-op。
    monkeypatch.setattr(common.distribution, "weights_push_cache_reset", lambda: None)
    h = _Harness(
        tmp_path,
        monkeypatch,
        1,
        nodes=[_node("a97", 1), _node("kaggle", 1)],
        ping_fn=lambda url, *_a, **_k: (
            None if "kaggle" in str(url) else dict(GOOD_PING)
        ),
    )
    wver = _wver_of(h)
    common.distribution.note_weights_pushed(wver, "kaggle")
    assert common.distribution.weights_already_pushed(wver, "kaggle") is True

    h.run(lambda *a, **k: h.manifest())

    assert common.distribution.weights_already_pushed(wver, "kaggle") is True, (
        "ping 超时只是「这轮够不着」，不是「节点重启丢了桶」——摘账本 = 下轮整份重传"
    )


def test_codehash_mismatch_still_forgets_the_weights_ledger(tmp_path, monkeypatch) -> None:
    """真重启（codeHash 变了）才摘：那条路径的桶确实空了，不摘就会带脏缓存进 reuse。"""
    monkeypatch.setattr(common.distribution, "weights_push_cache_reset", lambda: None)
    # 不锁存升级分支 ⇒ mismatch 只打⛔「excluded (red)」，不发重启请求（不碰网络）
    monkeypatch.setattr(common.distribution, "current_upgrade_branch", lambda: "")
    stale = {**GOOD_PING, "codeHash": "ffffffff"}
    h = _Harness(
        tmp_path,
        monkeypatch,
        1,
        nodes=[_node("a97", 1), _node("stale", 1)],
        ping_fn=lambda url, *_a, **_k: (stale if "stale" in str(url) else dict(GOOD_PING)),
    )
    wver = _wver_of(h)
    common.distribution.note_weights_pushed(wver, "stale")

    h.run(lambda *a, **k: h.manifest())

    assert common.distribution.weights_already_pushed(wver, "stale") is False


# ------------------------------------------------------------------ 3) 并发槽位：config 优先


def test_node_slots_config_wins_over_cores() -> None:
    """显式 config 是操作者旋钮，**不再**被核数取小；缺省才回落 workers → cpus。

    反转的理由（plan/node-throughput-and-keepalive D1）：派工线程每局被隧道往返与结果
    回传整段占住（gcs `wall 9.3s` / `svc 2.3s`），按核数取小会让节点 2 个游戏槽 64%
    时间空转。超额认购的防线在 agent 准入队列（有界排队 / 503），不在派工侧。
    """
    assert common.distribution.node_slots(4, 2) == 4  # config 4 + 2 核 ⇒ 4（旧口径是 2）
    assert common.distribution.node_slots(4, 2, 8) == 4  # config 优先于节点池容量
    assert common.distribution.node_slots(8, 6, 4) == 8  # 允许超额派工（排队由节点兜）
    assert common.distribution.node_slots(0, 2, 8) == 8  # 缺省 → ping.workers
    assert common.distribution.node_slots(0, 2) == 2  # 老 agent 无 workers ⇒ 回落 cpus
    assert common.distribution.node_slots(0, 0, 0) == 1  # 谁都没给 ⇒ 1（不许 0 槽）


def test_dispatch_slots_follow_config(tmp_path, monkeypatch) -> None:
    """派发侧真的走 `node_slots`：`concurrency=4` 而 ping 只报 2 核 ⇒ 仍孵 **4** 个线程。

    为什么看信号而不是日志：槽位数就是「该节点起了几个消费者线程」，在飞计数是同一
    事实的可调用出口（日志行是措辞，改文案就误报）。关掉 rescan 免得回场线程混进来。
    """
    import threading

    ping = {**GOOD_PING, "cpus": 2, "workers": 4}
    h = _Harness(
        tmp_path,
        monkeypatch,
        5,
        nodes=[_node("kaggle", 4)],
        ping_fn=lambda *a, **k: ping,
        policy={"nodeRecoverFirstSec": 0, "recoverPingSec": 0},
    )
    release = threading.Event()  # 让在飞者停在半路（否则「谁先跑完」变成时序骰子）
    fourth = threading.Event()
    fifth = threading.Event()
    lock = threading.Lock()
    now = {"n": 0, "peak": 0}

    def fetch(*_a, **_k):
        with lock:
            now["n"] += 1
            now["peak"] = max(now["peak"], now["n"])
            n = now["n"]
        if n == 4:
            fourth.set()
        elif n >= 5:
            fifth.set()
        release.wait(5.0)
        with lock:
            now["n"] -= 1
        return h.manifest(), {}

    done = threading.Thread(target=lambda: h.run(fetch), daemon=True)
    done.start()
    assert fourth.wait(5.0), f"4 个槽位没起满（config 优先失效？）logs={h.logs[-8:]}"
    assert not fifth.wait(0.5), "出现了第 5 个线程 ⇒ 超过 config=4"
    release.set()
    done.join(10.0)
    assert not done.is_alive(), "本轮没收工（槽位/窗口语义变了？）"
    assert now["peak"] == 4, f"peak 在飞 = {now['peak']}（应为 config 的 4，不再是 cpus 的 2）"


def test_dispatch_defaults_to_ping_workers(tmp_path, monkeypatch) -> None:
    """config 缺省 ⇒ 派发数 = 节点自报 `workers`（旧 agent 无 workers 时才回落 cpus）。"""
    import threading

    ping = {**GOOD_PING, "cpus": 2, "workers": 3}
    h = _Harness(
        tmp_path,
        monkeypatch,
        4,
        nodes=[{"id": "kaggle", "url": "http://kaggle.local"}],  # 无 concurrency 键
        ping_fn=lambda *a, **k: ping,
        policy={"nodeRecoverFirstSec": 0, "recoverPingSec": 0},
    )
    release = threading.Event()
    third = threading.Event()
    fourth = threading.Event()
    lock = threading.Lock()
    now = {"n": 0}

    def fetch(*_a, **_k):
        with lock:
            now["n"] += 1
            n = now["n"]
        if n == 3:
            third.set()
        elif n >= 4:
            fourth.set()
        release.wait(5.0)
        with lock:
            now["n"] -= 1
        return h.manifest(), {}

    done = threading.Thread(target=lambda: h.run(fetch), daemon=True)
    done.start()
    assert third.wait(5.0), f"缺省没回落到 workers=3（日志？）logs={h.logs[-8:]}"
    assert not fourth.wait(0.5), "出现了第 4 个线程 ⇒ 缺省口径不是 workers=3"
    release.set()
    done.join(10.0)
    assert not done.is_alive(), "本轮没收工（槽位/窗口语义变了？）"
