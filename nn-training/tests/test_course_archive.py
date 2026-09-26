"""课程封存契约（plan/course-archive.plan.md §3/§6.1）。

三组：
  · **纯函数表**（`classify` / `archive_plan` / `opt_keep_iters` / `detect_form`）——无磁盘；
  · **闸与顺序**——在训拒绝零变化 · dry-run 零写零删 · 校验失败不删源 · 二次封存不动档案；
  · **压缩与索引**——解压后逐字节一致 · manifest/`ARCHIVE.md` 永不压缩 · 无裸 `shutil.rmtree`。

为什么判据要按**形态**（A/B/C）而不是文件名：实测同课可混两套语料布局
（`c6-chip` = A+B、`x20-dodge-l3` = A+C），旧 plan 的文件名清单会同时漏删与误删
（详见 `docs/nn/course-archive.md §1.4`）。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

NN_ROOT = Path(__file__).resolve().parent.parent
if str(NN_ROOT) not in sys.path:
    sys.path.insert(0, str(NN_ROOT))

from rl import course_archive as CA

# ────────────────────────── 纯函数表 ──────────────────────────


def test_detect_form_covers_all_shapes():
    """A / B / C 及**同课混合**（实测 `c6-chip`=A+B、`x20-dodge-l3`=A+C）。"""
    assert CA.detect_form(["it1/dist/mac/rl_s2_seed1/a.npy"]) == "A"
    assert CA.detect_form(["it1/w7/rl_s2_seed1/a.npy"]) == "B"
    assert CA.detect_form(["deliver/run/it-001/weights.json"]) == "C"
    assert CA.detect_form(["it1/dist/mac/x/a.npy", "it1/w7/y/b.npy"]) == "A+B"
    assert CA.detect_form(["it1/dist/mac/x/a.npy", "remote-jobs/offline/r/it-1/row.json"]) == "A+C"
    assert CA.detect_form(["eval_log.jsonl"]) == "?"


def test_opt_keep_iters_terminal_every_and_extra_clamped():
    """关键轮 = 终点 ∪ 每 25 轮 ∪ extra；区间外的 extra 被夹掉（否则 manifest 声称留了不存在的轮）。"""
    got = CA.opt_keep_iters((100, 110), extra=[105, 999])
    assert 110 in got and 100 in got and 105 in got
    assert 999 not in got
    assert got == {100, 105, 110}
    # 每 25 轮
    assert CA.opt_keep_iters((1, 60), every=25, extra=[]) == {50, 25, 60}
    # every<=0 退化为「只留终点」，不炸
    assert CA.opt_keep_iters((5, 9), every=0, extra=[]) == {9}


@pytest.mark.parametrize(
    ("rel", "size", "action", "reason", "compress"),
    [
        # L1 语料：A 与 B **两套布局都要删**
        ("it180/dist/mac/rl_s2000_seed1/obs.npy", 10, "delete", "L1-corpus", False),
        ("it152/w7/rl_s2000_seed9/obs.npy", 10, "delete", "L1-corpus", False),
        # L0' offline 三件套（不是 L2！）：weights 有归档 ⇒ 删；opt 只留关键轮；row 留且压
        ("remote-jobs/offline/r1/it-100/weights.json", 5, "delete", "L0'-dup-weights", False),
        ("remote-jobs/offline/r1/it-100/opt.tar", 5, "keep", "L0'-opt-key", False),
        ("remote-jobs/offline/r1/it-101/opt.tar", 5, "delete", "L0'-opt-nonkey", False),
        ("remote-jobs/offline/r1/it-101/row.json", 5, "keep", "L0'-row", True),
        ("remote-jobs/offline/r1/run.json", 5, "keep", "L0'-run", True),
        # L2：deliver 是同一批三件套的镜像；job staging；junk
        ("deliver/r1/it-100/weights.json", 5, "delete", "L2-deliver-mirror", False),
        ("remote-jobs/abc123/payload.tar.xz", 5, "delete", "L2-job-staging", False),
        ("judge-414000-it100.console.log", 5, "delete", "L2-junk", False),
        ("judge-414000-it100.jsonl.run/x.json", 5, "delete", "L2-junk", False),
        ("stale-packs/y.zip", 5, "delete", "L2-junk", False),
        ("task-x20-demo-mix.zip", 5, "delete", "L2-junk", False),
        ("evalA-tmp/z", 5, "delete", "L2-junk", False),
        (".pool-actuals-cache.json", 5, "delete", "L2-junk", False),
        # ckpt 只留关键轮
        ("it100/ppo_ckpt_remote.tar", 5, "keep", "L0-ckpt-key", False),
        ("it101/ppo_ckpt_remote/opt.pt", 5, "delete", "L0-ckpt-nonkey", False),
        # L0 文本件
        ("eval_log.jsonl", 5, "keep", "L0", True),
        ("training_log.jsonl", 5, "keep", "L0", True),
        ("weights.json", 5, "keep", "L0", True),
        ("dist-agent-meta.jsonl", 5, "keep", "L0", True),
        ("judge-414000-it100.jsonl", 5, "keep", "L0", True),
        ("settle/backtest-416000-it230.jsonl", 5, "keep", "L0", True),
        ("it100/metrics_stats.jsonl", 5, "keep", "L0", True),
        ("it100/per-game.json", 5, "keep", "L0", True),
        # 兜底：认不出的**保守保留**（旧 plan 的清单会漏 settle/ 这类未列项）
        ("weird-new-artifact.bin", 5, "keep", "unknown-keep", False),
    ],
)
def test_classify_table(rel, size, action, reason, compress):
    op = CA.classify(rel, size, keep_shards=False, key_iters={100})
    assert (op.action, op.reason, op.compress) == (action, reason, compress)


def test_classify_keep_shards_flips_only_corpus():
    """L3：声明后语料改保留（且**不压**——npy 要 mmap）；其余归属不变。"""
    for rel in ("it180/dist/mac/x/a.npy", "it152/w7/x/a.npy"):
        op = CA.classify(rel, 5, keep_shards=True, key_iters=set())
        assert (op.action, op.reason, op.compress) == ("keep", "L3-shards", False)
    op = CA.classify("deliver/r/it-1/weights.json", 5, keep_shards=True, key_iters=set())
    assert op.action == "delete"


def test_archive_plan_derives_key_iters_from_judge_points():
    """`key_iters=None` ⇒ 由条目推导：it 区间 ∪ 终点 ∪ 每 25 轮 ∪ 判决/回测点。"""
    entries = [
        ("it100/dist/mac/x/a.npy", 1),
        ("it110/ppo_ckpt_remote.tar", 1),
        ("it110/ppo_ckpt_remote/opt.pt", 1),
        ("judge-414000-it105.jsonl", 1),
        ("settle/backtest-416000-it108.jsonl", 1),
    ]
    ops = {o.rel: o for o in CA.archive_plan(entries)}
    assert ops["judge-414000-it105.jsonl"].reason == "L0"
    # 110 是终点（in）、100 是区间起点（in）、105/108 是判决/回测点（in）
    assert ops["it110/ppo_ckpt_remote.tar"].reason == "L0-ckpt-key"
    assert CA.judge_backtest_iters([r for r, _ in entries]) == {105, 108}


# ────────────────────────── 夹具 ──────────────────────────


class Course:
    """一门合成课程的活体目录（形态由 `form` 决定）。"""

    def __init__(self, root: Path, name: str, form: str = "A") -> None:
        self.traj = root / name
        self.traj.mkdir(parents=True, exist_ok=True)
        self.archive = root / "archive"
        self.name = name
        if "A" in form:
            self.write("it100/dist/mac/rl_s2000_seed1/obs.npy", b"x" * 4096)
        if "B" in form:
            self.write("it100/w7/rl_s2000_seed2/obs.npy", b"y" * 4096)
        if "C" in form:
            # 用 101/102 而不是 100/101：100 是 25 的倍数、本身就是「每 25 轮」关键轮，
            # 用它会让「非关键轮被删」那条断言实际在验另一件事。
            for it in (101, 102):
                self.write(f"remote-jobs/offline/run1/it-{it:03d}/weights.json", b"w" * 512)
                self.write(f"remote-jobs/offline/run1/it-{it:03d}/opt.tar", b"o" * 512)
                self.write(f"remote-jobs/offline/run1/it-{it:03d}/row.json", b"r" * 128)
                self.write(f"deliver/run1/it-{it:03d}/weights.json", b"w" * 512)
        self.write("eval_log.jsonl", b'{"event":"eval","iter":100}\n' * 40)
        self.write("training_log.jsonl", b'{"event":"iteration","iter":100}\n')
        self.write("weights.json", b'{"w":1}')
        self.write("judge-414000-it100.jsonl", b'{"judge":1}\n')
        self.write("judge-414000-it100.console.log", b"noise")
        self.write("task-fake.zip", b"zip")

    def write(self, rel: str, data: bytes) -> None:
        p = self.traj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def marker(self, fresh: bool = True) -> None:
        self.write("training-enabled.txt", b"on")
        if not fresh:
            old = time.time() - 10 * CA.FRESH_WINDOW_SEC
            for p in self.traj.rglob("*"):
                import os

                os.utime(p, (old, old))

    def run(self, **kw):
        return CA.archive_course(
            self.name,
            traj_root=self.traj.parent,
            archive_root=self.archive,
            keys=kw.pop("keys", {}),
            **kw,
        )


# ────────────────────────── 闸与顺序 ──────────────────────────


def test_in_training_course_refused_and_zero_change(tmp_path):
    """在训（marker + 新鲜）⇒ 拒绝，**目录零变化**；`--force` 也不能越过新鲜 marker。"""
    c = Course(tmp_path, "x-live", "A")
    c.marker(fresh=True)
    before = sorted(p.relative_to(c.traj).as_posix() for p in c.traj.rglob("*"))

    rep = c.run(dry_run=False, now=time.time())
    assert rep.refused and "在训" in rep.refused
    assert not rep.ops  # 连清单都不生成
    assert not (c.archive / c.name).exists()

    rep_f = c.run(dry_run=False, force=True, now=time.time())
    assert rep_f.refused and "在训" in rep_f.refused
    after = sorted(p.relative_to(c.traj).as_posix() for p in c.traj.rglob("*"))
    assert before == after


def test_stale_marker_needs_force(tmp_path):
    """陈旧 marker：无 `--force` 拒绝（宁可不做），有 `--force` 才继续。"""
    c = Course(tmp_path, "x-stale", "A")
    c.marker(fresh=False)
    far = time.time() + 10**6

    assert "陈旧" in c.run(dry_run=False, now=far).refused
    assert c.traj.is_dir()  # 未动

    rep = c.run(dry_run=False, force=True, now=far)
    assert rep.ok and not rep.refused


def test_dry_run_zero_write_zero_delete(tmp_path):
    """`--dry-run`（默认）⇒ 零写零删，且给出字节账。"""
    c = Course(tmp_path, "x-dry", "A")
    before = sorted(p.relative_to(c.traj).as_posix() for p in c.traj.rglob("*"))

    rep = CA.archive_course(
        c.name, traj_root=c.traj.parent, archive_root=c.archive, dry_run=True, keys={}
    )
    assert rep.ok and rep.dry_run
    assert rep.bytes_freed > 0
    assert not c.archive.exists()  # 没建档案
    after = sorted(p.relative_to(c.traj).as_posix() for p in c.traj.rglob("*"))
    assert before == after


def test_verify_failure_leaves_source_untouched(tmp_path, monkeypatch):
    """⑤ 校验失败 ⇒ 源目录**原样**（最贵的错误是删了才发现没搬成）。"""
    c = Course(tmp_path, "x-verify", "A")
    before = sorted(p.relative_to(c.traj).as_posix() for p in c.traj.rglob("*"))

    monkeypatch.setattr(CA, "read_gz_aware", lambda _p: b"corrupted")
    rep = c.run(dry_run=False)
    assert rep.verify_failed and not rep.ok
    after = sorted(p.relative_to(c.traj).as_posix() for p in c.traj.rglob("*"))
    assert before == after
    # 源里的语料也一个没删
    assert (c.traj / "it100/dist/mac/rl_s2000_seed1/obs.npy").exists()


# ────────────────────────── 压缩与索引 ──────────────────────────


@pytest.mark.parametrize("codec", ["gzip", "xz", "none"])
def test_archive_roundtrip_byte_identical(tmp_path, codec):
    """解压后与源**逐字节**一致（G5 的根）；语料/junk 已删，marker 已消失。"""
    c = Course(tmp_path, "x-rt", "A")
    originals = {
        p.relative_to(c.traj).as_posix(): p.read_bytes() for p in c.traj.rglob("*") if p.is_file()
    }

    rep = c.run(dry_run=False, codec=codec)
    assert rep.ok, rep.summary()

    dest = c.archive / c.name
    for a in rep.artifacts:
        stored = dest / a["stored"]
        assert stored.is_file(), a
        if codec == "none":
            assert stored.read_bytes() == originals[a["rel"]]
        else:
            assert CA.read_gz_aware(stored) == originals[a["rel"]], a["rel"]

    # L1 语料释放了
    assert not (c.traj / "it100/dist").exists()
    # ⑦ 退出活体
    assert not (c.traj / "training-enabled.txt").exists()
    assert not c.traj.exists()


def test_manifest_and_archive_md_never_compressed(tmp_path):
    """索引两件是控制台读面 ⇒ 永不压缩（零解压成本）。"""
    c = Course(tmp_path, "x-idx", "C")
    rep = c.run(dry_run=False, codec="gzip")
    assert rep.ok, rep.summary()
    dest = c.archive / c.name
    assert (dest / "archive-manifest.json").is_file()
    assert (dest / "ARCHIVE.md").is_file()
    for p in dest.iterdir():
        if p.name in ("archive-manifest.json", "ARCHIVE.md"):
            # 未压缩：文件头不是 gzip/xz magic
            head = p.read_bytes()[:6]
            assert head[:2] != b"\x1f\x8b" and head[:6] != b"\xfd7zXZ\x00"

    m = json.loads((dest / "archive-manifest.json").read_text(encoding="utf-8"))
    assert m["course"] == "x-idx"
    assert m["form"] == "C"
    assert m["codec"] == "gzip"
    assert m["reads"]["eval_log"] == "eval_log.jsonl.gz"
    assert m["shards_kept"] is False
    # 起点指向**归档**（nn-training/weights/），不指 tmp
    assert all(w["src"] == "archive" for w in m["weights"])


def test_offline_form_keeps_row_only_at_key_rounds(tmp_path):
    """C 形态：`row.json` 全留、`opt.tar` 只留关键轮、`weights.json` 与 `deliver/` 删。"""
    c = Course(tmp_path, "x-off", "C")
    rep = c.run(dry_run=False, keys={})
    kept = {o.rel for o in rep.ops if o.action == "keep"}
    deleted = {o.rel for o in rep.ops if o.action == "delete"}

    # 101/102 两轮；终点 102 是关键轮、101 不是 ⇒ 只有 102 的 opt 留
    assert "remote-jobs/offline/run1/it-102/opt.tar" in kept
    assert "remote-jobs/offline/run1/it-101/opt.tar" in deleted
    assert "remote-jobs/offline/run1/it-102/row.json" in kept
    assert "remote-jobs/offline/run1/it-101/row.json" in kept
    for it in (101, 102):
        assert f"remote-jobs/offline/run1/it-{it:03d}/weights.json" in deleted
        assert f"deliver/run1/it-{it:03d}/weights.json" in deleted


def test_keep_shards_declaration_keeps_corpus(tmp_path):
    """课程声明 `archive_keep_shards` ⇒ 语料保留（两种布局都覆盖）。"""
    c = Course(tmp_path, "x-shards", "A+B")
    rep = c.run(dry_run=False, keys={"archive_keep_shards": True})
    assert rep.shards_kept
    kept = {o.rel for o in rep.ops if o.action == "keep"}
    assert "it100/dist/mac/rl_s2000_seed1/obs.npy" in kept
    assert "it100/w7/rl_s2000_seed2/obs.npy" in kept
    # 保留 ≠ 删除：原件仍在（封存后整目录移走前，档案里也有一份）
    dest = c.archive / c.name
    assert (dest / "it100/dist/mac/rl_s2000_seed1/obs.npy").is_file()


def test_keep_shards_manifest_holds_aggregate_not_per_file(tmp_path):
    """L3 保留 shards 时 manifest **不**逐件记 sha：一门课两千多个 npy，逐件进去会让
    manifest 变几百 KB，而控制台每拍都要 `JSON.parse` 它（§R5）。

    完整性改在**拷贝当场**对流校验（源流 sha == 目标流 sha）——逐件保真不变，只是
    不进索引。
    """
    c = Course(tmp_path, "x-shards2", "A")
    rep = c.run(dry_run=False, keys={"archive_keep_shards": True})
    assert rep.ok, rep.summary()
    m = json.loads((c.archive / c.name / "archive-manifest.json").read_text(encoding="utf-8"))
    rels = [a["rel"] for a in m["artifacts"]]
    assert not any("/dist/" in r for r in rels), "语料不该逐件进 manifest"
    assert m["shards"]["files"] >= 1 and m["shards"]["bytes"] > 0
    # 区间仍由保留项（含 shard 目录）算出——不是 (0,0)
    assert m["it_range"][1] == 100
    assert (c.archive / c.name / "it100/dist/mac/rl_s2000_seed1/obs.npy").is_file()


def test_second_run_is_safe_noop(tmp_path):
    """二次封存：活体已移走 ⇒ 拒绝且**不动档案**（不回滚、不重搬）。"""
    c = Course(tmp_path, "x-twice", "A")
    first = c.run(dry_run=False)
    assert first.ok
    manifest_before = (c.archive / c.name / "archive-manifest.json").read_text(encoding="utf-8")

    second = c.run(dry_run=False)
    assert second.refused and "不存在" in second.refused
    assert (c.archive / c.name / "archive-manifest.json").read_text(
        encoding="utf-8"
    ) == manifest_before


def test_compression_is_deterministic():
    """gzip 的 mtime 钉 0 ⇒ 同一输入两次压出**同字节**（档案 diff 不抖）。"""
    data = b'{"a":1}\n' * 100
    assert CA.compress_bytes(data, "gzip") == CA.compress_bytes(data, "gzip")
    assert CA.compress_bytes(data, "xz") == CA.compress_bytes(data, "xz")
    assert len(CA.compress_bytes(data, "gzip")) < len(data)


# ────────────────────────── 静态检查 ──────────────────────────


def test_no_bare_shutil_rmtree_call():
    """删除纪律：模块里不得出现**裸 `shutil.rmtree(` 调用**（沙箱会打死线程）。

    只查调用（注释/文档里的名字是说明，不是违规）。
    """
    src = (NN_ROOT / "rl" / "course_archive.py").read_text(encoding="utf-8")
    assert not re.search(r"\bshutil\.rmtree\s*\(", src)
    assert "rmtree_best_effort" in src
