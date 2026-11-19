"""test_node_speed — 派工顺序的速度表（P2，plan/node-throughput-and-keepalive D3）。

四条钉子：① K 局窗口截断 + 坏值不入均值；② meta 只认 `mode=rollout ∧ ok=true`，坏行不炸；
③ 冷启动（样本 < 5）用全体中位数，全表空 ⇒ 保持配置序（stable）；④ 派发侧真的打观测行。

生效面已知：worker 从同一个 `pending` 队列抢活 ⇒ 排序只影响开轮第一波；`need` 节点在
权重 POST 完成回调里孵化，那部分尽力而为。验收只要求「不劣化 + 观测可见」。
"""

from __future__ import annotations

import json
from pathlib import Path

from common import node_speed
from tests.trainer.test_rollout_dispatch_resilience import GOOD_PING, _Harness, _node


def test_window_truncates_and_ignores_bad_values() -> None:
    w = node_speed.SpeedWindow(k=3)
    for v in (10, 20, 30, 40, 50):
        w.add(v)
    assert w.n() == 3
    assert w.avg() == 40.0  # (30+40+50)/3：只留最近 K 局
    w.add("abc")
    w.add(-1)
    w.add(None)
    w.add(0)
    assert w.n() == 3 and w.avg() == 40.0  # 坏值/非正值不污染均值


def test_parse_meta_rows_filters_and_tolerates_bad_lines() -> None:
    lines = [
        json.dumps({"node": "gcs", "mode": "rollout", "ok": True, "wallSec": 9.0}),
        json.dumps({"node": "gcs", "mode": "rollout", "ok": False, "wallSec": 99.0}),
        json.dumps({"node": "gcs", "mode": "eval", "ok": True, "wallSec": 4.0}),
        json.dumps({"node": "a97", "mode": "rollout", "ok": True, "wallSec": "12.5"}),
        json.dumps({"node": "a97", "mode": "rollout", "ok": True}),  # 缺 wallSec
        "{broken json",
        "",
        "[1, 2]",  # 不是 dict
    ]
    assert node_speed.parse_meta_rows(lines) == {"gcs": [9.0], "a97": [12.5]}


def test_seed_from_meta_reads_tail_and_missing_is_empty(tmp_path: Path) -> None:
    p = tmp_path / "dist-agent-meta.jsonl"
    rows = [("gcs", 5), ("gcs", 6), ("gcs", 7), ("a97", 30)]
    p.write_text(
        "\n".join(
            json.dumps({"node": n, "mode": "rollout", "ok": True, "wallSec": v})
            for n, v in rows
        ),
        encoding="utf-8",
    )
    w = node_speed.seed_from_meta(p, k=2)
    assert w["gcs"].n() == 2
    assert w["gcs"].avg() == 6.5  # 只留尾部 2 条
    assert w["a97"].avg() == 30.0
    # 文件不存在/读不动 ⇒ 空表（排序退回配置序），绝不抛
    assert node_speed.seed_from_meta(tmp_path / "nope.jsonl") == {}


def test_order_nodes_fast_first_and_cold_start_median() -> None:
    ws = {"fast": node_speed.SpeedWindow(), "slow": node_speed.SpeedWindow()}
    for _ in range(5):
        ws["fast"].add(5)
        ws["slow"].add(20)
    assert node_speed.order_nodes(["slow", "fast"], ws) == ["fast", "slow"]
    # 冷启动（n<5）用全体中位数 12.5 ⇒ 排在两者之间；并列时保持配置序（stable）
    assert node_speed.order_nodes(["slow", "cold", "fast"], ws) == ["fast", "cold", "slow"]


def test_order_nodes_all_unknown_keeps_config_order() -> None:
    assert node_speed.order_nodes(["b", "a"], {}) == ["b", "a"]


def test_wall_note_shapes() -> None:
    ws = {"gcs": node_speed.SpeedWindow(k=50)}
    assert node_speed.wall_note(ws, "gcs") == "wall=无样本 n=0"
    ws["gcs"].add(14.9)
    ws["gcs"].add(15.1)
    # n<5 时如实标注（否则读者以为均值可靠）；整串相等，不挂措辞子串
    assert node_speed.wall_note(ws, "gcs") == "wall=15.0s/局 n=2（冷启动，取中位数）"


def test_dispatch_logs_order_line(tmp_path, monkeypatch) -> None:
    """派发侧真的走这张表（观测行可 grep；槽位不变）。"""
    h = _Harness(
        tmp_path,
        monkeypatch,
        2,
        nodes=[_node("a", 1), _node("b", 1)],
        ping_fn=lambda *a, **k: dict(GOOD_PING),
        policy={"nodeRecoverFirstSec": 0, "recoverPingSec": 0},
    )

    def fetch(*_a, **_k):
        return h.manifest(), {}

    h.run(fetch)
    assert any(line.startswith("[dist] node order:") for line in h.logs), h.logs[-8:]
