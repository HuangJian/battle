"""R0（M0，`plan/wire-diet.plan.md` §3 R0）—— 身份映射的判据。

钉住六件事（每条都对应「没有它会静默用错起点」）：

  ① **命中**：`latest.job_id == manifest.opt_from` 且两份本机副本自校通过 ⇒ 返回两份 f32 字节；
  ② **身份不符不命中**：`opt_from` 是别人/别的轮 ⇒ None（回退内容寻址，不是「差不多就用」）；
  ③ **自校失败不命中**：blob 被 prune 掉 / 字节数与记录不符 / 键是 sha 内容却不是它 ⇒ None
     （**绝不能**把坏件当起点）；
  ④ **输了竞速必须作废**：回传被拒/被别人赢 ⇒ `invalidate` 之后同一 job_id 不再命中
     （这是本模块存在的头号陷阱：输了的那台也算出过一份产物，但 hub 的权威链不是它）；
  ⑤ **pending 不参与命中**：还没等到回传受理的产物**不算**可用起点（`confirm` 才升）；
  ⑥ **写失败只丢优化**：目录不可写时 record/confirm 不抛，也不留半截文件；
  ⑦ **接线**：发布端写 `opt_from`（同 run/课程的 it−1，扫不到不添键；别的课/别的 run 不算）、
     解析端身份命中写 `init_weights.json` 且零字节（不成立就逐字节旧行为）、
     落定端按状态码处置（`settle`）、`prune_job_dirs` 眼开 `course_cache`。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import course_cache as cc

COURSE_FP = "c" * 64
JOB_ID = "job-1"
BLOB_ROOT_NAME = "blob_cache"


def _tree(tmp_path: Path) -> tuple[Path, Path]:
    """`(blob_root, cache_root)` —— 与生产同源：cache_root 是 blob_root 的父目录。"""
    blob_root = tmp_path / BLOB_ROOT_NAME
    blob_root.mkdir(parents=True, exist_ok=True)
    return blob_root, cc.cache_root_of(blob_root)


def _put_blob(blob_root: Path, raw: bytes) -> tuple[str, int]:
    sha = hashlib.sha256(raw).hexdigest()
    (blob_root / sha).write_bytes(raw)
    return sha, len(raw)


def _record(blob_root: Path, cache_root: Path, *, weights: bytes, opt: bytes, job_id: str = JOB_ID,
            course_fp: str = COURSE_FP) -> None:
    cc.record_pending(
        cache_root,
        course_fp,
        job_id=job_id,
        it=2,
        run_id="run-1",
        weights=_put_blob(blob_root, weights),
        opt=_put_blob(blob_root, opt),
    )


def _hit(blob_root: Path, cache_root: Path, *, opt_from: str = JOB_ID,
         course_fp: str = COURSE_FP) -> dict[str, bytes] | None:
    return cc.hit(
        cache_root=cache_root,
        blob_root=blob_root,
        course_fp=course_fp,
        opt_from=opt_from,
    )


def test_pending_is_not_a_hit_until_confirmed(tmp_path: Path) -> None:
    """⑤ 跑完 ≠ 可用：还没等到回传受理的产物不算起点。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    assert _hit(blob_root, cache_root) is None, "pending 不该命中"
    assert cc.confirm(cache_root, COURSE_FP, JOB_ID) is True
    got = _hit(blob_root, cache_root)
    assert got == {"weights": b"w" * 8, "opt": b"o" * 8}


def test_hit_requires_same_job_identity(tmp_path: Path) -> None:
    """② 身份（`opt_from`）不符 ⇒ 不命中：换机/换轮都不能「差不多就用」。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    cc.confirm(cache_root, COURSE_FP, JOB_ID)
    assert _hit(blob_root, cache_root, opt_from="job-2") is None
    assert _hit(blob_root, cache_root, opt_from="") is None  # 缺键（旧 hub）⇒ 回退


def test_confirm_only_for_that_job(tmp_path: Path) -> None:
    """④ 的细节：pending 是**别的** job ⇒ 不升（不然会把别人那轮当自己的起点）。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    assert cc.confirm(cache_root, COURSE_FP, "job-9") is False
    assert _hit(blob_root, cache_root) is None


