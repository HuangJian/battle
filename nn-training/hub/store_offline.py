"""hub/store_offline.py — 离线段的自回传产物（第二份拷贝 + 续跑锚点）。

`_JobStore` 的六个域混入之一（S4 第十四刀）。它独立成簇的理由是**与 job 队列完全隔离**：
不写 `job_pending` / `job_completed`（补传没有 job 也没有租约，不存在「谁来领」的问题），
只落 `offline/<run_id>/it-NNN/` 与**自己那本**账（`metrics.jsonl`，与 job 队列的 jsonl
**不同文件**——混进同一本账会让「一行一 job」的读方（控制台 / 池重建）出现怪行）。

`complete_rounds` 的判据是「三件齐全」（`RESUME_PARTS`）：续跑要么重放 Adam 动量（缺 opt
就是动量归零），要么丢指标行（那轮在曲线上消失）——两者都是「看起来能跑但读数少一截」。
所以本簇的落盘一律走 `_write_bytes`（原子写）。

## 依赖方向

`store_offline → {common.protocol, common.fs, remote.artifacts, remote.delta_files}`（向下）；
跨域调用只有 `self._append_ledger`（住 `store_ledger`）。**不 import 任何兄弟混入**。

回传 delta 的**就地落地**（`stage`）也**不 import**：落地器是接收边界（`hub/queue_resume`）注入的
—— 本模块是 L1 叶子（层号 = 拓扑秩，`tests/test_remote_dag.py`），import L2 的 `hub.deltas`
会把它顶到 L3 而整个 hub 栈跟着涨一层。`remote.delta_files`(L0) 只用来判形/定名，合法。

## 随簇搬来的 `_write_bytes`

它原先住 `hub_server.py`（模块级），唯一调用方就是本簇的逐件落盘与段末覆盖写。它是
`common.fs.atomic_write_bytes` 的**转调**——之所以还留一层名字，是因为 `common/fs.py` 与
`common/__init__.py` 头部把「曾有两份同名实现」记进了本仓的重复清单（历史，不必改）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

import remote.delta_files as delta_files
from common.fs import atomic_write_bytes, same_bytes
from common.hashing import sha256_file
from common.protocol import ProtocolError, decode_opt_tar, decode_weights_json, sanitize_run_id
from remote.artifacts import (
    WEIGHTS_ARCHIVE_ROOT as WEIGHTS_ARCHIVE_ROOT,
)
from remote.artifacts import (
    WEIGHTS_ARCHIVE_ROOT_ENV as WEIGHTS_ARCHIVE_ROOT_ENV,
)
from remote.artifacts import (
    ArtifactStore,
    ledger_row_from_metrics,
)
from remote.artifacts import (
    weights_archive_root as _weights_archive_root,
)

#: 仓库根 —— 课程配置与权重归档的相对路径基准。
#: 2026-09-30（刀 1，hub 出包）：本模块从 `nn-training/remote/hub/store_offline.py` 搬到
#: `nn-training/hub/store_offline.py`，**少了一层** ⇒ 上溯 4 层变 3 层（`parents[3]` → `parents[2]`）。
#: 这是个静默类错误：层数是错的，但代码不报错，只是 REPO_ROOT 指到了仓库的**父目录**
#: （工装用例 `tests/hub/test_multi_course_hub.py::test_backup_target_follows_the_course_config` 当场抓住）。
REPO_ROOT = Path(__file__).resolve().parents[2]
#: 权重归档根/开关与取用入口（`_weights_archive_root`）的**唯一定义**已下沉到 `remote.artifacts`
#: （2026-10-15 去重）：本模块与 `remote.weights_standard` 曾各留一份逐字节相同的实现 ——
#: reuse 账本当场红，而两者都够得着的最低层就是那个 L0 产物模块。名字一律 `import ... as ...`
#: 门面（同对象、同 patch 语义）：`tests/test_production_isolation.py` 读常量，
#: `hub/backfill_offline.py` 与 `hub/store_offline` 自己的两处调用点读函数。


#: 课程配置目录（`nn-training/curricula/<课>.jsonc`）——归档 prefix/dir 的单一事实来源。
CURRICULA_DIR = REPO_ROOT / "nn-training" / "curricula"

#: 落地器的形状：`{"weights"|"opt": 原始字节} → {"weights.json"/"opt.tar": 整份字节，
#: "landed": {件: 状态}, "note": 失败原因}`。实现住 `hub/deltas.land_into_stage`（L2），
#: 由**接收边界**（`hub/queue_resume`）注入 —— 本模块不 import 它，理由见
#: `store_offline_artifact` 的 `stage` 段。
Stager = Callable[[dict[str, bytes]], dict]


def _round_is_complete(it_dir: Path) -> bool:
    """回传轮是不是**三件齐全**（两种名字都认；`row.json` 只有一种名字）。

    权重/动量那两件的名字**跟着内容走**（`weights.json`/`weights.delta`、`opt.tar`/`opt.delta`，
    2026-10-15 后缀约定）⇒ 一律走 `remote.delta_files.find_existing`（两种名字都认），
    不自己拼名字。本函数是 hub 受理回传时的判据，也是补做工具（`hub/backfill_offline`）
    与续跑锚点（`hub/queue_resume`）的那一份**同一实现**。
    """
    return (
        delta_files.find_existing(it_dir, "weights.json") is not None
        and delta_files.find_existing(it_dir, "opt.tar") is not None
        and (it_dir / "row.json").is_file()
    )


def _archived(bdir: Path, prefix: str, it: int) -> bool:
    """该轮的归档名（两种后缀）在不在（归档只写一次的判据）。"""
    return bool(sorted(bdir.glob(f"{prefix}.it{it}.*")))


def _write_bytes(path: Path, data: bytes) -> None:
    """原子写（半截权重比没有权重更危险）——转调 `common.fs.atomic_write_bytes`。

    唯一实现见 `common.fs.atomic_write_bytes`（与 `remote/artifacts.atomic_write_bytes`
    同源；本仓那次去重的记录在 `common/fs.py` 头部）。
    """
    atomic_write_bytes(path, data)


def _same_bytes(path: Path, blob: bytes) -> bool:
    """文件与字节是否逐字节相同（**先比 size，再分块比**：幂等重传是常态）。

    2026-10-17（plan/hub-server-memory-slim L2）：实现搬到 `common.fs.same_bytes` ——
    「整份入内存只为比一下」是 hub 6.2GB RSS 的次要峰值源，分块后峰值 = 一块。
    """
    return same_bytes(path, blob)


def _heal_round(it_dir: Path, to_write: dict[str, bytes], *, w_full: bytes, wj: bytes) -> list[str]:
    """幂等命中时把「形态没到位」的名字补上，返回真写了哪些（空 = 本来就齐）。

    两种必须治的形状（2026-10-15）：① 第一次落地失败 ⇒ 盘上只有 `weights.delta`（原件保留）；
    ② 旧数据 ⇒ 容器占着 `weights.json` 这个名字（`tmp/y5-term` 的形状）。补完这轮就能当锚点
    （`resume_anchor` 只挑整份轮），而不是等人跑清扫工具。

    两条红线：
      * **绝不把容器写进 `weights.json`**（那是「可装载权重」这个身份的载体）——落地失败时
        `w_full is wj`，整份那格直接跳过；
      * **绝不覆盖不同字节**（不同字节在调用方那一步已经判成 conflict）——只写内容不同的那些；
        原件字节一个不丢（容器旁存 `.delta`，两版都留）。
    """
    wrote: list[str] = []
    for name, blob in to_write.items():
        if name == "weights.json" and w_full == wj:
            continue
        if _same_bytes(it_dir / name, blob):
            continue
        _write_bytes(it_dir / name, blob)
        wrote.append(name)
    return wrote


def _token_of(raw: object) -> str:
    """远端来的「身份字段」只认**非空 `str`**（其它类型一律当没给）。

    体是远端来的字节：一个 dict/`int` 落进账本就会把 dashboard 的读面打红，而那不是本任务要修的
    东西 —— 入口该挡住（与 `sanitize_run_id` 同一条思路：先验证，再落盘）。
    """
    return raw.strip() if isinstance(raw, str) else ""


def _artifact_identity(body: dict, hold_meta: dict[str, Any] | None) -> dict[str, str]:
    """这一轮补传的**归属身份**（两个键，分工不同，别合成一个）——plan/offline-leg-ppo-attribution。

    为什么要它：账本行原本**一个身份字段都没有** ⇒ dashboard 的 PPO 组看不见自主腿完成的轮
    （用户 2026-10-12 报障：「kaggle 自主 worker 完成了 600 轮 ppo 任务，却不显示在节点贡献统计里」）。

      * `worker` = **claim 身份**（`{HOSTNAME}-{uuid8}`，`offline_boot.worker_id_of`）：客户端自报优先，
        否则用**本课 hold 镜像**（`hold_meta`）兜底——补传天然发生在 claim→release 之间，那正是它的
        窗口。这里**不判活性**：不回答「hold 还活着吗」（那是派发闸 `hold_blocked` 的活），
        只回答「这轮是谁推的」；
      * `worker_name` = **可读名**（`kaggle-o`，面板列名）：**只有客户端自报**这一条来源——
        别拿 claim 身份去填（填了面板就多一行 `3908d414c3ca-…` 假列名）。

    调用点：`store_offline_artifact` 的**两处落点**（`<run>/metrics.jsonl` 与课程账本）都要带；
    在 metrics 行里还必须排在 `**row` **之后**（`row` 是客户端给的字节，位置就是「谁说了算」）。

    两者都没有 ⇒ **该键整个缺席**（不写空串、不写 `null`、不编）：读方按既有 `(未登记)` 口径渲染。
    老行不回填、不猜（hold 已清 ⇒ 历史轮拿不回身份）。
    """
    out: dict[str, str] = {}
    name = _token_of(body.get("worker_name"))
    if name:
        out["worker_name"] = name
    who = _token_of(body.get("worker")) or _token_of((hold_meta or {}).get("worker_id"))
    if who:
        out["worker"] = who
    return out


def _metrics_seen(path: Path) -> set[tuple[int, str]]:
    """`<run>/metrics.jsonl` 里已有的 `(it, weights_fp)` —— 统计补投的**幂等判据**。

    读不动/半行 ⇒ 当它不存在（那只会导致重投一轮，统计可再生；漏掉才是真的丢）。
    """
    out: set[tuple[int, str]] = set()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        it = r.get("it")
        if isinstance(it, bool) or not isinstance(it, int):
            continue
        out.add((int(it), str(r.get("weights_fp") or "")))
    return out


class OfflineRoundsMixin:
    """域混入：见模块头部。"""

    # ---- 由组合类 / 兄弟簇提供（混入只见 `self`，实现不在本模块）----
    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock
    #: 时钟（`now_fn` 注入点，住 `_AuthGuard`）
    _now: Any
    #: 构造身份：组合类 `__init__` 直接声明
    job_root: Path
    jsonl_path: Path
    #: 本课程的 hold 镜像（`LeaseMixin` 声明、`_HubQueue._sync_hold` 推）：补传行的 claim 身份
    #: 兜底来源（plan/offline-leg-ppo-attribution §3-A）——只读，**不写**（写者仍是 `_HubQueue`）。
    hold_meta: dict[str, Any]
    #: 兄弟簇 `store_ledger` 的方法（补传产物要与 job 账本**分家**落自己那本，但仍走同一套追加原语）
    _append_ledger: Any

    # ---- 产物补传（offline 腿；2026-09-17）----
    # 语义：节点自主段的**第二份拷贝**。产物本来就已经落在节点本地目录里（那是它的交付
    # 面）；这里接收的是「中途发现 hub 可达」时顺手推上来的那一份，让控制面不用等人搬 zip。
    # 与 job 队列**完全隔离**：不写 job_pending/job_completed（补传没有 job 也没有租约，
    # 这条腿不存在「谁来领」的问题），只落 `offline/<run_id>/` 与账本 audit 事件。
    #: 补传落位根目录名（`<job_root>/offline/<run_id>/`）。
    OFFLINE_DIR = "offline"

    #: 补传账本文件名（`offline/<run_id>/` 下；与 job 队列的 jsonl **不同文件**——
    #: 补传不是 job，混进同一本账会让「一行一 job」的读方（控制台/池重建）出现怪行）。
    OFFLINE_METRICS_NAME = "metrics.jsonl"

    #: 段末摘要文件名（同一目录；覆盖写）。
    OFFLINE_RESULT_NAME = "result.json"

    #: 续跑锚点必须**同轮齐全**的三件（用户 2026-09-22 口径：缺一件就退到更早轮）。
    #: ⚠ 权重/动量这**两件的名字随形态走**（`weights.json`/`weights.delta`、`opt.tar`/`opt.delta`，
    #: 2026-10-15 后缀约定）⇒ 齐全判据一律走 `remote.delta_files.find_existing`（两种名字都认），
    #: 本元组只留着表达「哪三件」（`row.json` 只有一种名字）。
    RESUME_PARTS: tuple[str, ...] = ("weights.json", "opt.tar", "row.json")

    #: 回传轮的**交付镜像**目录名（`<traj>/deliver/<run_id>/it-NNN/`）。`deliver/` 此前只有
    #: 人工导入写（`dashboard/src/server/bundles/import.ts`）⇒「导入的段」与「回传的段」分居
    #: 两棵树、找东西要翻两处（用户 2026-09-23 口径：统一命名空间）。
    DELIVER_DIR = "deliver"
    #: 段内**活动权重**指针（`<traj>/weights.json`）——本机循环与回传腿共用同一个文件。
    ACTIVE_WEIGHTS_NAME = "weights.json"

    def complete_rounds(self) -> dict[int, dict]:
        """自回传产物（`offline/<run_id>/it-NNN/`）里**三件齐全**的轮次：`{it: {run_id, dir}}`。

        齐全 = weights + opt + row 都在：续跑要么重放 Adam 动量（缺 opt 就是动量归零），
        要么丢指标行（那轮在曲线上消失）——两者都是「看起来能跑但读数少一截」。
        """
        out: dict[int, dict] = {}
        base = self.job_root / self.OFFLINE_DIR
        try:
            run_dirs = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            return out
        for run_dir in run_dirs:
            try:
                it_dirs = sorted(p for p in run_dir.iterdir() if p.is_dir())
            except OSError:
                continue
            for it_dir in it_dirs:
                if not it_dir.name.startswith("it-"):
                    continue
                try:
                    it = int(it_dir.name[3:])
                except ValueError:
                    continue
                if not _round_is_complete(it_dir):
                    continue
                out[it] = {"run_id": run_dir.name, "dir": str(it_dir)}
        return out

    def offline_run_dir(self, run_id: object) -> Path:
        """补传落位目录。`run_id` 来自远端 ⇒ 必须先过 `sanitize_run_id`（它会是目录名）。"""
        return self.job_root / self.OFFLINE_DIR / sanitize_run_id(run_id)

    def store_offline_artifact(
        self, body: dict, *, advance_active: bool = True, stage: Stager | None = None
    ) -> dict:
        """落一轮补传产物，返回 {"status": "accepted"|"duplicate", "it": n, "run_id": r}。

        `stage`（★ 2026-10-15 用户口径：hub 收到 delta 就**就地重建为整份**）——由**接收边界**
        （`hub/queue_resume.QueueResumeMixin.store_offline_artifact`，L3）注入 `hub.deltas.land_into_stage`：
        先重建、再记账是接收边界的职责，本模块是 L1 叶子（`tests/helpers/remote_dag.py`：层号 = 拓扑秩），
        import `hub/deltas`(L2) 会把本模块顶到 L3 而整个 hub 栈跟着涨一层。
        `stage=None`（单测 / 没有落地器的调用点）⇒ 原件按内容定名原样落盘，不重建。

        `advance_active=False`（★P1-7 / R3-f）⇒ 这轮回传**不得推进活动权重**
        （`weights.json`）：旧主（hold 已 stale）或没带对 token 的迟到回传，其终点不是
        「当前进度」，把它拉回旧轮就是报障一的另一半。判据在 `offline_advance_ok`
        （★M4b：旧口径的 `pinned_online` 随模式退役）。镜像/归档照落（它们是「算过什么」
        的证据，不是「现在在哪」的声明）。

        校验（任一不过抛 ProtocolError → 400，且**不落盘任何东西**）：
          * `run_id` 合法（目录名的唯一防护面）；
          * `it` 是非负整数；
          * `weights_json` 能解码出**非空**字节；
          * **声明指纹与实际字节相符**——传输损坏（截断/串包）必须在入口拦住，否则一条
            损坏的权重会以「hub 上的产物」身份进入 eval/续跑，而真因在几千行日志之外。

        幂等：`it-NNN` 里已经有**同一份字节**的权重（整份 `weights.json` 或原件 `weights.delta`，
        判据是内容、不是名字）⇒ duplicate。此时**只补**「形态没到位」的那些名字（`_heal_round`：
        第一次落地失败只剩 `.delta`、旧数据容器占着 `.json`）—— 同一轮权重是不可变快照，补的是
        同一个内容的另一个名字；**覆盖不同字节仍然是 conflict**（见下）。

        ★ 2026-10-11 事故新增：已存在但**字节不同** ⇒ `conflict`（调用方透成 409）。
        事故现场就是这个形状的反面：起点（无账本行）先占了 `it-1`，而真 it1 推上来被判 duplicate
        （=「成功」）静默丢弃 ⇒ 真权重与读数永不到岸，而运维以为那一轮已经回传了。
        响比静默好：一份不匹配的**不可变快照**只能由人来裁决（哪一份才是这一轮的真实产物）。
        """
        run_id = sanitize_run_id(body.get("run_id"))
        it = body.get("it")
        if not isinstance(it, int) or isinstance(it, bool) or it < 0:
            raise ProtocolError(f"补传 it 非法（要求非负整数）: {it!r}")
        wj_raw = body.get("weights_json")
        if not isinstance(wj_raw, str) or not wj_raw:
            raise ProtocolError("补传缺 weights_json（权重是这一轮唯一不可再生的东西）")
        wj = decode_weights_json(wj_raw)
        if not wj:
            raise ProtocolError("补传 weights_json 解码后为空")
        declared = str(body.get("weights_fp", "") or "")
        got = hashlib.sha256(wj).hexdigest()
        if declared and declared != got:
            raise ProtocolError(
                f"补传 it{it} 的权重指纹不符：声明 {declared[:16]}… 实得 {got[:16]}…"
                "（传输损坏）——拒收"
            )
        row = body.get("row")
        # 账本行自称的权重指纹必须与实收字节一致：这是**节点自己产的**一致性证据
        # （`ArtifactStore.checkpoint` 写权重后当场算的 sha）。不符 = 产物目录内部不一致
        # （人改过 / 半截写入），把这样的权重收成「hub 上的产物」比拒收危险得多。
        if isinstance(row, dict) and row.get("weights_fp"):
            row_fp = str(row["weights_fp"])
            if row_fp != got:
                raise ProtocolError(
                    f"补传 it{it} 的账本行与权重不符：行记 {row_fp[:16]}… 实得 {got[:16]}…"
                    "（产物目录内部不一致）——拒收"
                )
        opt = b""
        opt_raw = body.get("opt_tar_b64")
        if isinstance(opt_raw, str) and opt_raw:
            try:
                opt = decode_opt_tar(opt_raw)
            except Exception:  # opt 损坏不必拒整轮：代价只是「hub 侧续跑 Adam 归零」
                opt = b""
        # ★ 2026-10-15（用户口径）：回传的可能是 **delta 容器** ⇒ **在 hub 进程里就地重建为整份**
        #   ——此后 hub 树里的一切（回传树/镜像/归档/活动指针/锚点）都是整份，派发侧永远只发
        #   整份（云机已有就不传：`init_weights_fp`/`opt_sha` + 内容寻址 blob_cache 本来就在），
        #   trainer 与 TS 两端看不到 delta。落地在**暂存目录**里做（`it-NNN/` 那份是不可变快照，
        #   首写锁定 + 409 冲突那套不许被覆盖）；失败也**不丢字节**：原件照落成 `*.delta`。
        #   落地器是**注入的**（`stage`，见 docstring）：本模块不 import `hub/deltas`。
        staged: dict = stage({"weights": wj, "opt": opt}) if stage is not None else {}
        # 缺席/落地失败 ⇒ 回落到原件（“绝不静默丢字节”），此时 `landed` 会把失败说出来。
        w_full: bytes = staged.get("weights.json") or wj
        o_full: bytes = staged.get("opt.tar") or opt
        landed: dict = staged.get("landed") or {}
        note = str(staged.get("note") or "")
        #: 这一轮回传的 opt **形态**（`plan/anchor-full-rounds` S4）：按内容判（不看名字）。
        #: 读方靠它回答「这一轮发的是整份还是 delta」——省量只能从账本上量，不能靠推断。
        opt_form = delta_files.form_of(opt)
        full_fp = hashlib.sha256(w_full).hexdigest()
        with self._lock:
            d = self.offline_run_dir(run_id)
            it_dir = d / f"it-{int(it):03d}"
            # 两个形态各写各的名字（名字自述形态，两版都留）：整份 → `weights.json`/`opt.tar`，
            # 原件 → `*.delta`（只在它确实是容器时）。落地失败 ⇒ 只有 `*.delta`（锚点那边会跳过它）。
            to_write: dict[str, bytes] = {delta_files.named_for("weights.json", w_full): w_full}
            if delta_files.named_for("weights.json", wj) != delta_files.named_for("weights.json", w_full):
                to_write[delta_files.named_for("weights.json", wj)] = wj
            if opt:
                to_write[delta_files.named_for("opt.tar", o_full)] = o_full
                if delta_files.named_for("opt.tar", opt) != delta_files.named_for("opt.tar", o_full):
                    to_write[delta_files.named_for("opt.tar", opt)] = opt
            have_full = it_dir / "weights.json"
            have_raw = it_dir / "weights.delta"
            # ★ 2026-10-17：只记**路径**不读字节（`have in (w_full, wj)` 改成分块比对，
            # 冲突时才整份算一次哈希——那本来就是稀有路径；判据逐字不变）。
            have_path: Path | None = None
            if have_full.is_file() and not delta_files.any_container_file(have_full):
                have_path = have_full
            elif have_raw.is_file():
                have_path = have_raw
            elif have_full.is_file():  # 旧数据：`weights.json` 里装着容器（名字没跟着内容走）
                have_path = have_full
            if have_path is not None:
                # 幂等/冲突判据 = **同一轮的权威字节**：整份先到、或原件先到，都算命中。
                # ★ 2026-10-15 修：旧口径在「原件命中」上加了个 `not weights.json.is_file()` 的
                #   条件 ⇒ 旧数据（`.json` 里装容器）**同一份字节**重传会被判 conflict 409，
                #   而客户端把 409 当硬拒（`remote/offline_deliver._reject_round`）⇒ 一轮正确产物
                #   永久出局。丢包/超时重传是常态（下面那段注释就是这么写的），不能这么判。
                if same_bytes(have_path, w_full) or same_bytes(have_path, wj):
                    healed = _heal_round(it_dir, to_write, w_full=w_full, wj=wj)
                    if healed:
                        print(
                            f"[hub-server] 补传 it{int(it)} 幂等命中：补落 {'、'.join(healed)}"
                            "（内容同源，原件旁存）",
                            flush=True,
                        )
                    return {
                        "status": "duplicate",
                        "run_id": run_id,
                        "it": int(it),
                        "landed": landed,
                        "healed": healed,
                    }
                # 冲突是**稀有路径**（要人裁决）⇒ 这里才读文件算哈希，且分块算（L2）。
                have_fp = sha256_file(have_path)
                return {
                    "status": "conflict",
                    "run_id": run_id,
                    "it": int(it),
                    "have_fp": have_fp,
                    "got_fp": full_fp,
                    "error": (
                        f"补传冲突 it{int(it)}：hub 已有**不同**字节的权重"
                        f"（hub {have_fp[:12]}… 本次 {full_fp[:12]}…）"
                        "——不覆盖、需人裁决"
                    ),
                }
            it_dir.mkdir(parents=True, exist_ok=True)
            for name, blob in to_write.items():
                _write_bytes(it_dir / name, blob)
            _write_bytes(
                it_dir / "row.json",
                json.dumps(
                    row if isinstance(row, dict) else {"it": int(it)},
                    ensure_ascii=False,
                    indent=1,
                ).encode("utf-8"),
            )
            if not (d / "run.json").exists():
                _write_bytes(
                    d / "run.json",
                    json.dumps(
                        {
                            "run_id": run_id,
                            "plan_sha256": str(body.get("plan_sha256", "") or ""),
                            "course_fp": str(body.get("course_fp", "") or ""),
                            "commit": str(body.get("commit", "") or ""),
                            "source_dir": str(body.get("source_dir", "") or "")[:300],
                            "first_seen": self._now(),
                        },
                        ensure_ascii=False,
                        indent=1,
                    ).encode("utf-8"),
                )
            # 账本一行 = 一轮（只在**接新**时追加；重复投递不再写——否则同一轮会出现两行，
            # 而这个文件的读方（人/控制台）按 it 画曲线）。
            with open(d / self.OFFLINE_METRICS_NAME, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "event": "offline_artifact",
                            "run_id": run_id,
                            "it": int(it),
                            "weights_fp": got,
                            "landed_fp": full_fp,
                            # ★ 空串 = 整份到手；有值 = 没到手 + 原因（读方据此判断这一轮能不能直接用）
                            "land_note": str(staged.get("note") or ""),
                            "opt_bytes": len(opt),
                            "opt_form": opt_form,
                            **(row if isinstance(row, dict) else {}),
                            # ★ 归属身份排在 `**row` **之后**：那是**位置判据**（客户端给的字节不许
                            #   冒名顶替 hub 的认定），别顺手挪到上面去。
                            **_artifact_identity(body, self.hold_meta),
                            "ts": self._now(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            self._append_ledger(
                {
                    "event": "offline_artifact",
                    "run_id": run_id,
                    "it": int(it),
                    "weights_fp": got,
                    # hub 手上那份（**落地后的整份**）的地址：与 `weights_fp`（回传原件）分开记——
                    # 读方按"我能不能直接用它"选地址时看这一个。
                    "landed_fp": full_fp,
                    "opt_form": opt_form,
                    **_artifact_identity(body, self.hold_meta),
                    "ts": self._now(),
                }
            )
            self._land_round_metrics(row, run_id=run_id, it=int(it))
            # 镜像/活动指针/归档一律用**落地后的整份**（那三处是给人/控制台/TS 读的）。
            self._land_offline_round_extras(
                it=int(it),
                run_id=run_id,
                weights_json=w_full,
                opt=o_full,
                row=row,
                advance_active=advance_active,
            )
        # ★ 2026-10-23（现场教训）：**「这一轮没拿到整份」必须留痕**。两种形态：
        #   ① `stage` 缺席（本进程没带整份落地器——hub 是 2026-10-15 之前启动的老进程就长这样）：
        #      `staged` 是空、`note` 也是空 ⇒ 2026-10-23 实测 y31-null **28 轮全落成 `.delta`**，
        #      而全程零提示，只能靠人去数文件才发现；
        #   ② `stage` 在、但基准不在 hub 侧（`note` 非空，原有路径）。
        # 两者都写进账本行与返回值（不只是 stdout）。
        if not note and delta_files.named_for("weights.json", wj) != "weights.json" and full_fp == got:
            note = (
                "no-stage: 本 hub 进程没有整份落地器（stage 未接线 / 进程未重启到带它的代码）"
                "——原件已保留；重启 hub 后跑 tools/offline-weights-restore.py 可补"
            )
        if note:
            print(
                f"[hub-server] 回传 it{int(it)} 未落地为整份（原件保留，锚点会跳过它）"
                f"：{note}",
                flush=True,
            )
        return {
            "status": "accepted",
            "run_id": run_id,
            "it": int(it),
            "landed": landed,
            "land_note": note,
            "raw_fp": got,
            "weights_fp": full_fp,
        }

    def store_offline_metrics(self, body: object) -> dict:
        """★ 2026-10-23（`plan/offline-deliver-full-and-gaps` §3.4 / S4）：**只带统计**的补传。

        为什么要有第二条入口：抖动期「权重发不动、但读数不能丢」，以及 `anchors` 档里锚点之间
        那几轮——它们从来没有权重上行的机会，而控制台的胜率/击杀曲线就靠这半边。

        落点只有两处（**不碰权重那一半**）：
          ① `<run>/metrics.jsonl`（补传自己的账）；
          ② 课程侧 `training_log.jsonl`（`_land_round_metrics`，控制台指标表读它）。

        **不创建 `it-N/`、不碰活动指针/归档/交付镜像**：它没有权重，写进去就是脏轮
        （`resume_anchor` 会看到一个没有权重的轮）。

        幂等：按 `(it, weights_fp)` 去重——同一轮重复投递 ⇒ 跳过（不追加第二行）。
        失败（体不对 / `it` 非法）⇒ 抛 `ProtocolError`（HTTP 400），**不落任何东西**。
        """
        if not isinstance(body, dict):
            raise ProtocolError("metrics 体必须是 JSON 对象")
        run_id = sanitize_run_id(body.get("run_id"))
        rows = body.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ProtocolError("metrics 体缺 `rows`（非空数组）")
        with self._lock:
            d = self.offline_run_dir(run_id)
            d.mkdir(parents=True, exist_ok=True)
            mpath = d / self.OFFLINE_METRICS_NAME
            seen = _metrics_seen(mpath)
            fresh: list[dict] = []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                it = raw.get("it")
                if not isinstance(it, int) or isinstance(it, bool) or it < 0:
                    continue
                raw_row = raw.get("row")
                row: dict = raw_row if isinstance(raw_row, dict) else {}
                fp = str(row.get("weights_fp") or raw.get("weights_fp") or "")
                key = (int(it), fp)
                if key in seen:
                    continue
                seen.add(key)
                fresh.append({"it": int(it), "row": row, "weights_fp": fp})
            if fresh:
                lines: list[str] = []
                for item in fresh:
                    lines.append(
                        json.dumps(
                            {
                                "event": "offline_metrics",
                                "run_id": run_id,
                                "it": item["it"],
                                # landed_fp 缺失是有意的：这一轮**没有权重落地**
                                # ⇒ 读方按「能不能直接用它」选地址时不会误选它。
                                "weights_fp": item["weights_fp"],
                                "parts": "metrics",
                                **item["row"],
                                "ts": self._now(),
                            },
                            ensure_ascii=False,
                        )
                    )
                self._append_metrics_lines(mpath, lines)
                for item in fresh:
                    self._land_round_metrics(
                        item["row"], run_id=run_id, it=item["it"], weights_landed=False
                    )
        return {
            "status": "accepted",
            "run_id": run_id,
            "it": [f["it"] for f in fresh],
            "rows": len(fresh),
            "skipped": max(0, len(rows) - len(fresh)),
        }

    def _append_metrics_lines(self, path: Path, lines: list[str]) -> None:
        """把统计行追加进 `<run>/metrics.jsonl`（每行一条，与既有写法同形）。"""
        try:
            with open(path, "a", encoding="utf-8") as f:
                for ln in lines:
                    f.write(ln + "\n")
        except OSError as e:
            raise ProtocolError(f"写 {path.name} 失败：{e}") from e

    def _land_round_metrics(
        self, row: object, *, run_id: str, it: int, weights_landed: bool = True
    ) -> None:
        """把这一轮的度量搬进**课程侧**（实时回传也能让控制台指标表动起来）。

        用户之问（2026-09-22）：「云机通过网络请求回传，会算这些数据回显吗？」——之前**不会**：
        回传只落 `remote-jobs/offline/<run_id>/it-NNN/{weights,opt,row}.json` + 一条
        `offline_artifact` 事件（没有 `iteration` 事件，也没人把逐局画像铺到读方能找到的地方）
        ⇒ 权重/优化器都在、末轮也能评，但控制台的「各轮指标表」（含耗时/击杀/残血/道具）
        一行不显示，而且「没有 `iteration` 事件」这件事连人工导入都能修正、实时回传不能。

        现在：与人工导入（`remote/deliver_zip`）走**同一张翻译表**
        （`remote.artifacts.ledger_row_from_metrics`）+ 同一个逐局画像落点
        （`<课程>/it<N>/per-game.json`），两路结果逐字段一致。

        只住课程目录（`jsonl_path` 的父目录）：hub 的 `--jsonl` 就是
        `<traj_root>/training_log.jsonl`，课程侧与它是同一个根。权重轮（duplicate 之后）
        走不到这里——只有接新才写。**统计批**则是另一回事：它没有权重，写进来的是一条
        `weights_landed=false` 的**临时** `iteration`，不能参与「哪一轮已落权重」的判据
        （否则一个跑在前面的统计行会把活动权重指针冻住，见 `_ledger_has_newer_iter`）。
        任何失败只记日志：回传的主价值是权重到岸。
        """
        if not isinstance(row, dict):
            return
        try:
            # 统计批的 row 是 `{"it": n, "row": {...}}` 里**内层**那一份（可能没有 `it`）；
            # `ledger_row_from_metrics` 用 row["it"] 当轮号。**外层轮号是权威**，覆盖之——
            # 否则统计行要么进不了课程账本（D2 的 `training_log.jsonl` 半边静默缺失），要么
            # 按内层一个不一致的旧 it 落位。
            row = {**row, "it": int(it)}
            ev = ledger_row_from_metrics(row, run_id=run_id, source="offline_backfeed")
            if ev is not None and not weights_landed:
                # 显式标记：这个 it 只有读数、没有权重。读方（活动指针 / resume / 门禁）
                # 必须能把它与真正落过权重的那一轮分开。
                ev["parts"] = "metrics"
                ev["weights_landed"] = False
            traj = self.jsonl_path.parent
            it_dir = traj / f"it{it}"
            pg = row.get("perGame")
            if isinstance(pg, list) and pg:
                it_dir.mkdir(parents=True, exist_ok=True)
                (it_dir / ArtifactStore.PER_GAME_NAME).write_text(
                    json.dumps(pg, ensure_ascii=False), encoding="utf-8"
                )
            if ev is not None:
                self._append_ledger(ev)
        except Exception as e:  # 观测面不拖垮回传
            print(
                f"[hub-server] 补传 it{it} 的课程侧度量落位失败（忽略）：{type(e).__name__}: {e}",
                flush=True,
            )

    def _ledger_has_newer_iter(self, it: int) -> bool:
        """课程账本里是否存在**比 `it` 更新**的已落轮次（`iteration.iter` / `offline_artifact.it`）。

        为什么用账本而不是另开一个 sidecar：这张账本**两条腿都写**（本机循环每轮写
        `iteration`、回传腿经 `_land_round_metrics` 也写），所以「课程已知的最新轮」是两腿
        合用的单一判据 —— 不需要额外状态，也就不会出现「另一条腿推进了我不知道」的打架。

        只认这两种事件：`job_pending`/`job_cancelled` 之类虽然也带 `it`，但它们说的是**某台
        机器上的一个 job**（发了还没落权重），不是「这一轮已落」——拿它们当判据会让一个发了
        又取消的更大 it 永久挡住活动权重推进。
        账本不存在 ⇒ False（新课程，没有任何更新轮次）；存在却读不到 ⇒ True（保守不推进：
        宁可不写，也不覆盖可能更新的权重）。
        """
        best = 0
        if not self.jsonl_path.exists():
            return False  # 新课程还没有账本 ⇒ 没有任何更新轮次（不是“读不到”）
        try:
            text = self.jsonl_path.read_text(encoding="utf-8")
        except OSError:
            return True  # 存在却读不到 ⇒ 保守不推进
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue  # 坏行跳过：一个坏行不该冻结活动权重
            if not isinstance(ev, dict):
                continue
            kind = ev.get("event")
            if kind == "iteration":
                # ★ 2026-10-24（评审 P0-2）：统计批写的是 `weights_landed=false` 的临时行。
                #   把它当「已落轮次」会让 `best > it` 永久成立（统计比权重跑得快时，
                #   活动权重指针再也不会推进）。
                if ev.get("weights_landed") is False:
                    continue
                v = ev.get("iter")
            elif kind == "offline_artifact":
                v = ev.get("it")
            else:
                continue
            if isinstance(v, int) and not isinstance(v, bool) and v > best:
                best = v
        return best > int(it)

    def _course_backup_target(self, course: str) -> tuple[str, str]:
        """课程配置里的归档 `(prefix, dir)`；缺配置/读不动 ⇒ `(课名, <归档根>/<课>)`。

        单一事实来源 = `nn-training/curricula/<课>.jsonc`（与 `biz/config.py` 同一份文件，
        命名约定与 dashboard 的课程发现一致：`<课>.jsonc` / `<课>.bc.jsonc`）。
        课程没声明归档键时按**同构缺省**（课名 + `<归档根>/<课>`）—— 与
        `TrainingLoop._export_weights` 的缺省（按 mode 前缀）**不是**同一套，所以这里必须先
        读课程：两种缺省混用会把不同课程的归档倒进同一个目录。

        `backup_dir` 是**仓根相对**路径（`biz.archive.backup_weights` 的契约，课程值形如
        `nn-training/weights/<课>`）；绝对路径原样用。
        """
        for name in (f"{course}.jsonc", f"{course}.bc.jsonc"):
            p = CURRICULA_DIR / name
            if not p.is_file():
                continue
            try:
                from common.jsonc import strip_comments

                cfg = json.loads(strip_comments(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if not isinstance(cfg, dict):
                continue
            prefix = str(cfg.get("backup_prefix") or course)
            raw = cfg.get("backup_dir")
            if isinstance(raw, str) and raw:
                bdir = Path(raw)
                bdir = bdir if bdir.is_absolute() else REPO_ROOT / bdir
            else:
                bdir = _weights_archive_root() / course
            return prefix, str(bdir)
        return course, str(_weights_archive_root() / course)

    def _land_offline_round_extras(
        self,
        *,
        it: int,
        run_id: str,
        weights_json: bytes,
        opt: bytes,
        row: object,
        advance_active: bool = True,
    ) -> None:
        """回传轮在**课程侧**的三处落位（2026-09-23 用户口径；全部 best-effort）。

        ① **交付镜像**：同一轮也写进 `<traj>/deliver/<run_id>/it-NNN/`。`deliver/` 此前只有
           人工导入写 ⇒「导入的段」与「回传的段」分居两棵树，找东西要翻两处。
        ② **活动权重推进**：`<traj>/weights.json` 在**确证没有更新的轮次**时原子替换。此前它
           整段不动（x20-demo-mix 实测停在段起点指纹），而 `biz/eval_replays_once` 的「活动
           权重」兜底、本机续跑、控制台显示读的都是它 ⇒ 整段期间「当前权重」是假的。
        ③ **归档**：`<归档根>/<课>/<prefix>.it<N>.<时间戳>.json`（同 `biz.archive.backup_weights`）。
           控制台 evalA 的 iter 选择器只扫那个目录（`eval-board/ckpts.ts`）⇒ 不归档就看不见
           回传段的任何一轮 —— 这才是真正意义上的「权重没落盘」。

        为什么 best-effort：回传的主价值是**权重到岸**（调用方已经三校验地落定了），这三处是
        可见性/观感 —— 任何一步失败都只记一行，不该把一轮合法回传判负。
        """
        traj = self.jsonl_path.parent
        course = traj.name
        it_dir = self.offline_run_dir(run_id) / f"it-{int(it):03d}"
        row_bytes = json.dumps(
            row if isinstance(row, dict) else {"it": int(it)}, ensure_ascii=False, indent=1
        ).encode("utf-8")
        # ① 交付镜像（幂等：已存在即不重写；与导入腿同路径同文件名 ⇒ 两腿同构）
        mirror = traj / self.DELIVER_DIR / run_id / f"it-{int(it):03d}"
        try:
            if delta_files.find_existing(mirror, "weights.json") is None:
                mirror.mkdir(parents=True, exist_ok=True)
                _write_bytes(
                    mirror / delta_files.named_for("weights.json", weights_json), weights_json
                )
                if opt:
                    _write_bytes(mirror / delta_files.named_for("opt.tar", opt), opt)
                _write_bytes(mirror / "row.json", row_bytes)
        except OSError as e:
            print(f"[hub-server] 补传 it{it} 交付镜像失败（忽略）：{e}", flush=True)
        # ② 活动权重推进（只在没有更新的轮次时；判据是账本，两腿共用）
        # ★P1-7：`advance_active=False`（人已切固定在线）⇒ 不推进；镜像/归档照落。
        if not advance_active:
            print(
                f"[hub-server] 补传 it{it} 不推进活动权重（课程已固定在线；旧会话的回传只归档）",
                flush=True,
            )
        elif not isinstance(row, dict):
            # ★ 2026-10-11 事故：起点（`ArtifactStore.checkpoint(row=None)`）没有账本行，却照样
            # 把活动权重推到了它自己 —— 「假轮次推进了当前进度」。判据看**请求体**的 `row`：
            # 落盘后的 `row.json` 无论真假都存在（无 row 时写裸行），反推不出来。
            # 不变量：**有账本行 ⟺ 真轮次**（客户端 `pending()` 用的是同一条）。
            print(
                f"[hub-server] 补传 it{it} 不推进活动权重：非真轮次"
                "（无账本行，疑似起点/半截写入）——镜像与归档照落",
                flush=True,
            )
        elif not self._ledger_has_newer_iter(int(it)):
            try:
                _write_bytes(traj / self.ACTIVE_WEIGHTS_NAME, weights_json)
                fp12 = hashlib.sha256(weights_json).hexdigest()[:12]
                print(
                    f"[hub-server] 活动权重推进 → it{it}（{fp12}…，源 run={run_id}）",
                    flush=True,
                )
            except OSError as e:
                print(f"[hub-server] 补传 it{it} 活动权重推进失败（忽略）：{e}", flush=True)
        # ③ 归档（evalA 的 iter 选择器只扫归档目录；**同一轮只写一次**）
        try:
            from worker.archive import backup_weights

            prefix, bdir = self._course_backup_target(course)
            # 幂等：这一轮的归档已在就不再写。归档名带时间戳（同轮重跑用改名区分），任其重写
            # 会让归档目录堆积同轮副本；而本轮的权重是不可变快照（存储层拒绝覆写），所以
            # “已在”就是全部要告诉我们的信息。
            # 后缀跟着内容走（2026-10-15）：压缩版 → `.delta` ⇒ 控制台 evalA 的 iter 选择器
            # （只扫 `*.json`）不会再列出一份解不开的容器；两种名字都算“已在”。
            wname = delta_files.named_for("weights.json", weights_json)
            if _archived(Path(bdir), prefix, int(it)):
                print(f"[hub-server] 补传 it{it} 归档已在（跳过重复）", flush=True)
            else:
                dst = backup_weights(
                    str(it_dir / wname),
                    int(it),
                    prefix=prefix,
                    backup_dir=bdir,
                    suffix=Path(wname).suffix,
                )
                print(
                    f"[hub-server] 补传 it{it} 权重已归档 → {dst}"
                    if dst
                    else f"[hub-server] 补传 it{it} 归档未成（见上一条 WARN）",
                    flush=True,
                )
        except Exception as e:  # 归档不该拖垮回传（含 rl 包不在的截断快照）
            print(
                f"[hub-server] 补传 it{it} 归档不可用（忽略）：{type(e).__name__}: {e}",
                flush=True,
            )

    def store_offline_result(self, body: dict) -> dict:
        """落段末摘要（**覆盖写**：它是「这条腿现在到哪了」的最新答案，不是不可变快照）。

        `end_it_reached` = 云机自报「本段跑满计划区间」（T6，`plan/auto-offline-handoff §3.6`）：
        它是**唯一**让控制台在导入那一步转交 `run_complete` 的判据（由调用方
        `hub/offline.py::_post_offline_result` 联动 `note_offline_completed`）。
        `seg_end_kind`（J2，三态 `open`/`course`/`segment`；`""` = 非段末/未知）：
        「本段完成」与「课末」的分界 —— 调用方据此决定封课还是触发下一段。
        白名单固定键集：新字段必须在这里显式落一格（漏了 = hub 读不到，调用方那条腿
        只能退回反推）。
        """
        run_id = sanitize_run_id(body.get("run_id"))
        it_end = body.get("it_end")
        if not isinstance(it_end, int) or isinstance(it_end, bool) or it_end < 0:
            raise ProtocolError(f"补传 it_end 非法（要求非负整数）: {it_end!r}")
        state = str(body.get("state", "") or "")[:40]
        end_it_reached = body.get("end_it_reached") is True
        seg_end_kind = str(body.get("seg_end_kind") or "")[:20]
        rec: dict = {
            "run_id": run_id,
            "it_end": int(it_end),
            "state": state,
            "end_it_reached": end_it_reached,
            "seg_end_kind": seg_end_kind,
            "delivered": (body.get("delivered") if isinstance(body.get("delivered"), int) else 0),
            "summary": body.get("summary") if isinstance(body.get("summary"), dict) else {},
            "plan_sha256": str(body.get("plan_sha256", "") or ""),
            "course_fp": str(body.get("course_fp", "") or ""),
            "commit": str(body.get("commit", "") or ""),
            "source_dir": str(body.get("source_dir", "") or "")[:300],
            "received_at": self._now(),
        }
        with self._lock:
            d = self.offline_run_dir(run_id)
            d.mkdir(parents=True, exist_ok=True)
            _write_bytes(
                d / self.OFFLINE_RESULT_NAME,
                json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"),
            )
            self._append_ledger(
                {
                    "event": "offline_result",
                    "run_id": run_id,
                    "it_end": int(it_end),
                    "state": state,
                    "end_it_reached": end_it_reached,
                    "seg_end_kind": seg_end_kind,
                    "ts": self._now(),
                }
            )
        return {
            "status": "accepted",
            "run_id": run_id,
            "it_end": int(it_end),
            "end_it_reached": end_it_reached,
            "seg_end_kind": seg_end_kind,
        }
