"""backup_drawer —— 网络侧「抽屉」的**写方**：待回传轮次 + 读数痕迹。

**所有者**：`产物根 → 抽屉根` 的拷贝策略（拷哪些件、什么时候拷、拷失败算什么）。
**触发者**：补传腿每拍（`OfflineDeliverer.backup_pending()`）与段末 flush —— 每次一个
`write(pending())`。**读方**：`OfflineDeliverer.status()` 读 `readings()`（零 IO）。

四条纪律（逐字沿用 `remote/job_fs` 的备份腿先例，`plan/offline-local-disk-first` §3.4）：

  * **绝不抛**（写不成只记一笔；抽屉坏了不能反过来把补传腿或训练拖住）—— 所有落盘动作都在
    `call_bounded` 里，异常由调用方给的 `throttled` 收走；
  * **不改 job 状态**（hub 根本不知道抽屉存在）；
  * **有界**（逐件一个 `call_bounded(DRAIN_CEILING_SEC)`：抽屉也可能挂在同一个坏挂载点上，
    而它跑在补传线程里 ⇒ 绝不能反过来把段末挂住）；
  * **幂等**（已存在**且逐件字节一致**才跳过：只判「存在」会把本机重跑过的旧字节永久留在
    抽屉里，恢复腿再把它当「这一轮」铺回去 —— 看起来完全正常）。

**常态零写入**：`write([])` 连抽屉目录都不建（拍板-1 的全部意义：常态路径上网络盘出现 0 次）。

为什么单独成家（2026-10-16）：这段策略与「什么时候该推 hub」（`remote/offline_deliver`）是
**两件事**——前者只认字节与配额，后者只认网络与账本；混在一个 1000+ 行的文件里时，
「抽屉的写放大」（每次重写整份账本）这种问题没人能一眼看见。
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from common.game_watch import DRAIN_CEILING_SEC
from common.hashing import sha256_file
from common.platform_utils import call_bounded
from remote.artifacts import ArtifactStore
from remote.offline_deliverable import (
    ALL_ZIP,
    MANIFEST_NAME,
    OPT_TAR_NAME,
    PLAN_NAME,
    STATE_NAME,
    WEIGHTS_NAME,
)

#: 抽屉里的**锚点**：让抽屉自己就是一份**能反向恢复**的产物（恢复腿的判据 = 三件 + 权重）。
#: 两个账本（2026-10-16，评审 D8 / 期望 2）也在名单里 —— 现场「权重能救、读数救不回」的不
#: 对称就出在这：抽屉里只有 `it-NNN/weights.json` + `opt.tar`，导入后曲线与 eval 全空。
BACKUP_ANCHOR_NAMES: tuple[str, ...] = (
    ALL_ZIP,
    MANIFEST_NAME,
    PLAN_NAME,
    STATE_NAME,
    ArtifactStore.METRICS_NAME,
    ArtifactStore.EVAL_LOG_NAME,
)

#: 上表里**只在变化时**重拷的那两件（D8 / §3.2-2）：两个账本是追加型（`metrics.jsonl` ~10–20KB/轮
#: 含 `perGame`、`eval_log.jsonl` 每 400 局数百 KB），每拍全量重写会把抽屉的网络写放大到 MB/轮
#: （plan §14）；其余四件是小文件，且 `state.json` **必须**与轮次同步更新（导入/恢复腿靠它拿
#: `run_id`/`last_it`，`deliver_zip.py:155-176`）⇒ 那四件保持每拍重写。
BACKUP_CHANGE_ONLY_NAMES: tuple[str, ...] = (ArtifactStore.METRICS_NAME, ArtifactStore.EVAL_LOG_NAME)

#: 删除的**滞回**（D3 定案「回传成功即删」，所以两个默认都是 0 = 不保留）。
#: 为什么把接口留着：真要出现「hub 收了又丢」的事故，先把事故记一条 DECISIONS，再打开这两个——
#: 那时它们就是「只留最近 N 轮 / 只留最近 H 小时」的回退手段，不用再改一次接口。
BACKUP_KEEP_ROUNDS = 0
BACKUP_KEEP_HOURS = 0.0


@dataclass(frozen=True)
class PruneReadings:
    """一次**抽屉清理**的读数（判据化删除，D3）。

    `pruned` = 真删掉的轮数（`delivered` = 凭「hub 已确认」删的，`local_dup` = 凭「本地有同一份字节」
    删的孤儿）；`failed` = 删不了的（下拍再试）；`orphans` = **没删的**孤儿数（本会话账本里没有证据
    且本地也没有同一份 ⇒ 保留 + 计数，**绝不默默不删**）；`skipped` = 这一趟根本不删的原因
    （非空 = 账本不可读——那是唯一的硬门槛）。
    """

    pruned: int = 0
    delivered: int = 0
    local_dup: int = 0
    failed: int = 0
    orphans: int = 0
    skipped: str = ""


@dataclass(frozen=True)
class DrawerReadings:
    """一次写盘的读数（`OfflineDeliverer.status()` 直接读，调用方不重算、不额外走目录）。

    `total`/`ledger`/`round_avg` 是 §14 那条**容量行**：`待投轮数 × round_avg + ledger` ≈ 下一步
    还要往网络盘写多少 —— 抽屉落 `net_root`/`/kaggle/working`（有配额），段中盘满会砸在训练中途。

    ⚠ 目录字节是**上次真写过之后**的快照（不是实时值）：`status()` 每拍被调，而抽屉在网络盘上，
    为了一个读数去 `rglob` 整棵抽屉是把 IO 花在展示上。
    """

    made: int = 0
    bytes: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    ledger: int = 0
    round_avg: int = 0


def _tree_bytes(path: Path) -> int:
    """一棵小目录的字节数（读数用；拿不到就 0——读数不该把备份拖红）。"""
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _file_sig(p: Path) -> tuple[int, int] | None:
    """小文件的变更签名 `(size, mtime_ns)`；拿不到（不存在/权限/IO）= `None`。

    与 `OfflineDeliverer._ledger_its()` 的缓存失效判据**逐字同源**：抽屉的「账本变了才拷」与
    补传的「账本变了才重解析」必须是同一条，否则两者会互相打脸（一个说没变、另一个说变了）。
    """
    try:
        st = p.stat()
    except OSError:
        return None
    return (int(st.st_size), int(st.st_mtime_ns))


def _same_file(a: Path, b: Path) -> bool:
    """两个文件**字节相同**吗（sha256 对账）；任一读不到 ⇒ `False`（当不一致处理）。"""
    try:
        return a.is_file() and b.is_file() and sha256_file(a) == sha256_file(b)
    except OSError:
        return False


def _round_matches(src: Path, dst: Path) -> bool:
    """抽屉里那一轮与本机产物是**同一份字节**吗（幂等判据的实质）。

    ⚠ 只判「存在」是不够的（plan §3.4 的原话是「已存在**且对账一致** ⇒ 跳过」）：本机的同一轮
    可能被**重跑**过（中途崩了之后从更早的锚点接上，同一个 `it` 又落一次、字节不同），那时
    抽屉里那份是旧的 —— 恢复腿会把它当成「这一轮」铺回去，而看起来完全正常。

    对账两件：`weights.json`（这一轮的产物身份）与 `opt.tar`（Adam 动量，恢复后接着跑就靠它）；
    `opt.tar` 本机没有时按「一致」算（不拿一个不存在的文件当差异 —— 起点快照那一轮就可能没它）。
    """
    if not _same_file(src / WEIGHTS_NAME, dst / WEIGHTS_NAME):
        return False
    if not (src / OPT_TAR_NAME).is_file():
        return True
    return _same_file(src / OPT_TAR_NAME, dst / OPT_TAR_NAME)


class BackupDrawer:
    """一个产物根 → 一个抽屉根。实例可长期复用（`_anchor_sig`/`_last` 就是它的记忆）。"""

    def __init__(
        self,
        *,
        source_root: Path,
        backup_root: Path,
        log: Callable[[str], None] = lambda _m: None,
        throttled: Callable[[str, str], None] = lambda _k, _m: None,
    ) -> None:
        self.source = Path(source_root)
        self.root = Path(backup_root)
        self.log = log
        #: 同一类失败的最小间隔由调用方决定（它有自己的钟与配额）；这里只负责「说什么」。
        self.throttled = throttled
        #: 「变化才拷」的源签名 `name → (size, mtime_ns)`：只住内存 ⇒ 新会话把两账本重拷一次
        #: （无害，反而把上个会话末尾追加的行带过去）。
        self._anchor_sig: dict[str, tuple[int, int]] = {}
        self._last = DrawerReadings()
        #: 上一次清理的读数（`status()` 读它）。
        self._prune_last = PruneReadings()

    def readings(self) -> DrawerReadings:
        """最近一次写盘的读数（`status()` 读它 —— 零 IO）。"""
        return self._last

    def prune_delivered(
        self,
        *,
        confirmed: set[int],
        local_rounds: set[int],
        ledger_ok: bool,
        keep_rounds: int = BACKUP_KEEP_ROUNDS,
        keep_hours: float = BACKUP_KEEP_HOURS,
        now: float = 0.0,
    ) -> PruneReadings:
        """把**已确认送达**的轮次从抽屉里删掉（D3：回传成功即删；不加保留窗口）。

        两个证据，缺一不可：

          * `it in confirmed`：hub 收过（`_delivered` 里只有 200/201 才进；写账本**先于**删除）；
          * `ledger_ok`：**账本可读**。它是唯一的硬门槛 —— `delivered.json` 丢了/坏了时本会话
            一份确认都没有（`_load_ledger` 返回空），而抽屉是**跨会话的唯一第二副本**（`/tmp` 一关
            就只剩它）⇒ 宁可全留着，也不拿「读不出来」当「没送达」。

        孤儿（`it not in confirmed`）——账本是 **run 级**、抽屉是 **课级**（续段换 run_id 后老段遗留
        的轮次在新账本里没有任何证据）：

          * 本地源盘上有**同一份字节**（`local_rounds` 命中且逐件对账一致）⇒ 删（它是可重建的冗余）；
          * 否则**保留 + 计数**（`orphans`）——这是「抽屉是别的 run / 别的机器的第二副本」的形状，
            删它就是永久丢一份。

        `keep_rounds` / `keep_hours`（默认都 0）是滞回接口：>0 时只删「最新 N 轮之外 / 早于 H 小时」
        的；真出现「hub 收了又丢」的现场再打开（那时先记一条 DECISIONS）。
        **永不抛**：逐件有界，失败只记数（下一拍再试）。
        """
        prev_orphans = self._prune_last.orphans
        if not ledger_ok:
            return PruneReadings(skipped="账本不可读（缺 delivered.json 或它坏了）——本轮不删")
        try:
            if not self.root.is_dir():
                return self._prune_last
            dirs = [d for d in self.root.glob(f"{ArtifactStore.IT_PREFIX}*") if d.is_dir()]
        except OSError:
            return self._prune_last

        def _num(d: Path) -> int:
            name = d.name[len(ArtifactStore.IT_PREFIX) :]
            return int(name) if name.isdigit() else -1

        dirs = sorted((d for d in dirs if _num(d) >= 0), key=_num)
        keep_n = max(0, int(keep_rounds))
        young = float(keep_hours) * 3600.0
        pruned = failed = orphans = by_delivered = by_local = 0
        for i, d in enumerate(dirs):
            it = _num(d)
            if keep_n and i >= len(dirs) - keep_n:
                continue  # 滞回：最近 N 轮留在抽屉里
            src = self.source / d.name
            if young > 0.0 and now > 0.0:
                m = 0.0
                for p in (src, d):
                    try:
                        m = max(m, float(p.stat().st_mtime))
                    except OSError:
                        continue
                if m and now - m < young:
                    continue  # 滞回：刚备不久的先留着
            if it in confirmed:
                why = "delivered"
            elif it in local_rounds and src.is_dir() and _round_matches(src, d):
                why = "local"
            else:
                orphans += 1
                self.throttled(
                    f"orphan:{it}",
                    f"抽屉孤儿 it{it}：本会话账本里没有它的送达证据，本地也没有同一份 —— 保留"
                    "（删它就是永久丢一份；换过 run_id / 别的机器留下的都长这样）",
                )
                continue
            try:
                # `call_bounded` 会把 `fn` 的异常**原样重抛**（它的契约）⇒ 收在这里：删不掉只
                # 是一笔 WARN，下一拍再试（Windows 上被句柄占住是最常见的形状）。
                ok, _n = call_bounded(
                    partial(shutil.rmtree, d, ignore_errors=False),
                    float(DRAIN_CEILING_SEC),
                    name="deliver-backup-prune",
                )
            except OSError:
                ok = False
            if not ok or d.exists():
                failed += 1
                self.log(f"WARN 抽屉清理 it{it} 失败（{why}）——下一拍再试")
                continue
            pruned += 1
            if why == "delivered":
                by_delivered += 1
            else:
                by_local += 1
        self._prune_last = PruneReadings(
            pruned=pruned,
            delivered=by_delivered,
            local_dup=by_local,
            failed=failed,
            orphans=orphans,
        )
        if pruned or failed or orphans != prev_orphans:
            # 一行一个拍就够：孤儿**数变了**才再响一次（`throttled` 也拦不住累积计数）。
            self.log(
                f"抽屉清理：删 {pruned} 轮（已送达 {by_delivered}｜本地同份 {by_local}）"
                f"｜失败 {failed}｜孤儿保留 {orphans}｜抽屉 {self.root}"
            )
        return self._prune_last

    def backup_rounds(self, rounds: Sequence[int]) -> DrawerReadings:
        """把这几轮各备份一份（+ 锚点），返回本次读数。**不抛**：失败只记进读数与日志。

        `rounds` 的判据（「磁盘上有、账本里没有」）不在这里算 —— 那是补传腿的定义
        （`OfflineDeliverer.pending()`），本模块只负责「把这些轮次挪到网络盘上」。
        """
        rounds = list(rounds)
        if not rounds:
            return self._last  # 常态零写入：连抽屉目录都不建
        made = bytes_ = failed = skipped = 0
        root = self.root
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.throttled("backup-root", f"备份跳过：抽屉建不出来（{root}：{e}）")
            return self._last
        for it in rounds:
            # 目录名的**唯一口径**是 `ArtifactStore.dir_for`（`it-<03d>`）：自己拼 `it-1` 会
            # 找一个不存在的源目录（实测：三轮全报 0 字节失败，而抽屉里空空如也）。
            src = self.source / f"{ArtifactStore.IT_PREFIX}{int(it):03d}"
            dst = root / f"{ArtifactStore.IT_PREFIX}{int(it):03d}"
            if dst.is_dir() and _round_matches(src, dst):
                continue  # 幂等：这一轮已经备份过且**字节一致**（重跑不重复写）
            if dst.is_dir():
                # 对账不一致 ⇒ 本机的这一轮被重跑过：撤掉重装，否则恢复腿会把旧字节当成「这一轮」
                self.log(f"备份 it{it}：抽屉里那份与本机字节不符（本机这一轮被重跑过）⇒ 重写")
                shutil.rmtree(dst, ignore_errors=True)
            ok, n = call_bounded(
                partial(self._copy_round, src=src, dst=dst),
                float(DRAIN_CEILING_SEC),
                name="deliver-backup",
            )
            if not ok or not n:
                failed += 1
                self.log(
                    f"WARN 备份 it{it} 失败或超界（>{float(DRAIN_CEILING_SEC):g}s）——"
                    "这一轮仍只在本地产物目录里（下一拍再试）"
                )
                continue
            made += 1
            bytes_ += int(n)
        if made:
            s, n_bytes, f = self._write_anchors()
            skipped += s
            bytes_ += n_bytes
            failed += f
        if made or skipped:
            self._last = self._scan(
                DrawerReadings(made=made, bytes=bytes_, failed=failed, skipped=skipped)
            )
        else:
            self._last = DrawerReadings(
                made=made,
                bytes=bytes_,
                failed=failed,
                skipped=skipped,
                total=self._last.total,
                ledger=self._last.ledger,
                round_avg=self._last.round_avg,
            )
        return self._last

    def _write_anchors(self) -> tuple[int, int, int]:
        """拷锚点，返回 `(跳过, 字节, 失败)`。**只在真写过轮次时调**（否则抽屉里没东西可锚）。"""
        skipped = bytes_ = failed = 0
        root = self.root
        for name in BACKUP_ANCHOR_NAMES:
            src = self.source / name
            if not src.is_file():
                continue
            change_only = name in BACKUP_CHANGE_ONLY_NAMES
            if change_only and not self._anchor_changed(name, src):
                skipped += 1  # 账本没变 ⇒ 不重写（D8）：这是「不把网络盘当追加缓冲」的判据
                continue
            ok, n = call_bounded(
                partial(self._copy_file, src=src, dst=root / name),
                float(DRAIN_CEILING_SEC),
                name="deliver-backup-anchor",
            )
            if not ok or not n:
                self._anchor_sig.pop(name, None)  # 失败不记账 ⇒ 下一拍重试
                failed += 1
            else:
                bytes_ += int(n)
                if change_only:
                    self._anchor_sig[name] = _file_sig(src) or (0, 0)
        return skipped, bytes_, failed

    def _anchor_changed(self, name: str, src: Path) -> bool:
        """这个「变化才拷」的锚点相对上次拷贝变了吗？口径 `(size, mtime_ns)`，与
        `OfflineDeliverer._ledger_its()` 的失效判据同源。"""
        sig = _file_sig(src)
        if sig is None:
            return True  # 量不出来 ⇒ 当它变了（让这次拷贝自己说话：失败会记账并下一拍重试）
        return self._anchor_sig.get(name) != sig

    def _scan(self, r: DrawerReadings) -> DrawerReadings:
        """量一次抽屉：总字节 / 两账本字节 / 单轮均字节（§3.3 DoD-2c + §14 容量行）。"""
        total = _tree_bytes(self.root)
        ledger = 0
        for name in BACKUP_CHANGE_ONLY_NAMES:
            sig = _file_sig(self.root / name)
            if sig is not None:
                ledger += int(sig[0])
        rounds = n = 0
        for d in self.root.glob(f"{ArtifactStore.IT_PREFIX}*"):
            if d.is_dir():
                rounds += _tree_bytes(d)
                n += 1
        return DrawerReadings(
            made=r.made,
            bytes=r.bytes,
            failed=r.failed,
            skipped=r.skipped,
            total=total,
            ledger=ledger,
            round_avg=int(rounds / n) if n else 0,
        )

    def _copy_round(self, *, src: Path, dst: Path) -> int:
        """拷一轮到抽屉（先 `dst.tmp-<pid>` 再 `os.replace`：半截绝不进抽屉）；返回字节数。"""
        tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
        try:
            if tmp.exists():
                shutil.rmtree(tmp, ignore_errors=True)
            shutil.copytree(src, tmp)
            os.replace(tmp, dst)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            return 0
        return _tree_bytes(dst)

    def _copy_file(self, *, src: Path, dst: Path) -> int:
        """拷一件小文件（同样先 tmp 再 replace）；返回字节数。"""
        tmp = dst.with_name(f".{dst.name}.tmp-{os.getpid()}")
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        except OSError:
            return 0
        try:
            return int(dst.stat().st_size)
        except OSError:
            return 0