def test_pruned_blob_falls_back_not_hits(tmp_path: Path) -> None:
    """③ 本机副本被 prune 掉（或换机）⇒ 不命中，调用方回退内容寻址。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    cc.confirm(cache_root, COURSE_FP, JOB_ID)
    for p in blob_root.iterdir():
        p.unlink()  # prune_caches 的预算行为
    assert _hit(blob_root, cache_root) is None


def test_corrupt_blob_falls_back_not_hits(tmp_path: Path) -> None:
    """③ 键是 sha、内容却不是它 ⇒ 坏件，绝不拿来当起点。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    cc.confirm(cache_root, COURSE_FP, JOB_ID)
    victim = next(blob_root.iterdir())
    victim.write_bytes(b"x" * 9)  # 长度与内容都不符
    assert _hit(blob_root, cache_root) is None


def test_lost_race_invalidates_the_mapping(tmp_path: Path) -> None:
    """④ ★ 头号陷阱：输了竞速的那台**也必须**作废 —— 否则下一轮静默分叉。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    cc.confirm(cache_root, COURSE_FP, JOB_ID)
    assert _hit(blob_root, cache_root) is not None
    cc.invalidate(cache_root, COURSE_FP, JOB_ID)
    assert _hit(blob_root, cache_root) is None, "作废后不得再命中（那份链不是 hub 的延续点）"


def test_invalidate_does_not_touch_another_job(tmp_path: Path) -> None:
    """④ 的边界：作废只动**这份** job 的键，别误伤更早那份（它可能是别人的起点）。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8, job_id="job-old")
    cc.confirm(cache_root, COURSE_FP, "job-old")
    _record(blob_root, cache_root, weights=b"w2" * 8, opt=b"o2" * 8, job_id="job-new")
    cc.confirm(cache_root, COURSE_FP, "job-new")
    cc.invalidate(cache_root, COURSE_FP, "job-none")
    assert _hit(blob_root, cache_root, opt_from="job-new") == {
        "weights": b"w2" * 8,
        "opt": b"o2" * 8,
    }


def test_missing_or_broken_latest_is_not_a_hit(tmp_path: Path) -> None:
    """① 缺文件 / 坏 JSON / 没有 job_id ⇒ 一律 None（宁可回退，不可猜）。"""
    blob_root, cache_root = _tree(tmp_path)
    d = cc.identity_dir(cache_root, COURSE_FP)
    d.mkdir(parents=True, exist_ok=True)
    assert _hit(blob_root, cache_root) is None
    (d / cc.LATEST_NAME).write_text("{ not json", encoding="utf-8")
    assert _hit(blob_root, cache_root) is None
    (d / cc.LATEST_NAME).write_text(json.dumps({"it": 2}), encoding="utf-8")
    assert _hit(blob_root, cache_root) is None


def test_course_fp_is_required_and_partitioned(tmp_path: Path) -> None:
    """身份映射必须按课程分区：空 `course_fp` 抛（不退化成公共目录），两门课互不命中。"""
    blob_root, cache_root = _tree(tmp_path)
    with pytest.raises(ValueError):
        cc.identity_dir(cache_root, "")
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8, course_fp="a" * 8)
    cc.confirm(cache_root, "a" * 8, JOB_ID)
    assert _hit(blob_root, cache_root, course_fp="a" * 8) is not None
    assert _hit(blob_root, cache_root, course_fp="b" * 8) is None


def test_settle_reads_course_fp_from_manifest(tmp_path: Path) -> None:
    """W1a（2026-10-21）：claim 响应的 `course_fp` 在 **`manifest`** 里，顶层没有。

    接线断在 `job_round` 只读顶层 ⇒ `settle` 收到空串 ⇒ 早退 ⇒ `latest.json` 永不写
    ⇒ 身份命中率恒 0。这里同时钉住两种形态与「没有就是空串」。
    """
    assert cc.course_fp_of({"job_id": "j", "manifest": {"course_fp": COURSE_FP}}) == COURSE_FP
    assert cc.course_fp_of({"course_fp": COURSE_FP}) == COURSE_FP  # 旧形态/测试里放顶层
    assert cc.course_fp_of({"job_id": "j"}) == ""
    assert cc.course_fp_of({"job_id": "j", "manifest": None}) == ""
    assert cc.course_fp_of(None) == ""
    # 端到端：拿真实 claim 形状过一遍 settle（这就是 W1a 修的那一条链）
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8, job_id="job-claim")
    job = {"job_id": "job-claim", "manifest": {"course_fp": COURSE_FP}}
    action = cc.settle(cache_root, cc.course_fp_of(job), "job-claim", status=200, log=_quiet)
    assert action == "confirm"
    assert _hit(blob_root, cache_root, opt_from="job-claim") is not None


