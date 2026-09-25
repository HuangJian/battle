"""tests/test_job_identity_collision.py —— job 身份跨课程碰撞（2026-09-24 现场事故的回归锚）。

事故（plan/job-identity-collision.plan.md §1.1）：单进程多课程（`--serve` 共享 trainer）下
`runId` 同源、`data_fp` 不含课程身份、`init_weights_fp` 同 warm-start、`it` 同轮 ⇒
`idempotency_key = (runId, it, init_weights_fp, data_fp)` **四分量全同** ⇒ 两门课发布出**同一个
`job_id`** ⇒ hub 的 per-job 路由（`course_of` 取第一个匹配）让**两个 trainer 读到同一份
`result.json`**，各自落进自己的 `args.out`（静默污染，且自持循环）。

本文件钉住四件事：
  ① **id 分叉**：`course_fp` 进键 ⇒ 不同课程的同四分量活必须是两个 job（并保留旧公式的碰撞复现）；
  ② **归属唯一化**：≥2 门课都认识同一个 jid ⇒ 一律拒答（None / 404），**绝不取第一个**；
  ③ **发布端守卫**：同一个完整键不许被两个 store 拥有（拒发且不落任何文件）；
  ④ **原因可读**：409 不再被读成「hub 异常」。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.job_lifecycle as jl_mod
import remote.worker as worker_mod
from common.protocol import (
    COURSE_MODE_ONLINE,
    collision_rows,
    idempotency_key,
    normalize_manifest,
)
from common.protocol import (
    job_id as make_job_id,
)
from remote.hub_client import HubClientError, publish_job
from remote.hub_server import _HubQueue, _JobStore

#: 两门课共用的四分量（事故现场的「全同」部分）。
_SHARED = {
    "runId": "923c663c35dc3e19",
    "it": 2,
    "init_weights_fp": "a11a96231269",
    "data_fp": "41f7d73f5d14",
}


def _manifest(*, course_fp: str = "8" * 64, course_name: str = "", **over) -> dict:
    """最小合法 manifest（`idempotency_key` 的五个分量齐全）。"""
    m = {
        "proto": 1,
        **_SHARED,
        "job_id": "j" * 16,
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": '// course jsonc\n{"reward": {"formula": "score"}}',
        "course_fp": course_fp,
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
        "payload_sha256": "p" * 64,
    }
    m.update(over)
    if course_name:
        m["course_name"] = course_name
    return normalize_manifest(m)


def _old_key(m: dict) -> tuple:
    """**事故当时的**幂等键公式（4 分量）——刻意在测试里独立重实现。

    它存在的唯一理由：把「旧公式下确实撞」焊成回归锚（AGENTS §8：codec/数据判据优先独立
    重实现对账）。若哪天有人把 `course_fp` 从键里拿掉，这条会立刻红。
    """
    return (m["runId"], m["it"], m["init_weights_fp"], m["data_fp"])


def _old_job_id(m: dict) -> str:
    h = hashlib.sha256()
    for part in _old_key(m):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


# ------------------------------------------------------------------ ① id 分叉


def test_job_id_differs_across_courses_same_key_components() -> None:
    """两门课只差课程身份（course_fp / course / course_name / payload）⇒ id 必须不同。"""
    l1 = _manifest(course_fp="86" * 32, course_name="x20-dodge-l1")
    l3 = _manifest(course_fp="33" * 32, course_name="x20-dodge-l3")
    assert l1["course_fp"] != l3["course_fp"]
    assert make_job_id(l1) != make_job_id(l3), "不同课程的同四分量活必须是两个 job"


def test_old_key_formula_still_collides() -> None:
    """事故锚：**旧公式**（不含 course_fp）对这两份 manifest 给出同一个 id。

    这就是 2026-09-24 的现场：两门课共享一个 job 身份，于是共享一份结果。
    """
    l1 = _manifest(course_fp="86" * 32, course_name="x20-dodge-l1")
    l3 = _manifest(course_fp="33" * 32, course_name="x20-dodge-l3")
    assert _old_key(l1) == _old_key(l3)
    assert _old_job_id(l1) == _old_job_id(l3), "旧公式下确实撞——这条断言是事故的化石"
    # 新公式必须已经分开（与上面成对：一个证明旧病，一个证明已愈）
    assert idempotency_key(l1) != idempotency_key(l3)


def test_same_course_same_key_is_still_idempotent() -> None:
    """幂等语义不变：同课程 + 同键 ⇒ 同 id（hub 重启重发布不产生重复 job）。"""
    a = _manifest(course_fp="86" * 32)
    b = _manifest(course_fp="86" * 32)
    assert make_job_id(a) == make_job_id(b) == make_job_id(a)


# ------------------------------------------------------------------ ③ 发布端守卫


def _job_tree(tmp_path: Path, course: str, m: dict) -> Path:
    """造一个「某课程已发布过这份 job」的盘上事实（守卫的扫描输入）。

    形状与 `publish_job` 的写入**逐字同构**：目录名 = `make_job_id(manifest)`、manifest 里的
    `job_id` 同值（这是发布端的写入不变量，也是守卫快速闸与 `course_of` 共用的那条）。
    """
    jid = make_job_id(m)
    jd = tmp_path / course / "remote-jobs" / jid
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "manifest.json").write_text(
        json.dumps({**m, "job_id": jid}, ensure_ascii=False), encoding="utf-8"
    )
    return jd


def test_collision_rows_flags_only_other_store(tmp_path: Path) -> None:
    """守卫判据：**完整键**相同 且 落在**别的 store** ⇒ 命中；同 store / 异键 ⇒ 不命中。"""
    own_root = tmp_path / "c-l1" / "remote-jobs"
    other = _manifest(course_fp="33" * 32)
    own = _manifest(course_fp="86" * 32)
    _job_tree(tmp_path, "c-l3", other)
    _job_tree(tmp_path, "c-l1", own)  # 本课自己的历史 job

    # 本课要发的这一份，与 c-l3 的那份**完整键相同**（同 course_fp ⇒ 同身份）
    same_as_other = _manifest(course_fp="33" * 32)
    rows = collision_rows(own_root, same_as_other)
    assert [r["course"] for r in rows] == ["c-l3"], rows
    assert rows[0]["job_id"] == make_job_id(same_as_other)

    # 只差 course_fp ⇒ 是两份不同的活（本事故的配置）⇒ **不得**命中
    assert collision_rows(own_root, own) == []
    # 只差 it ⇒ 不命中
    assert collision_rows(own_root, _manifest(course_fp="33" * 32, it=3)) == []
    # 扫描根不存在（节点侧/自定义 job_root 布局）⇒ 静默放过，绝不误伤
    assert collision_rows(tmp_path / "ghost" / "c" / "remote-jobs", same_as_other) == []


def _publish_args(tmp_path: Path, course: str) -> dict:
    """`publish_job` 的最小实参（真落盘：payload + manifest + 账本）。"""
    d = tmp_path / course
    job_root = d / "remote-jobs"
    jsonl = d / "training_log.jsonl"
    d.mkdir(parents=True, exist_ok=True)  # 生产里课程目录由训练侧先建
    jsonl.touch()
    w = tmp_path / "init_weights.json"
    if not w.exists():
        w.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    return {
        "job_root": job_root,
        "jsonl_path": jsonl,
        "run_id": _SHARED["runId"],
        "it": _SHARED["it"],
        "traj_dir": d / "it2",
        "shard_dirs": [],
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": '// course jsonc\n{"reward": {"formula": "score"}}',
        "init_weights_path": str(w),
        "reward_formula": "score",
        "formula_hash": "h" * 40,
        "metrics_version": 1,
        "gamma": 0.995,
        "lam": 0.95,
        "mode": "per-tick",
        "epochs": 2,
        "mb": 512,
        "lr": 3e-4,
        "log": (lambda _m: None),
    }


def _ledger(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_publish_refuses_cross_store_identity(tmp_path: Path) -> None:
    """同一份活（同完整键）已在**别的课程**发布过 ⇒ 第二次必须响亮拒发且不落任何文件。"""
    a = _publish_args(tmp_path, "c-l3")
    m1 = publish_job(**a, course_fp="33" * 32)
    assert (Path(a["job_root"]) / m1["job_id"] / "manifest.json").exists()

    b = _publish_args(tmp_path, "c-l1")
    with pytest.raises(HubClientError) as ei:
        publish_job(**b, course_fp="33" * 32)
    msg = str(ei.value)
    assert "c-l3" in msg and "c-l1" in msg, f"拒发消息必须点名双方课程：{msg}"
    # 不落任何文件、不记账本（半份 job 比没有 job 更危险）
    assert not (Path(b["job_root"]) / m1["job_id"]).exists()
    assert not [x for x in _ledger(b["jsonl_path"]) if x.get("event") == "job_pending"]
    assert not Path(b["job_root"]).joinpath(".extra_tmp").exists()


def test_publish_allows_same_components_different_course_fp(tmp_path: Path) -> None:
    """**事故配置必须放行**：四分量全同但课程身份不同 ⇒ 两份合法的活（守卫不得误伤）。"""
    a = _publish_args(tmp_path, "c-l1")
    m1 = publish_job(**a, course_fp="86" * 32)
    b = _publish_args(tmp_path, "c-l3")
    m3 = publish_job(**b, course_fp="33" * 32)
    assert m1["job_id"] != m3["job_id"]
    assert _old_key(m1) == _old_key(m3), "前提：四分量确实全同（否则这条用例没压到东西）"


def test_publish_twice_in_same_store_is_still_idempotent(tmp_path: Path) -> None:
    """同课程重发布同一份活：幂等（同 id、覆盖 payload），**不得**被守卫拦下。"""
    a = _publish_args(tmp_path, "c-l1")
    m1 = publish_job(**a, course_fp="86" * 32)
    m2 = publish_job(**a, course_fp="86" * 32)
    assert m1["job_id"] == m2["job_id"]


# ------------------------------------------------------------------ ② 归属唯一化


def _hub(tmp_path: Path, courses: tuple[str, ...] = ("a", "b")) -> _HubQueue:
    stores = {
        c: _JobStore(tmp_path / c / "remote-jobs", tmp_path / c / "training_log.jsonl")
        for c in courses
    }
    return _HubQueue(
        stores,
        order=list(courses),
        modes={c: COURSE_MODE_ONLINE for c in courses},
    )


def _publish_store(hub: _HubQueue, course: str, jid: str, *, course_fp: str = "8" * 64) -> None:
    hub._stores[course].publish(jid, _manifest(course_fp=course_fp), b"PK\x03\x04fake")


def test_ambiguous_job_id_is_refused_not_guessed(tmp_path: Path) -> None:
    """同一个 jid 挂在两门课（旧时代残留）⇒ 一律拒答，绝不取第一个。

    取第一个正是事故的静默通道：worker 领 l3 的候选、hub 把它路由到 l1 的副本。
    """
    hub = _hub(tmp_path)
    jid = "e" * 16
    _publish_store(hub, "a", jid)
    _publish_store(hub, "b", jid)

    assert hub.course_of(jid) is None, "歧义必须拒答（None），不许猜第一个"
    assert hub._locate_cache.get(jid) is None, "歧义不得进归属缓存（一次歧义会变成永久归属）"
    # 拒答 ⇒ 所有 job 作用域入口都落在「不归本 hub 管」的那条路上
    assert hub.result_token_ok(jid, "") is False
    assert hub.store_result(jid, {"x": 1}) is False
    assert hub.get_result(jid) is None
    assert hub._job_dir(jid).joinpath("manifest.json").exists() is False


def test_unambiguous_job_still_routes(tmp_path: Path) -> None:
    """唯一命中照旧（唯一化不许把正常路径一起拒掉）。"""
    hub = _hub(tmp_path)
    _publish_store(hub, "a", "a" * 16)
    _publish_store(hub, "b", "b" * 16)
    assert hub.course_of("a" * 16) == "a" and hub.course_of("b" * 16) == "b"
    assert hub.course_of("x" * 16) is None


def test_queue_state_reports_ambiguous_jids(tmp_path: Path) -> None:
    """观测面：`/admin/queue` 要能看见「哪些 jid 挂在 ≥2 门课」（不必先被谁查一次）。"""
    hub = _hub(tmp_path)
    jid = "e" * 16
    _publish_store(hub, "a", jid)
    _publish_store(hub, "b", jid)
    _publish_store(hub, "a", "c" * 16)
    assert hub.queue_state()["ambiguous_jids"] == {jid: ["a", "b"]}


def test_single_course_legacy_semantics_unchanged(tmp_path: Path) -> None:
    """单课程（课程名就是空串）的旧语义逐字不变：找到返回空串、歧义面为空。"""
    hub = _hub(tmp_path, courses=("",))
    _publish_store(hub, "", "j" * 16)
    assert hub.course_of("j" * 16) == ""
    assert hub.course_of("x" * 16) is None
    assert hub.queue_state()["ambiguous_jids"] == {}


# ------------------------------------------------------------------ ④ 原因可读


def test_claim_reject_reason_is_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    """409 = 调度面拒绝（被持有/冻结/歧义）——**不是** hub 异常，且必须带 reason 与 jid。

    现场就是被这句兜底文案骗了：worker 打「hub 异常，请检查 hub 进程与隧道」，
    而真因是 `held`（身份歧义导致的跨课程路由）。
    """
    monkeypatch.setattr(worker_mod, "_POLL_WARN_AT", {})  # 关掉 (url,status) 节流

    def _fake_request(*_a, **_kw):
        return 409, json.dumps({"error": "claim 被拒: held (already claimed)"}).encode("utf-8")

    # patch 面 = `remote.job_lifecycle`：`claim_job` 与它直调的 `_request` 都已搬到那一簇
    # （`patch remote.worker._request` 自从拆分起就是**静默空操作**——真请求打到 "http://hub" 上
    # 变成 502，正是本条用例第一次跑出来的形态）。分档见 `tests/test_job_lifecycle_split.py`。
    monkeypatch.setattr(jl_mod, "_request", _fake_request)
    lines: list[str] = []
    got = worker_mod.claim_job("http://hub", "t", "e" * 16, log=lines.append)
    assert got is None
    text = "\n".join(lines)
    assert "held" in text, f"日志必须带 hub 给的 reason：{text}"
    assert "e" * 16 in text, f"日志必须带 jid：{text}"
    assert "hub 异常" not in text, f"409 不是 hub 异常：{text}"


def test_warn_non_200_still_calls_5xx_a_hub_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    """5xx 仍是 hub 异常（文案分派不许把真故障也说成调度拒绝）。"""
    monkeypatch.setattr(worker_mod, "_POLL_WARN_AT", {})
    lines: list[str] = []
    worker_mod._warn_non_200("http://hub", 502, lines.append, body=b"", jid="j" * 16)
    assert "hub 异常" in "\n".join(lines)
