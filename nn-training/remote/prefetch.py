"""remote/prefetch.py — 软持有预取队列（plan/transfer-scheduling §2.6 / P2）。

要解决的问题（§0 触发）：串行 `claim → download → PPO → post_result → poll` 里，**下载期 GPU 空转**。
预取把这半场叠到 `PPO_A` 上：

    PPO_A 期间: 下载 B（P2 通道，唯 bulk）      ← 软持有：**没有租约**
    PPO_A 结束: 开算 B（零下载）‖ 上传 result A
    预取未命中: 先串行下载关键 payload（P1），GPU 短暂空转——用命中率压掉

三条设计约束（§2.6，都有用例钉住）：

1. **软持有 = 无租约**：候选来自 `GET /jobs/peek`（无副作用、不动游标），下载走**低优 P2 通道**
   （可被高优/控制面当场打断）。所以 worker 死亡 = 本地暂存蒸发，hub 侧零残留——这是选软持有
   而非「预认领」的主要原因（预认领会在 hub 上留下要过期的租约与毒包计账）。
2. **暂存 + 让路**：payload 的**唯一**副本就在暂存区（`prefetch/<jid>/`）——内容寻址那一层
   （`blob_cache`）只服务 `opt/init/ref/demo` 四类 blob，**payload 不进**（`payload_sha256` 从不被查，
   写进去也没人读：2026-10-12 删掉那笔纯写税，plan/prefetch-stage-unpack §3）。总量有硬预算，
   超限按 `updated_at` 丢最旧。预取目录必须进 `prune_job_dirs` 豁免名单（否则每轮被当旧 job
   删掉——`blob_cache` 已经栽过一次）。
3. **不撤销 minimize-payload 的瘦身**：预取**只走 `download_payload` 这一条路**（§4.1 的
   omit 协商落在那个函数里，落地后预取自动继承）。绝不在预取路径里另写一个「整包 GET」。

另一个必须记住的口径：**预取失败不是失败**。被挤走（`BulkPreemptError`）、404、sha 不符、预算不足
——一律就地丢弃、**不进** `ProtocolError`/`report_job_failure`（预取是提前量，不是任务）。

★ 2026-10-09（plan/dashboard-ppo-live-rows）：本类还多一份职责——回答「现在下到哪一份了」。
`begin` / `end` / `snapshot` 只服务那个**观测**问句（`snapshot()` → `POST /admin/worker-prefetch`
→ 控制台首页），与 `held()` / `take()` 的去重与命中逻辑**互不影响**：它们不改 `_items`、
不参与 `pick_candidates`，也不进任何调度判据。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: 预取深度（缺省 3，§2.6）。`0` = 关预取（回退路径：只调度不预取）。
#: ⚠ 有效窗口 = `min(depth, 开课数)`（peek 每课程至多一个候选 + 游标只读，见
#: `hub/queue_claims.peek_jobs` 的注释；「靠多轮 peek 加深」不成立，2026-10-02）。
PREFETCH_DEPTH_DEFAULT = 3
#: 暂存区总字节上限（硬预算，§2.6：**必须写死一个数**）。缺省 64 MB —— 单 job payload 量级
#: 是 3–10 MB（`docs/nn/remote-transport.md` 的 wire 账），深度 3 的均值约 10–30 MB，留一倍余量。
PREFETCH_BUDGET_BYTES_DEFAULT = 64 * 1024 * 1024
#: 暂存目录名（`work_dir/prefetch/`，单 hub ⇒ 不做 hub{i} 分区）。**必须在 prune 豁免名单里**。
PREFETCH_DIR_NAME = "prefetch"

# ── 进程模式（2026-10-20，`plan/ppo-worker-transfer-isolation` 的下行半场）───────
#: 下行腿（`peek` + payload 下载 + 解包）跑在哪里：`process` = 独立子进程
#: （`python -m remote.payload_worker`，父侧代理 `remote/payload_proc.py`）；`thread` = 进程内填充
#: 线程（= 改造前的旧行为，逃生口/测试档）。
#:
#: **为什么缺省是进程**：这条腿的网络 + `lzma` 解包（单份 300MB 级）此前全压在训练进程上——
#: 预取的价值是「与上一轮 PPO 重叠」，而重叠的代价是抢同一个 GIL / 同一颗核。搬走 = 重叠保持、
#: 竞争消失（与回传腿 `--result-upload process` 同一条理由，同一个 plan）。
#: 测试钉 `NN_PREFETCH_MODE=thread`（`tests/conftest.py`）：单测不该为预取起真子进程。
PREFETCH_MODE_ENV = "NN_PREFETCH_MODE"
PREFETCH_MODE_DEFAULT = "process"
PREFETCH_MODES = ("process", "thread")
#: 子进程的**控制行**前缀：`@@pf@@ {json}`（父侧 `payload_proc` 的解析口径）。
#: 两边同源 = 这个 L0 常量（子进程与父侧**互不 import**，与 `result_upload.OUTCOME_PREFIX` 同形）。
PREFETCH_LINE_PREFIX = "@@pf@@"
#: 预取**解包**的份数（缺省 1，plan/prefetch-stage-unpack §2.3）：只解 peek 顺序里的**前 N 份**，
#: `0` = 只存字节（= 改造前的旧行为，回退档）。解包是纯 CPU（单线程 lzma）而窗口是上一轮的
#: **PPO 32–35s** ⇒ 白解包的成本被这条闸限在「每轮最多 N 份」；命中的那份按 `rotation_order`
#: 通常就是 head。
PREFETCH_UNPACK_DEFAULT = 1
#: staged（解包产物）在册**条数**上限的缺省值 = `PREFETCH_DEPTH_DEFAULT`（预取槽位数）。
#: 单位为什么是「槽位」而不是「每轮解包份数（`PREFETCH_UNPACK_DEFAULT`）」：解包份数是被
#: `pick_candidates(held=…)` **去重后**的前 N 份 —— 上一轮 staged 的那份还在册时它会被跳过，
#: 于是每轮都新解一份，条数会跨轮累加；真正要限的就是这个累加面，而它天然的单位是「我们愿意
#: 同时持有几个候选」（= depth）。按 depth 限 ⇒ 稳态 ≤depth 份 staged（缺省 3 × ~330MB ≈ 1GB，
#: 且有主之后各轮被 take 掉，健康会话实际恒定 0–1 份）；超出的按最旧丢（`prune`）。
#: 下限由调用方给（`worker.py` 传 `max(prefetch_depth, prefetch_unpack)`）。
#:
#: ★ 2026-10-12 撤销字节常数 `PREFETCH_STAGED_BUDGET_BYTES_DEFAULT = 256MiB`（旧注释把它
#: 同时当「总预算」与「单份上界」用）。撤销的实证：现行课程 payload 3MB → 解包 300–330MB，
#: **实测膨胀 ~110×**（真机日志 `解包 325.7MB / 299.6MB 超 staged 预算 256MB`），256MiB
#: 一进门就超 ⇒ staged 路径 **100%** 退回字节形状 = 该档位等于没开（plan §7 判据 1 要求的
#: `解包 aMB → bMB / k shard / t s` 那行一次都没出现过，job 准备行照旧写着 `解包=1.1s`）。
#: 旧注释给的三条支撑全不成立：
#:   ①「解包后体积未知，先量再定」—— 体积就在同一函数里量得到（`_tree_bytes(tmp)`，在
#:      否决之前那一行）⇒ 拿一个从未量过的常数去否决刚量到的真数是把测量结果丢掉；
#:   ②「它同时是总预算」—— `unpack_n = 1` ⇒ staged 至多 1 条 ⇒ 「总预算」那半边**不可达**，
#:      真正干活的只有单份否决；而否决权的唯一效果是让 `prune` 那条混账淘汰分支永不触发
#:      （自证循环：为了掩护一个坏分支而关掉一整条特性）；
#:   ③「否则 280MB 级课程会合法留在盘上」—— 退回 zip 之后同一份字节仍在**必走路径**上被
#:      解包进 `job_dir`（`remote/download.py::_ensure_payload`，**零体积检查**），而
#:      `JOB_DIR_KEEP = 2`（`remote/job_fs.py:61`）让它合法长驻；staged 的搬入是 `rename`
#:      不是复制 ⇒ 那道闸**不保护磁盘**，只决定那 300MB 落在哪个目录。真正的磁盘大头
#:      （2×~300MB job 目录 + 256MB code cache）从头到尾不在它的管辖内。
#: ⇒ 盘上的量 = **条数 × 实测体积**：条数是配置整数，体积每轮都打进日志，两者都不需要猜。

# ── 会话级自停（plan/aistudio-transfer-hardening §3.1）─────────────────────────
#: 下结论前的最小下载份数。**只需 2**：真正兜住误杀的是下面那道白传门槛（健康预取的白传
#: 恒 ≈0，因为每份都会被下一次 claim 吃掉；白传 ≥3MiB 本身就是「至少两份没被吃」）。
#: 这道样本位只挡一种退化：单份 payload 恰好 >3MiB 时「一份没被吃」就触发。
#: ⚠ **不要**改用 `misses` 当分母（会话首个 job 的命中率按构造 = 0，会把健康会话前两轮关掉）。
PREFETCH_GIVEUP_MIN_SAMPLES = 2
#: 白传字节门槛：低于它不下结论（省下的带宽不足以补偿误杀风险）。
PREFETCH_GIVEUP_WASTED_BYTES = 3 * 1024 * 1024
#: 命中率门槛：`taken / prefetched` 低于它且白传超门槛 ⇒ 停。
PREFETCH_GIVEUP_TAKEN_RATIO = 1.0 / 3.0


def prefetch_worth_it(
    *,
    prefetched: int,
    taken: int,
    white_bytes: int,
    min_samples: int = PREFETCH_GIVEUP_MIN_SAMPLES,
    min_wasted: int = PREFETCH_GIVEUP_WASTED_BYTES,
    min_taken_ratio: float = PREFETCH_GIVEUP_TAKEN_RATIO,
) -> bool:
    """预取还划不划算（纯函数，判据不埋进控制流）。**True = 继续，False = 停**。

    口径（plan §53 的门槛①，去掉了它对 `misses` 的依赖——`misses` 会被「claim 到的
    job ≠ 预取候选」这类**正常**事件灌满，当命中率分母就把健康会话判死）：

      · `prefetched` —— 成功入库（= 真下过网）的份数，**不是** take 尝试次数；
      · `taken` —— 其中被 `take()` 拿走的份数；
      · `white_bytes` —— **已下载但从未被 take 的字节**（含被 P1 挤走的半截）。

    返回值语义是「继续」，所以**证据不足一律 True**：样本不够 / 白传不够，都不构成
    「停」的理由。三条同时成立才停。
    """
    if int(prefetched) < int(min_samples):
        return True
    if int(white_bytes) < int(min_wasted):
        return True
    if int(prefetched) <= 0:
        return True
    return (int(taken) / int(prefetched)) >= float(min_taken_ratio)


class PrefetchStore:
    """预取暂存区：`work_dir/prefetch/<jid>/` = `payload.zip`（或解好的 `shards/`）+ `meta.json`

    （纯磁盘，无租约）。内容寻址那一层（`blob_cache`）**不参与**（见模块头 §2）：本类只在自己
    的暂存目录里放一份，`take()` 取走即删。
    """

    def __init__(
        self,
        work_dir: Path,
        *,
        budget_bytes: int = PREFETCH_BUDGET_BYTES_DEFAULT,
        staged_max: int = PREFETCH_DEPTH_DEFAULT,
        clock: Callable[[], float] = time.time,
        log: Any = None,
    ) -> None:
        self.root = Path(work_dir) / PREFETCH_DIR_NAME
        self.budget_bytes = int(budget_bytes)
        #: staged 在册**条数**上限 —— **不是字节预算**（撤销理由见 `PREFETCH_DEPTH_DEFAULT`
        #: 上面那段长注）。缺省 = 预取槽位数；体积只记账（`stats()["staged_bytes"]`）。
        self.staged_max = max(0, int(staged_max))
        self._clock = clock
        self._log = log
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}  # jid -> {sha, bytes, at, summary, staged, staged_bytes, unpack_sec}
        #: 已交给调用方（`take()`）但**还没被搬走**的 staged 目录（jid -> shards 路径）。
        #: 它是「不在 `_items` 里但也不该被清扫」的那一档：搬入在 `_ensure_payload`
        #: （`_result.json` 复用检查**之后**），中间任何早退路径（结果复用/bun 自检失败/
        #: 取消/热替换）都得有人收尾 —— 收尾人 = `release_handoff()`（job 收尾）+ `prune()`。
        self._handoff: dict[str, Path] = {}
        #: **正在下载**的候选（jid -> {summary, at}）——只为观测面存在（plan/dashboard-ppo-live-rows）：
        #: 「这一轮下到哪一份了」是控制台首页唯一想知道的问句，而进程内此前根本没有这个集合
        #: （`_items` 只记「已入库」）。不入任何调度判据（与 `_items` 无关的两件事）。
        self._active: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        #: 会话级自停读数（plan/aistudio-transfer-hardening §3.1）：`prefetched` = 成功入库
        #: 次数（= 真下过网），`taken` 见 `hits`，白传 = `downloaded_bytes - taken_bytes`
        #: （+ `_preempt_wasted`）。**只有这三个数进判据**，`misses` 不进（理由见
        #: `prefetch_worth_it`）。
        self.prefetched = 0
        self.downloaded_bytes = 0
        self.taken_bytes = 0
        self._preempt_wasted = 0

    # ---------------------------------------------------------------- 写

    def store(self, jid: str, payload: bytes, summary: dict, *, unpack: bool = False) -> bool:
        """存一份候选 payload（**sha 不符就地拒绝**）；超预算先丢最旧。返回是否入库。

        `summary` = peek 摘要（只需 `payload_sha256`；缺它就无从就地校验，仍然存下、
        由 claim 时那份权威 manifest 再校一次）。

        `unpack=True`（plan/prefetch-stage-unpack §2.3）= 顺手解包成 `shards/` 并**删掉**
        `payload.zip`（解包这半场从关键路径搬到预取线程）。**缺省 False = 旧行为逐字**
        （只存 zip）——`--prefetch-unpack 0` 的回退档走这条。解包失败/超上界都只降级不报错
        （预取失败不是失败）。
        """
        sha = hashlib.sha256(payload).hexdigest()
        want = str(summary.get("payload_sha256") or "")
        if want and sha != want:
            # peek 摘要与真下到的字节不符（hub 换过 job / 暂存陈旧）：丢弃，**不报失败**。
            self._drop_dir(jid)
            self.misses += 1
            self._log_line(f"prefetch {jid[:8]}: payload sha 不符——丢弃（重下即可）")
            return False
        d = self.root / jid
        d.mkdir(parents=True, exist_ok=True)
        zip_path = d / "payload.zip"
        zip_path.write_bytes(payload)  # 暂存副本：命中路径零网络
        staged = False
        staged_bytes = 0
        unpack_sec = 0.0
        if unpack:
            staged, staged_bytes, unpack_sec = self._unpack_into(d, jid, payload, zip_path)
        (d / "meta.json").write_text(
            json.dumps(
                {
                    "sha": sha,
                    "bytes": len(payload),
                    "at": self._clock(),
                    "summary": summary,
                    "staged": staged,
                    "staged_bytes": staged_bytes,
                    "unpack_sec": unpack_sec,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with self._lock:
            self._items[jid] = {
                "sha": sha,
                "bytes": len(payload),
                "at": self._clock(),
                "summary": summary,
                "staged": staged,
                "staged_bytes": staged_bytes,
                "unpack_sec": unpack_sec,
            }
            # 入库即「下好了」：从观测面的"正在下载"里摘掉（调用方的 `end` 也幂等）。
            self._active.pop(jid, None)
        # 自停读数：**只有成功入库才计白传分母**（sha 不符那条不算——它没进暂存区）。
        self.prefetched += 1
        self.downloaded_bytes += len(payload)
        self.prune()
        return True

    def _unpack_into(
        self, d: Path, jid: str, payload: bytes, zip_path: Path
    ) -> tuple[bool, int, float]:
        """把暂存的 zip 解成 `shards/`、**删掉 zip**；失败 ⇒ 保留 zip 退回字节形状。

        返回 `(staged, staged_bytes, unpack_sec)`。异常一律就地降级（预取失败不是失败；
        也绝不进 `ProtocolError`/`report_job_failure`）。解包产物的完整性由 `unpack_payload`
        自己的空包断言 + `tmp → shards` 原子改名兑住（半成品永远是 `.tmp`）。

        **没有体积闸**（2026-10-12 撤掉 256MiB 单份上界，理由见 `PREFETCH_UNPACK_DEFAULT`
        上面那段长注）：解包后体积只**记账**（返回 `staged_bytes` → `meta.json` / `stats()`），
        不进任何否决判据。盘上的量由「条数上限（`staged_max`）× 实测体积」约束。
        """
        from common.payload import unpack_payload

        tmp = d / "shards.tmp"
        if tmp.exists():
            self._rmtree(tmp)
        try:
            t0 = self._clock()
            _unused, shard_dirs = unpack_payload(zip_path, tmp)
            unpack_sec = round(self._clock() - t0, 3)
            nbytes = _tree_bytes(tmp)
            tmp.rename(d / "shards")  # 原子：提交点在这里，之前都叫 .tmp
            try:
                zip_path.unlink()  # `shards/` 就是唯一那份（不再多留一份压缩副本）
            except OSError:
                pass
        except Exception as e:  # 含 ProtocolError（空包/容器异常）：降级不报错
            self._rmtree(tmp)
            self._log_line(
                f"prefetch {jid[:8]}: 解包失败（{type(e).__name__}: {e}）——退回字节形状"
            )
            return False, 0, 0.0
        self._log_line(
            f"prefetch {jid[:8]}: 解包 {len(payload) / 1048576:.1f}MB → {nbytes / 1048576:.1f}MB / "
            f"{len(shard_dirs)} shard / {unpack_sec:.1f}s（命中则不在关键路径上解）"
        )
        return True, nbytes, unpack_sec

    # ---------------------------------------------------------------- 读

    def held(self) -> set[str]:
        """当前持有的候选 jid 集合（填充器用它去重：同一份活不重复预取）。"""
        with self._lock:
            return set(self._items)

    def begin(self, jid: str, summary: dict | None = None) -> None:
        """标记「这一份开始下了」（进 `download_payload` **之前**调）。

        `summary` 只是留给排障/观测的上下文（`meta.json` 的形状就是它），本类不改它、不读它。
        """
        with self._lock:
            self._active[jid] = {"summary": dict(summary or {}), "at": self._clock()}

    def end(self, jid: str) -> None:
        """标记「这一份下完了」（成功与**每一条**失败路径都要调；幂等）。

        不调它只会让观测面多挂一会儿——所以调用方用 `try/finally` 兜，而不是靠每条分支自觉
        （预取失败不是失败，同样也不该在面板上装成「还在下」）。
        """
        with self._lock:
            self._active.pop(jid, None)

    def downloading(self) -> set[str]:
        """正在下载的候选 jid 集合（观测面用；与 `held()` 不交）。"""
        with self._lock:
            return set(self._active)

    def snapshot(self) -> dict:
        """观测快照：`{"held": [jid…], "dl": [jid…]}`（**排序**，供「形状未变就不上报」比对）。

        为什么排序：上报是每轮一次的 HTTP，列表顺序抖动不该被当成状态变化；规范化在这里
        一次做掉，调用方只管 `==` 比对。
        """
        with self._lock:
            held = sorted(self._items)
            dl = sorted(self._active)
        return {"held": held, "dl": dl}

    def has(self, jid: str) -> bool:
        """在册 **且盘上有那份东西**（两种形状都认：解好的 `shards/` 或压缩 `payload.zip`）。

        ⚠ 只认 `shards/` 会把 `--prefetch-unpack 0`（只存字节）的会话打成 0 命中——
        那个档位的意义是「退回旧行为」，不是「关掉预取」（评审 P0-2）。
        """
        with self._lock:
            it = self._items.get(jid)
        if not it:
            return False
        d = self.root / jid
        return (d / "shards").is_dir() or (d / "payload.zip").exists()

    def take(self, jid: str) -> dict | None:
        """取走命中项 → `preloaded` 块；未命中 → None。**按盘上的形状分派**：

        * staged（`shards/`）⇒ `{"staged_dir": str, "payload_nbytes": int, "unpack_sec": float,
          "blob_sha": str}`，并把它记进 `_handoff`（**不碰 job 目录**）。搬入由
          `_ensure_payload` 做 —— 那里晚于 `run_job` 的 `_result.json` 复用检查，
          在这里搬会把「算完只差重传」的缓存删掉（评审 P0-3）。
        * 字节（`payload.zip`）⇒ 逐字旧行为（`{"payload_zip": bytes, …}`），取走即删。

        取走即删（软持有只用一次）：下次要再预取就重新 peek —— 少一份「陈旧副本被误用」的面。
        staged 那半由 `release_handoff()` / `prune()` 收尾（搬走成功时它们是空操作）。
        """
        if not self.has(jid):
            self.misses += 1
            return None
        d = self.root / jid
        staged = d / "shards"
        if staged.is_dir():
            with self._lock:
                it = self._items.pop(jid, {})
                self._handoff[jid] = staged
            sha = str(it.get("sha") or "")
            want = str((it.get("summary") or {}).get("payload_sha256") or "")
            if want and sha != want:  # 陈旧/损坏：丢弃，走关键下载（**绝不**把坏字节喂给 run_job）
                self.drop(jid)
                self.misses += 1
                self._log_line(f"prefetch {jid[:8]}: 暂存副本 sha 不符——丢弃走关键下载")
                return None
            self.hits += 1
            self.taken_bytes += int(it.get("bytes") or 0)
            self._log_line(
                f"prefetch {jid[:8]}: 命中（{int(it.get('bytes') or 0)} bytes，解包 "
                f"{float(it.get('unpack_sec') or 0.0):.1f}s 已就绪——零下载开算）"
            )
            return {
                "staged_dir": str(staged),
                "payload_nbytes": int(it.get("bytes") or 0),
                "unpack_sec": float(it.get("unpack_sec") or 0.0),
                "blob_sha": sha,
                "summary": it.get("summary") or {},
            }
        p = d / "payload.zip"
        try:
            payload = p.read_bytes()
        except OSError:
            self.drop(jid)
            self.misses += 1
            return None
        with self._lock:
            it = self._items.pop(jid, {})
        sha = hashlib.sha256(payload).hexdigest()
        want = str((it.get("summary") or {}).get("payload_sha256") or "")
        if want and sha != want:  # 陈旧/损坏：丢弃，走关键下载（**绝不**把坏字节喂给 run_job）
            self._drop_dir(jid)
            self.misses += 1
            self._log_line(f"prefetch {jid[:8]}: 暂存副本 sha 不符——丢弃走关键下载")
            return None
        self._drop_dir(jid)
        self.hits += 1
        self.taken_bytes += len(payload)
        self._log_line(f"prefetch {jid[:8]}: 命中（{len(payload)} bytes 零下载开算）")
        return {"payload_zip": payload, "blob_sha": sha, "summary": it.get("summary") or {}}

    def drop(self, jid: str) -> None:
        """丢弃一个候选（claim 失败/被降级/`none` 级/**已 landed**/sha 不符）——丢弃不是失败。"""
        with self._lock:
            self._items.pop(jid, None)
            self._handoff.pop(jid, None)
        self._drop_dir(jid)

    def release_handoff(self, jid: str | None = None) -> None:
        """放掉「已交给调用方但还没被搬走」的 staged 暂存（job 收尾用；幂等，不抛）。

        为什么必须有：`take()` 之后那份已不归 `_items` 管，而搬入在 `_ensure_payload`
        （`_result.json` 复用检查之后）——中间任何一条早退路径（结果复用、bun 自检失败、
        取消、热替换、抛异常）都会让它留成孤儿。搬入成功时这里**找不到目录**（rename 走了），
        是正常路径的空操作。也招呼不住的那些（崩溃/沙箱拒删）留给下一次 `prune()`。
        """
        with self._lock:
            if jid is None:
                items = list(self._handoff.items())
                self._handoff.clear()
            else:
                it = self._handoff.pop(jid, None)
                items = [] if it is None else [(jid, it)]
        for _j, p in items:
            # 两种情形（评审 P2-3）：`shards/` 还在 ⇒ **真没被搬**（早退路径）；不在 ⇒ 搬走了，
            # 但 `prefetch/<jid>/` 里还留着 `meta.json`（rename 走的是 `shards` 子目录）——余壳
            # 不进 `_items` 也没人记账，不清就会按命中次数无界堆积。两种都收，日志分开写。
            staged_left = Path(p).exists()
            d = Path(p).parent
            if not d.exists():
                continue
            if self._rmtree(d):
                if staged_left:
                    self._log_line(f"prefetch {_j[:8]}: 未搬走的暂存已清理（{d.name}）")
                else:
                    self._log_line(f"prefetch {_j[:8]}: 搬入成功，余壳已清（{d.name}）")

    def clear(self) -> None:
        with self._lock:
            jids = list(self._items)
            handed = [Path(p).parent for p in self._handoff.values()]
            self._items.clear()
            self._active.clear()
            self._handoff.clear()
        for jid in jids:
            self._drop_dir(jid)
        for d in handed:
            if d.exists():
                self._rmtree(d)

    def _rmtree(self, p: Path) -> bool:
        """best-effort 删目录（沙箱删除守卫拦得住 `shutil.rmtree`，拦不住它）；返回是否删成功。"""
        try:
            from common.platform_utils import rmtree_best_effort

            return bool(rmtree_best_effort(p, ignore_errors=True))
        except BaseException:  # 含 SystemExit：留给下次 prune，不打断主循环
            return False

    def _drop_dir(self, jid: str) -> None:
        d = self.root / jid
        if not d.exists():
            return
        self._rmtree(d)

    # ---------------------------------------------------------------- 预算

    def prune(self) -> int:
        """① 清孤儿（`.tmp` 半成品 / 不在册的残壳）；② 超量 ⇒ 丢**最旧**的候选。

        两本账（plan §2.3；2026-10-12 修订）：zip 条目按压缩字节（`budget_bytes`，旧账 64MB，
        语义与「保留最后一份」口径逐字不变），staged 条目只按**条数**（`staged_max` = 每轮
        最多解包的份数，见 `PREFETCH_UNPACK_DEFAULT` 上的长注——字节预算已撤销）。

        staged 超条数时**只丢最旧的 staged 条目**，不拿 zip 候选偿债：两者量纲差 ~100×
        （3MB 压缩 vs 300MB 解包），混在一处比大小必然误杀健康的候选。旧实现是「全池按最旧
        丢」，且因单份上界而**永不可达**——撤销上界后若照旧混账，一条超体积的 staged 条目会
        把整池 zip 候选逐个吃掉仍不达标。所以这两条分派必须分开写。
        """
        dropped = self._sweep_orphans()
        while True:
            with self._lock:
                payload_total = sum(
                    int(it["bytes"]) for it in self._items.values() if not it.get("staged")
                )
                staged = sorted(
                    ((jid, it) for jid, it in self._items.items() if it.get("staged")),
                    key=lambda kv: float(kv[1]["at"]),
                )
                over_payload = payload_total > self.budget_bytes
                over_staged = len(staged) > self.staged_max
                if not over_payload and not over_staged:
                    return dropped
                if over_staged:
                    victim, why = staged[0][0], f"staged 条数 {len(staged)}/{self.staged_max}"
                elif len(self._items) > 1:
                    victim = min(self._items.items(), key=lambda kv: float(kv[1]["at"]))[0]
                    why = f"zip {payload_total}/{self.budget_bytes} bytes"
                else:
                    # 仅剩一份（staged 侧不可能走到这里：超条数 ⇒ 至少 2 条）——保留。
                    self._log_line(
                        f"prefetch: 超预算（zip {payload_total}/{self.budget_bytes} bytes）"
                        "且仅剩一份——保留"
                    )
                    return dropped
            self.drop(victim)
            dropped += 1
            self._log_line(f"prefetch {victim[:8]}: 丢弃（超量：{why}，按最旧先丢）")

    def _sweep_orphans(self) -> int:
        """清 `prefetch/` 下的**孤儿**：`.tmp` 半成品、以及既不在 `_items` 也不在 `_handoff` 的残壳。

        为什么方向与 `prune_caches` 的「`*.tmp` 永不 prune」**相反**：那里的 `.tmp` 是写入中的
        半成品、靠 `replace` 自己收拾；这里的 `.tmp` 是崩溃留下的孤儿（`rename` 才是提交点）。
        在册条目只清它的 `shards.tmp`，整目录留给预算账；跨会话遗留（上一进程的暂存）也走这里。
        """
        with self._lock:
            live = set(self._items) | set(self._handoff)
        try:
            children = list(self.root.iterdir()) if self.root.is_dir() else []
        except OSError:
            return 0
        n = 0
        for child in children:
            if child.name in live:
                tmp = child / "shards.tmp"
                if tmp.exists() and self._rmtree(tmp):
                    n += 1
                continue
            if self._rmtree(child):
                n += 1
        if n:
            self._log_line(f"prefetch: 清 {n} 个孤儿暂存（不在册的残壳 / 崩溃 .tmp）")
        return n

    def note_preempt_wasted(self, nbytes: int) -> None:
        """记一笔「被 P1 挤走的半截字节」（由调用方从 wire 的 `preempt_wasted` 推进来）。

        挤走的字节**从未入库**，所以不在 `downloaded_bytes` 里；漏记它 = 自停判据看不见
        最大的一笔浪费（aistudio 现场：`preempt=2(wasted 0.25MB)`）。
        """
        if int(nbytes) > 0:
            self._preempt_wasted += int(nbytes)

    def white_bytes(self) -> int:
        """已下载但**从未被 `take()`** 的字节（含被挤走的半截）= 纯白传。"""
        with self._lock:
            return max(0, self.downloaded_bytes - self.taken_bytes) + self._preempt_wasted

    def stats(self) -> dict:
        with self._lock:
            return {
                "held": len(self._items),
                "bytes": sum(int(it["bytes"]) for it in self._items.values()),
                "hits": self.hits,
                "misses": self.misses,
                # 自停判据的三件读数（plan §3.1）——`prefetch_worth_it` 只读这三个。
                "prefetched": self.prefetched,
                "taken": self.hits,
                "white_bytes": max(0, self.downloaded_bytes - self.taken_bytes)
                + self._preempt_wasted,
                "preempt_wasted": self._preempt_wasted,
                # staged 观测（plan §2.5 的读数源）：份数与解包后总字节。**纯读数**——
                # 撤销字节预算后它们只用来回答「解包后到底占多少」，不进任何淘汰判据。
                "staged": sum(1 for it in self._items.values() if it.get("staged")),
                "staged_bytes": sum(
                    int(it.get("staged_bytes") or 0)
                    for it in self._items.values()
                    if it.get("staged")
                ),
                #: 交棒但还没被搬走的那份（正常路径恒 0~1；非 0 说明它还在关键路径上等着）。
                "handoff": len(self._handoff),
            }

    def _log_line(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


def _tree_bytes(p: Path) -> int:
    """目录字节数（best-effort：读不到的项按 0 计，绝不让预取因 I/O 抛）。

    与 `remote/download.py::_dir_bytes` 同口径（那边服务 prune 的预算账）；这里量的是
    解包产物，只作**读数**（落 `meta.json` / `stats()`）—— 2026-10-12 起不再参与任何否决
    （旧「单份上界」已撤，理由见 `PREFETCH_UNPACK_DEFAULT` 上的长注）。
    """
    total = 0
    try:
        for root, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += int(os.stat(os.path.join(root, f)).st_size)
                except OSError:
                    pass
    except OSError:
        return 0
    return total


def pick_candidates(
    peeked: list[dict],
    *,
    held: set[str],
    skip: set[str],
    depth: int,
) -> list[dict]:
    """从 `peek` 结果里挑候选（纯函数）：跳过已持有/正在算/刚失败的，最多 `depth` 个。

    **跨课程轮转**由 `peek` 的返回顺序 + 这条「已持有就跳过」实现：hub 侧候选已按
    `rotation_order` 排过（§2.3），worker 只负责别把同一份活重复预取。
    """
    out: list[dict] = []
    for j in peeked or []:
        jid = str(j.get("job_id") or "")
        if not jid or jid in held or jid in skip:
            continue
        if not j.get("payload_sha256") or not j.get("payload_bytes"):
            continue  # peek 摘要不完整（旧 hub）：无从校验，宁可不预取
        out.append(j)
        if len(out) >= max(0, int(depth)):
            break
    return out