def test_blob_read_touches_mtime(tmp_path: Path) -> None:
    """命中的副本也得抬 mtime（`prune_caches` 按 mtime 丢最旧）。

    身份链的两份副本命中时**不走下载**（`_cache_blob` 的 touch 不会发生）——若这里也不
    touch，它们的年龄永远停在「生产那一刻」⇒ 共享缓存的第二个 worker 一写，热件就按
    「谁先来谁先死」被挤掉（不是 LRU）。
    """
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    assert cc.confirm(cache_root, COURSE_FP, JOB_ID) is True
    victim = blob_root / hashlib.sha256(b"w" * 8).hexdigest()
    old = 1_000_000_000.0
    os.utime(victim, (old, old))
    assert _hit(blob_root, cache_root) is not None
    assert victim.stat().st_mtime > old, "身份命中读过的那份没被 touch（会被 prune 当最旧）"


def test_write_failure_is_optimization_only(tmp_path: Path) -> None:
    """⑥ 目录写不进去 ⇒ 只丢优化：不抛、不留半截（半截文件会让下一次读成坏 JSON）。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    # 把 course 目录换成一个**文件**：mkdir 必失败（Windows/Linux 都会 OSError）
    d = cc.identity_dir(cache_root, COURSE_FP)
    for p in sorted(d.iterdir()):
        p.unlink()
    d.rmdir()
    d.write_text("blocked", encoding="utf-8")
    cc.record_pending(
        cache_root,
        COURSE_FP,
        job_id="job-2",
        it=3,
        run_id="run-1",
        weights=("a" * 64, 8),
        opt=("b" * 64, 8),
    )
    assert cc.confirm(cache_root, COURSE_FP, "job-2") is False
    assert d.read_text(encoding="utf-8") == "blocked", "别把挡路的文件也动了"


# ───────────────────────── ⑦ 接线：落定 / 发布 / 解析 / prune ─────────────────────────


def _quiet(_msg: str = "") -> None:
    return None


def test_settle_maps_status_to_action(tmp_path: Path) -> None:
    """落定策略单点：`200` ⇒ confirm；`403`/`409` ⇒ invalidate（输的那份必须作废）；其余 ⇒ 不动。"""
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8, job_id="job-win")
    assert cc.settle(cache_root, COURSE_FP, "job-win", status=200, log=_quiet) == "confirm"
    assert _hit(blob_root, cache_root, opt_from="job-win") is not None
    # 输了竞速：本份也跑完了，但 hub 的权威链不是它 ⇒ 必须作废，且不误伤赢家那份
    _record(blob_root, cache_root, weights=b"w2" * 8, opt=b"o2" * 8, job_id="job-lost")
    assert cc.settle(cache_root, COURSE_FP, "job-lost", status=409, log=_quiet) == "invalidate"
    latest = cc.read_latest(cache_root, COURSE_FP)
    assert latest is not None and latest["job_id"] == "job-win"
    # 403（租约不符）走同一条处置
    _record(blob_root, cache_root, weights=b"w3" * 8, opt=b"o3" * 8, job_id="job-403")
    assert cc.settle(cache_root, COURSE_FP, "job-403", status=403, log=_quiet) == "invalidate"


def test_settle_leaves_pending_alone_on_unknown_status(tmp_path: Path) -> None:
    """其余状态（含 `None` = 回传没送达）⇒ 不动：pending 留着无害，作废它反而有风险。"""
    _, cache_root = _tree(tmp_path)
    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8, job_id="job-late")
    assert cc.settle(cache_root, COURSE_FP, "job-late", status=None, log=_quiet) == "none"
    assert cc.settle(cache_root, COURSE_FP, "job-late", status=500, log=_quiet) == "none"
    assert (cc.identity_dir(cache_root, COURSE_FP) / cc.PENDING_NAME).exists()
    assert cc.settle(cache_root, "", "job-late", status=200, log=_quiet) == "none"


def _publish(tmp_path: Path, *, it: int, run_id: str = "r1", course_fp: str = COURSE_FP) -> dict:
    from remote.hub_client import publish_job

    wfile = tmp_path / "init_weights.json"
    if not wfile.exists():
        wfile.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    jsonl = tmp_path / "training_log.jsonl"
    if not jsonl.exists():
        jsonl.write_text("", encoding="utf-8")
    return publish_job(
        job_root=tmp_path / "jobs",
        jsonl_path=jsonl,
        run_id=run_id,
        it=it,
        traj_dir=tmp_path / "traj",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='{"reward":{"formula":"score"}}',
        course_fp=course_fp,
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
        log=_quiet,
    )


def _opt_from(m: dict) -> str:
    """`manifest.opt_from` 的读法（缺席 = 空串：回退内容寻址的准确表述）。

    用例只比**值**不查键（口径 A 的「文本断言」护栏：`assert "k" in m` 那族算措辞判据）。
    """
    return str(m.get("opt_from") or "")


def test_publish_marks_previous_round_of_same_course(tmp_path: Path) -> None:
    """发布端：同 run/课程的 it−1 进 `opt_from`（盘上权威 manifest 也得有）；首轮不添键。"""
    first = _publish(tmp_path, it=1)
    second = _publish(tmp_path, it=2)
    assert _opt_from(first) == "", "首轮没有上一轮 ⇒ 不添键（回退内容寻址的准确表述）"
    assert _opt_from(second) == first["job_id"]
    on_disk = json.loads(
        (tmp_path / "jobs" / second["job_id"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert _opt_from(on_disk) == first["job_id"], "worker 读的是盘上那份，不是返回值"


def test_publish_opt_from_is_per_course_and_per_run(tmp_path: Path) -> None:
    """别的课程 / 别的 run 的上一轮**不许**当成本课的起点（多课程单进程会串链）。"""
    _publish(tmp_path, it=1, course_fp="a" * 64)
    other_course = _publish(tmp_path, it=2, course_fp="b" * 64)
    other_run = _publish(tmp_path, it=2, run_id="r2", course_fp="a" * 64)
    assert _opt_from(other_course) == "", "别的课程的上一轮不算本课的起点"
    assert _opt_from(other_run) == "", "别的 run 的上一轮不算本轮的起点"


def test_previous_job_id_declines_without_identity(tmp_path: Path) -> None:
    """缺 `course_fp` / `it ≤ 0` ⇒ 空串（宁可不命中，不可猜一个起点）。"""
    from remote.hub_client import _previous_job_id

    assert _previous_job_id(tmp_path, "r1", "", 2) == ""
    assert _previous_job_id(tmp_path, "r1", "f" * 64, 0) == ""


def test_resolve_weights_prefers_identity_hit(tmp_path: Path) -> None:
    """解析端：身份命中 ⇒ 写 `job_dir/init_weights.json`、零字节、`src=course_cache`。"""
    from remote.download import _resolve_weights

    blob_root, cache_root = _tree(tmp_path)
    _record(blob_root, cache_root, weights=b"w" * 8, opt=b"o" * 8)
    cc.confirm(cache_root, COURSE_FP, JOB_ID)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    path, src, nbytes = _resolve_weights(
        job_dir=job_dir,
        init_weights_fp="0" * 64,
        blob_root=blob_root,
        jid="j1",
        base_url="",
        token="",
        preloaded=None,
        course_fp=COURSE_FP,
        opt_from=JOB_ID,
        # 消费方是 it=N 的那一轮，`opt_from` 指向 it−1 的产物（`_record` 记的正是 it=2）
        it=3,
        log=_quiet,
    )
    assert (src, nbytes) == ("course_cache", 0)
    assert path is not None and path.read_bytes() == b"w" * 8


def test_resolve_weights_falls_back_when_identity_misses(tmp_path: Path) -> None:
    """身份不成立（这里没有 latest）⇒ 逐字节旧行为（不碰 payload 那份）。"""
    from remote.download import _resolve_weights

    blob_root, _cache_root = _tree(tmp_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    (job_dir / "init_weights.json").write_bytes(b"payload-bytes")
    path, src, nbytes = _resolve_weights(
        job_dir=job_dir,
        init_weights_fp="",  # 非内容 sha ⇒ 走 payload 分支（旧口径）
        blob_root=blob_root,
        jid="j2",
        base_url="",
        token="",
        preloaded=None,
        course_fp=COURSE_FP,
        opt_from="job-nowhere",
        it=2,
        log=_quiet,
    )
    assert (src, nbytes) == ("payload", 0)
    assert path is not None and path.read_bytes() == b"payload-bytes"


def test_prune_job_dirs_spares_course_cache(tmp_path: Path) -> None:
    """`course_cache` 是**状态**不是缓存：prune 必须眼开（2026-09-17 `blob_cache` 漏项同型）。"""
    from remote.job_fs import prune_job_dirs

    work = tmp_path / "work"
    work.mkdir()
    for name in ("job-a", "job-b", "job-c", "blob_cache", "course_cache"):
        (work / name).mkdir()
    removed = prune_job_dirs(work, keep=1, log=_quiet)
    assert removed >= 1, "三个 job 目录 + keep=1 应当删掉至少一个（否则这条用例没测到）"
    assert (work / "course_cache").exists(), "身份映射被当旧 job 目录删了"
    assert (work / "blob_cache").exists()
