"""remote/artifacts.py —— 半离线训练的**本地产物目录**（kind=run 的交付面）。

为什么有这个模块：`kind=run` 的语义是「hub 只在交接时必需，此后云机自主」。而云端
（Kaggle / Colab）**不是一个可信的持久存储**——会话到点即断（Kaggle 9/12h）、机器随时
会被回收。所以自主段唯一的交付面就是**本机产物目录**：

  * Kaggle：`/kaggle/working/<run>/`（官方的 output 目录，Save Version 即打包下载）；
  * Colab：`/content/drive/MyDrive/<run>/`（挂 Drive 时，跨会话存活），否则 `<cwd>`；
  * 其它：`--artifacts` 显式指定（本机演练 / 自建节点）。

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
最后一轮」。会话被 kill 也只丢正在跑的那一轮——新会话用同一个目录再跑一次即可续上。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def sha256_file(path: str | Path) -> str:
    """文件字节 sha256（与 `dist_common.weights_fingerprint` / hub 的 `_sha256_file` 同义）。

    三处同定义不是巧合：`wver` / `init_weights_fp` / blob 键都靠「同一份字节同一个哈希」
    串起来。**不**依赖 dist_common 的导入，纯 stdlib（云端产物目录也可能被单独搬运）。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


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


def resolve_artifact_dir(
    explicit: str | Path | None,
    *,
    run_id: str,
    work_dir: str | Path,
    env: Mapping[str, str] | None = None,
    exists=os.path.isdir,
) -> Path:
    """产物根目录（显式 > 环境变量 > Kaggle > Colab(Drive 优先) > 工作目录）。

    顺序刻意如此：**显式永远赢**（自建节点/本机演练），其次是 Kaggle（官方 output 目录
    是唯一能一键下载的位置），最后才是 Colab 的 Drive（挂载可能失败——那时退回
    `/content`，人用 `files.download` 拿）。
    """
    e = os.environ if env is None else env
    if explicit:
        return Path(explicit).expanduser()
    from_env = (e.get("BATTLE_ARTIFACT_DIR") or "").strip()
    if from_env:
        return Path(from_env).expanduser()
    tag = f"battle2-{run_id}" if run_id else "battle2-run"
    if is_kaggle(e, exists=exists):
        return Path("/kaggle/working") / tag
    if is_colab(e, exists=exists):
        drive = Path("/content/drive/MyDrive")
        if exists(str(drive)):
            return drive / tag
        return Path("/content") / tag
    return Path(work_dir) / "artifacts" / tag


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """tmp + replace：任何时刻读到的都是完整文件（会话被 kill 也不留半截）。

    公开（同一包内被测代码共用）：补传端写 `delivered.json` 时也必须走这一条——
    「半截的记账文件」与「半截的产物」一样会把续跑判据带偏（读方拿 OSError/ValueError
    当「没记过」，于是重复投递，或者更糟：把已投递的当成未投递）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8"))


# ------------------------------------------------------------------ 产物目录


class ArtifactStore:
    """一个半离线段的产物目录（`kind=run` 的交付面）。

    实例不持有任何「未落盘」的关键状态：`state.json` 是唯一的续跑真值，每次 checkpoint
    都原子刷新——进程随时可以死，卷上永远是一个**自洽**的目录。
    """

    #: 逐轮权重/opt 的子目录名前缀（字典序即轮次序，便于人肉翻看）。
    IT_PREFIX = "it-"
    STATE_NAME = "state.json"
    METRICS_NAME = "metrics.jsonl"
    PLAN_NAME = "plan.json"
    MANIFEST_NAME = "manifest.json"
    README_NAME = "README.txt"
    LATEST_ZIP = "LATEST.zip"
    ALL_ZIP = "artifacts.zip"

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
        # 哈希）——它与本目录写盘的格式不同（hub 走 `rl.plan.dump_plan` 的规范形
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

    def finalize(self, *, state: str, summary: dict | None = None) -> Path | None:
        """收尾：刷新状态 + 打全量包（`artifacts.zip`）。返回包路径（失败 → None）。

        全量包**只在收尾打**（每轮都打会让 I/O 变成 O(n²)，而逐轮明细本来就在目录里、
        Kaggle 的 output 也是整目录提交）。
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
                if (self.root / self.METRICS_NAME).exists():
                    z.write(self.root / self.METRICS_NAME, arcname=self.METRICS_NAME)
                for d in sorted(self.root.glob(f"{self.IT_PREFIX}*")):
                    if not d.is_dir():
                        continue
                    for p in sorted(d.iterdir()):
                        if p.is_file():
                            z.write(p, arcname=f"{d.name}/{p.name}")
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

怎么评估（本产物不含评估）
--------------------------
  评估与门判是 hub 侧权威，本段**不**在云上自评。拿 it-NNN/weights.json 在本机按课程
  跑 eval（`bun run eval:…` / EvalBoard）即可，口径与其它腿一致。
"""


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
    if extra:
        row.update({str(k): v for k, v in extra.items()})
    return row
