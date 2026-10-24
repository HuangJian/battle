"""remote/deliver_parts.py — 补传「发什么」的纯装配（2026-10-23，plan/offline-deliver-full-and-gaps）。

为什么又拆一个文件：`offline_deliver` 是 L2 的腿本体，LOC 预算是 1000 行
（`tests/test_python_loc_budget.py`）。把「这一轮发整份还是原件」「统计批怎么装」这两块
**数据/判据**下沉到这里，腿本体只留「怎么发」（POST / 记账 / 档位）。

依赖面：`remote.delta_files`(L0) / `remote.artifacts`(L0) ⇒ 本模块 **L1**。
两个**整份落地器**（`weights_standard` / `opt_standard`，均 L1）**不在这里 import**——由调用方
经 `expand_weights` / `expand_opt` 注入（与 `offline_deliverable.write_deliver_zip(full_for=…)`
同一条纪律：L0/L1 的模块不向上 import，读者才持有那条边）。注入的落地器会把整份副本写进
`.weights-standard/` / `.opt-standard/` 旁路缓存——那正是 `plan_run` 装载那条路同一个目录、
同一个函数（**不新增落盘形态**）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path

import remote.delta_files as delta_files
from common.protocol import OFFLINE_ARTIFACT_PATH, OFFLINE_RESULT_BODY_MAX
from remote.artifacts import ArtifactStore
from remote.deliver_wire import wire_row

#: 整份展开器（调用方注入）：`(容器路径) -> 整份路径`。
Expander = Callable[[Path], Path]


def is_container_bytes(blob: bytes) -> bool:
    """字节是不是 delta 容器（只看魔数；魔数从**各自的模块**取，不抄第二份）。"""
    try:
        from remote.opt_delta import OPT_DELTA_MAGIC
        from remote.weights_delta import W_DELTA_MAGIC

        return blob[:4] in (W_DELTA_MAGIC, OPT_DELTA_MAGIC)
    except Exception:
        return False


def full_parts_for(
    root: str | Path,
    run_id: str,
    it: int,
    *,
    mode: str,
    expand_weights: Expander | None = None,
    expand_opt: Expander | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[bytes, bytes, str]:
    """这一轮要发的 `(权重字节, opt字节, wire_form)`——**档位决定发整份还是原件**。

    ⚠ **今天没有任何生产调用点**（`offline_deliver._post_artifact` 直接发盘上字节，不看档位），
    会调它的只有 `tests/remote/test_deliver_mode.py`。**别把它接上**：2026-10-23 起形态在
    **写盘时**就已定好（`plan/anchor-full-rounds`：非锚点轮的 `opt.tar` 本来就是容器），而
    `mode == "full"` 会把它们重新展开成整份 ⇒ 那个 plan 的省量当场归零，且无人察觉。

    * `mode != "full"` ⇒ **不展开**：直接发产物目录里的原件（delta 容器）。这一档的存在
      意义就是把体积压到最小，展开成整份再发是自相矛盾；
    * `mode == "full"` ⇒ 用注入进来的落地器展开（调用方传 `plan_run` 装载那条路同一个函数）；
    * 展开失败 ⇒ 回落发原件并把 `wire_form` 记成 `delta`（**不阻塞、不静默**）。
    """
    store_dir = ArtifactStore(Path(root), run_id=run_id).dir_for(int(it))
    wpath = delta_files.find_existing(store_dir, "weights.json")
    opath = delta_files.find_existing(store_dir, "opt.tar")
    try:
        wj = wpath.read_bytes() if wpath is not None else b""
    except OSError:
        wj = b""
    try:
        opt = opath.read_bytes() if opath is not None else b""
    except OSError:
        opt = b""
    if str(mode) != "full":
        return wj, opt, ("delta" if is_container_bytes(wj) else "full")
    try:
        if wpath is not None and expand_weights and delta_files.any_container_file(wpath):
            wj = Path(expand_weights(wpath)).read_bytes()
        if opath is not None and expand_opt and delta_files.any_container_file(opath):
            opt = Path(expand_opt(opath)).read_bytes()
    except Exception as e:  # 展开失败 = 这一轮发不了整份，不是投递失败
        if log:
            log(
                f"补传 it{int(it)}：整份展开失败（{type(e).__name__}: {e}）"
                "——本轮回落发原件（delta），hub 侧落地器照旧兜"
            )
    return wj, opt, ("delta" if is_container_bytes(wj) else "full")


def rounds_with_rows(root: str | Path) -> set[int]:
    """产物目录里**真轮次**的集合 = `metrics.jsonl` 里有行的那些 `it`（判据唯一实现）。

    起点与真轮次的唯一结构差别就是「记不记账」：`ArtifactStore.checkpoint(row=None)` 是起点
    专用，`else` 才追加行 ⇒ **有行 ⟺ 真轮次**（2026-10-11 事故 x24-stack5：把起点当成 it1 推
    上去，hub 首写锁定 ⇒ 真 it1 永不到岸）。HTTP 腿与 outbox 必须同一把尺子。

    不做缓存（outbox 每拍对一个段目录调一次；HTTP 腿的 `_ledger_its` 自己按 mtime 缓存）。
    """
    p = Path(root) / ArtifactStore.METRICS_NAME
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return set()
    its: set[int] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue  # 半截行（被 kill）——与 `ArtifactStore.rows` 同口径地跳过
        if isinstance(row, dict):
            it = row.get("it")
            if isinstance(it, int) and not isinstance(it, bool):
                its.add(int(it))
    return its


def row_for(
    root: str | Path,
    it: int,
    weights_fp: str,
    *,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    """账本里**与本轮权重字节相符**的那一行（同名 it 有多行时取**最后一个**）。

    2026-09-22 事故的两个关键点（旧的「返回第一条同 it 行」两者都错）：

      * `metrics.jsonl` 是**跨会话追加**的：同一个目录续跑/重开时，同一个 it 会再落一行
        （`ArtifactStore.checkpoint` 追加，不重写）。旧实现返回**第一条** ⇒ 拿上一会话的行去配
        这一会话的权重，hub 侧「账本行与权重不符」直接 400 拒收（现场：
        `行记 bff93df1… 实得 5f099e55…`）。这与控制台「同 iter 取最后一条 = 最新对账结果」
        是同一条口径（`dashboard/src/server/iters.ts`）；
      * 只有 `weights_fp` **对得上本轮字节**的行才是这一轮的行——配不上就宁可不发：
        一个指错轮次的行比没有行危险得多（读数看起来完全正常）。

    ★ 2026-10-23（W3）：从 `OfflineDeliverer._row_for` 原样抽出（`deliver_outbox` 的本地 outbox
    要用**同一份**判据，而它不能 import 本模块——见该模块 `OutboxParts` 的注释）。
    """
    from remote.artifacts import weights_fps

    p = Path(root) / ArtifactStore.METRICS_NAME
    if not p.exists():
        return None
    cands: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and int(row.get("it", -1)) == int(it):
                cands.append(row)
    except OSError:
        return None
    for fp in weights_fps(Path(root), int(it), prefer=weights_fp):
        for row in reversed(cands):  # 新→旧：最新的那一行最先被认
            if fp and str(row.get("weights_fp", "") or "") == fp:
                return row
    if cands and log is not None:
        log(
            f"补传 it{it}: 账本行与权重不符（行记 {str(cands[-1].get('weights_fp', ''))[:16]}… "
            f"实得 {weights_fp[:16]}…）——本轮**不发 row**（宁少不错：指错轮次的行比缺行危险）"
        )
    return None


def build_round_body(
    root: str | Path,
    run_id: str,
    it: int,
    *,
    course: str = "",
    hub_lease: str = "",
    worker_name: str = "",
    eval_rows: list[dict] | None = None,
    row_for: Callable[[int, str], dict | None] | None = None,
    lineage: Mapping | None = None,
    pergame: bool = True,
    now: float = 0.0,
    log: Callable[[str], None] | None = None,
) -> tuple[dict, bytes, str, str, int] | None:
    """产物目录 → 一轮的完整 wire body：`(body, raw, weights_fp, opt_form, pergame_deferred)`。

    **唯一装配实现**（2026-10-23，plan/push-mode-notebooks §6.5 / 评审 P1-1）：HTTP 腿
    （`OfflineDeliverer._post_artifact`）与本地 outbox（`LocalOutboxDeliverer`）都调它 ⇒ 两边
    的字节一致是**构造性质**，不是一个「看起来严格」的测试比较出来的。

    权重读不到 / 为空 ⇒ `None`（那一轮本拍不投：扫描时还在、读时没了是瞬时状态，不标记已投）。
    `row_for(it, weights_fp)` 由调用方给（配不上本轮字节就宁可不带行）；`eval_rows` 已由
    调用方算好（需不需要评估读数、上界多少都是调用方的策略）。
    """
    d = ArtifactStore(Path(root), run_id=run_id)
    wp = d.weights_path(int(it))
    try:
        wj = wp.read_bytes()
    except OSError as e:
        if log:
            log(f"补传 it{int(it)} 本轮跳过：读不到权重 {wp}（{e}）")
        return None
    if not wj:
        if log:
            log(f"补传 it{int(it)} 本轮跳过：权重文件是空的（{wp}）")
        return None
    opt = b""
    op = d.opt_path(int(it))
    if op.exists():
        try:
            opt = op.read_bytes()
        except OSError:
            opt = b""  # 动量缺失的代价是「hub 侧续跑 Adam 归零」，不是投递失败
    wfp = hashlib.sha256(wj).hexdigest()
    row, pergame_n = wire_row(
        row_for(int(it), wfp) if row_for is not None else None, keep_per_game=bool(pergame)
    )
    body = build_artifact_body(
        run_id=run_id,
        it=int(it),
        weights_json=wj,
        opt_tar=opt,
        row=row,
        eval_rows=list(eval_rows or []),
        lineage=lineage or {},
        source_dir=str(root),
        course=course,
        hub_lease=hub_lease,
        worker_name=worker_name,
        now=now,
    )
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return body, raw, wfp, delta_files.form_of(opt), pergame_n


def build_result_body(
    *,
    run_id: str,
    it_end: int,
    state: str,
    summary: dict | None = None,
    end_it_reached: bool = False,
    seg_end_kind: str = "",
    delivered: int = 0,
    lineage: Mapping | None = None,
    source_dir: str = "",
    course: str = "",
    now: float = 0.0,
) -> tuple[dict, bytes]:
    """段末摘要的体：`(body, raw)`——从 `offline_deliver.deliver_result` 原样搬来的**唯一实现**。

    与逐轮产物的差别：摘要是**会变的**（同一 run 续跑后 `it_end` 更大），hub 覆盖写；它不是
    不可变快照，而是「这条腿现在到哪了」的最新答案。超限时只换 `summary`（标 `truncated`），
    其余字段原样——那正是旧实现的行为。
    """
    m = lineage or {}
    body: dict = {
        "run_id": run_id,
        "it_end": int(it_end),
        "state": str(state),
        "end_it_reached": bool(end_it_reached),
        #: 段末三态（J2）：与 `end_it_reached` 同源同刻，**总是发**（空串 = 非段末/未知）——
        #: hub 的固定键集读它，缺席会被读成「旧形状」（容忍读）。
        "seg_end_kind": str(seg_end_kind or ""),
        "delivered": int(delivered),
        "summary": dict(summary or {}),
        "plan_sha256": str(m.get("plan_sha256", "") or ""),
        "course_fp": str(m.get("course_fp", "") or ""),
        "commit": str(m.get("commit", "") or ""),
        "source_dir": str(source_dir)[-300:],
        "ts": float(now),
    }
    if course:
        body["course"] = course
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(raw) > OFFLINE_RESULT_BODY_MAX:
        raw = json.dumps({**body, "summary": {"truncated": True}}, ensure_ascii=False).encode(
            "utf-8"
        )
    return body, raw


def eval_rows_for(
    root: str | Path,
    it: int,
    *,
    cap: int,
    log: Callable[[str], None] | None = None,
    throttled_log: Callable[[str, str], None] | None = None,
) -> tuple[list[dict], int, int]:
    """产物目录里这一轮的评估读数：`(行, 原始字节, 线上字节)`（没评过 = 空，永不抛）。

    从 `offline_deliver._eval_rows_for` 原样搬来（2026-10-23，LOC 下沉）：判据一字不改——
    逐局行只取 `iter == it` 且**没有 `source`**（B/C evalboard 行的标记）；`summary` 行一起发
    （控制台指标表 / eval 列 / 开课回执 / 门判据唯一认的读数键）且不占上界；逐局行过白名单
    （`EVAL_ROW_WIRE_KEYS`）瘦身。
    """
    from remote.deliver_wire import slim_eval_rows

    p = Path(root) / ArtifactStore.EVAL_LOG_NAME
    rows: list[dict] = []
    summary: list[dict] = []
    try:
        if not p.exists():
            return rows, 0, 0
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict) or "source" in r:
                continue
            if r.get("iter") != int(it):
                continue
            if r.get("event") == "eval_summary":
                summary.append(r)
                continue
            if r.get("event") != "eval":
                continue
            rows.append(r)
    except OSError:
        return [], 0, 0
    if len(rows) > cap:
        if log:
            log(
                f"补传 it{it}: 评估行 {len(rows)} 条 > 上界 {cap}"
                f"——只发前 {cap} 条（其余随 artifacts zip 回去）"
            )
        rows = rows[:cap]
    raw_bytes = wire_bytes = 0
    if rows:
        rows, raw_bytes, wire_bytes = slim_eval_rows(rows)
        if wire_bytes < raw_bytes and throttled_log:
            throttled_log(
                "eval-slim",
                f"评估行白名单：it{it} {raw_bytes / 1024:.0f}KB → {wire_bytes / 1024:.0f}KB"
                f"（{len(rows)} 行；分析列随抽屉/离线包到达）",
            )
    return rows + summary, raw_bytes, wire_bytes


def build_artifact_body(
    *,
    run_id: str,
    it: int,
    weights_json: bytes,
    opt_tar: bytes,
    row: dict | None,
    eval_rows: list[dict],
    lineage: Mapping,
    source_dir: str,
    course: str = "",
    hub_lease: str = "",
    worker_name: str = "",
    now: float = 0.0,
) -> dict:
    """一轮补传的体（`/offline/artifact`）——从 `offline_deliver._post_artifact` 原样搬来。

    键的取舍是**契约**（hub 侧 `store_offline_artifact` 认它们）：缺省不带 `course` /
    `lease_token` / `worker_name`（与「带空串」不等价，更贴近旧字节行为）。
    """
    from common.protocol import encode_opt_tar, encode_weights_json

    body: dict = {
        "run_id": run_id,
        "it": int(it),
        "weights_fp": hashlib.sha256(weights_json).hexdigest(),
        "weights_json": encode_weights_json(weights_json),
        "opt_fp": hashlib.sha256(opt_tar).hexdigest() if opt_tar else "",
        "opt_tar_b64": encode_opt_tar(opt_tar) if opt_tar else "",
        # 账本行**随本轮一起走**（hub 侧要拿它画曲线/对账）：只取**与本轮字节相符**的那一行
        # （同名 it 的历史行一律不取——见 `_row_for`）；找不到就只发权重（不含 row）。
        "row": row,
        # 云机 A 层评估的就地读数（`eval_on_cloud`）：与权重同一趟回去，hub 侧并进课程账本
        # （`_HubQueue.merge_eval_rows`）——否则控制台要等整段结束才知道读数，而「一条跑偏的腿」
        # 正是这条腿要尽早看见的东西。空列表 = 本轮没评（或已降到「只发权重」）。
        "eval_rows": eval_rows,
        # 血缘（hub 侧据此把这条腿接回某个 run；全是 manifest 里的原值）
        "plan_sha256": str(lineage.get("plan_sha256", "") or ""),
        "course_fp": str(lineage.get("course_fp", "") or ""),
        "commit": str(lineage.get("commit", "") or ""),
        "source_dir": str(source_dir)[-300:],
        "ts": float(now),
    }
    if course:
        body["course"] = course
    if hub_lease:
        body["lease_token"] = hub_lease
    # 可读名（`kaggle-o`）：dashboard 节点贡献统计的 PPO 组拿它当列名（hub 收下这一轮时
    # 把它记进账本的 `offline_artifact` 行）。**缺省不带键**（与 `course`/`lease_token` 同纪律）。
    if worker_name:
        body["worker_name"] = worker_name
    return body


class DeliverMetricsMixin:
    """统计批（`?parts=metrics`）——抖动期「权重发不动、读数也要到」的那半边。

    2026-10-23（`plan/offline-deliver-full-and-gaps` §3.3 / S5）从 `OfflineDeliverer` 原样搬来
    （LOC 预算：腿本体只留「怎么发」）。宿主（`OfflineDeliverer`）提供的面：
    `root` / `run_id` / `base_url` / `course` / `hub_lease` / `enabled` / `log` /
    `_row_for` / `_post` / `_metrics_sent` / `_metrics_stat` / `metrics_parts` /
    `_metrics_unsupported`。
    """

    #: 一批最多带多少轮 / 多少字节。为什么先发它：抖动期权重发不动时**读数也要到**（D11）。
    #: ⚠ 行里保留 `perGame`（与 `anchor-full-rounds` S6 一致）⇒ 实际约 24 KB/轮，
    #: 200 KB 的批上限通常先于「100 轮」生效（约 4–8 轮/批）。`perGame` 一旦再摘掉，
    #: 这里的字节模型与 `deliver_mode.BYTES_PER_ROUND["anchors"]` 必须同步改。
    METRICS_BATCH_ROUNDS = 100
    METRICS_BATCH_BYTES = 200 * 1024

    root: Path
    run_id: str
    base_url: str
    course: str
    hub_lease: str
    enabled: bool
    log: Callable[[str], None]
    _metrics_sent: set
    #: 权重已到岸但体里没有 row 的轮次（评审 P1-7）。
    _metrics_owed: set
    _metrics_stat: tuple
    metrics_parts: bool
    _metrics_unsupported: bool

    def _metrics_gap(self) -> list:
        """还没把统计送上 hub 的轮（升序）——**不分档**：统计不能等权重。

        两个来源缺一不可：
          * `pending()`：权重还没发出去的轮；
          * `_metrics_owed`：权重已发出去、但体里**没有 row** 的轮（评审 P1-7）。它们已经从
            `pending()` 消失，只有这本欠账能把它们捞回来（跨会话随 `delivered.json`）。
        """
        sent = self._metrics_sent
        pending = self.pending()  # type: ignore[attr-defined]
        owed: set[int] = getattr(self, "_metrics_owed", set())
        return [it for it in sorted(set(pending) | owed) if it not in sent]

    def _post_metrics_batch(self, its: list, *, budget_sec: float | None = None) -> int:
        """把一批轮的**统计**（`row`）单独送到 hub（`?parts=metrics`），返回送达轮数。

        没有权重 ⇒ 走 hub 的统计入口（`store_offline_metrics`）：只写 `<run>/metrics.jsonl`
        与课程侧 `training_log.jsonl`，**不创建 `it-N/`**（没权重不该让 `resume_anchor` 看见）。
        失败 ⇒ 不销账（`_metrics_sent` 不动）⇒ 下一拍照旧再试；统计可再生，不进 `_repost`。
        """
        if not self.enabled or not self.metrics_parts or self._metrics_unsupported:  # type: ignore[attr-defined]
            return 0
        if not its:
            return 0
        body, n = build_metrics_body(
            self.root,
            self.run_id,
            list(its),
            row_for=lambda it: self._row_for(int(it), ""),  # type: ignore[attr-defined]
            cap_rounds=self.METRICS_BATCH_ROUNDS,
            cap_bytes=self.METRICS_BATCH_BYTES,
            course=self.course,
        )
        if not n:
            return 0
        if self.hub_lease:
            body["lease_token"] = self.hub_lease
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        status, _resp = self._post(  # type: ignore[attr-defined]
            f"{OFFLINE_ARTIFACT_PATH}?parts=metrics", raw, budget_sec=budget_sec
        )
        if status == 404:
            # 旧 hub 没有这个入口 ⇒ 本会话停发（再做就是每拍一次 404 + 污染探活缓存）。
            # 权重那条路照旧 —— 统计上不去只丢读数，不丢产物。
            self._metrics_unsupported = True
            self.log("补传统计停用：hub 不认 `?parts=metrics`（404）——本会话只发权重")
            return 0
        if status in (200, 201):
            for r in body["rows"]:
                it = int(r["it"])
                self._metrics_sent.add(it)
                # 统计欠账销掉（P1-7）：这一轮终于把 row 送出去了。
                getattr(self, "_metrics_owed", set()).discard(it)
            self._metrics_stat = (n, len(raw))
            first, last = body["rows"][0]["it"], body["rows"][-1]["it"]
            self.log(
                f"补传统计 {n} 轮（it{first}…it{last}，{len(raw) / 1024:.0f}KB）→ {self.base_url}"
            )
            return n
        return 0


def build_metrics_body(
    root: str | Path,
    run_id: str,
    its: list[int],
    *,
    row_for: Callable[[int], Mapping | None],
    cap_rounds: int,
    cap_bytes: int,
    course: str = "",
) -> tuple[dict, int]:
    """统计批的体：`(body, 实际装入的轮数)`（按字节切批，至少装一轮）。

    `row_for(it)` 由调用方给（要读 `metrics.jsonl` 并按 `weights_fp` 对账——那是腿本体的事）。
    """
    rows: list[dict] = []
    used = 0
    for it in list(its)[: int(cap_rounds)]:
        row = row_for(int(it))
        if not isinstance(row, Mapping):
            continue
        blob = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if rows and used + blob > int(cap_bytes):
            break
        rows.append({"it": int(it), "row": dict(row)})
        used += blob
    if not rows:
        return {}, 0
    body: dict = {"run_id": run_id, "rows": rows}
    if course:
        body["course"] = course
    return body, len(rows)
