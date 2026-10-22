"""remote/artifacts.py —— 半离线训练的**本地产物目录**（kind=run 的交付面）。

为什么有这个模块：`kind=run` 的语义是「hub 只在交接时必需，此后云机自主」。而云端
（Kaggle / Colab）**不是一个可信的持久存储**——会话到点即断（Kaggle 9/12h）、机器随时
会被回收。所以自主段唯一的交付面就是**产物目录**：

  ★ 2026-10-14（`plan/offline-local-disk-first` §3.2，用户拍板-1）：这个目录默认落在
  **节点本地盘**（`<本地根>/battle2-offline[-<课>]`，本地根 = `NN_LOCAL_ROOT` ∨ `/tmp`），
  而**不再**是网络盘。网络盘退到两个角色：**交付面**（`deliver-<课>.zip` 的下载落点）与
  **备份抽屉**（`<网络产物根>/backup/`，只装回传失败的那些轮）。生效点是
  `remote/offline_boot` 的 `--artifacts`（它指的是本地根），**不是**本模块的默认分支。
  * Kaggle：`/kaggle/working/<run>/`（官方的 output 目录，Save Version 即打包下载）；
  * Colab：`/content/drive/MyDrive/<run>/`（挂 Drive 时，跨会话存活），否则 `<cwd>`；
  * 其它：`--artifacts` 显式指定（本机演练 / 自建节点）——生产路径走的就是这一支。

布局（每轮一份不可变快照 + 一个追加式账本）：

    <art>/plan.json        计划快照（**唯一输入**：据此可在任何机器上重放）
    <art>/manifest.json    本 job 的 manifest 快照（课程全文、commit、超参都在内）
    <art>/state.json       续跑点（last_it / 指纹 / 状态 / 计数）——唯一可写的小状态
    <art>/metrics.jsonl    逐轮追加一行（agg + 采集报告 + 墙钟）：人看的曲线
    <art>/it-013/weights.json  第 13 轮结束时的权重（= 下一轮的 init_weights.json）
    <art>/it-013/opt.tar       同轮 Adam 动量（**真续训必需**：没有它 resume 等于
                               把动量静默归零，见 D5）
    <art>/LATEST.zip       每次 checkpoint 刷新的「最新一轮」小包（人手动下载用）
    <art>/artifacts.zip    收尾/中断时打的全量包（含逐轮 + 账本 + 计划 + README）
    <art>/delivered.json   产物补传的记账（已投递到 hub 的轮次；`remote/offline_deliver.py`
                           写——重启后靠它接着补，而不是重传已投递的轮次）

**中断即有效**：每一步先写临时文件再原子改名，且 state.json 永远指向「已完整落盘的
最后一轮」。会话被 kill 也只丢正在跑的那一轮——**只要那个卷还在**，新会话用同一个目录
再跑一次即可续上。

★ **续跑真值的完整口径**（2026-10-14，§3.2/§3.5 改口径 —— 旧文「新会话用同一个目录再跑
一次即可续上」在产物落本地盘之后会误导人）：

    可续跑性 = hub 已收轮次　∪　**网络盘备份抽屉**（`backup/`）

  本地盘（`/tmp`）**不抗容器回收**：本地产物在本段会话内是权威（同会话重跑 = 从最后一轮接上），
  而跨会话的凭据是上面并起来的那一份。恢复腿（`offline_boot.restore_from_backup`）在
  「本地三件不齐 ∧ 备份齐」时把它反向铺回本地根（优先级：本地根 > hub 锚点 > 备份）。
"""

from __future__ import annotations

import json
import os
import time
import zipfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from common.fs import atomic_write_bytes, atomic_write_json
from common.hashing import sha256_bytes, sha256_file  # re-export：调用点与测试不变

#: 仓库根（`nn-training/remote/artifacts.py` 上溯两层）。
REPO_ROOT = Path(__file__).resolve().parents[2]
#: 权重归档根（`nn-training/weights/`；即 `biz.archive.backup_weights` 的缺省目录）。
#: **唯一定义处**（2026-10-15 去重）：回传落归档的 `hub.store_offline` 与读侧落地的
#: `remote.weights_standard` 都站在它上面 —— 之前两处各留一份副本，各自定义 `_archive_root` /
#: `_weights_archive_root`（逐字节重复，reuse 账本当场红）；两者都够得着的**最低层**
#: 就是这里（L0 产物模块，双方本来就在它上面）。
WEIGHTS_ARCHIVE_ROOT = REPO_ROOT / "nn-training" / "weights"
#: 归档根的**测试隔离开关**（2026-09-23）：设了就改用它。为什么必须有：回传轮会往归档根写
#: `<课>.it<N>.<时间戳>.json`，而工装用例不得往真归档目录撒文件——那些文件会被控制台的
#: evalA 权重选择器（`eval-board/ckpts.ts` 扫 `nn-training/weights/<leg>/`）当成真训练轮次
#: 列出来。env 形态（而不是只留可 patch 的模块常量）是因为 e2e 那条是真子进程
#: （`e2e/test_offline_training_e2e.py` 拉 `hub.server`），patch 传不进去。
WEIGHTS_ARCHIVE_ROOT_ENV = "BCITY_WEIGHTS_ARCHIVE_ROOT"


