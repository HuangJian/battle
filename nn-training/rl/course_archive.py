"""rl/course_archive.py — 课程封存：把已停课程从 `tmp/<课>/` 搬成只读档案。

设计 = `plan/course-archive.plan.md`（基线实测 `docs/nn/course-archive.md §1`）。

**三层目录语义**（§3.1）：活体 `tmp/<课>/`（在训期间全量）· 权重归档
`nn-training/weights/<课>/`（逐轮，本模块**只读**）· 封存 `archive/courses/<课>/`（只读，
本模块唯一写者）。封存目录里**只有两件进 git**（`ARCHIVE.md` + `archive-manifest.json`，
都不压缩），其余留本地。

**保留集按课程形态**（§3.2，不是文件名清单）——实测三形态：

| 形态 | 判据 | 语料位置 |
|---|---|---|
| A 本地-旧 | `it<N>/dist/` | `it<N>/dist/<node>/rl_s<stage>_seed<n>/*.npy` |
| B 本地-新 | `it<N>/w<id>/` | `it<N>/w<id>/rl_s<stage>_seed<n>/*.npy` |
| C offline 回传 | `deliver/` + `remote-jobs/offline/` | 无 it 语料；三件套在 `<run>/it-NNN/` |

**顺序即契约**（§3.3）：① 闸 → ② 扫/清单 → ③ dry-run 退出 → ④ 建（copy+gzip）→
⑤ 校验（**解压后** sha）→ ⑥ 删 delete 项 → ⑦ 退出活体（删 marker + 移走剩余空壳）。
④⑤ 任一步失败 ⇒ 源目录原样不动（最贵的错误是「删了才发现没搬成」）。

**压缩**（`DECISIONS §2026-09-26-course-archive-compress`）：默认 gzip -6 作用于 L0
**文本件**；`archive-manifest.json` / `ARCHIVE.md` / `.npy` shards / `*.tar` 永不压缩。
manifest 的 sha256 一律算**解压后的原字节**（否则换机重压 ⇒ 校验仪式失效）。

**删除纪律**：一律 `platform_utils.rmtree_best_effort`（裸 `shutil.rmtree` 会被沙箱
删除保护 shim 打死线程——2026-09-10 门禁事故）；本模块不得 import 训练热路径。

**幂等**：二次封存（活体已移走）⇒ 拒绝、档案逐字节不动；中断重跑（活体仍在、目标已存在）
⇒ 逐件重搬并覆盖同路径——压缩件**确定性**（gzip `mtime` 钉 0）⇒ 结果一致。
"""

from __future__ import annotations

import argparse
import gzip
import json
import lzma
import os
import re
import shutil
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.hashing import sha256_bytes, sha256_file  # re-export：唯一实现在 common/hashing.py
from platform_utils import rmtree_best_effort

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRAJ_ROOT = REPO_ROOT / "tmp"
DEFAULT_ARCHIVE_ROOT = REPO_ROOT / "archive" / "courses"
CURRICULA_DIR = REPO_ROOT / "nn-training" / "curricula"

#: 开课标记（与训练侧 `loop_plan.enabled_courses` / hub `_course_dir_live` **同一个**闸）。
COURSE_ENABLE_MARKER = "training-enabled.txt"
#: 「新鲜」窗口（秒）——与 `remote/hub/queue_discover.DISCOVER_FRESH_SEC` 同口径。
FRESH_WINDOW_SEC = 3600.0
#: opt/ckpt 的「关键轮」默认间隔（§3.2）。
DEFAULT_OPT_KEY_EVERY = 25
#: 「关键轮」保留理由的两族（C 形态 offline opt.tar / A/B 形态 ppo_ckpt_remote.tar）。
#: manifest 的 `keys.opt_key_iters` 与 `weights[]` 必须取**并集**——只认 C 族会让 A/B 课
#: `weights[]` 恒空（2026-09-26 评审：G4-① 的起点入口对多数课拿不到东西）。
_KEY_REASONS = ("L0'-opt-key", "L0-ckpt-key")
#: 逐轮权重归档根（与 `rl/archive.py::WEIGHTS_BACKUP_DIR` 同路径；本模块**只读**它）。
WEIGHTS_ARCHIVE_DIR = REPO_ROOT / "nn-training" / "weights"
#: 归档件名：`<prefix>.it<N>.<YYYYMMDD-HHMMSS>.json`（`rl/archive.py::backup_weights`）
_WEIGHT_ARCH_RE = re.compile(r"^.+\.it(\d+)\.\d{8}-\d{6}\.json$")

# ────────────────────────── 路径判据（纯正则，无 IO） ──────────────────────────

