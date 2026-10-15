"""tests/hub/test_offline_backfeed.py —— 撤销后的回传处置（★P1-7 / R3-f，2026-10-05）。

两个洞（plan/offline-online-status-switch §5 P1-7）：

  ① **切在线后旧会话的回传不得推进活动权重**：`pinned_online` = 人固定在线（§4.2 半回摆），
     而离线盘的旧会话还在跑；它回传的轮不是「课程现在的进度」——把 `weights.json` 拉回旧轮
     等于人切了在线、起点却倒退（报障一的另一半）。镜像/归档照落（算过什么的证据）。
  ② **段末自报要对得上当前包才盖章**：`end_it_reached` 是唯一让当前包变 completed 的判据
     （U6）；旧包/旧会话的自报若把**新包**封住，重导之后依旧不可领（U6 反着生效）。

判据全在 hub 侧（`store_offline.store_offline_artifact` / `hub/offline._end_seal_ok`）；
本文件走真 HTTP（进程内 hub），落盘事实与读面各断言一次。
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import (
    AUTH_HEADER,
    COURSE_ENABLE_MARKER,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_RESULT_PATH,
    encode_opt_tar,
    encode_weights_json,
)
from hub.server import _HubQueue, _JobStore, make_server

TOKEN = "sekret"
COURSE = "c5-gae"


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """权重归档根指到 tmp（理由同 `test_offline_resume_anchor`：别往真 `nn-training/weights/` 撒）。"""
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


def _boot(tmp_path: Path, now_fn=None) -> tuple[str, _HubQueue, ThreadingHTTPServer]:
    d = tmp_path / COURSE
    (d / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    store = _JobStore(d / "remote-jobs", d / "training_log.jsonl", now_fn=now_fn)
    hub = _HubQueue({COURSE: store}, order=[COURSE], now_fn=now_fn)
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", hub, srv


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={AUTH_HEADER: f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        status = e.code
    else:
        status = 200
    try:
        loaded = json.loads(raw.decode("utf-8"))
    except ValueError:
        loaded = {}
    return status, loaded if isinstance(loaded, dict) else {}


def _round_body(it: int, weights: bytes, *, run_id: str, opt: bytes = b"") -> dict:
    """一轮补传体的最小形状（weights + 逐轮行；指纹两侧一致，先过入口校验）。"""
    fp = hashlib.sha256(weights).hexdigest()
    body: dict = {
        "course": COURSE,
        "run_id": run_id,
        "it": it,
        "weights_json": encode_weights_json(weights),
        "weights_fp": fp,
        "row": {"it": it, "weights_fp": fp},
    }
    if opt:
        body["opt_tar_b64"] = encode_opt_tar(opt)
    return body


def _weights_json(params: dict[str, list[float]]) -> bytes:
    """一份真形状的 `nn-weights-json`（params = `{名字: f32 列表}`）。"""
    out: dict[str, dict[str, object]] = {}
    for name, vals in params.items():
        out[name] = {
            "shape": [len(vals)],
            "data": base64.b64encode(struct.pack(f"<{len(vals)}f", *vals)).decode("ascii"),
        }
    return json.dumps({"format": "nn-weights-json", "params": out}).encode("utf-8")


def test_backfeed_lands_delta_containers_into_full_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 2026-10-15（用户口径）：hub 收到 delta **就地重建为整份**（hub 树里再没有容器）。

    口径原话：*只有 hub 和回传进程知道有 delta 这玩意儿* —— 云机按形态位回传压缩版，hub 受理
    时当场落地（`hub/deltas.py`），此后回传树/交付镜像/活动指针/归档全是整份，派发侧永远只发整份
    （云机已有就不传 = 既有指纹 + blob_cache）。

    覆盖三条：① 落地成功（回传树 = `weights.json` 整份 + `weights.delta` 原件、活动指针与镜像
    也是整份、应答里报 `landed`）；② **基准不在 hub 侧** ⇒ 原件保留 + `failed`（不静默、不丢字节）；
    ③ 那种轮次不能当锚点交回云机（`resume_anchor` 跳过它）。
    """
    from remote.weights_delta import pack_weights_delta

    base = _weights_json({"stem.weight": [0.1, 0.2, 0.3]})
    new = _weights_json({"stem.weight": [0.5, -0.2, 0.9]})
    container = pack_weights_delta(base, new, base_sha=hashlib.sha256(base).hexdigest())
    assert container[:4] == b"BWD1"

    base_url, hub, srv = _boot(tmp_path)
    try:
        # 基准 = 上一轮的整份（hub 自己发出去/刚落地的那份）：按产物链的形状摆在 it-001
        prev = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-001"
        prev.mkdir(parents=True, exist_ok=True)
        (prev / "weights.json").write_bytes(base)
        st, res = _post(
            base_url,
            OFFLINE_ARTIFACT_PATH,
            _round_body(2, container, run_id="run-a", opt=b"opt"),
        )
        assert st == 200 and res.get("status") == "accepted", (st, res)
        assert res.get("landed", {}).get("weights") == "landed", res

        it2 = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-002"
        full = (it2 / "weights.json").read_bytes()
        assert json.loads(full.decode("utf-8"))["params"]["stem.weight"]["shape"] == [3]
        assert (it2 / "weights.delta").read_bytes() == container, "原件两版都留（名字自述形态）"
        # 活动指针与交付镜像也是**整份**（它们是给本机循环/控制台/TS 读的）
        ptr = tmp_path / COURSE / "weights.json"
        assert ptr.is_file() and ptr.read_bytes() == full
        mirror = tmp_path / COURSE / "deliver" / "run-a" / "it-002" / "weights.json"
        assert mirror.read_bytes() == full
        # 账本行分开记：`weights_fp` = 回传原件，`landed_fp` = hub 手上那份整份
        rows = [
            json.loads(x)
            for x in (tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "metrics.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if x.strip()
        ]
        row = [r for r in rows if r.get("it") == 2][-1]
        assert row["weights_fp"] == hashlib.sha256(container).hexdigest()
        assert row["landed_fp"] == hashlib.sha256(full).hexdigest()
        # 锚点交回云机的就是那份整份
        anchor = hub.resume_anchor(COURSE)
        assert anchor is not None and anchor["it"] == 2 and anchor["files"]["weights"] == "weights.json"

        # ② 基准不在 hub 侧（没有 it-003 能靠的上一轮）⇒ failed + 原件保留
        nowhere = _weights_json({"stem.weight": [9.0, 9.0, 9.0]})
        orphan = pack_weights_delta(nowhere, new, base_sha=hashlib.sha256(nowhere).hexdigest())
        st2, res2 = _post(
            base_url,
            OFFLINE_ARTIFACT_PATH,
            _round_body(9, orphan, run_id="run-a", opt=b"opt"),
        )
        assert st2 == 200 and res2.get("status") == "accepted", (st2, res2)
        assert res2.get("landed", {}).get("weights") == "failed", res2
        it9 = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-009"
        assert (it9 / "weights.delta").read_bytes() == orphan
        assert not (it9 / "weights.json").is_file()
        # ③ 还是容器的轮次不能当锚点（云机没有链可解）
        assert hub.resume_anchor(COURSE)["it"] == 2  # type: ignore[index]
    finally:
        srv.shutdown()


def _container_pair() -> tuple[bytes, bytes]:
    """`(基准整份, delta 容器)` —— 闭环用的一对（容器头指向那份基准）。"""
    from remote.weights_delta import pack_weights_delta

    base = _weights_json({"stem.weight": [0.1, 0.2, 0.3]})
    new = _weights_json({"stem.weight": [0.5, -0.2, 0.9]})
    return base, pack_weights_delta(base, new, base_sha=hashlib.sha256(base).hexdigest())


def test_repeat_after_a_failed_landing_heals_the_round(tmp_path: Path) -> None:
    """落地失败过的那一轮，重传时必须**补落整份**（duplicate + 自愈），而不是干说一句幂等。

    形状：第一次回传时基准还不在 hub 侧 ⇒ 只落 `weights.delta`（原件保留，锚点跳过它，见上一格）。
    基准随后到位（换机续传/前一轮晚到）再传同一轮 ⇒ 若只回 duplicate 不落整份，那一轮**永远**
    当不了锚点（`resume_anchor` 会一直跳过），只能人工跑清扫工具。
    """
    base, container = _container_pair()
    base_url, hub, srv = _boot(tmp_path)
    try:
        body = _round_body(2, container, run_id="run-a", opt=b"opt")
        st, res = _post(base_url, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and res.get("landed", {}).get("weights") == "failed", (st, res)
        it2 = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-002"
        assert not (it2 / "weights.json").is_file()

        prev = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-001"
        prev.mkdir(parents=True, exist_ok=True)
        (prev / "weights.json").write_bytes(base)  # 基准晚到

        st2, res2 = _post(base_url, OFFLINE_ARTIFACT_PATH, body)
        assert st2 == 200 and res2.get("status") == "duplicate", (st2, res2)
        assert res2.get("landed", {}).get("weights") == "landed", res2
        full = (it2 / "weights.json").read_bytes()
        assert json.loads(full.decode("utf-8"))["params"]["stem.weight"]["shape"] == [3]
        assert (it2 / "weights.delta").read_bytes() == container
        assert hub.resume_anchor(COURSE)["it"] == 2  # type: ignore[index]
    finally:
        srv.shutdown()


def test_legacy_container_round_repeat_is_duplicate_not_conflict(tmp_path: Path) -> None:
    """旧数据（`weights.json` 里装着容器）同一份字节重传 ⇒ **duplicate**（顺手补落整份），不是 409。

    旧 hub 把容器原样写进 `weights.json`（名字没跟着内容走），而那正是现场 `tmp/y5-term` 的形状。
    丢包/超时重传=常态（`store_offline_artifact` 自己的注释就是这么写的），客户端又把 409 当硬拒
    （`remote/offline_deliver._reject_round`）⇒ 「字节完全相同」被判成「不同字节、需人裁决」会把
    一轮正确产物永久挡在门外。
    """
    base, container = _container_pair()
    base_url, hub, srv = _boot(tmp_path)
    try:
        prev = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-001"
        prev.mkdir(parents=True, exist_ok=True)
        (prev / "weights.json").write_bytes(base)
        legacy = tmp_path / COURSE / "remote-jobs" / "offline" / "run-a" / "it-002"
        legacy.mkdir(parents=True)
        (legacy / "weights.json").write_bytes(container)  # ← 旧形状：`.json` 里是容器
        (legacy / "opt.tar").write_bytes(b"opt")
        (legacy / "row.json").write_text("{}", encoding="utf-8")

        st, res = _post(
            base_url,
            OFFLINE_ARTIFACT_PATH,
            _round_body(2, container, run_id="run-a", opt=b"opt"),
        )
        assert st == 200 and res.get("status") == "duplicate", (st, res)
        assert not (legacy / "weights.json").read_bytes().startswith(b"BWD1"), "自愈：就地补落整份"
        assert (legacy / "weights.delta").read_bytes() == container, "原件旁存（两版都留）"
        assert hub.resume_anchor(COURSE)["it"] == 2  # type: ignore[index]
    finally:
        srv.shutdown()


def _export_pack(tmp_path: Path, *, run_id: str, plan: bytes, monkeypatch) -> Path:
    """真导出器写一份包（判据的输入 = 索引里的 `run_id` / `parts[plan.json].sha256`）。"""
    from remote import bundle as bundle_mod
    from remote.artifacts import sha256_bytes

    monkeypatch.setattr(bundle_mod, "normalize_manifest", lambda m: m)
    src = tmp_path / "_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "init_weights.json").write_bytes(b'{"format":"nn-weights-json","params":{"w":1}}')
    (src / "code.zip").write_bytes(b"PK\x03\x04code")
    with zipfile.ZipFile(src / "ts_code.zip", "w") as z:
        z.writestr("tools/sim/x.ts", "// ts\n")
    out = tmp_path / COURSE / f"task-{COURSE}.zip"
    bundle_mod.export_bundle(
        out,
        manifest={
            "kind": "run",
            "runId": run_id,
            "it": 1,
            "plan_sha256": sha256_bytes(plan),
            "commit": "c" * 40,
        },
        plan_bytes=plan,
        init_weights_path=src / "init_weights.json",
        code_zip_path=src / "code.zip",
        ts_code_zip_path=src / "ts_code.zip",
    )
    return out


# ───────────────── ① 撤销后的回传：不推进活动权重（镜像/归档照落） ─────────────────


def test_backfeed_without_a_hold_advances_active_weights(tmp_path: Path) -> None:
    """无 hold ⇒ 回传照旧推进活动权重（手动送包 / 旧端 / 协作回传的兼容腿，★M4b）。

    旧的 pinned_online「人固定在线 ⇒ 不推进」用例随权威删除；接管期的两态（live+token /
    stale）由 `test_live_hold_needs_the_matching_lease_token_to_advance` 与
    `test_stale_hold_backfeed_does_not_advance` 覆盖 —— 本条钉第三态（无 hold ⇒ 准）。
    """
    base, _hub, srv = _boot(tmp_path)
    try:
        active = b'{"format":"nn-weights-json","params":{"w":1}}'
        (tmp_path / COURSE / "weights.json").write_bytes(active)
        newer = b'{"format":"nn-weights-json","params":{"w":999}}'
        st, doc = _post(
            base, OFFLINE_ARTIFACT_PATH, _round_body(7, newer, run_id="seg-old")
        )
        assert st == 200 and doc.get("status") == "accepted", doc
        assert doc.get("advance_active") is True, doc
        # 镜像照落（「算过什么」的证据面）且活动权重推进
        mirror = (
            tmp_path / COURSE / "remote-jobs" / "offline" / "seg-old" / "it-007" / "weights.json"
        )
        assert mirror.is_file() and mirror.read_bytes() == newer
        assert (tmp_path / COURSE / "weights.json").read_bytes() == newer
    finally:
        srv.shutdown()


# ───────────────── ② 段末自报的身份校验：旧包身份不得封住新包 ─────────────────


def test_end_it_reached_must_match_the_current_pack_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = json.dumps({"start_it": 1, "end_it": 5}).encode("utf-8")
    _export_pack(tmp_path, run_id="run-A", plan=plan, monkeypatch=monkeypatch)
    plan_sha = hashlib.sha256(plan).hexdigest()
    base, hub, srv = _boot(tmp_path)
    try:
        # 旧包 / 旧会话的身份自报 ⇒ 200 如实回显 `end_it_reached`，但**不盖章**
        st, doc = _post(
            base,
            OFFLINE_RESULT_PATH,
            {
                "course": COURSE,
                "run_id": "run-B",
                "it_end": 5,
                "state": "complete",
                "end_it_reached": True,
                "plan_sha256": "f" * 64,
            },
        )
        assert st == 200, doc
        assert doc.get("end_it_reached") is True, doc
        assert doc.get("completed_sealed") is False, doc
        row = next(r for r in hub.offline_tasks() if r["course"] == COURSE)
        assert row["state"] != "completed" and row["claimable"] is True, row
        assert row["run_id"] == "run-A", row  # ★ 索引的 snake_case `run_id` 也读得到

        # 当前包身份（run_id + plan sha 都对上）⇒ 盖章 ⇒ 不可再领（U6）
        st2, doc2 = _post(
            base,
            OFFLINE_RESULT_PATH,
            {
                "course": COURSE,
                "run_id": "run-A",
                "it_end": 5,
                "state": "complete",
                "end_it_reached": True,
                "plan_sha256": plan_sha,
            },
        )
        assert st2 == 200 and doc2.get("completed_sealed") is True, doc2
        row2 = next(r for r in hub.offline_tasks() if r["course"] == COURSE)
        assert row2["state"] == "completed" and row2["claimable"] is False, row2

    finally:
        srv.shutdown()


# ─────────── ③ ★M1b / P1-1：盖章无条件，advance 要「活跃 hold + token 相符」 ───────────


def test_live_hold_needs_the_matching_lease_token_to_advance(tmp_path: Path) -> None:
    """接管期间的回传：**镜像照落、活动起点不动**，除非带着本课租约 token 来。

    三道判据（`offline_advance_ok`）：无 hold ⇒ 准（旧端/手动腿，见另一条用例）；hold 活但
    token 不符 ⇒ 拒；stale ⇒ 拒（下一条用例）。这里钉的是「活 + token 不符」与「活 + 相符」。
    """
    active = b'{"format":"nn-weights-json","params":{"w":1}}'
    newer = b'{"format":"nn-weights-json","params":{"w":9}}'
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    (tmp_path / COURSE / "weights.json").write_bytes(active)
    base, hub, srv = _boot(tmp_path)
    try:
        hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
        # ① 不带 token（旧端 / 手动腿在接管期间回传）⇒ 200 收下但**不推进**
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, _round_body(7, newer, run_id="seg-x"))
        assert st == 200 and doc.get("status") == "accepted", doc
        assert doc.get("advance_active") is False and doc.get("advance_skipped") == "hold_token"
        mirror = (
            tmp_path / COURSE / "remote-jobs" / "offline" / "seg-x" / "it-007" / "weights.json"
        )
        assert mirror.read_bytes() == newer, "镜像照落（算过什么的证据面不受归属影响）"
        assert (tmp_path / COURSE / "weights.json").read_bytes() == active
        # ② token 不符（别的盘提着别人的名头）⇒ 同样拒
        body = _round_body(8, newer, run_id="seg-x") | {"lease_token": "tok-BAD"}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("advance_active") is False, doc
        assert (tmp_path / COURSE / "weights.json").read_bytes() == active
        # ③ 带对 token ⇒ 推进（持有人自己的回传是「课程现在的进度」）
        body = _round_body(9, newer, run_id="seg-x") | {"lease_token": "tok-1"}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("advance_active") is True, doc
        assert (tmp_path / COURSE / "weights.json").read_bytes() == newer
    finally:
        srv.shutdown()


def test_stale_hold_backfeed_does_not_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """心跳活、进度死（§68 的假活）⇒ 迟到回传不把活动起点夺回来（F2 与 `lease_verdict` 同尺）。"""
    monkeypatch.setenv("BCITY_HOLD_PROGRESS_STALE_SEC", "10")
    active = b'{"format":"nn-weights-json","params":{"w":1}}'
    newer = b'{"format":"nn-weights-json","params":{"w":9}}'
    clock = [1000.0]
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    (tmp_path / COURSE / "weights.json").write_bytes(active)
    base, hub, srv = _boot(tmp_path, now_fn=lambda: clock[0])
    try:
        hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
        clock[0] += 20.0  # 超过 10s 的进度窗 ⇒ stale
        body = _round_body(7, newer, run_id="seg-x") | {"lease_token": "tok-1"}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
        assert doc.get("advance_active") is False, doc
        assert doc.get("advance_skipped") == "hold_stale", doc
        assert (tmp_path / COURSE / "weights.json").read_bytes() == active
    finally:
        srv.shutdown()


def test_unreadable_pack_keeps_the_old_seal_behaviour(tmp_path: Path) -> None:
    """包读不到（占位/旧形状）⇒ **不判**、照旧盖章：不制造新的失败态（兼容面）。"""
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    (tmp_path / COURSE / f"task-{COURSE}.zip").write_bytes(b"PK-test-placeholder")
    base, _hub, srv = _boot(tmp_path)
    try:
        st, doc = _post(
            base,
            OFFLINE_RESULT_PATH,
            {
                "course": COURSE,
                "run_id": "seg-any",
                "it_end": 3,
                "state": "complete",
                "end_it_reached": True,
            },
        )
        assert st == 200 and doc.get("end_it_reached") is True, doc
        assert doc.get("completed_sealed") is True, doc
    finally:
        srv.shutdown()


# ────── ④ 归属身份：这一轮是谁推的（plan/offline-leg-ppo-attribution，2026-10-12） ──────
#
# 账本行原本**一个身份字段都没有** ⇒ dashboard 的 PPO 组看不到自主腿完成的轮（用户 2026-10-12
# 报障：「kaggle 自主 worker 今天完成了 600 轮 ppo 任务，却不显示在节点贡献统计里」）。两个键分工
# 不同，**别合成一个**：
#   · `worker` = claim 身份（`{HOSTNAME}-{uuid8}`）—— 客户端自报优先，hub 侧 hold 镜像兜底；
#   · `worker_name` = **可读名**（`kaggle-o`）—— 面板列名，只有客户端自报这一条来源。
# 两处落点（`<run>/metrics.jsonl` 与课程账本）**都要带**：只改一处 = 面板照样空 / 复盘对不上。

W1 = b'{"format":"nn-weights-json","params":{"w":1}}'


def _rows_of(tmp_path: Path, run_id: str) -> tuple[dict, dict]:
    """一轮补传的**两处落点**：① `<run>/metrics.jsonl`（人工复盘面）② 课程账本（dashboard 读面）。"""

    def last(p: Path) -> dict:
        text = p.read_text(encoding="utf-8")
        parsed: list[dict] = [json.loads(x) for x in text.splitlines() if x.strip()]
        rows = [r for r in parsed if r.get("event") == "offline_artifact"]
        assert rows, f"{p} 里没有 offline_artifact 行"
        return rows[-1]

    return (
        last(tmp_path / COURSE / "remote-jobs" / "offline" / run_id / "metrics.jsonl"),
        last(tmp_path / COURSE / "training_log.jsonl"),
    )


def test_artifact_rows_carry_the_readable_name_in_both_places(tmp_path: Path) -> None:
    """`worker_name` 逐字落**两处**；没有 hold 时 `worker` 键**整个缺席**（不是空串 —— 见下条）。"""
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    base, _hub, srv = _boot(tmp_path)
    try:
        body = _round_body(3, W1, run_id="seg-n") | {"worker_name": "kaggle-o"}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
    finally:
        srv.shutdown()
    mirror, ledger = _rows_of(tmp_path, "seg-n")
    for row in (mirror, ledger):
        assert row["worker_name"] == "kaggle-o", row
        assert row["it"] == 3, row
        assert row.keys().isdisjoint({"worker"}), f"无 hold ⇒ claim 身份应当缺席：{row}"


def test_self_reported_worker_wins_over_the_hold_mirror(tmp_path: Path) -> None:
    """自报的 claim 身份**优先**：hold 兜底只补空缺、不覆盖（正常路径不依赖 hub 内存状态）。"""
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    base, hub, srv = _boot(tmp_path)
    try:
        hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
        body = _round_body(4, W1, run_id="seg-c") | {
            "worker": "3908d414c3ca-217c4984",
            "worker_name": "kaggle-o",
        }
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
    finally:
        srv.shutdown()
    mirror, ledger = _rows_of(tmp_path, "seg-c")
    for row in (mirror, ledger):
        assert row["worker"] == "3908d414c3ca-217c4984", row
        assert row["worker_name"] == "kaggle-o", row


def test_worker_falls_back_to_the_live_hold_but_never_fills_the_name(tmp_path: Path) -> None:
    """两条反着钉：**有 hold** 时 claim 身份从镜像兜底；而可读名**绝不**拿它去填。

    填了就是面板上多一个 `cloud-1` 假列名（那是进程身份，不是 `kaggle-o` 那族可读名）。
    末段是「无 hold」的另一半：hold 清掉后重传，键又整个消失（不是留着上一次的值）。
    """
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    base, hub, srv = _boot(tmp_path)
    try:
        hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
        body = _round_body(5, W1, run_id="seg-h") | {"worker_name": "kaggle-o"}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
        mirror, ledger = _rows_of(tmp_path, "seg-h")
        for row in (mirror, ledger):
            assert row["worker"] == "cloud-1", row
            assert row["worker_name"] == "kaggle-o", row
        # hold 清了（release）⇒ 下一轮不再有 claim 身份，只有自报的可读名
        hub.note_release(COURSE)
        body = _round_body(6, W1, run_id="seg-h") | {"worker_name": "kaggle-o"}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
        mirror, ledger = _rows_of(tmp_path, "seg-h")
        for row in (mirror, ledger):
            assert row.keys().isdisjoint({"worker"}), f"hold 已清 ⇒ 不得留下旧的 claim：{row}"
            assert row["worker_name"] == "kaggle-o", row
    finally:
        srv.shutdown()


def test_a_client_cannot_spoof_the_identity_through_row(tmp_path: Path) -> None:
    """`row` 是**客户端给的字节**：它里面的 `worker` / `worker_name` 不得冒名顶替 hub 的认定。

    钉 `store_offline` 新键的**位置**（排在 `**(row if …)` 之后）；写在前面的后果是任何持有 token
    的腿都能把归属写到别人名下（面板是给人做决定的，赃数据比没数据更坏）。
    """
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    base, hub, srv = _boot(tmp_path)
    try:
        hub.note_hold(COURSE, worker_id="cloud-1", token="tok-1")
        fp = hashlib.sha256(W1).hexdigest()
        body = _round_body(7, W1, run_id="seg-s") | {
            "worker_name": "kaggle-o",
            "row": {"it": 7, "weights_fp": fp, "worker": "evil", "worker_name": "evil"},
        }
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
    finally:
        srv.shutdown()
    mirror, ledger = _rows_of(tmp_path, "seg-s")
    for row in (mirror, ledger):
        assert row["worker"] == "cloud-1", row
        assert row["worker_name"] == "kaggle-o", row


def test_non_string_identity_fields_are_treated_as_absent(tmp_path: Path) -> None:
    """体是**远端来的字节**：`worker_name=123` / `worker={}` ⇒ 当没给（键缺席）。

    一个 dict 落进账本会把 dashboard 的读面打红，而那不是本任务要修的东西 —— 入口就该挡住。
    """
    (tmp_path / COURSE).mkdir(parents=True, exist_ok=True)
    base, _hub, srv = _boot(tmp_path)
    try:
        body = _round_body(8, W1, run_id="seg-t") | {"worker_name": 123, "worker": {}}
        st, doc = _post(base, OFFLINE_ARTIFACT_PATH, body)
        assert st == 200 and doc.get("status") == "accepted", doc
    finally:
        srv.shutdown()
    mirror, ledger = _rows_of(tmp_path, "seg-t")
    for row in (mirror, ledger):
        assert row.keys().isdisjoint({"worker_name", "worker"}), row
