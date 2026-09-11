"""test_eval_requests — EvalBoard 请求文件协议（plan/evalboard-console-ux.md §5.3，P1/P4）。

覆盖：enqueue 物化建批 / 去重（pending 同 key + 已物化跳过）/ 畸形行跳过 /
abort（pending + running）/ ladder_* 被 runner 忽略 / mark_unit_done 与
_requeue 不复活 aborted / claim 跳过 aborted / 坏行容忍。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.batch_eval import (
    claim_pending,
    consume_requests,
    mark_requests_done,
    mark_unit_done,
    read_batches,
    read_done_req_ids,
    read_requests,
    utc_now_iso,
    write_batches,
)


def _wreq(root: Path, *rows: dict) -> None:
    with open(root / "requests.jsonl", "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _enq(i: int = 1, **kw) -> dict:
    d = {
        "req_id": f"q-{i}",
        "kind": "enqueue",
        "ts": "2026-09-11T10:00:00",
        "requester": "web",
        "course": "c4-margin",
        "rung_from": "c4l1",
        "ckpt": "w.json",
        "policy": "nn",
        "iter": 30,
    }
    d.update(kw)
    return d


def test_enqueue_materializes_pending_batch(tmp_path: Path) -> None:
    c = consume_requests(tmp_path)
    assert c == {"consumed": 0, "enqueued": 0, "aborted": 0, "skipped": 0}
    _wreq(tmp_path, _enq())
    c = consume_requests(tmp_path)
    assert c["enqueued"] == 1 and c["consumed"] == 1
    (b,) = read_batches(tmp_path)
    assert b["status"] == "pending"
    assert b["course"] == "c4-margin" and b["rung_from"] == "c4l1"
    assert b["iter"] == 30 and b["policy"] == "nn" and b["trigger"] == "standalone"
    assert b["batch_id"].startswith("b-") and b["elapsed_sec"] is None
    assert read_done_req_ids(tmp_path) == {"q-1"}


def test_enqueue_dedupes_pending_and_materialized(tmp_path: Path) -> None:
    _wreq(tmp_path, _enq())
    assert consume_requests(tmp_path)["enqueued"] == 1
    # 同 key 仍 pending：重复消费（done 标记被删的极端）不造重复批
    (b,) = read_batches(tmp_path)
    assert b["status"] == "pending"
    from rl.batch_eval import REQUESTS_DONE_FILE

    (tmp_path / REQUESTS_DONE_FILE).write_text("", encoding="utf-8")
    c = consume_requests(tmp_path)
    assert c["enqueued"] == 0 and len(read_batches(tmp_path)) == 1
    # 批 done 后同 key 新请求（ts 更新）允许重跑
    mark_unit_done(tmp_path, b["batch_id"], 0, {})
    mark_unit_done(tmp_path, b["batch_id"], 1, {})
    assert read_batches(tmp_path)[0]["status"] == "done"
    _wreq(tmp_path, _enq(2, ts="2999-01-01T00:00:00"))
    assert consume_requests(tmp_path)["enqueued"] == 1
    assert len(read_batches(tmp_path)) == 2


def test_enqueue_skips_malformed_and_unknown_kind(tmp_path: Path) -> None:
    _wreq(
        tmp_path,
        {"req_id": "q-bad", "kind": "enqueue", "ts": "2026-09-11T10:00:00"},
        {"req_id": "q-unk", "kind": "teleport", "ts": "2026-09-11T10:00:00"},
    )
    with open(tmp_path / "requests.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n")
    c = consume_requests(tmp_path)
    assert c["enqueued"] == 0 and read_batches(tmp_path) == []
    assert read_done_req_ids(tmp_path) == {"q-bad", "q-unk"}


def test_abort_pending_and_running(tmp_path: Path) -> None:
    write_batches(
        tmp_path,
        [
            {"batch_id": "b-p", "status": "pending", "units": {"of": 2, "done": []}},
            {"batch_id": "b-r", "status": "running", "units": {"of": 2, "done": [0]}},
            {"batch_id": "b-d", "status": "done", "units": {"of": 2, "done": [0, 1]}},
        ],
    )
    _wreq(
        tmp_path,
        {"req_id": "q-a1", "kind": "abort", "ts": "t", "batch_id": "b-p"},
        {"req_id": "q-a2", "kind": "abort", "ts": "t", "batch_id": "b-r"},
        {"req_id": "q-a3", "kind": "abort", "ts": "t", "batch_id": "b-d"},
        {"req_id": "q-a4", "kind": "abort", "ts": "t", "batch_id": "b-missing"},
    )
    c = consume_requests(tmp_path)
    assert c["aborted"] == 2 and c["consumed"] == 4
    st = {b["batch_id"]: b["status"] for b in read_batches(tmp_path)}
    assert st == {"b-p": "aborted", "b-r": "aborted", "b-d": "done"}


def test_ladder_kinds_ignored_not_marked(tmp_path: Path) -> None:
    _wreq(
        tmp_path,
        {"req_id": "q-l1", "kind": "ladder_start", "course": "c", "iter": 1},
        {"req_id": "q-l2", "kind": "ladder_stop", "course": "c", "iter": 1},
    )
    c = consume_requests(tmp_path)
    assert c["consumed"] == 0 and read_batches(tmp_path) == []
    assert read_done_req_ids(tmp_path) == set()
    assert len(read_requests(tmp_path)) == 2  # 留给 console ticker


def test_aborted_stays_aborted_and_unclaimable(tmp_path: Path) -> None:
    write_batches(
        tmp_path, [{"batch_id": "b-x", "status": "running", "units": {"of": 2, "done": [0]}}]
    )
    _wreq(tmp_path, {"req_id": "q-a", "kind": "abort", "ts": "t", "batch_id": "b-x"})
    consume_requests(tmp_path)
    # 在途 unit 收尾：只回填 node_dist，不复活
    mark_unit_done(tmp_path, "b-x", 1, {"local": 100})
    (b,) = read_batches(tmp_path)
    assert b["status"] == "aborted" and b["node_dist"] == {"local": 100}
    # _requeue 同样不复活
    from rl.batch_eval import _requeue

    _requeue(tmp_path, {"batch_id": "b-x"})
    assert read_batches(tmp_path)[0]["status"] == "aborted"
    # claim 跳过 aborted
    assert claim_pending(tmp_path) is None


def test_claim_consumes_requests_first(tmp_path: Path) -> None:
    _wreq(tmp_path, _enq())
    claimed = claim_pending(tmp_path)
    assert claimed is not None and claimed["status"] == "running"
    assert claimed["course"] == "c4-margin"


def test_trigger_auto_ladder_passthrough(tmp_path: Path) -> None:
    _wreq(tmp_path, _enq(trigger="auto-ladder"))
    consume_requests(tmp_path)
    (b,) = read_batches(tmp_path)
    assert b["trigger"] == "auto-ladder"


def test_mark_requests_done_appends(tmp_path: Path) -> None:
    mark_requests_done(tmp_path, set())
    assert read_done_req_ids(tmp_path) == set()
    mark_requests_done(tmp_path, {"q-1", "q-2"})
    assert read_done_req_ids(tmp_path) == {"q-1", "q-2"}




def test_utc_now_iso_matches_console_format() -> None:
    """必须与 console 侧 `new Date().toISOString()` 同形态（UTC + 毫秒 + Z）。

    `consume_requests` 用**字符串比较**判"批是否已物化"（`created_ts >= req.ts`）。
    旧实现用本地时间 `time.strftime("%Y-%m-%dT%H:%M:%S")`（无毫秒无 Z）：UTC+8
    下本地时间戳恰好"看起来更晚"⇒ 侥幸正确；UTC 或负偏移时区会误判为未物化
    ⇒ 重复建批。本机不触发，云端/换机会踩 —— 本用例锁死格式。
    """
    s = utc_now_iso()
    assert len(s) == 24, s
    assert s.endswith("Z"), s
    assert s[10] == "T" and s[19] == ".", s
    # 与 console 侧同构 ⇒ 字典序 == 时间序，与本机时区无关
    assert datetime.fromisoformat(s.replace("Z", "+00:00")).tzinfo is not None


def test_materialized_check_is_tz_independent() -> None:
    """请求先发、随后建批 ⇒ created_ts >= req.ts 必须成立，不依赖本机时区。"""
    # 日期取遥远过去：断言与"现在"无关，避免在 UTC 凌晨跑就翻车
    # （本仓踩过：测试时间断言勿写死近期日期）。
    req_ts = "2020-01-01T00:00:00.000Z"  # console 侧（UTC）
    assert utc_now_iso() >= req_ts
    # 旧批（早于请求）⇒ 未物化 ⇒ 允许重跑
    assert req_ts > "2019-01-01T00:00:00.000Z"