def weights_archive_root() -> Path:
    """当前归档根（`BCITY_WEIGHTS_ARCHIVE_ROOT` 优先，缺省 `nn-training/weights`）。

    **调用时读** env（不是 import 时算一次）——这样测试能在同一进程里 monkeypatch。
    """
    raw = os.environ.get(WEIGHTS_ARCHIVE_ROOT_ENV, "")
    return Path(raw) if raw else WEIGHTS_ARCHIVE_ROOT

# 本模块此前自留一份 `sha256_file` / `sha256_bytes`，注释理由是「不依赖 common.distribution 的
# 导入，纯 stdlib（云端产物目录也可能被单独搬运）」。诉求成立，落点换成 `common.hashing`
# ——它同样是纯 stdlib，且全仓只有一份定义（`wver` / init 指纹 / blob 键的「同字节同
# 哈希」契约不再靠四处字形相同的实现维持）。名字在这里 re-export，历史调用点不动。

# ------------------------------------------------------------------ 目录解析


def is_kaggle(env: Mapping[str, str] | None = None, *, exists=os.path.isdir) -> bool:
    """是否在 Kaggle 容器里（官方标记 `KAGGLE_KERNEL_RUN_TYPE` + 工作目录存在）。"""
    e = os.environ if env is None else env
    return bool(e.get("KAGGLE_KERNEL_RUN_TYPE")) or exists("/kaggle/working")


def is_colab(env: Mapping[str, str] | None = None, *, exists=os.path.isdir) -> bool:
    """是否在 Colab 里（`/content` + `google.colab` 其一即可）。"""
    e = os.environ if env is None else env
    if "COLAB_GPU" in e or "COLAB_RELEASE_TAG" in e:
        return True
    return exists("/content") and not exists("/kaggle/working")


#: 网络侧**备份抽屉**名（与 `remote/offline_deliverable.py::BACKUP_DIR_NAME` 逐字相同；测试守）。
#: ⚠ **不得**以 `it-` 开头：`ArtifactStore.finalize` / `_refresh_latest` 按顶层 `it-*` 扫轮次
#: （`self.root.glob("it-*")`），叫 `it-backup` 之类就会把备份打进交付物。
#: 与 `remote/job_fs.RESULT_BACKUP_DIR`（= 单个 job 的线上体备份）**同名不同义**，别合并。
BACKUP_DIR_NAME = "backup"


def resolve_artifact_dir(
    explicit: str | Path | None,
    *,
    run_id: str,
    work_dir: str | Path,
    env: Mapping[str, str] | None = None,
    exists=os.path.isdir,
) -> Path:
    """产物根目录（显式 > 环境变量 > **本地盘** > Kaggle > Colab(Drive 优先) > 工作目录）。

   顺序刻意如此：**显式永远赢**（自建节点/本机演练），其次 `BATTLE_ARTIFACT_DIR`，再次
    **节点本地盘**（2026-10-14，`plan/offline-local-disk-first` §3.2：产物默认落本地盘，
    网络盘只在回传失败时作备份），之后才是 Kaggle（官方 output 目录是唯一能一键下载的位置），
   最后是 Colab 的 Drive（挂载可能失败——那时退回 `/content`，人用 `files.download` 拿）。

   ⚠ **本函数的默认分支不是拍板-1 的生效点**（评审 P0-1）：生产路径上恒有**显式**
   `--artifacts`（`remote/offline_boot.build_run_argv` 无条件传），而显式永远赢 ⇒ 本地优先
   这一档只是「无显式」时的**兜底**（唯一走默认分支的 `kind=run` 节点腿已退休，见
   `remote/worker.py`）。生效点在 `offline_deliverable.resolve_local_root`（拆两个根）。
   """
    e = os.environ if env is None else env
    if explicit:
        return Path(explicit).expanduser()
    from_env = (e.get("BATTLE_ARTIFACT_DIR") or "").strip()
    if from_env:
        return Path(from_env).expanduser()
    tag = f"battle2-{run_id}" if run_id else "battle2-run"
    local_parent = (e.get("NN_LOCAL_ROOT") or "").strip() or "/tmp"
    if exists(local_parent):
        return Path(local_parent).expanduser() / tag
    if is_kaggle(e, exists=exists):
        return Path("/kaggle/working") / tag
    if is_colab(e, exists=exists):
        drive = Path("/content/drive/MyDrive")
        if exists(str(drive)):
            return drive / tag
        return Path("/content") / tag
    return Path(work_dir) / "artifacts" / tag