_IT_RE = re.compile(r"^it(\d+)/")
#: A 形态语料：`it<N>/dist/**`
_CORPUS_OLD_RE = re.compile(r"^it\d+/dist/")
#: B 形态语料：`it<N>/w<id>/**`（长驻池/新布局，2026-09-26 实测存在于 `x20-state-init`）
_CORPUS_NEW_RE = re.compile(r"^it\d+/w\d+/")
_IT_DIR_RE = re.compile(r"^it\d+/")
#: C 形态三件套：`remote-jobs/offline/<run>/it-NNN/<file>`
_OFFLINE_IT_RE = re.compile(r"^remote-jobs/offline/([^/]+)/it-(\d+)/(.+)$")
#: 远程 PPO 的 model+opt（`ppo_ckpt_remote.tar` 与解出的目录）
_PPO_RE = re.compile(r"^it\d+/ppo_ckpt_remote(?:\.tar|/)")
#: 判决/回测原始行（**两套布局都存在**：课程根与 `settle/`）
_JUDGE_RE = re.compile(r"^judge-\d+-it(\d+)\.jsonl$")
_BACKTEST_RE = re.compile(r"^backtest-\d+-it(\d+)\.jsonl$")
_LINES_RE = re.compile(r"^(?:judge|backtest)-\d+-it\d+\.jsonl$")
#: settle 目录（`settle-re/` 不存在——那是旧 plan 的错记）
_SETTLE_PREFIX = "settle/"
#: L0 根层件
_L0_ROOT = frozenset(
    {
        "eval_log.jsonl",
        "training_log.jsonl",
        "weights.json",
        "dist-agent-meta.jsonl",
        "commit_journal.jsonl",
    }
)
#: L2 junk（一次性/可重建）：`.console.log`、`*.jsonl.run/`、evalA、stale-packs、task zip、池缓存
_JUNK_SUFFIX_RE = re.compile(r"\.console\.log$")
_JUNK_PREFIX_RE = re.compile(r"^(?:evalA-tmp|stale-packs|evalA\.log|\.pool-actuals-cache\.json)")
_JSONL_RUN_RE = re.compile(r"\.jsonl\.run(?:/|$)")
_TASK_ZIP_RE = re.compile(r"^task-.*\.zip$")

#: 压缩目标后缀（文本件；`.tar` 实测只 1.4×，不压）
_TEXT_SUFFIXES = (".jsonl", ".json", ".log", ".txt", ".md", ".csv")


def _iter_of(rel: str) -> int | None:
    """从 `it<N>/…` 取轮号。"""
    m = _IT_RE.match(rel)
    return int(m.group(1)) if m else None


def _any_iter_of(rel: str) -> int | None:
    """从**任一轮目录形态**取轮号。

    为什么不能只看 `it<N>/`：C 形态（offline 回传）**没有** `it<N>/`，它的轮是
    `remote-jobs/offline/<run>/it-NNN/`——只看前者会让整个 C 课的 it 区间退化成 `(0,0)`，
    关键轮集合全空 ⇒ `opt.tar` 全删（正是「续跑锚点没了」那类不可逆错误）。
    """
    it = _iter_of(rel)
    if it is not None:
        return it
    m = _OFFLINE_IT_RE.match(rel)
    return int(m.group(2)) if m else None


def is_corpus(rel: str) -> bool:
    """是否语料 shard（A 或 B 形态的 `dist/` / `w<id>/`）。"""
    return bool(_CORPUS_OLD_RE.match(rel) or _CORPUS_NEW_RE.match(rel))


def is_text(rel: str) -> bool:
    """是否可压缩的文本件（语料 shard 一律不算——npy 要 mmap）。"""
    if is_corpus(rel):
        return False
    return rel.endswith(_TEXT_SUFFIXES)


def detect_form(rels: Iterable[str]) -> str:
    """课程形态（§3.2）：`A` / `B` / `C` 及其组合（`A+B`、`A+C`…）；认不出 ⇒ `?`。

    形态是**同课可混合**的（实测 `c6-chip` = A+B、`x20-dodge-l3` = A+C），所以返回组合串
    而不是单值——保留集规则要能同时命中两套布局。
    """
    forms: set[str] = set()
    for rel in rels:
        if _CORPUS_OLD_RE.match(rel):
            forms.add("A")
        elif _CORPUS_NEW_RE.match(rel):
            forms.add("B")
        if rel.startswith("deliver/") or rel.startswith("remote-jobs/offline/"):
            forms.add("C")
    return "+".join(sorted(forms)) if forms else "?"


def opt_keep_iters(
    it_range: tuple[int, int], *, every: int = DEFAULT_OPT_KEY_EVERY, extra: Iterable[int] = ()
) -> set[int]:
    """opt/ckpt 的**关键轮**集合 = 终点 ∪ 每 `every` 轮 ∪ `extra`（判决/回测点），并夹到区间内。

    为什么要夹：`extra` 里的判决点可能落在 `it_range` 之外（旧课重跑 / 手工补档），
    放任它们会让 manifest 声称保留了不存在的轮次。
    """
    lo, hi = it_range
    if hi < lo:
        return set()
    out = {hi}
    step = int(every)
    if step > 1:
        # every<=1 不是「每轮」而是「不采样」（只留终点 + extra）：`--every 1` 把整段
        # 都标成关键轮等于没筛，而 opt/ckpt 的本意就是省空间。
        out.update(it for it in range(lo, hi + 1) if it % step == 0)
    out.update(int(x) for x in extra)
    return {it for it in out if lo <= it <= hi}


