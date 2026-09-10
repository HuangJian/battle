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
    assert claim_pending(tmp_path) is None
    mark_unit_done(tmp_path, "b1", 0, {"self": 100})
    mark_unit_done(tmp_path, "b1", 1, {"self": 100})
    (final,) = read_batches(tmp_path)
    assert final["status"] == "done"
    assert sorted(final["units"]["done"]) == [0, 1]


def test_hooks_wired_both_modes() -> None:
    """P2 DoD：`if not eval_on_round` 分支生效（grep 断言，一轮内 A/B 不共存）。"""
    src = (ROOT / "rl" / "rollout_phase.py").read_text(encoding="utf-8")
    assert src.count("maybe_dispatch_batch") >= 2  # serial + stream 各一处
    assert "if not eval_on_round" in src
    ed = (ROOT / "rl" / "eval_dispatch.py").read_text(encoding="utf-8")
    assert "check_engine_epoch" in ed
    import dist_common

    assert hasattr(dist_common, "compute_engine_epoch")
    assert hasattr(dist_common, "check_engine_epoch")


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
        "engineEpoch": epoch,
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