def resolve_backup_root(
    explicit: str | Path | None,
    *,
    run_id: str,
    work_dir: str | Path,
    env: Mapping[str, str] | None = None,
    exists=os.path.isdir,
) -> Path:
    """**备份抽屉的父目录**（显式 > `BATTLE_BACKUP_DIR` > Kaggle/Colab 平台分支 > `<work_dir>/backup`）。

    它必须是**网络侧**（人不在云机上，机器挂了本地盘一起没）：这就是它不能用
    `resolve_artifact_dir` 的原因（后者已本地优先）。生产路径上由
    `remote/offline_boot` 显式传（= `<网络产物根>/backup`），这里同样只是兜底。
    名与 `remote/job_fs.RESULT_BACKUP_DIR`（单 job 的线上体）分开：本抽屉装的是
    「没回传成功的**轮次产物**」（见 `plan/offline-local-disk-first` §3.4）。
    """
    e = os.environ if env is None else env
    if explicit:
        return Path(explicit).expanduser()
    from_env = (e.get("BATTLE_BACKUP_DIR") or "").strip()
    if from_env:
        return Path(from_env).expanduser()
    tag = f"battle2-{run_id}" if run_id else "battle2-run"
    if is_kaggle(e, exists=exists):
        return Path("/kaggle/working") / tag / BACKUP_DIR_NAME
    if is_colab(e, exists=exists):
        drive = Path("/content/drive/MyDrive")
        base = drive if exists(str(drive)) else Path("/content")
        return base / tag / BACKUP_DIR_NAME
    return Path(work_dir) / BACKUP_DIR_NAME / tag


# 原子写 / JSON 原子写 —— 唯一实现见 `common.fs`（re-export；同包调用点与测试不变）。
# 历史：本模块与 `hub.server._write_bytes` 各有一份字形相同的实现，两份的注释
# 都在解释同一件事（「半截的记账文件会把续跑判据带偏」）——口径写在注释里不算单实现。


# ------------------------------------------------------------------ 产物目录


def _round_entries(
    d: Path, root: Path, full_for: Callable[[Path, int], Mapping[str, Path]] | None
) -> list[tuple[str, Path]]:
    """一轮进包的 `(包内名, 源路径)` 清单：给了整份 ⇒ 整份顶名 + 原件旁存成同主名的 `.delta`。

    `full_for(root, it)` 由调用方注入（见 `finalize` 的 docstring；实现见 `remote/land_provider`）；
    不给 ⇒ 逐字就是以前的行为。只处理**平坦**的轮目录（真实形状：`it-NNN/` 下只有几个文件）。
    """
    full: Mapping[str, Path] = {}
    if full_for is not None:
        try:
            full = full_for(Path(root), int(d.name[len(ArtifactStore.IT_PREFIX) :])) or {}
        except Exception:  # 整份只是加值（提供者自己逐件 WARN）；包必须照出
            full = {}  # 目录名不是轮次 / 落地失败：逐字旧行为
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()  # 包内名唯一：重复名会让 zip 里出现第二条同名头（读方取哪条看工具）

    def add(arc: str, src: Path) -> None:
        if arc not in seen:
            seen.add(arc)
            out.append((arc, src))

    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        alt = full.get(p.name)
        if alt is not None and Path(alt) != p and Path(alt).is_file():
            add(f"{d.name}/{p.name}", Path(alt))
            add(f"{d.name}/{p.with_suffix('.delta').name}", p)
        else:
            add(f"{d.name}/{p.name}", p)  # 本来就是整份（或 .delta 旁件）⇒ 原样装
    return out