@dataclass(frozen=True)
class ArchiveOp:
    """一条封存决定：保留（`keep`）或删除（`delete`），带理由与字节数。"""

    rel: str
    action: str  # "keep" | "delete"
    reason: str
    compress: bool
    size: int


def classify(rel: str, size: int, *, keep_shards: bool, key_iters: set[int]) -> ArchiveOp:
    """单件的归属判据（§3.2 表）。**顺序即语义**：语料 → offline 三件套 → 镜像 → junk →
    job staging → ckpt → L0 → 兜底。

    兜底是**保守保留**（不是删）：认不出的东西宁可进档案也不要丢——旧 plan 按文件名清单
    分类，实测会漏 `evalA.log` / `dist-agent-meta.jsonl` / `settle/` 这类未列项。
    """
    if is_corpus(rel):
        if keep_shards:
            return ArchiveOp(rel, "keep", "L3-shards", False, size)
        return ArchiveOp(rel, "delete", "L1-corpus", False, size)

    if rel.startswith("remote-jobs/offline/"):
        # ★ offline 段是 **L0'**，不是 L2：`weights.json` 不必留（逐轮权重已在
        # nn-training/weights/<课>/，实测 301 份 ↔ tmp 301 轮一一对应），`opt.tar` 是
        # Adam 动量（tmp 独有的部分）⇒ 只留关键轮；`row.json` 全留。
        m = _OFFLINE_IT_RE.match(rel)
        if m is not None:
            it_n = int(m.group(2))
            name = m.group(3)
            if name == "weights.json":
                return ArchiveOp(rel, "delete", "L0'-dup-weights", False, size)
            if name == "opt.tar":
                if it_n in key_iters:
                    return ArchiveOp(rel, "keep", "L0'-opt-key", False, size)
                return ArchiveOp(rel, "delete", "L0'-opt-nonkey", False, size)
            if name == "row.json":
                return ArchiveOp(rel, "keep", "L0'-row", True, size)
        # run.json / metrics.jsonl / result.json：段级 provenance 账（run_id/plan_sha256/commit），
        # 小且审计要用——留。
        return ArchiveOp(rel, "keep", "L0'-run", is_text(rel), size)

    if rel.startswith("deliver/"):
        # 与 remote-jobs/offline/ 是同一批三件套的镜像（store_offline ① 交付镜像 ②主份）
        return ArchiveOp(rel, "delete", "L2-deliver-mirror", False, size)

    if (
        _JUNK_SUFFIX_RE.search(rel)
        or _JUNK_PREFIX_RE.match(rel)
        or _JSONL_RUN_RE.search(rel)
        or _TASK_ZIP_RE.match(rel)
    ):
        return ArchiveOp(rel, "delete", "L2-junk", False, size)

    if rel.startswith("remote-jobs/"):
        return ArchiveOp(rel, "delete", "L2-job-staging", False, size)

    if _PPO_RE.match(rel):
        it_opt = _any_iter_of(rel)
        if it_opt is not None and it_opt in key_iters:
            return ArchiveOp(rel, "keep", "L0-ckpt-key", False, size)
        return ArchiveOp(rel, "delete", "L0-ckpt-nonkey", False, size)

    base = rel.rsplit("/", 1)[-1]
    if base in _L0_ROOT or rel.startswith(_SETTLE_PREFIX) or _LINES_RE.match(base):
        return ArchiveOp(rel, "keep", "L0", is_text(rel), size)
    if _IT_DIR_RE.match(rel) and base in ("metrics_stats.jsonl", "per-game.json"):
        return ArchiveOp(rel, "keep", "L0", True, size)

    return ArchiveOp(rel, "keep", "unknown-keep", is_text(rel), size)


def judge_backtest_iters(rels: Iterable[str]) -> set[int]:
    """从判决/回测行文件名里抽 it（`judge-<stage>-it<N>.jsonl`）。

    只看 basename：两套布局（课程根与 `settle/`）的同名形态都能命中。
    """
    out: set[int] = set()
    for rel in rels:
        base = _base(rel)
        m = _JUDGE_RE.match(base) or _BACKTEST_RE.match(base)
        if m:
            out.add(int(m.group(1)))
    return out


def _base(rel: str) -> str:
    """相对路径 → basename（走查两套布局用）。"""
    return rel.rsplit("/", 1)[-1]


