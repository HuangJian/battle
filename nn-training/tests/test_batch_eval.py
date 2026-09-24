"""test_batch_eval — B 层批次编排（P2）单测：规划镜像 + 队列 + 接线断言。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.batch_eval import (
    batch_iter_id,
    claim_pending,
    load_ladder,
    mark_unit_done,
    plan_units,
    read_batches,
    write_batches,
)


def test_plan_units_mirrors_runner_ts() -> None:
    ladder = load_ladder()
    ids = [r["id"] for r in ladder["rungs"]]
    assert ids == ["c4l1", "c6l1", "c8l2", "c10l2", "c14l3", "c20l3", "s1l3b0", "s1l3b1"]
    u0 = plan_units(ladder, 0, 0)
    assert [u["rung"] for u in u0] == ["c4l1", "c6l1"]
    assert all(u["seed0"] == 860001 and len(u["seeds"]) == 100 for u in u0)
    u2 = plan_units(ladder, 0, 2)
    assert [u["rung"] for u in u2] == ["c4l1", "c6l1", "c4l1"]
    assert u2[2]["seed0"] == 860001  # 回归位钉死段 0
    u17 = plan_units(ladder, 3, 17)
    assert u17[0]["rung"] == "c10l2" and u17[0]["seed0"] == 860101  # seg=17%16=1
    # 派发指纹齐备（agent resultCache 键分量 + 自定义关能力位前提）
    assert len(u0[0]["stageJsonHash"]) == 16
    assert u0[0]["stageId"] == 2000
    assert u0[0]["lives"] == 1 and u0[0]["maxTicks"] == 12000


def test_batch_iter_id_namespace() -> None:
    assert batch_iter_id("run1", "some-batch-id").startswith("run1.b")
    assert "ev" not in batch_iter_id("run1", "x").split(".b")[0].split(".")[-1]


def test_queue_claim_done_cycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    import rl.batch_eval as be

    assert be.data_root() == tmp_path
    b = {"batch_id": "b1", "status": "pending", "units": {"of": 2, "done": []}}
    write_batches(tmp_path, [b])
    claimed = claim_pending(tmp_path)
    assert claimed is not None and claimed["status"] == "running"
    # 单 unit 完成但 of=2：回 pending 供下一 idle 领 u1
    mark_unit_done(tmp_path, "b1", 0, {"self": 100})
    (mid,) = read_batches(tmp_path)
    assert mid["status"] == "pending"
    assert mid["units"]["done"] == [0]
    # running + incomplete 也可被 claim（重启/孤儿批续跑）
    claimed2 = claim_pending(tmp_path)
    assert claimed2 is not None and claimed2["status"] == "running"
    assert claimed2["units"]["done"] == [0]
    mark_unit_done(tmp_path, "b1", 1, {"self": 100})
    (final,) = read_batches(tmp_path)
    assert final["status"] == "done"
    assert sorted(final["units"]["done"]) == [0, 1]
    assert claim_pending(tmp_path) is None


def test_hooks_decoupled_from_a_eval() -> None:
    """B/C 批与 A-eval 解耦（2026-09-11）：rollout 不再领批；TrainingLoop idle 窗领取。"""
    src = (ROOT / "rl" / "rollout_phase.py").read_text(encoding="utf-8")
    assert "maybe_dispatch_batch" not in src
    lc = (ROOT / "rl" / "loop_core.py").read_text(encoding="utf-8")
    assert "_evalboard_idle" in lc
    assert "_evalboard_yield" in lc
    assert "maybe_dispatch_batch" in lc
    assert "window_event=self._eb_window" in lc
    ed = (ROOT / "rl" / "eval_dispatch.py").read_text(encoding="utf-8")
    # 节点门判定在 eval_dispatch 里（2026-09-17 起 = check_code_hash，与 rollout 同源）
    assert "check_code_hash" in ed
    import dist_common

    # engine_epoch 仍是账本记录值（EvalGameRow.engine / 心跳），但不再是节点门判据
    assert hasattr(dist_common, "compute_engine_epoch")
    assert hasattr(dist_common, "check_code_hash")


def test_partial_unit_reopens_batch(tmp_path: Path, monkeypatch) -> None:
    """yield/超时部分完成：不标 unit done，批回 pending 供续跑。"""
    monkeypatch.setenv("EVALBOARD_DATA", str(tmp_path))
    from rl.batch_eval import _reopen_for_resume, read_batches

    write_batches(
        tmp_path,
        [{"batch_id": "b1", "status": "running", "units": {"of": 2, "done": []}}],
    )
    _reopen_for_resume(tmp_path, "b1")
    (b,) = read_batches(tmp_path)
    assert b["status"] == "pending"
    assert b["units"]["done"] == []


def test_select_next_unit_only_rungs() -> None:
    from rl.batch_eval import select_next_unit

    units = [{"rung": "c4l1"}, {"rung": "c6l1"}, {"rung": "c4l1"}]
    u, i, one = select_next_unit(units, set(), ["c6l1"])
    assert [x["rung"] for x in u] == ["c6l1"] and i == 0 and one == {"rung": "c6l1"}
    u2, i2, _ = select_next_unit(units, {0}, None)
    assert i2 == 1
    _, i3, one3 = select_next_unit(units, {0, 1, 2}, None)
    assert i3 is None and one3 is None
    _, i4, _ = select_next_unit(units, set(), ["nope"])
    assert i4 is None


def test_window_close_no_new_games(tmp_path: Path, monkeypatch) -> None:
    """P2 DoD：窗口关闭不派新局；在途局自然收完（fake 节点 + fake manifest）。"""
    import threading
    import types

    import dist_common
    import rl.batch_eval as be

    weights = tmp_path / "w.json"
    weights.write_text("{}", encoding="utf-8")
    eval_log = tmp_path / "eval_log.jsonl"
    epoch = dist_common.compute_engine_epoch()
    ping = {
        "evalSupport": True,
        "stageJsonSupport": True,
        "bunVersion": "9.9.9",
        "cpus": 1,
        # 节点门指纹 = codeHash（2026-09-17 起；engine_epoch 不再进 ping）
        "codeHash": dist_common.compute_code_hash(),
    }
    monkeypatch.setattr(dist_common, "node_ping", lambda *a, **k: dict(ping))
    monkeypatch.setattr(dist_common, "post_weights", lambda *a, **k: "kept")
    monkeypatch.setattr("rl.batch_eval.bun_version", lambda *a, **k: "9.9.9")

    def fake_fetch(url, key, **kw):
        return (
            {
                "wver": kw["wver"],
                "mode": "eval",
                "outcome": "stage_clear",
                "ticks": 100,
                "win": True,
                "stage": kw["stage"],
                "seed": kw["seed"],
                "policy": kw.get("policy", "nn"),
            },
            {},
        )

    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    args = types.SimpleNamespace(eval_window_sec=2)
    cfg = {
        "policy": {
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 1,
            "evalLocalSlots": 0,
        },
        "nodes": [
            {"id": "n1", "url": "http://x", "authKey": "", "enabled": True, "concurrency": 1}
        ],
    }
    ladder = load_ladder()
    units = plan_units(ladder, 0, 0)
    batch = {"batch_id": "bw", "iter": 1, "units": {"of": 1, "done": []}}
    # 关窗：unset 事件 + 假 deadline 已过——用 window_event 未置位且 eval_window 很小
    closed = threading.Event()
    r = be.BatchEvalRunner(
        "bun",
        str(weights),
        eval_log,
        args,
        cfg,
        batch,
        units[0],
        0,
        1,
        "run1",
        epoch,
        "nn",
        closed,
        "",
    )
    out = r.run()
    assert out["settled"] == 0 and out["dropped"] == 100
    # 开窗：同样 100 局全部结算。
    # eval_window_sec 必须给足 —— 它是**墙钟预算**，而 worker 在 pending 清空时立即返回，
    # 所以宽窗口不会拖慢测试（正常 2-3s 就收完），却能在门禁并行（ruff+mypy+pytest 同时跑）
    # 的 CPU 争用下不被误判成 dropped。实测：2s 窗口在门禁负载下会掉 5-18 局 → 假红。
    args_open = types.SimpleNamespace(eval_window_sec=120)
    opened = threading.Event()
    opened.set()
    batch2 = {"batch_id": "bw2", "iter": 1, "units": {"of": 1, "done": []}}
    r2 = be.BatchEvalRunner(
        "bun",
        str(weights),
        eval_log,
        args_open,
        cfg,
        batch2,
        units[0],
        0,
        1,
        "run1",
        epoch,
        "nn",
        opened,
        "",
    )
    out2 = r2.run()
    assert out2["settled"] == 100 and out2["dropped"] == 0


# ---- 背压 vs 节点故障（2026-09-19 x20-powered it0 探针事故回归） ----
#
# 事故现场（tmp/x20-powered-zeroshot.log）：集群被训练作业占满，每个单元开头一瞬
# 十几条 `task fetch failed: [WinError 10054]`。原实现把它们计入 nodeFailStreak ⇒
# 6 个节点在同一秒内全被停派 ⇒ 200 局**全部**落本地（逐局行 node 列 = local），
# 单元墙钟 172–191s，而日志只有十几行“requeued”看不出降级。
# 判据与 rl/bc_dispatch 的 busy 背压同源（busy 是限流信号，不是故障）。


def _run_unit(
    tmp_path: Path, monkeypatch, *, cfg_policy: dict, fake_fetch, units_pick=None, nodes=None,
    window: float = 60.0,
):
    import types

    import dist_common
    import rl.batch_eval as be

    weights = tmp_path / "w.json"
    weights.write_text("{}", encoding="utf-8")
    eval_log = tmp_path / "eval_log.jsonl"
    epoch = dist_common.compute_engine_epoch()
    ping = {
        "evalSupport": True,
        "stageJsonSupport": True,
        "bunVersion": "9.9.9",
        "cpus": 1,
        "codeHash": dist_common.compute_code_hash(),
    }
    monkeypatch.setattr(dist_common, "node_ping", lambda *a, **k: dict(ping))
    monkeypatch.setattr(dist_common, "post_weights", lambda *a, **k: "kept")
    monkeypatch.setattr("rl.batch_eval.bun_version", lambda *a, **k: "9.9.9")
    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    args = types.SimpleNamespace(eval_window_sec=window)
    cfg = {
        "policy": cfg_policy,
        "nodes": nodes
        or [
            {
                "id": "n1",
                "url": "http://x",
                "authKey": "",
                "enabled": True,
                "concurrency": 2,
            }
        ],
    }
    ladder = load_ladder()
    units = plan_units(ladder, 0, 0)
    unit = units_pick(units) if units_pick else units[0]
    batch = {"batch_id": "bp", "iter": 1, "units": {"of": 1, "done": []}}
    logs: list[str] = []
    monkeypatch.setattr("rl.batch_eval.log", lambda m: logs.append(str(m)))
    r = be.BatchEvalRunner(
        "bun", str(weights), eval_log, args, cfg, batch, unit, 0, 1, "run1", epoch, "nn", None, ""
    )
    out = r.run()
    return out, logs, eval_log


def _ok_manifest(stage: int, seed: int, wver: str) -> dict:
    return {
        "wver": wver,
        "mode": "eval",
        "outcome": "stage_clear",
        "ticks": 100,
        "win": True,
        "stage": stage,
        "seed": seed,
        "policy": "nn",
    }


def test_transient_reset_is_backpressure_not_node_fault(tmp_path: Path, monkeypatch) -> None:
    """连接被重置（10054）→ 背压重排、不计节点失败：全部仍由节点完成。"""
    import dist_common

    seen_calls: dict[tuple[int, int], int] = {}

    def fake_fetch(url, key, **kw):
        task = (int(kw["stage"]), int(kw["seed"]))
        n = seen_calls.get(task, 0) + 1
        seen_calls[task] = n
        if n <= 3:  # 一瞬的 10054（节点满负荷）
            raise dist_common.DistError(
                0,
                "task fetch failed: [WinError 10054] An existing connection was forcibly closed",
                transient=True,
            )
        return _ok_manifest(task[0], task[1], kw["wver"]), {}

    # nodeFailStreak=3：旧实现会让“同一秒 3 次瞬断”把节点停派。
    out, logs, _ = _run_unit(
        tmp_path,
        monkeypatch,
        cfg_policy={
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 3,
            "evalLocalSlots": 0,
            "busyRetryLimit": 6,
            "busyBackoffSec": 0.001,
        },
        fake_fetch=fake_fetch,
        units_pick=lambda us: us[0],
    )
    assert out["dropped"] == 0 and out["settled"] == out["total"]
    assert any("背压" in m for m in logs), "应有背压重排日志"
    # 关键性质（旧实现反例）：节点不得被停派，真失败计数必须为空。
    assert not any("停派" in m for m in logs), "瞬断不得熔断节点"
    tally = next(m for m in logs if "真失败计数" in m)
    assert tally.split("真失败计数")[1].strip() == "—", f"真失败计数应空：{tally}"
    assert any(m.endswith("provenance: n1=100") for m in logs), "全部应由节点完成（无本地）"


def test_hard_failure_still_trips_node(tmp_path: Path, monkeypatch) -> None:
    """真失败（非瞬断）照旧熔断——背压通道不能把坏节点洗成健康。"""
    import dist_common

    def fake_fetch(url, key, **kw):
        raise dist_common.DistError(0, "TypeError: undefined is not an object ('s.obs')")

    out, logs, _ = _run_unit(
        tmp_path,
        monkeypatch,
        cfg_policy={
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 2,
            "evalLocalSlots": 0,
            "busyRetryLimit": 6,
            "busyBackoffSec": 0.001,
            # 失联重探间隔：生产缺省 20s（三轮回满一分钟）；单测压到 50ms。
            "recoverPingSec": 0.05,
        },
        fake_fetch=fake_fetch,
        units_pick=lambda us: us[0],
    )
    assert any("真失败" in m and "停派" in m for m in logs), "硬失败必须熔断"
    # 有界重探：连续三轮恢复后仍 0 局 ⇒ 放弃该节点（不拖满整窗），日志响亮。
    assert any("不再等它" in m for m in logs), "坏节点必须有界放弃"
    # 计了失败计数就必须留痕（2026-09-19 实测「真失败计数 gcs=1」却零日志可查）。
    assert any("failed (" in m and ("requeued" in m or "试满" in m) for m in logs), logs
    assert out["dropped"] > 0


def test_remote_zero_participation_is_loud(tmp_path: Path, monkeypatch) -> None:
    """远端 0 参与（全节点失败 + 本地槽位兜底）→ 响亮告警，不再静默降级。"""
    import dist_common

    calls = {"n": 0}

    def fake_fetch(url, key, **kw):
        # 前几次瞬断耗尽背压额度，随后一律硬失败 → 节点停派。
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            raise dist_common.DistError(0, "boom")
        raise dist_common.DistError(0, "task fetch failed: connection reset", transient=True)

    # 本机槽位签名：run_local_eval_game(bun, weights, stage, seed, dir, wver=…) —— manifest
    # 必须回显 wver（validate_eval_result 按 wver 对账）。
    monkeypatch.setattr(
        "rl.batch_eval.run_local_eval_game",
        lambda *a, **k: _ok_manifest(int(a[2]), int(a[3]), str(k.get("wver", ""))),
        raising=False,
    )
    out, logs, _ = _run_unit(
        tmp_path,
        monkeypatch,
        cfg_policy={
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 1,
            "evalLocalSlots": 2,
            "busyRetryLimit": 1,
            "busyBackoffSec": 0.001,
        },
        fake_fetch=fake_fetch,
        units_pick=lambda us: us[0],
    )
    assert any("远端 0 参与" in m for m in logs), "降级必须响亮"


def test_tail_race_steals_slow_node_tail(tmp_path: Path, monkeypatch) -> None:
    """尾段竞速（与 A 层 rl/eval_dispatch 同机制）：队列空了但还有局在慢节点上 ⇒
    空闲的快节点复制一份抢单，先返回者结算、败者按 dup 丢弃。

    事故背景（2026-09-19 800 局探针）：单元前 ~30s 快节点就干完，之后只剩 3 台慢节点
    拖尾巴（a96 平均 124s/局），每单元空转 1–3 分钟——3 台只出 8.9% 的局却吃 63% 节点秒。
    """
    import time

    import dist_common

    calls: list[tuple[str, tuple[int, int]]] = []

    def fake_fetch(url, key, **kw):
        task = (int(kw["stage"]), int(kw["seed"]))
        slow = "slow" in url
        calls.append(("slow" if slow else "fast", task))
        if slow:
            time.sleep(1.5)  # 慢节点：一口 1.5s（真集群 a96 是它的一百倍）
        return _ok_manifest(task[0], task[1], kw["wver"]), {}

    nodes = [
        {"id": "slow", "url": "http://slow", "authKey": "", "enabled": True, "concurrency": 1},
        {"id": "fast", "url": "http://fast", "authKey": "", "enabled": True, "concurrency": 1},
    ]
    t0 = time.monotonic()
    out, logs, eval_log = _run_unit(
        tmp_path,
        monkeypatch,
        cfg_policy={"statusTimeoutSec": 1, "taskTimeoutSec": 30, "evalLocalSlots": 0},
        fake_fetch=fake_fetch,
        units_pick=lambda us: us[0],
        nodes=nodes,
    )
    dt = time.monotonic() - t0
    assert out["dropped"] == 0 and out["settled"] == out["total"], out
    # 竞速真的发生了：空闲快节点领了慢节点手上的局。
    assert any("tail-race" in m and "race lane" in m for m in logs), logs
    # 败者被丢弃：不得落第二行、不得重复计数。
    rows = [json.loads(x) for x in eval_log.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(rows) == out["total"], f"逐局行数应 == 局数：{len(rows)} vs {out['total']}"
    assert len({(r["stage"], r["seed"]) for r in rows}) == out["total"]
    # 墙钟不得被慢节点拖满：慢副本各 1.5s，竞速后应明显更短（宽松上界，防抖动）。
    assert dt < out["total"] * 1.4, f"尾段未被竞速抢走：{dt:.2f}s for {out['total']} games"


def test_backpressure_requeue_does_not_drain_attempts(tmp_path: Path, monkeypatch) -> None:
    """背压重排**不得**消耗 attempt 配额（2026-09-19 修复）。

    事故：`pending.popleft()` 无条件给 `attempts[task]` +1，而背压分支（503 busy /
    连接重置）走的是同一条重排路径 ⇒ 持续背压会把 attempts 顶过 `EVAL_TASK_ATTEMPTS`，
    使一局「根本没跑成」被误判成「试满丢弃」（实测 attempt=7 > 2）⇒ `len(seen) >= total`
    永不成立 ⇒ settled 799/800 干等到 deadline（用户手动停）。

    本用例：一局连吃 8 次 503（> busyRetryLimit=6）之后才成功 —— 修复前该局会被丢弃。
    """
    import dist_common

    calls: dict[tuple[int, int], int] = {}
    victim: list[tuple[int, int]] = []

    def fake_fetch(url, key, **kw):
        task = (int(kw["stage"]), int(kw["seed"]))
        if not victim:
            victim.append(task)
        n = calls.get(task, 0) + 1
        calls[task] = n
        if task == victim[0] and n <= 8:
            raise dist_common.DistError(503, 'HTTP 503: {"error":"busy"}', transient=True)
        return _ok_manifest(task[0], task[1], kw["wver"]), {}

    out, logs, _ = _run_unit(
        tmp_path,
        monkeypatch,
        cfg_policy={
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 3,
            "evalLocalSlots": 0,
            "busyRetryLimit": 6,
            "busyBackoffSec": 0.001,
        },
        fake_fetch=fake_fetch,
        units_pick=lambda us: us[0],
        window=15.0,
    )
    assert out["settled"] == out["total"], f"背压重排不得丢局：{out}"
    assert not any("试满" in m for m in logs), "背压重排不得消耗 attempt 配额"


def test_settle_stall_exits_loudly_not_at_deadline(tmp_path: Path, monkeypatch) -> None:
    """有局被丢弃后，收尾必须**响亮收工**，而不是干等到 deadline（2026-09-19 修复）。

    事故：`len(seen) >= total` 是唯一完成条件；某局真失败耗尽配额被丢弃后该条件永不成立，
    其余 break 分支也不命中 ⇒ 主线程在 `pending=0 inflight=0 settled=799/800` 上空转
    （实测两分钟，用户手动停）。

    本用例：一局始终硬失败（HTTP 400，非瞬断）直到被丢弃，其余 99 局正常完成 ⇒
    收尾必须在 STUCK_GRACE_SEC 内以「收尾僵死」收工。
    """
    import dist_common
    import rl.batch_eval as be

    monkeypatch.setattr(be, "STUCK_GRACE_SEC", 0.5)

    victim: list[tuple[int, int]] = []

    def fake_fetch(url, key, **kw):
        task = (int(kw["stage"]), int(kw["seed"]))
        if not victim:
            victim.append(task)
        if task == victim[0]:
            raise dist_common.DistError(400, "HTTP 400 bad request (not transient)")
        return _ok_manifest(task[0], task[1], kw["wver"]), {}

    nodes = [
        {"id": "n1", "url": "http://n1", "authKey": "", "enabled": True, "concurrency": 2},
        {"id": "n2", "url": "http://n2", "authKey": "", "enabled": True, "concurrency": 2},
    ]
    out, logs, _ = _run_unit(
        tmp_path,
        monkeypatch,
        cfg_policy={
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 3,
            "evalLocalSlots": 0,
            "busyBackoffSec": 0.001,
        },
        fake_fetch=fake_fetch,
        units_pick=lambda us: us[0],
        nodes=nodes,
        window=30.0,
    )
    assert out["settled"] == out["total"] - 1, f"应恰好丢一局：{out}"
    assert any("收尾僵死" in m for m in logs), f"缺局必须响亮收工：{logs[-6:]}"

# ---- 每节点独立通道（2026-09-19 用户五条裁定）----
#
# 1. 权重下发与派发**不得**是串行阶段：某节点的权重一成功就**立即**派活，不等慢节点。
# 2. 不得再有 u0/u1/u2 单元阶段（串行屏障）：单权重 → 一个连续单元（见 eval_course_once）。
# 3. 已在正常工作的节点：不再 ping、不再重传同一份权重，一直派活。
# 4. settled 一满：**立即**关闭所有节点的连接收工，不等慢节点/竞速副本。
# 5. 失联节点每 recover_ping_sec 重探一次，通了立即传权重派单（有界，坏节点会放弃）。


def _channels_runner(
    tmp_path: Path, monkeypatch, *, nodes: list, cfg_policy: dict | None = None, unit=None, window=30.0
):
    """通道测试基座：调用方自己 patch `dist_common.{node_ping,post_weights,fetch_task}`。"""
    import types

    import dist_common
    import rl.batch_eval as be

    weights = tmp_path / "w.json"
    weights.write_text("{}", encoding="utf-8")
    eval_log = tmp_path / "eval_log.jsonl"
    monkeypatch.setattr("rl.batch_eval.bun_version", lambda *a, **k: "9.9.9")
    args = types.SimpleNamespace(eval_window_sec=window)
    cfg = {
        "policy": {
            "statusTimeoutSec": 1,
            "taskTimeoutSec": 30,
            "nodeFailStreak": 3,
            "evalLocalSlots": 0,
            **(cfg_policy or {}),
        },
        "nodes": nodes,
    }
    u = unit if unit is not None else plan_units(load_ladder(), 0, 0)[0]
    batch = {"batch_id": "bch", "iter": 1, "units": {"of": 1, "done": []}}
    logs: list[str] = []
    monkeypatch.setattr("rl.batch_eval.log", lambda m: logs.append(str(m)))
    r = be.BatchEvalRunner(
        "bun",
        str(weights),
        eval_log,
        args,
        cfg,
        batch,
        u,
        0,
        1,
        "run1",
        dist_common.compute_engine_epoch(),
        "nn",
        None,
        "",
    )
    return r, logs, eval_log


def _ok_ping() -> dict:
    import dist_common

    return {
        "evalSupport": True,
        "stageJsonSupport": True,
        "bunVersion": "9.9.9",
        "cpus": 2,
        "codeHash": dist_common.compute_code_hash(),
    }


def test_node_gate_reason_pure() -> None:
    """节点门判据是纯函数（单测直接钉四种拒绝原因，不必起线程）。"""
    import dist_common
    from rl.batch_eval import node_gate_reason

    h = dist_common.compute_code_hash()
    ok = _ok_ping()
    assert node_gate_reason(ok, "9.9.9", h) is None
    assert "evalSupport" in (node_gate_reason({**ok, "evalSupport": False}, "9.9.9", h) or "")
    assert "stageJson" in (node_gate_reason({**ok, "stageJsonSupport": False}, "9.9.9", h) or "")
    assert "bun" in (node_gate_reason({**ok, "bunVersion": "1.0.0"}, "9.9.9", h) or "")
    why = node_gate_reason({**ok, "codeHash": "deadbeef"}, "9.9.9", h)
    assert why and "deadbeef" in why, why


def test_working_node_is_never_repinged_or_reuploaded(tmp_path: Path, monkeypatch) -> None:
    """req 3：一个节点跑 100 局，只允许 1 次 ping + 1 次权重 POST。"""
    import dist_common

    counts = {"ping": 0, "post": 0}

    def fake_ping(url, key, **kw):
        counts["ping"] += 1
        return _ok_ping()

    def fake_post(*a, **k):
        counts["post"] += 1
        return "kept"

    def fake_fetch(url, key, **kw):
        return _ok_manifest(int(kw["stage"]), int(kw["seed"]), kw["wver"]), {}

    monkeypatch.setattr(dist_common, "node_ping", fake_ping)
    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    nodes = [{"id": "n1", "url": "http://n1", "authKey": "", "enabled": True, "concurrency": 2}]
    r, logs, _ = _channels_runner(tmp_path, monkeypatch, nodes=nodes)
    out = r.run()
    assert out["settled"] == out["total"] == 100
    assert counts == {"ping": 1, "post": 1}, f"就绪节点被重复探测/重传权重：{counts}"
    assert any("node n1 就绪" in m and "立即派单" in m for m in logs), logs


def test_unreachable_node_is_retried_then_joins(tmp_path: Path, monkeypatch) -> None:
    """req 5：首探失联（agent 重启中）⇒ recover_ping_sec 后重探，通了立即派单。"""
    import time

    import dist_common

    st: dict[str, float] = {"pings": 0.0, "ping_ok_at": 0.0, "first_fetch_at": 0.0}

    def fake_ping(url, key, **kw):
        st["pings"] += 1
        if st["pings"] == 1:
            return None  # 第一探失联
        st["ping_ok_at"] = time.monotonic()
        return _ok_ping()

    def fake_post(*a, **k):
        return "kept"

    def fake_fetch(url, key, **kw):
        if st["first_fetch_at"] == 0.0:
            st["first_fetch_at"] = time.monotonic()
        return _ok_manifest(int(kw["stage"]), int(kw["seed"]), kw["wver"]), {}

    monkeypatch.setattr(dist_common, "node_ping", fake_ping)
    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    nodes = [{"id": "n1", "url": "http://n1", "authKey": "", "enabled": True, "concurrency": 2}]
    r, logs, _ = _channels_runner(
        tmp_path, monkeypatch, nodes=nodes, cfg_policy={"recoverPingSec": 0.05}
    )
    out = r.run()
    assert out["settled"] == out["total"] == 100 and out["dropped"] == 0, out
    assert st["pings"] >= 2, "失联节点必须被重探"
    assert st["first_fetch_at"] > 0 and st["first_fetch_at"] >= st["ping_ok_at"] - 0.001, (
        "必须 ping 通之后才派单"
    )
    assert any("ping 失败/超时" in m and "后重探" in m for m in logs), logs


def test_fast_node_dispatches_without_waiting_for_slow_bringup(
    tmp_path: Path, monkeypatch
) -> None:
    """req 1：慢节点还在 ping/收权重，快节点就必须已经开派（旧的阶段串行会红）。

    事件驱动（2026-09-24 修 CPU 满载下的门禁 flake）：慢节点的 ping **阻塞等到快节点
    真的派了第一单**再返回 —— 「快节点不等慢节点就绪」从「机器够快」变成构造性事实，
    断言也就不用比墙钟。旧形态（阶段串行）下这个等待永远等不到 ⇒ 兜底 15s 后
    `slow_gate_ok=False` ⇒ 响亮地红。
    """
    import threading
    import time

    import dist_common

    t: dict[str, float] = {}
    fast_dispatched = threading.Event()

    def fake_ping(url, key, **kw):
        if "slow" in url:
            # 兜底时间只是挂起护栏，不参与判定（判定看 slow_gate_ok）
            t["slow_gate_ok"] = fast_dispatched.wait(15.0)
            t["slow_ping_done"] = time.monotonic()
        return _ok_ping()

    def fake_post(url, *a, **k):
        if "slow" in url:
            t["slow_weights_done"] = time.monotonic()
        return "kept"

    def fake_fetch(url, key, **kw):
        t.setdefault("first_fetch", time.monotonic())
        if "slow" not in url:
            fast_dispatched.set()
        return _ok_manifest(int(kw["stage"]), int(kw["seed"]), kw["wver"]), {}

    monkeypatch.setattr(dist_common, "node_ping", fake_ping)
    monkeypatch.setattr(dist_common, "post_weights", fake_post)
    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    nodes = [
        {"id": "fast", "url": "http://fast", "authKey": "", "enabled": True, "concurrency": 1},
        {"id": "slow", "url": "http://slow", "authKey": "", "enabled": True, "concurrency": 2},
    ]
    r, logs, _ = _channels_runner(tmp_path, monkeypatch, nodes=nodes)
    out = r.run()
    assert out["settled"] == out["total"] == 100
    # 次序已由构造保证（慢节点的 ping 只可能在快节点派完第一单之后才返回），
    # 所以这里不再比两个只差几微秒的 `monotonic()` 戳（Windows 粒度 ~15.6ms，比大小是掷硬币）。
    assert t.get("slow_gate_ok"), "快节点没能在慢节点就绪前派单 = 阶段串行回归"
    assert "slow_ping_done" in t and "slow_weights_done" in t
    assert any("node slow 就绪" in m for m in logs), "慢节点就绪后也必须投入"


def test_settle_complete_closes_inflight_connections(tmp_path: Path, monkeypatch) -> None:
    """req 4：settled 一满 ⇒ 立即关闭在飞连接收工（慢节点那局不再等）。"""
    import threading
    import time

    import dist_common

    release = threading.Event()
    aborted = {"n": 0}

    def fake_abort(tag=""):
        aborted["n"] += 1
        release.set()  # 模拟 close() 让阻塞的 read 立刻抛
        return 7

    def fake_fetch(url, key, **kw):
        if "slow" in url:
            release.wait(30.0)
            # 连接被关闭 → 与真实现同类的瞬断异常（回包已无用 ⇒ 必须按「无关」丢弃）
            raise dist_common.DistError(0, "connection reset by abort", transient=True)
        return _ok_manifest(int(kw["stage"]), int(kw["seed"]), kw["wver"]), {}

    monkeypatch.setattr(dist_common, "node_ping", lambda *a, **k: _ok_ping())
    monkeypatch.setattr(dist_common, "post_weights", lambda *a, **k: "kept")
    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    monkeypatch.setattr(dist_common, "abort_active_requests", fake_abort)
    nodes = [
        {"id": "fast", "url": "http://fast", "authKey": "", "enabled": True, "concurrency": 2},
        {"id": "slow", "url": "http://slow", "authKey": "", "enabled": True, "concurrency": 2},
    ]
    r, logs, _ = _channels_runner(tmp_path, monkeypatch, nodes=nodes)
    t0 = time.monotonic()
    out = r.run()
    dt = time.monotonic() - t0
    assert out["settled"] == out["total"] == 100 and out["dropped"] == 0, out
    assert aborted["n"] >= 1, "settled 满必须触发断连"
    assert dt < 5.0, f"settled 满后未立即收工（慢节点在飞局拖着）：{dt:.1f}s"
    assert any("在飞连接" in m and "立即收工" in m for m in logs), logs
    # 被主动断开的回包不得被当成节点故障（否则会误熔断慢节点）：无失败计数行，
    # 更不得出现 failed/requeued。
    hard = [m for m in logs if "真失败计数" in m]
    assert not hard or hard[0].split("真失败计数")[1].strip() == "—", hard
    assert not any("failed (" in m for m in logs), [m for m in logs if "failed (" in m]


def test_unit_pairs_route_per_stage_params(tmp_path: Path, monkeypatch) -> None:
    """req 2 接线：单单元跨多关时，逐局必须拿到**本关**的 stageJson/lives/level/maxTicks。"""
    import dist_common

    got: dict[int, dict] = {}

    def fake_fetch(url, key, **kw):
        got[int(kw["stage"])] = {
            "json": kw["stage_json"],
            "lives": kw["lives_override"],
            "level": kw["player_level"],
            "max": kw["max_ticks"],
        }
        return _ok_manifest(int(kw["stage"]), int(kw["seed"]), kw["wver"]), {}

    monkeypatch.setattr(dist_common, "node_ping", lambda *a, **k: _ok_ping())
    monkeypatch.setattr(dist_common, "post_weights", lambda *a, **k: "kept")
    monkeypatch.setattr(dist_common, "fetch_task", fake_fetch)
    unit = {
        "rung": "multi",
        "stageId": 2000,
        "seeds": [],
        "pairs": [[2000, 5], [2001, 6]],
        "stageParams": {
            "2000": {
                "stageJson": '{"name":"a"}',
                "maxTicks": 100,
                "difficulty": "hard",
                "lives": 1,
                "level": 0,
            },
            "2001": {
                "stageJson": '{"name":"b"}',
                "maxTicks": 200,
                "difficulty": "hard",
                "lives": 3,
                "level": 2,
            },
        },
        # unit 级值故意全设为「绝不该被用到」的哨兵值
        "maxTicks": 999,
        "difficulty": "hard",
        "stageJson": "{}",
        "lives": 9,
        "level": 9,
    }
    nodes = [{"id": "n1", "url": "http://n1", "authKey": "", "enabled": True, "concurrency": 2}]
    r, logs, _ = _channels_runner(tmp_path, monkeypatch, nodes=nodes, unit=unit)
    out = r.run()
    assert out["total"] == 2 and out["settled"] == 2 and out["dropped"] == 0, out
    assert got[2000] == {"json": '{"name":"a"}', "lives": 1, "level": 0, "max": 100}, got
    assert got[2001] == {"json": '{"name":"b"}', "lives": 3, "level": 2, "max": 200}, got