def weights_fps(root: Path, it: int, prefer: str = "") -> Iterator[str]:
    """一轮在盘上**可能对得上的**权重指纹（先给 `prefer`（不读盘），再给整份/原件的实算 sha）。

    两态落地（2026-10-22，`plan/offline-full-weights-backup`）之后，同一轮目录里会同时有
    `weights.json`（整份）与 `weights.delta`（原件），而账本行 / `state.last_weights_fp` 记的
    永远是**回传出去的**那份字节（`checkpoint` 的语义不变）⇒ 对账必须两态都认，否则落地过的
    包/目录会报一片「指纹不符」（假红），甚至整轮不发账本行（`offline_deliver._row_for`）。

    是生成器：命中 `prefer` 的调用方**一次盘都不用读**；名字写死两个（本模块是 L0，`delta_files`
    也是 L0 —— 同秩不算「严格向下」，见 `tests/test_remote_dag.py`）。
    """
    if prefer:
        yield prefer
    for name in ("weights.json", "weights.delta"):
        try:
            yield sha256_bytes((root / f"{ArtifactStore.IT_PREFIX}{int(it):03d}" / name).read_bytes())
        except OSError:
            continue


class ArtifactStore:
    """一个半离线段的产物目录（`kind=run` 的交付面）。

    实例不持有任何「未落盘」的关键状态：`state.json` 是唯一的续跑真值，每次 checkpoint
    都原子刷新——进程随时可以死，卷上永远是一个**自洽**的目录。

    ⚠ 那个「卷」默认是**节点本地盘**（2026-10-14）：它不抗容器回收 ⇒ 跨会话的可续跑性 =
    hub 已收轮次 ∪ 网络盘备份抽屉（模块头「续跑真值的完整口径」）。
    """

    #: 逐轮权重/opt 的子目录名前缀（字典序即轮次序，便于人肉翻看）。
    IT_PREFIX = "it-"
    STATE_NAME = "state.json"
    METRICS_NAME = "metrics.jsonl"
    PLAN_NAME = "plan.json"
    MANIFEST_NAME = "manifest.json"
    #: 云机 A 层评估账本（`eval_on_cloud`）：随 artifacts zip 回去，导入侧并进课程账本。
    EVAL_LOG_NAME = "eval_log.jsonl"
    README_NAME = "README.txt"
    LATEST_ZIP = "LATEST.zip"
    ALL_ZIP = "artifacts.zip"
    #: 逐局压缩画像的落盘名（**课程产物目录**侧：`tmp/<课>/it<N>/per-game.json`）。
    #: 控制台的「耗时/击杀/残血/道具」列是逐局聚合的，而单局 manifest 只在跑它的那台机器上
    #: （云机轮末就 prune 了）⇒ 产物行的 `perGame` 由落地方写成这个文件（读方优先它）。
    PER_GAME_NAME = "per-game.json"

    def __init__(
        self,
        root: str | Path,
        *,
        run_id: str,
        log=lambda msg: None,
    ) -> None:
        self.root = Path(root)
        self.run_id = str(run_id)
        self.log = log
        self.rows: list[dict] = []

    # ---- 开段 / 续跑 ----

    def dir_for(self, it: int) -> Path:
        return self.root / f"{self.IT_PREFIX}{int(it):03d}"

    def weights_path(self, it: int) -> Path:
        return self.dir_for(it) / "weights.json"

    def opt_path(self, it: int) -> Path:
        return self.dir_for(it) / "opt.tar"

    def read_state(self) -> dict | None:
        """读续跑点（缺失/损坏 → None，绝不让坏状态毒死整段）。"""
        p = self.root / self.STATE_NAME
        if not p.exists():
            return None
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def start(self, plan: dict, manifest: dict, *, plan_sha256: str = "") -> dict:
        """开段：写入计划/manifest 快照 + README，返回续跑点（新段 = 空状态）。

        续跑判据（**必须两个都同**才续）：`run_id` 与 `plan_sha256`——同一课程换一份计划
        （不同 it 区间/不同 rotateSeed）在同一个目录里继续跑 = 两条腿的产物混在一起，
        比丢进度严重得多。判据不符时**拒绝复用**，改为在本目录下开一个新的 run 子目录。
        """
        self.root.mkdir(parents=True, exist_ok=True)
        st = self.read_state()
        declared = plan_sha256 or sha256_bytes(json.dumps(plan, sort_keys=True).encode("utf-8"))
        atomic_write_json(self.root / self.PLAN_NAME, plan)
        atomic_write_json(self.root / self.MANIFEST_NAME, manifest)
        # 续跑判据里的计划身份以**磁盘上这份 plan.json** 为准（**写盘之后**再算）：于是
        # 「谁算」都得到同一个数。曾经用调用方传进来的 sha（= hub payload 里那份的字节
        # 哈希）——它与本目录写盘的格式不同（hub 走 `biz.plan.dump_plan` 的规范形
        # `sort_keys=True`），新会话拿着同一个目录再跑时永远算不出相等的值 ⇒ 每次都被
        # 当成**新段**、从 start_it 重跑。半离线最重要的那条路径（关掉会话明天接着跑）
        # 会静默退化成「重跑」，而且只有对着日志才看得出来（DECISIONS
        # §2026-09-17-halfoffline-run 记录）。`declared` 另存一份只为审计对账。
        plan_sha = sha256_file(self.root / self.PLAN_NAME)
        resumable = bool(
            st
            and st.get("run_id") == self.run_id
            and st.get("plan_sha256") == plan_sha
            and self.weights_path(int(st.get("last_it", 0))).exists()
        )
        if st and not resumable:
            self.log(
                f"[artifacts] 目录已在但是别的段（run_id/计划不符）——不复用："
                f"{self.root}（旧 last_it={st.get('last_it')}）"
            )
        if not resumable:
            atomic_write_bytes(
                self.root / self.README_NAME, self._readme(plan, manifest, plan_sha).encode("utf-8")
            )
            atomic_write_json(
                self.root / self.STATE_NAME,
                {
                    "run_id": self.run_id,
                    "plan_sha256": plan_sha,
                    "plan_sha256_declared": declared,
                    "state": "running",
                    "last_it": int(plan["start_it"]),
                    "rows": 0,
                    "started_at": time.time(),
                    "updated_at": time.time(),
                },
            )
            st = self.read_state()
        self.rows = self._load_rows()
        self.log(
            f"[artifacts] 目录 {self.root}（"
            + (
                f"续跑：已到 it{st['last_it']}，账本 {len(self.rows)} 行）"
                if resumable and st
                else "新开）"
            )
        )
        return st or {}

    def _load_rows(self) -> list[dict]:
        p = self.root / self.METRICS_NAME
        if not p.exists():
            return []
        rows: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 半截行（被 kill）——跳过，不毒死续跑
            if isinstance(row, dict):
                rows.append(row)
        return rows

    # ---- 逐轮 checkpoint ----

    def checkpoint(
        self,
        it: int,
        *,
        weights_json: bytes,
        opt_tar: bytes,
        row: dict | None = None,
    ) -> dict:
        """落一轮产物（权重 + opt + 账本行 + 状态 + LATEST.zip），返回该轮元信息。

        `row=None` = **只落 checkpoint 不记账**（起点快照专用）：起点不是一轮训练，写进
        账本会让「账本一行 = 一轮、it 唯一」这条不变量失效（下游画曲线/按 it 归因都会
        多出一条空点，进而得靠“过滤掉没有 report 的行”才能绕开——用一个过滤器去掩盖
        一条本不该写的行，是把错误藏进读者）。

        顺序刻意是「先产物后状态」：state.json 指名 last_it 之前，那一轮的文件必须已经
        完整落盘（否则续跑会从一堆不存在/半截的文件上接）。
        """
        d = self.dir_for(it)
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(self.weights_path(it), weights_json)
        if opt_tar:
            atomic_write_bytes(self.opt_path(it), opt_tar)
        wfp = sha256_file(self.weights_path(it))
        entry = {
            "it": int(it),
            "weights_fp": wfp,
            "opt_bytes": len(opt_tar),
            "ts": time.time(),
            **(row or {}),
        }
        if row is not None:
            with open(self.root / self.METRICS_NAME, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.rows.append(entry)
        self._refresh_latest(it, entry)
        atomic_write_json(
            self.root / self.STATE_NAME,
            {
                **(self.read_state() or {}),
                "run_id": self.run_id,
                "state": "running",
                "last_it": int(it),
                "last_weights_fp": wfp,
                "rows": len(self.rows),
                "updated_at": time.time(),
            },
        )
        return entry

    def _refresh_latest(self, it: int, entry: dict) -> None:
        """最新一轮的小包（人可以在会话中途下载、看两眼）。失败不致命（只是便利件）。"""
        try:
            with zipfile.ZipFile(self.root / self.LATEST_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
                z.writestr("metrics_row.json", json.dumps(entry, ensure_ascii=False, indent=1))
                for p in (self.weights_path(it), self.opt_path(it)):
                    if p.exists():
                        z.write(p, arcname=f"it-{int(it):03d}/{p.name}")
                for name in (self.PLAN_NAME, self.MANIFEST_NAME):
                    f = self.root / name
                    if f.exists():
                        z.write(f, arcname=name)
        except OSError as e:  # 磁盘满/权限——不影响训练继续
            self.log(f"[artifacts] LATEST.zip 刷新失败（不致命）: {e}")

    # ---- 收尾 ----

    def finalize(
        self,
        *,
        state: str,
        summary: dict | None = None,
        full_for: Callable[[Path, int], Mapping[str, Path]] | None = None,
    ) -> Path | None:
        """收尾：刷新状态 + 打全量包（`artifacts.zip`）。返回包路径（失败 → None）。

        全量包**只在收尾打**（每轮都打会让 I/O 变成 O(n²)，而逐轮明细本来就在目录里、
        Kaggle 的 output 也是整目录提交）。

        `full_for(root, it)`（2026-10-22，`plan/offline-full-weights-backup`）——**整份权重的
        提供者**，由调用方**注入**（实现见 `remote/land_provider`，命中旁路缓存就是一次 stat）：
        给了某件的整份路径 ⇒ 包内那个名字写**整份**，原件旁存为同主名的 `*.delta`（与 hub 树
        逐字同形：`hub/store_offline.py` 的 `to_write`）。**为什么是注入而不是 import**：落地器住在
        `remote/weights_standard`（L1）而本模块是 L0（被 `hub.store_offline` L1 直接 import）——
        在这里 import 它就是 L1→L3 的向上边（`tests/test_remote_dag.py`）。
        产品根**一个字节都不改**：整份只在 zip 里顶名（`plan §1.2`）。
        """
        st = self.read_state() or {}
        atomic_write_json(
            self.root / self.STATE_NAME,
            {
                **st,
                "run_id": self.run_id,
                "state": str(state),
                "rows": len(self.rows),
                "updated_at": time.time(),
                **({"summary": summary} if summary else {}),
            },
        )
        try:
            with zipfile.ZipFile(self.root / self.ALL_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
                for name in (self.PLAN_NAME, self.MANIFEST_NAME, self.README_NAME, self.STATE_NAME):
                    f = self.root / name
                    if f.exists():
                        z.write(f, arcname=name)
                for name in (self.METRICS_NAME, self.EVAL_LOG_NAME):
                    # eval_log.jsonl 也要进包：云上评的读数只有这一条路回来
                    # （`biz.eval_rows.merge_eval_rows` 在导入时并进课程账本）——
                    # 漏了它就等于「云上白评一轮」。
                    f = self.root / name
                    if f.exists():
                        z.write(f, arcname=name)
                for d in sorted(self.root.glob(f"{self.IT_PREFIX}*")):
                    if not d.is_dir():
                        continue
                    for arc, src in _round_entries(d, self.root, full_for):
                        z.write(src, arcname=arc)
        except OSError as e:
            self.log(f"[artifacts] artifacts.zip 打包失败: {e}")
            return None
        self.log(f"[artifacts] 产物已收尾（{state}）：{self.root / self.ALL_ZIP}")
        return self.root / self.ALL_ZIP

    # ---- 说明文件 ----

    def _readme(self, plan: dict, manifest: dict, plan_sha256: str) -> str:
        """给「拿到这个 zip 的人」看的：有什么、怎么续、怎么评估。

        为什么值得写在产物里：半离线段的长尾用法就是「人从 Kaggle 下载一个 zip，几周后
        想接着跑」——README 是**唯一**还能告诉他怎么接上的东西（本机没有任何账本记录
        这条腿）。
        """
        return f"""battle2 半离线训练产物（kind=run）
====================================

run_id      : {self.run_id}
计划区间    : it{plan.get('start_it')} → it{plan.get('end_it')}（课程预算 iters_total={plan.get('iters_total')}）
计划 sha256 : {plan_sha256}
代码 commit : {manifest.get('commit', '')}
课程指纹    : course_fp={manifest.get('course_fp', '')[:16]}… corpus_fp={str(manifest.get('corpus_fp', ''))[:16]}…

本目录里有什么
--------------
  plan.json        本次自主段的计划（对集参数 + argv 模板）——**续跑的唯一输入**
  manifest.json    本 job 的 manifest 快照（课程全文在 course 字段里）
  state.json       续跑点（last_it / 指纹 / 状态）
  metrics.jsonl    逐轮一行：agg（policy/value/entropy/kl/mean_ret）+ 采集报告
                   （games/winRate/outcomes/totalSamples）+ 墙钟（rollout/ppo/往返）
  it-NNN/          第 NNN 轮结束时的 weights.json（= 下一轮的 init_weights.json）
                   + opt.tar（Adam 动量：续训必需）
  LATEST.zip       最新一轮的小包（中途下载用）
  artifacts.zip    全量包（本文件所在的那一个）

怎么续跑（新会话/换机器都一样）
-------------------------------
  1. 把 artifacts.zip 解到某个目录（例如 Kaggle 的 /kaggle/working 下、Colab 的 Drive 里）；
  2. 在仓库里跑：
         python -m remote.run_loop --artifacts <解压目录> [--budget-sec 3600] [--device cuda]
     它会从 state.json 的 last_it 接着跑到计划的 end_it；`--budget-sec` 让它在会话
     到点前**干净停机**（先落完当轮再退，产物天然可续）。
  3. 想改预算/换设备都不影响续跑性——计划与轮次只认 plan.json。

怎么评估
--------
  评估与门判仍是 hub 侧权威。本段**可选**在云上自评（`--eval-on-cloud`）：打开时本目录
  会多一份 `eval_log.jsonl`（A 层语料逐局行，与 in-loop 同一 schema/同一 wver 定义），
  随本 zip 回到本机后由「导入产物」并进 `tmp/<课程>/eval_log.jsonl`——板子/门判读到的是
  同一口径。没打开也不影响：拿 it-NNN/weights.json 在本机跑 evalA 即可。
"""


#: 产物账本行 → **课程账本行**（`training_log.jsonl` 的 `iteration` 事件）的字段搬运表。
#: 左 = 课程账本字段名（`biz/events.py::write_iteration`，也是控制台读的那一份），
#: 右 = (来源, 键)：`agg` = 产物行的聚合子字典，`report` = 采集报告子字典，`row` = 行本身；
#: 键写成元组就是**下探路径**（如 `("scoreStats", "mean")`）——产物行是分层的，而课程账本平铺。
#: 两个落地方（人工导入 `remote/deliver_zip`、实时补传 `hub.server`）走**同一张表**：
#: 两份翻译必然漂开，而「两腿同字段」正是这张表存在的意义。
LEDGER_FIELDS: tuple[tuple[str, str, str | tuple[str, ...]], ...] = (
    ("winRate", "report", "winRate"),
    ("outcomes", "report", "outcomes"),
    ("samples", "report", "totalSamples"),
    ("ticks", "report", "totalTicks"),
    # 控制台用 `ticks / expectedGames` 算「平均每局时长」⇒ 缺了它这一列恒为 0（看起来像
    # 「跑了零 tick」）。本机账本里 expectedGames = 本轮**计划**局数（`len(pairs)`），
    # 产物行的 report.games = 实际结算局数，两者在这条腿上相等（满配额采集）。
    ("expectedGames", "report", "games"),
    ("rollout_sec", "row", "rollout_sec"),
    ("ppo_sec", "row", "ppo_sec"),
    ("steps", "row", "steps"),
    ("chunks", "row", "chunks"),
    ("policy", "agg", "policy"),
    ("value", "agg", "value"),
    ("entropy", "agg", "entropy"),
    ("kl", "agg", "kl"),
    ("mean_ret", "agg", "mean_ret"),
    # 缰绳与 demo 遥测（2026-09-23，用户发现「demo_bc 全缺」）：这两个键**产物行里一直有**
    # （`agg.kickstart` / `agg.demo_bc`，见 `remote/worker.py` 的 result.agg），只是这张
    # 搬运表没收 ⇒ `iteration` 行永远看不到它们——本机腿（`biz/events.write_iteration`）
    # 逐轮都写，回传/导入腿却恒空，同一张表两腿不可比。「缺数据」与「真的为 0」也不是
    # 一回事：`_dig` 给 None 就不写（旧包无此键），真 0 照写。
    ("kickstart", "agg", "kickstart"),
    ("demo_bc", "agg", "demo_bc"),
    # 逐维度画像与分数统计（控制台的 kills/accuracy/loot 与 score 列读它们）——
    # 2026-09-22 起产物行的 report 里带这两块；更早打的包没有 ⇒ 那几列留空，不编数字。
    ("dim_means", "report", "dimMeans"),
    ("score_mean", "report", ("scoreStats", "mean")),
    ("score_std", "report", ("scoreStats", "std")),
)


def ledger_row_from_metrics(row: Mapping[str, Any], *, run_id: str, source: str) -> dict | None:
    """产物账本行 → 课程账本的 `iteration` 事件（形状不符/没有 it ⇒ None）。

    `source` = 这一行是谁搬进来的（`deliver_import` / `offline_backfeed`）——控制台不读它，
    但复盘/归因需要能一眼分辨「这条曲线从哪来」。`it0` 是起点快照（不是一轮训练）⇒ 不搬。
    搬不到的字段（旧包没有 `dimMeans`/`scoreStats`）**留空**：写 0 会被读成真实读数
    （「真的零击杀」），而缺数据与零不是一回事。
    """
    raw_it = row.get("it")
    if not isinstance(raw_it, (int, float, str)):
        return None
    try:
        it = int(raw_it)
    except (TypeError, ValueError):
        return None
    if it < 1:
        return None
    ev: dict[str, Any] = {
        "event": "iteration",
        "iter": it,
        "time": _ledger_time(row.get("ts")),
        "run_id": str(run_id),
        "source": str(source),
    }
    for name, src, key in LEDGER_FIELDS:
        box = row if src == "row" else row.get(src)
        v = _dig(box, key)
        if v is not None:
            ev[name] = v
    return ev


def _dig(box: object, key: str | tuple[str, ...]) -> object:
    """从（可能分层的）产物行取值：`key` 是元组就逐层下探，任一层不是 dict 就 None。"""
    cur: object = box
    keys = key if isinstance(key, tuple) else (key,)
    for k in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(k)
    return cur


def _ledger_time(ts: object) -> str:
    """账本 `time` 的格式（与 `biz/events.py` 逐字同规：本地时间 `YYYY-MM-DD HH:mm:ss`）。"""
    secs = None
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0:
        secs = float(ts)
    t = time.localtime(secs) if secs is not None else time.localtime()
    return time.strftime("%Y-%m-%d %H:%M:%S", t)


def metrics_row(
    *,
    agg: Mapping[str, Any],
    report: Mapping[str, Any] | None,
    wall_sec: float,
    ppo_sec: float = 0.0,
    rollout_sec: float = 0.0,
    steps: int = 0,
    chunks: int = 0,
    extra: Mapping[str, Any] | None = None,
) -> dict:
    """一行账本（**只记人/复盘需要的**：训练指标 + 采集口径 + 墙钟）。

    刻意不塞 payload/原件：账本是给人看曲线的，体积要能承受几十上百轮。
    """
    row: dict[str, Any] = {
        "agg": {k: float(v) for k, v in (agg or {}).items()},
        "wall_sec": round(float(wall_sec), 2),
        "ppo_sec": round(float(ppo_sec or 0.0), 2),
        "rollout_sec": round(float(rollout_sec or 0.0), 2),
        "steps": int(steps or 0),
        "chunks": int(chunks or 0),
    }
    if report:
        row["report"] = {
            "games": int(report.get("games", 0)),
            "shards": int(report.get("shards", 0)),
            "winRate": float(report.get("winRate", 0.0)),
            "totalSamples": int(report.get("totalSamples", 0)),
            "totalTicks": int(report.get("totalTicks", 0)),
            "elapsedSec": float(report.get("elapsedSec", 0.0)),
            "outcomes": dict(report.get("outcomes", {})),
        }
        # 逐维度均值与分数统计（2026-09-22，additive）：本机账本行是带 `dim_means`/`score_mean`
        # 的（`biz/events.write_iteration` 读 `report.dimMeans`/`report.scoreStats`），而产物行
        # 此前只留摘要 ⇒ 导入后控制台那张表的 kills/accuracy/loot 与 score 列恒空——同一张
        # 表上「本机腿」与「导入腿」逐列不可比（两腿同字段是硬要求）。
        # 代价：`dimMeans` ~15 个 float + 2 个 float/轮（刻意**不**搬 600 点的 scoreList）。
        _dm = report.get("dimMeans")
        if isinstance(_dm, Mapping) and _dm:
            row["report"]["dimMeans"] = {str(k): float(v) for k, v in _dm.items()}
        _ss = report.get("scoreStats")
        if isinstance(_ss, Mapping) and _ss:
            row["report"]["scoreStats"] = dict(_ss)
    # 逐局压缩画像（2026-09-22，additive）：控制台的「耗时/击杀/残血/道具」是**逐局**聚合的
    # （`dashboard/src/server/iters.ts::readRoundActuals`），而这些单局 manifest 只在跑它的那台
    # 机器上——云机离线腿没人把分片拉回本机（轮末 prune 就删了）⇒ 那几列永远空。
    # 带上它们（~200B/局）随轮账本行走，回传/导入都不需要额外通道；**不落课程账本**
    # （那会让账本每轮大 65KB），而是由落地方写成 `it<N>/per-game.json`（读方按文件优先）。
    _pg = report.get("perGame") if report else None
    if isinstance(_pg, list) and _pg:
        row["perGame"] = [e for e in _pg if isinstance(e, Mapping)]
    if extra:
        row.update({str(k): v for k, v in extra.items()})
    return row