def archive_plan(
    entries: Sequence[tuple[str, int]],
    *,
    keep_shards: bool = False,
    opt_key_every: int = DEFAULT_OPT_KEY_EVERY,
    key_iters: Iterable[int] | None = None,
) -> list[ArchiveOp]:
    """清单（§3.2）。纯函数：输入 `(相对路径, 字节)` 序列，输出 `ArchiveOp` 列表。

    `key_iters=None`（缺省）⇒ 由条目自行推导：it 区间 ∪ 终点 ∪ 每 `opt_key_every` 轮 ∪
    判决/回测点。显式给 `key_iters` 则原样使用（测试与复算用）。
    """
    rels = [rel for rel, _ in entries]
    if key_iters is None:
        its = [it for it in (_any_iter_of(r) for r in rels) if it is not None]
        it_range = (min(its), max(its)) if its else (0, 0)
        derived = opt_keep_iters(it_range, every=opt_key_every, extra=judge_backtest_iters(rels))
    else:
        derived = set(key_iters)
    return [
        classify(rel, size, keep_shards=keep_shards, key_iters=derived) for rel, size in entries
    ]


# ────────────────────────── 压缩 / 哈希（纯函数） ──────────────────────────

CODECS = ("gzip", "xz", "none")
_EXT = {"gzip": ".gz", "xz": ".xz", "none": ""}


def stored_rel(rel: str, codec: str, *, compress: bool) -> str:
    """封存目录内的相对路径（压缩件 = 原名 + 扩展）。"""
    return rel + (_EXT[codec] if compress and codec != "none" else "")


def compress_bytes(data: bytes, codec: str) -> bytes:
    """压缩（**确定性**：gzip 的 mtime 钉 0，否则每次重压字节都不同 ⇒ 档案 diff 抖）。"""
    if codec == "gzip":
        return gzip.compress(data, compresslevel=6, mtime=0)
    if codec == "xz":
        return lzma.compress(data, preset=3)  # preset 3：§B6 已量，不采用 6
    if codec == "none":
        return data
    raise ValueError(f"未知 codec: {codec!r}（可选 {CODECS}）")


def decompress_bytes(data: bytes, codec: str) -> bytes:
    if codec == "gzip":
        return gzip.decompress(data)
    if codec == "xz":
        return lzma.decompress(data)
    if codec == "none":
        return data
    raise ValueError(f"未知 codec: {codec!r}")


def read_gz_aware(path: Path) -> bytes:
    """读一个可能被压缩的文本件（按 magic 判别，照 `remote/protocol.py` 先例）。"""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    if raw[:6] == b"\xfd7zXZ\x00":
        return lzma.decompress(raw)
    return raw


# ────────────────────────── 闸 / 扫描 ──────────────────────────


def _newest_mtime(paths: Iterable[Path]) -> float:
    newest = 0.0
    for p in paths:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    return newest


def course_guard(
    traj: Path,
    course: str,
    *,
    force: bool = False,
    now: float | None = None,
    window: float = FRESH_WINDOW_SEC,
) -> str:
    """在训硬闸。返回拒绝理由；空串 = 可封存。

    ★ `--force` **只**允许越过陈旧 marker（目录已不新鲜、无在训进程）——
    越过「新鲜 marker」等于把正在训练的目录搬走（2026-09-20 事故那一类），一律拒绝。
    """
    if not traj.is_dir():
        return f"课程目录不存在：{traj}"
    # ★ 新鲜度是**与 marker 无关的第二道闸**（2026-09-26 评审实测洞）：停课的动作就是删
    #   marker，但它**不杀循环**、不等在飞的那一轮——刚停完课的那几秒/几分钟里目录仍在被
    #   写。旧实现「无 marker 直接放行」正落到这个窗口：搬到撕裂的树、再 best-effort 删活体。
    #   故：新鲜 ⇒ 一律拒（不论 marker 在不在）；`--force` 仍是逃生阀，但**越不过
    #   「新鲜 + marker 在」**那一种（那是最像在训的形状）。
    has_marker = (traj / COURSE_ENABLE_MARKER).exists()
    t_now = time.time() if now is None else now
    newest = _newest_mtime(
        [
            traj,
            traj / "training_log.jsonl",
            traj / "remote-jobs",
            traj / "offline",
            traj / "deliver",
            traj / f"task-{course}.zip",
        ]
    )
    fresh = newest > 0 and (t_now - newest) <= window
    if has_marker and fresh:
        return (
            f"课程 {course} 在训（{COURSE_ENABLE_MARKER} 存在且目录新鲜）——拒绝封存；"
            "先停课（控制台「停课」删 marker）再跑。"
        )
    if has_marker and not force:
        return f"课程 {course} 残留开课标记（已陈旧）——确认它确实不在训练时用 --force 重跑。"
    if fresh and not force:
        age = max(0, int(t_now - newest))
        return (
            f"课程 {course} 目录 {age} 秒前刚被写过（**无开课标记**）——可能仍有循环在收尾"
            "或云机在回传；等它停稳（或确认已停）后用 --force 重跑。"
        )
    return ""


