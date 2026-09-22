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
2. **只存引用 + 让路**：payload 落**内容寻址**的 `blob_cache`（按 `payload_sha256`），暂存区只留
   `{job_id → blob_sha + manifest 摘要}`，**不复制字节**；总量有硬预算，超限按 `updated_at` 丢最旧。
   预取目录必须进 `prune_job_dirs` 豁免名单（否则每轮被当旧 job 删掉——`blob_cache` 已经栽过一次）。
3. **不撤销 minimize-payload 的瘦身**：预取**只走 `download_payload` 这一条路**（§4.1 的
   omit 协商落在那个函数里，落地后预取自动继承）。绝不在预取路径里另写一个「整包 GET」。

另一个必须记住的口径：**预取失败不是失败**。被挤走（`BulkPreemptError`）、404、sha 不符、预算不足
——一律就地丢弃、**不进** `ProtocolError`/`report_job_failure`（预取是提前量，不是任务）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: 预取深度（缺省 3，§2.6）。`0` = 关预取（回退路径：只调度不预取）。
PREFETCH_DEPTH_DEFAULT = 3
#: 暂存区总字节上限（硬预算，§2.6：**必须写死一个数**）。缺省 64 MB —— 单 job payload 量级
#: 是 3–10 MB（`docs/nn.progress.md` 的 wire 账），深度 3 的均值约 10–30 MB，留一倍余量。
PREFETCH_BUDGET_BYTES_DEFAULT = 64 * 1024 * 1024
#: 暂存目录名（`work_dir/prefetch/`，单 hub ⇒ 不做 hub{i} 分区）。**必须在 prune 豁免名单里**。
PREFETCH_DIR_NAME = "prefetch"


class PrefetchStore:
    """预取暂存区：`work_dir/prefetch/<jid>/` = `payload.zip` + `meta.json`（纯磁盘，无租约）。

    内容寻址那一层（`blob_cache`）由 `remote/worker.py::_cache_blob` 维护；本类的 `meta.json`
    记 `blob_sha` 作为**引用**，下载落盘时同时写内容寻址副本，于是同一份 payload 被多个
    候选/重领复用时只占一份空间。
    """

    def __init__(
        self,
        work_dir: Path,
        *,
        budget_bytes: int = PREFETCH_BUDGET_BYTES_DEFAULT,
        clock: Callable[[], float] = time.time,
        log: Any = None,
        blob_root: Path | None = None,
    ) -> None:
        self.root = Path(work_dir) / PREFETCH_DIR_NAME
        self.budget_bytes = int(budget_bytes)
        self._clock = clock
        self._log = log
        self.blob_root = Path(blob_root) if blob_root is not None else Path(work_dir) / "blob_cache"
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}  # jid -> {sha, bytes, at, manifest}
        self.hits = 0
        self.misses = 0

    # ---------------------------------------------------------------- 写

    def store(self, jid: str, payload: bytes, summary: dict) -> bool:
        """存一份候选 payload（**sha 不符就地拒绝**）；超预算先丢最旧。返回是否入库。

        `summary` = peek 摘要（只需 `payload_sha256`；缺它就无从就地校验，仍然存下、
        由 claim 时那份权威 manifest 再校一次）。
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
        (d / "payload.zip").write_bytes(payload)  # 暂存副本：命中路径零网络
        (d / "meta.json").write_text(
            json.dumps(
                {"sha": sha, "bytes": len(payload), "at": self._clock(), "summary": summary},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self._cache_blob(sha, payload)
        with self._lock:
            self._items[jid] = {
                "sha": sha,
                "bytes": len(payload),
                "at": self._clock(),
                "summary": summary,
            }
        self.prune()
        return True

    def _cache_blob(self, sha: str, payload: bytes) -> None:
        """同一份字节写进内容寻址目录（跨候选/重领复用，命中即省一次下载）。"""
        try:
            self.blob_root.mkdir(parents=True, exist_ok=True)
            p = self.blob_root / sha
            if not p.exists():
                p.write_bytes(payload)
        except OSError as e:  # 只读盘/空间不足：暂存副本仍是完整的，预取不受影响
            self._log_line(f"prefetch: blob_cache 写入失败（{type(e).__name__}）——继续用暂存副本")

    # ---------------------------------------------------------------- 读

    def held(self) -> set[str]:
        """当前持有的候选 jid 集合（填充器用它去重：同一份活不重复预取）。"""
        with self._lock:
            return set(self._items)

    def has(self, jid: str) -> bool:
        with self._lock:
            it = self._items.get(jid)
        return bool(it) and (self.root / jid / "payload.zip").exists()

    def take(self, jid: str) -> dict | None:
        """取走命中项 → `preloaded` 块（`{"payload_zip": bytes, "blob_sha": …}`）；未命中 → None。

        取走即删（软持有只用一次）：下次要再预取就重新 peek —— 少一份「陈旧副本被误用」的面。
        """
        if not self.has(jid):
            self.misses += 1
            return None
        p = self.root / jid / "payload.zip"
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
        self._log_line(f"prefetch {jid[:8]}: 命中（{len(payload)} bytes 零下载开算）")
        return {"payload_zip": payload, "blob_sha": sha, "summary": it.get("summary") or {}}

    def drop(self, jid: str) -> None:
        """丢弃一个候选（claim 失败/被降级/`none` 级/**已 landed**）——软持有丢弃不是失败。"""
        with self._lock:
            self._items.pop(jid, None)
        self._drop_dir(jid)

    def clear(self) -> None:
        with self._lock:
            jids = list(self._items)
            self._items.clear()
        for jid in jids:
            self._drop_dir(jid)

    def _drop_dir(self, jid: str) -> None:
        d = self.root / jid
        if not d.exists():
            return
        try:
            from platform_utils import rmtree_best_effort

            rmtree_best_effort(d, ignore_errors=True)
        except BaseException:  # 沙箱删除守卫：留给下次 prune，不打断主循环
            pass

    # ---------------------------------------------------------------- 预算

    def prune(self) -> int:
        """超预算 ⇒ 按 `updated_at` 丢**最旧**的候选（§2.6：预算写死，不许默默超）。"""
        dropped = 0
        while True:
            with self._lock:
                total = sum(int(it["bytes"]) for it in self._items.values())
                if total <= self.budget_bytes or len(self._items) <= 1:
                    if total > self.budget_bytes:
                        self._log_line(
                            f"prefetch: 超预算 {total} > {self.budget_bytes} bytes 且仅剩一份——保留"
                        )
                    return dropped
                oldest = min(self._items.items(), key=lambda kv: float(kv[1]["at"]))[0]
            self.drop(oldest)
            dropped += 1
            self._log_line(
                f"prefetch {oldest[:8]}: 丢弃（超预算 {self.budget_bytes // 1048576}MB，按最旧先丢）"
            )

    def stats(self) -> dict:
        with self._lock:
            return {
                "held": len(self._items),
                "bytes": sum(int(it["bytes"]) for it in self._items.values()),
                "hits": self.hits,
                "misses": self.misses,
            }

    def _log_line(self, msg: str) -> None:
        if self._log is not None:
            self._log(msg)


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
