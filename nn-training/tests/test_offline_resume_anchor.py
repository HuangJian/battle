"""tests/test_offline_resume_anchor.py —— 离线断点续跑：hub 侧的锚点选轮 + 读数回程。

用户指令（2026-09-22）两半，本文件各钉一半：

  * 「云机回传**或者**人工导入 权重/opt/指标 后，云端 worker 再来领离线任务，应传递这些内容
    给云机，让云机继续执行」⇒ `/offline/resume` 必须从**两个来源**（自回传产物目录 + 控制台
    导入的 deliver 目录）里选出可交回的那一轮；
  * 「必须同轮齐全，否则退到更早轮」⇒ 三件（weights/opt/指标）缺一就**不能**当锚点，
    宁可退到更早那一轮——缺 opt 是 Adam 动量归零，缺指标行是曲线少一个点，两者都
    「看起来能跑」所以在选轮那一步就得拦住。

另一半（云机如何采纳、以及「与下一轮 PPO 并行」）在 `tests/test_offline_eval_cloud.py`。
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    AUTH_HEADER,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_RESUME_BLOB_PATH,
    OFFLINE_RESUME_PATH,
    encode_weights_json,
)
from remote.hub_server import _HubQueue, _JobStore, make_server

TOKEN = "sekret"
COURSE = "c5-gae"


def _boot(tmp_path: Path) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    store = _JobStore(tmp_path / COURSE / "remote-jobs", tmp_path / COURSE / "training_log.jsonl")
    hub = _HubQueue({COURSE: store}, order=[COURSE])
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _get(base: str, path: str, *, token: str | None = TOKEN) -> tuple[int, bytes]:
    headers = {} if token is None else {AUTH_HEADER: f"Bearer {token}"}
    req = urllib.request.Request(base + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(base: str, path: str, body: dict) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={AUTH_HEADER: f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _round(dir_: Path, it: int, *, parts: tuple[str, ...] = ("weights.json", "opt.tar", "row.json")) -> Path:
    """造一轮产物（三件齐全；`parts` 用来故意缺件）。"""
    d = dir_ / f"it-{it:03d}"
    d.mkdir(parents=True, exist_ok=True)
    blob = f'{{"w":{it}}}'.encode()
    if "weights.json" in parts:
        (d / "weights.json").write_bytes(blob)
    if "opt.tar" in parts:
        (d / "opt.tar").write_bytes(b"opt")
    if "row.json" in parts:
        import hashlib

        (d / "row.json").write_text(
            json.dumps(
                {
                    "it": it,
                    "weights_fp": hashlib.sha256(blob).hexdigest(),
                    "opt_bytes": 3,
                    "agg": {"kl": 0.01},
                }
            ),
            encoding="utf-8",
        )
    return d


# ─────────────────────────── 选轮（纯逻辑） ───────────────────────────


def test_resume_anchor_picks_the_newest_fully_complete_round(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        offline = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a"
        _round(offline, 1)
        _round(offline, 2)
        _round(offline, 3, parts=("weights.json", "row.json"))  # 缺 opt ⇒ 不齐
        anchor = hub.resume_anchor(COURSE)
        assert anchor is not None
        assert anchor["it"] == 2, "最新一轮不齐 ⇒ 退到更早的完整轮（用户口径）"
        assert anchor["source"] == "backfeed" and anchor["run_id"] == "run-a"
        assert anchor["weights_fp"] and anchor["opt_bytes"] == 3
        assert anchor["metrics"]["agg"] == {"kl": 0.01}
    finally:
        srv.shutdown()


def test_resume_anchor_sees_manual_imports_and_prefers_the_newest_source(tmp_path: Path) -> None:
    """人工导入（`<traj>/<课>/deliver/<run>/it-NNN/`）与自回传同权：新进度优先。"""
    base, hub, srv = _boot(tmp_path)
    try:
        _round(tmp_path / COURSE / "remote-jobs" / "offline" / "run-a", 2)
        assert hub.resume_anchor(COURSE)["it"] == 2  # type: ignore[index]
        _round(tmp_path / COURSE / "deliver" / "deliver-c5-gae", 5)
        got = hub.resume_anchor(COURSE)
        assert got is not None and got["it"] == 5 and got["source"] == "import"
        # 同一 it 两个来源：取目录 mtime 更新的那份（人刚导完的更可信）
        import os
        import time

        a = _round(tmp_path / COURSE / "remote-jobs" / "offline" / "run-b", 5)
        os.utime(a, (time.time() + 10, time.time() + 10))
        again = hub.resume_anchor(COURSE)
        assert again is not None and again["source"] == "backfeed"
    finally:
        srv.shutdown()


def test_resume_anchor_is_none_without_a_complete_round(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        assert hub.resume_anchor(COURSE) is None
        _round(tmp_path / COURSE / "remote-jobs" / "offline" / "run-a", 4, parts=("weights.json",))
        assert hub.resume_anchor(COURSE) is None
    finally:
        srv.shutdown()


# ─────────────────────────── 端点 ───────────────────────────


def test_resume_endpoints_serve_meta_then_the_whitelisted_blobs(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        _round(tmp_path / COURSE / "remote-jobs" / "offline" / "run-a", 3)
        status, raw = _get(base, f"{OFFLINE_RESUME_PATH}?course={COURSE}")
        assert status == 200
        meta = json.loads(raw.decode("utf-8"))
        assert meta["resume"]["it"] == 3 and meta["resume"]["source"] == "backfeed"
        assert "_dir" not in meta["resume"], "内部路径不进应答"

        for name in ("weights.json", "opt.tar", "row.json"):
            st, body = _get(
                base, f"{OFFLINE_RESUME_BLOB_PATH}?course={COURSE}&it=3&name={name}"
            )
            assert st == 200 and body, (name, body[:120])
        st_w, body_w = _get(
            base, f"{OFFLINE_RESUME_BLOB_PATH}?course={COURSE}&it=3&name=weights.json"
        )
        assert st_w == 200 and body_w == b'{"w":3}'

        # 白名单：不许借名读别的文件（路径穿越在这里被断掉）
        st_bad, _ = _get(base, f"{OFFLINE_RESUME_BLOB_PATH}?course={COURSE}&it=3&name=../../etc/passwd")
        assert st_bad == 400
        # 不是当前锚点的 it ⇒ 409（响亮：锚点可能已被更新的轮次取代）
        st_old, body_old = _get(base, f"{OFFLINE_RESUME_BLOB_PATH}?course={COURSE}&it=2&name=weights.json")
        assert st_old == 409 and b"anchor_it" in body_old
        # 未知课程 ⇒ 404；未鉴权 ⇒ 401
        assert _get(base, f"{OFFLINE_RESUME_PATH}?course=nope")[0] == 404
        assert _get(base, f"{OFFLINE_RESUME_PATH}?course={COURSE}", token=None)[0] == 401
    finally:
        srv.shutdown()


def test_resume_endpoint_says_null_when_there_is_nothing_to_hand_back(tmp_path: Path) -> None:
    """`resume: null` 是**正常应答**（云机要能区分「hub 说没有」与「端点不可用」）。"""
    base, hub, srv = _boot(tmp_path)
    try:
        status, raw = _get(base, f"{OFFLINE_RESUME_PATH}?course={COURSE}")
        assert status == 200 and json.loads(raw.decode("utf-8"))["resume"] is None
    finally:
        srv.shutdown()


# ─────────────────────────── 补传的读数回程 ───────────────────────────


def test_backfeed_eval_rows_land_in_the_course_ledger(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        wj = b'{"w":1}'
        rows = [
            {
                "event": "eval",
                "iter": 2,
                "wver": "a" * 16,
                "stage": 0,
                "seed": 860001,
                "node": "cloud",
                "win": 1,
            },
            {"event": "eval_summary", "iter": 2, "wver": "a" * 16},  # 不该进逐局账本
        ]
        body = {
            "run_id": "run-a",
            "it": 2,
            "weights_fp": __import__("hashlib").sha256(wj).hexdigest(),
            "weights_json": encode_weights_json(wj),
            "row": {"it": 2, "weights_fp": __import__("hashlib").sha256(wj).hexdigest()},
            "eval_rows": rows,
            "course": COURSE,
        }
        status, raw = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert status == 200, raw[:200]
        ledger = tmp_path / COURSE / "eval_log.jsonl"
        got = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines()]
        assert len(got) == 1 and got[0]["seed"] == 860001 and got[0]["node"] == "cloud"

        # 幂等重投（补传天然会重传）：同一行不再写第二次
        status2, _ = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert status2 in (200, 409)
        got2 = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines()]
        assert len(got2) == 1
    finally:
        srv.shutdown()


def test_course_specific_ledger_helper_is_idempotent(tmp_path: Path) -> None:
    base, hub, srv = _boot(tmp_path)
    try:
        row = {"event": "eval", "iter": 1, "wver": "b" * 16, "stage": 3, "seed": 7}
        assert hub.merge_eval_rows(COURSE, [row, "junk", {"event": "eval_summary"}]) == 1
        assert hub.merge_eval_rows(COURSE, [row]) == 0
        assert hub.merge_eval_rows(COURSE, None) == 0
        ledger = tmp_path / COURSE / "eval_log.jsonl"
        assert json.loads(ledger.read_text(encoding="utf-8").strip())["seed"] == 7
    finally:
        srv.shutdown()