def scan_course(traj: Path) -> list[tuple[str, int]]:
    """递归扫课程目录 → `[(相对路径(posix), 字节)]`（符号链接跳过：别把别人的盘拖进档案）。"""
    out: list[tuple[str, int]] = []
    for root, dirs, files in os.walk(traj, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(root) / d).is_symlink()]
        for name in files:
            p = Path(root) / name
            if p.is_symlink():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append((p.relative_to(traj).as_posix(), size))
    out.sort(key=lambda e: e[0])
    return out


def read_course_keys(course: str) -> dict[str, Any]:
    """课程声明（`curricula/<课>.jsonc` 或 `<课>.bc.jsonc`）；读不到 ⇒ `{}`。

    只读三个可选键：`archive_keep_shards`（L3）、`parent`（manifest 的父臂）、`verdict`。
    **不强制任何现有课程改文件**（缺省 = 不留 shards）。
    读法照 `remote/hub/store_offline._course_backup_target`（同一份课程文件、同样的两个名字）。
    """
    from rl.jsonc import strip_comments

    for name in (f"{course}.jsonc", f"{course}.bc.jsonc"):
        p = CURRICULA_DIR / name
        if not p.is_file():
            continue
        try:
            doc = json.loads(strip_comments(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return doc
    return {}


# ────────────────────────── 报告 ──────────────────────────


@dataclass
class ArchiveReport:
    """一次封存的结果。`refused` 非空 ⇒ 什么都没做（闸拒绝）。"""

    course: str
    form: str = "?"
    dry_run: bool = True
    codec: str = "gzip"
    refused: str = ""
    ops: list[ArchiveOp] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    verify_failed: list[str] = field(default_factory=list)
    delete_failed: list[str] = field(default_factory=list)
    archive_dir: str = ""
    manifest_path: str = ""
    bytes_freed: int = 0
    bytes_kept_stored: int = 0
    bytes_kept_raw: int = 0
    shards_kept: bool = False
    #: L3 语料 shard 的聚合（不逐件进 manifest——那会让控制台每拍解析几百 KB）。
    shard_files: int = 0
    shard_bytes: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused and not self.verify_failed

    def summary(self) -> str:
        if self.refused:
            return f"✗ 拒绝：{self.refused}"
        head = "DRY-RUN" if self.dry_run else "封存"
        kept = sum(1 for o in self.ops if o.action == "keep")
        dele = sum(1 for o in self.ops if o.action == "delete")
        lines = [
            f"[{head}] {self.course} 形态={self.form} codec={self.codec}",
            f"  保留 {kept} 件 / {_mb(self.bytes_kept_raw)} → {_mb(self.bytes_kept_stored)}（已存）",
            f"  删除 {dele} 件 / 释放 {_mb(self.bytes_freed)}",
        ]
        if self.archive_dir:
            lines.append(f"  档案：{self.archive_dir}")
        if self.verify_failed:
            lines.append(f"  ✗ 校验失败（源未动）：{self.verify_failed[:3]}")
        if self.delete_failed:
            lines.append(f"  ⚠ 删不完（best-effort 记录）：{self.delete_failed[:3]}")
        lines.extend(f"  · {m}" for m in self.messages)
        return "\n".join(lines)


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


# ────────────────────────── 主流程 ──────────────────────────


def _delete_path(path: Path, *, ignore_errors: bool = True) -> bool:
    """删一个文件或目录（沙箱纪律：目录走 `rmtree_best_effort`，文件吞 SystemExit）。"""
    if path.is_dir() and not path.is_symlink():
        return rmtree_best_effort(path, ignore_errors=ignore_errors)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except SystemExit:  # 沙箱删除保护：best-effort，记录而非失败
        return False
    except OSError:
        if ignore_errors:
            return False
        raise


def archive_course(
    course: str,
    *,
    traj_root: Path | str = DEFAULT_TRAJ_ROOT,
    archive_root: Path | str = DEFAULT_ARCHIVE_ROOT,
    force: bool = False,
    dry_run: bool = True,
    codec: str = "gzip",
    now: float | None = None,
    log: Callable[[str], None] | None = None,
    keys: dict[str, Any] | None = None,
) -> ArchiveReport:
    """封存一门课（§3.3 七步）。**默认 dry_run**（最贵的错误是「删了才发现没搬成」）。"""
    say = log or (lambda _m: None)
    traj = Path(traj_root) / course
    dest = Path(archive_root) / course
    rep = ArchiveReport(course=course, dry_run=dry_run, codec=codec, archive_dir=str(dest))

    if codec not in CODECS:
        rep.refused = f"未知 codec: {codec!r}"
        return rep

    # ① 闸
    rep.refused = course_guard(traj, course, force=force, now=now)
    if rep.refused:
        return rep

    declared = read_course_keys(course) if keys is None else keys
    keep_shards = bool(declared.get("archive_keep_shards", False))
    rep.shards_kept = keep_shards

    # ② 扫 + 清单
    entries = scan_course(traj)
    rep.form = detect_form([rel for rel, _ in entries])
    ops = archive_plan(entries, keep_shards=keep_shards)
    rep.ops = ops
    rep.bytes_freed = sum(o.size for o in ops if o.action == "delete")
    rep.bytes_kept_raw = sum(o.size for o in ops if o.action == "keep")

    # ③ dry-run 退出（零写零删）
    if dry_run:
        say(rep.summary())
        return rep

    # ④ 建：copy（+压缩）
    #
    # ★ L3 语料 shard **不进 manifest 的逐件 sha 表**：一门课可有两千多个 npy
    #   （实测 x20-state-init 1356 个 w 目录），逐件记进去会让 manifest 变成几百 KB，
    #   而控制台每拍都要 `JSON.parse` 它（§R5）。shard 的完整性在**拷贝当场**对流校验
    #   （源流 sha == 目标流 sha），只把聚合计数写进 manifest。
    artifacts: list[dict[str, Any]] = []
    shard_files = 0
    shard_bytes = 0
    for op in ops:
        if op.action != "keep":
            continue
        src = traj / op.rel
        compress = op.compress and codec != "none"
        srel = stored_rel(op.rel, codec, compress=compress)
        dst = dest / srel
        try:
            if compress:
                raw = src.read_bytes()
                blob = compress_bytes(raw, codec)
                # 写前自检：往返一致才算搬到（否则 sha 记录的语义就假了）
                if decompress_bytes(blob, codec) != raw:
                    rep.verify_failed.append(op.rel)
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(blob)
                sha = sha256_bytes(raw)
                artifacts.append(
                    {
                        "rel": op.rel,
                        "stored": srel,
                        "sha256": sha,
                        "bytes_raw": len(raw),
                        "bytes_stored": len(blob),
                        "codec": codec,
                        "reason": op.reason,
                    }
                )
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if op.reason == "L3-shards":
                    src_sha = sha256_file(src)
                    shutil.copy2(src, dst)
                    if sha256_file(dst) != src_sha:
                        rep.verify_failed.append(op.rel)
                    shard_files += 1
                    shard_bytes += op.size
                    continue
                shutil.copy2(src, dst)
                artifacts.append(
                    {
                        "rel": op.rel,
                        "stored": srel,
                        "sha256": sha256_file(dst),
                        "bytes_raw": op.size,
                        "bytes_stored": op.size,
                        "codec": "none",
                        "reason": op.reason,
                    }
                )
        except OSError as e:
            rep.messages.append(f"搬 {op.rel} 失败（{e}）——中止、源不动")
            rep.verify_failed.append(op.rel)
            return rep

    # ⑤ 校验：逐件解压后 sha 与记录相符
    for a in artifacts:
        p = dest / a["stored"]
        try:
            got = sha256_bytes(read_gz_aware(p))
        except OSError:
            rep.verify_failed.append(a["rel"])
            continue
        if got != a["sha256"]:
            rep.verify_failed.append(a["rel"])
    if rep.verify_failed:
        rep.messages.append("校验失败 ⇒ 源目录原样不动（最贵的错误是删了才发现没搬成）")
        return rep

    rep.bytes_kept_stored = sum(int(a["bytes_stored"]) for a in artifacts) + shard_bytes
    rep.shard_files = shard_files
    rep.shard_bytes = shard_bytes
    rep.artifacts = artifacts

    # 写索引两件（**永不压缩**）
    manifest = _build_manifest(course, rep, artifacts, declared)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "archive-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (dest / "ARCHIVE.md").write_text(_build_archive_md(course, rep, manifest), encoding="utf-8")
    rep.manifest_path = str(dest / "archive-manifest.json")

    # ⑥ 删：只删清单里标记 delete 的项
    for op in ops:
        if op.action != "delete":
            continue
        if not _delete_path(traj / op.rel):
            rep.delete_failed.append(op.rel)

    # ⑦ 退出活体：删 marker + 移走剩余空壳（保留件原件 + 空目录）
    _delete_path(traj / COURSE_ENABLE_MARKER)
    if not rmtree_best_effort(traj, ignore_errors=True):
        rep.delete_failed.append(str(traj))

    say(rep.summary())
    return rep


def _build_manifest(
    course: str,
    rep: ArchiveReport,
    artifacts: list[dict[str, Any]],
    declared: dict[str, Any],
) -> dict[str, Any]:
    """`archive-manifest.json`（§3.4）——控制台**只读它、不扫盘、不解压**。"""
    # 轮次区间取**所有保留项**（不只是逐件 sha 表里的）：L3 保留 shards 时，shard 目录
    # 才是 it 的主要来源，漏了它整门课的 it 区间会退化成 (0, 0)。
    its = [
        it for it in (_any_iter_of(o.rel) for o in rep.ops if o.action == "keep") if it is not None
    ]
    # ★ 关键轮 = **两族理由的并集**（`_KEY_REASONS`）：C 形态的 offline `opt.tar` 与 A/B
    #   形态的 `it<N>/ppo_ckpt_remote.tar`。只认 C 族会让 13 门里多数的 A/B 课 `weights[]`
    #   恒空 ⇒ G4-① 的起点入口拿不到东西（2026-09-26 评审实测）。
    keys = sorted(
        {it for it in (_any_iter_of(o.rel) for o in rep.ops if o.reason in _KEY_REASONS) if it is not None}
    )

    def _its_of(rx: re.Pattern[str]) -> list[int]:
        got = (rx.match(_base(a["rel"])) for a in artifacts)
        return sorted(int(m.group(1)) for m in got if m is not None)

    judge_its = _its_of(_JUDGE_RE)
    backtest_its = _its_of(_BACKTEST_RE)
    reads = {
        "eval_log": stored_rel("eval_log.jsonl", rep.codec, compress=True)
        if any(a["rel"] == "eval_log.jsonl" for a in artifacts)
        else "",
        "train_log": stored_rel("training_log.jsonl", rep.codec, compress=True)
        if any(a["rel"] == "training_log.jsonl" for a in artifacts)
        else "",
        "judge": [a["stored"] for a in artifacts if a["rel"].startswith("judge-")],
        "backtest": [a["stored"] for a in artifacts if a["rel"].startswith("backtest-")],
        "settle": [a["stored"] for a in artifacts if a["rel"].startswith("settle/")],
    }
    active = next((a for a in artifacts if a["rel"] == "weights.json"), None)
    return {
        "course": course,
        "archived_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "parent": str(declared.get("parent", "") or ""),
        "form": rep.form,
        "it_range": [min(its), max(its)] if its else [0, 0],
        "keys": {
            "final_it": max(its) if its else 0,
            "opt_key_iters": keys,
            "judge_iters": judge_its,
            "backtest_iters": backtest_its,
        },
        # ★ 起点指向**归档**（nn-training/weights/<课>/），不指 tmp —— 封存后 tmp 路径不存在
        "weights": [_weight_entry(course, it) for it in keys],
        "active_weights": (
            {
                "path": active["stored"],
                "sha256": active["sha256"],
                "bytes_raw": active["bytes_raw"],
                "bytes_stored": active["bytes_stored"],
            }
            if active
            else None
        ),
        "shards_kept": bool(rep.shards_kept),
        "shards": {"files": rep.shard_files, "bytes": rep.shard_bytes},
        "codec": rep.codec,
        "reads": reads,
        "verdict": str(declared.get("verdict", "") or ""),
        "bytes_total": rep.bytes_kept_stored,
        "bytes_raw_total": rep.bytes_kept_raw,
        "files_total": len(artifacts),
        "artifacts": artifacts,
    }


def resolve_archived_weight(course: str, it: int) -> tuple[str, str, int] | None:
    """在权重归档里解析第 `it` 轮的**具体**归档件 → `(仓根相对路径, sha256, 字节)`。

    同一 it 可重跑多份（文件名带时间戳）⇒ 取**字典序最大**那份（时间戳定宽 ⇒ 序 = 时间序）。
    归档是可选资产（旧课可能没备份、`weights-prune` 可能删过）⇒ 解析不到返回 None，由调用方
    退回目录级 glob 提示；**绝不**编一个 sha256 出来——G4-① 的「sha 可验」要求它指向真实字节。
    """
    d = WEIGHTS_ARCHIVE_DIR / course
    if not d.is_dir():
        return None
    best: Path | None = None
    try:
        for p in d.iterdir():
            m = _WEIGHT_ARCH_RE.match(p.name)
            if m is not None and int(m.group(1)) == it and (best is None or p.name > best.name):
                best = p
    except OSError:
        return None
    if best is None:
        return None
    try:
        return (best.relative_to(REPO_ROOT).as_posix(), sha256_file(best), best.stat().st_size)
    except OSError:
        return None


def _weight_entry(course: str, it: int) -> dict[str, Any]:
    """起点条目（§3.4）：能解析到**具体**归档件就带 sha256/bytes（G4-①「sha 可验」）；
    解析不到 ⇒ 退回目录级 glob 提示且**不带** sha（不编造）。"""
    got = resolve_archived_weight(course, it)
    if got is None:
        return {"it": it, "src": "archive", "path": _archive_weight_hint(course, it)}
    rel, sha, size = got
    return {"it": it, "src": "archive", "path": rel, "sha256": sha, "bytes": size}


def _archive_weight_hint(course: str, it: int) -> str:
    """关键轮权重在**权重归档**里的目录级 glob（`.xz 惯例` 的 `nn-training/weights/<课>/`）。

    只在解析不到具体件时用：真实文件名带时间戳，且同一 it 可重跑多份。
    """
    return f"nn-training/weights/{course}/*.it{it}.*.json"


def _build_archive_md(course: str, rep: ArchiveReport, manifest: dict[str, Any]) -> str:
    """人读索引（**永不压缩**）。"""
    reads = manifest["reads"]
    wlines = [
        f"- it{w.get('it')}：`{w.get('path')}`"
        + (
            f"（sha256 `{str(w.get('sha256'))[:12]}…`，{w.get('bytes')} B）"
            if w.get("sha256")
            else "（归档里未找到具体件——按 glob 取）"
        )
        for w in (manifest.get("weights") or [])
    ] or ["- （无：本课无关键轮权重）"]
    lines = [
        f"# 课程档案：{course}",
        "",
        f"- 封存时刻：{manifest['archived_at']}",
        f"- 形态：**{rep.form}**（A=旧 `it<N>/dist/`；B=新 `it<N>/w<id>/`；C=offline 回传）",
        f"- 父臂：{manifest['parent'] or '（未声明）'}",
        f"- it 区间：{manifest['it_range'][0]} – {manifest['it_range'][1]}"
        f"（终点 {manifest['keys']['final_it']}）",
        f"- 关键轮（opt/ckpt 保留点）：{manifest['keys']['opt_key_iters']}",
        f"- 判决点 it：{manifest['keys']['judge_iters'] or '（无）'}",
        f"- 压缩：`{rep.codec}`（文本件为 `.gz`/`.xz` ⇒ 用 `zcat` / `zgrep` 读）",
        f"- **shards 是否留存**：{'是（可复算）' if rep.shards_kept else '否（**不可复算，只可比**）'}",
        f"- 保留 {len(rep.artifacts)} 件"
        f"{f' + shards {rep.shard_files} 件' if rep.shard_files else ''} / "
        f"{_mb(rep.bytes_kept_raw)} → {_mb(rep.bytes_kept_stored)}（已存）",
        f"- 释放 {_mb(rep.bytes_freed)}",
        "",
        "## 关键读数路径",
        "",
        f"- 逐局评估账本：`{reads['eval_log'] or '（无）'}`",
        f"- 训练账本：`{reads['train_log'] or '（无）'}`",
        f"- 判决行：{reads['judge'] or '（无）'}",
        f"- 回测行：{reads['backtest'] or '（无）'}",
        f"- settle 行：{reads['settle'] or '（无）'}",
        "",
        "## 起点权重（可作新腿 bc 起点）",
        "",
        *wlines,
        "",
        "## 何时不能用",
        "",
        "本档案是**只读快照**：`tmp/<课>/` 已移走，`training-enabled.txt` 已删。"
        "作为新腿起点时用 `archive-manifest.json` 的 `weights[]`（指向 `nn-training/weights/`）。",
        "若 `shards_kept=false`：**不能**用它重跑 rollout/BC 复算（语料只在归档外的活体目录里）。",
        "",
    ]
    return "\n".join(lines)


# ────────────────────────── CLI ──────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m rl.course_archive",
        description="课程封存：把已停课程从 tmp/<课>/ 搬成只读档案（默认 --dry-run）",
    )
    ap.add_argument("--course", required=True)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印清单与字节账（**默认**；显式 --apply 才真搬）",
    )
    ap.add_argument("--apply", action="store_true", help="真封存（默认 dry-run，必须显式给）")
    ap.add_argument("--force", action="store_true", help="越过**陈旧**开课标记")
    ap.add_argument("--root", default=str(DEFAULT_ARCHIVE_ROOT))
    ap.add_argument("--traj-root", default=str(DEFAULT_TRAJ_ROOT))
    ap.add_argument("--codec", default="gzip", choices=list(CODECS))
    ap.add_argument("--json", action="store_true", help="输出机器可读报告")
    args = ap.parse_args(argv)

    dry_run = not args.apply
    rep = archive_course(
        args.course,
        traj_root=args.traj_root,
        archive_root=args.root,
        force=args.force,
        dry_run=dry_run,
        codec=args.codec,
        log=lambda m: print(m, flush=True),
    )
    if args.json:
        print(
            json.dumps(
                {
                    "course": rep.course,
                    "form": rep.form,
                    "dry_run": rep.dry_run,
                    "refused": rep.refused,
                    "bytes_freed": rep.bytes_freed,
                    "bytes_kept_raw": rep.bytes_kept_raw,
                    "bytes_kept_stored": rep.bytes_kept_stored,
                    "kept": sum(1 for o in rep.ops if o.action == "keep"),
                    "deleted": sum(1 for o in rep.ops if o.action == "delete"),
                    "verify_failed": rep.verify_failed,
                    "delete_failed": rep.delete_failed,
                },
                ensure_ascii=False,
            )
        )
    else:
        for op in rep.ops:
            if op.action == "delete":
                print(f"  del  {op.rel}  ({op.reason}, {_mb(op.size)})")
            else:
                print(
                    f"  keep {op.rel}  ({op.reason}{', gz' if op.compress and rep.codec != 'none' else ''})"
                )
    return 0 if rep.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
