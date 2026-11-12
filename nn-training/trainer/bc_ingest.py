"""bc_ingest —— 等一个 BC job 回传，并把回传入账（S5 第四刀，2026-09-27）。

从 `trainer/bc_loop.py` 整块搬出（**逐字节不动**）。这里是「怎么问一个远端 BC job 的结果、怎么把它的
每-epoch 指标与 eval 边界写进账本」的**唯一**实现，与「一轮怎么跑（采集/发布 → 落位归档）」是两件事：

* **轮询会话**：`BcWait`（跨「让位 → 过一会儿回来问」保留 epoch 游标 / 无进展告警 / 错误连击退避）
  + `READY`/`PENDING`/`TRANSIENT` 三态分类 + `wait_bc_round`（单课程阻塞包装）；
* **回传入账**：`ingest_bc_metrics`（`bc-metrics` 增量 → `bc_epoch` 账本事件 + eval 边界；
  迟到补发的旧 epoch 只入账、不派 eval）
  + `run_epoch_eval`（hub resume 权重 → 多地图干净评估 → `bc_eval` 事件）+ `ledger_bc_epoch`；
* **节奏常量**：`IDLE_WARN_SEC` / `POLL_SEC` / `POLL_MAX_SEC`。

**为什么单独成家**：它是**读远端回传**的面（HTTP 轮询 + 退避 + 账本写入），与「编排一轮」（`BcLoop`
的采集/发布/落位/归档/磁盘有界）耦合度低；单课程入口（`wait_bc_round`）与 supervisor 的让位引擎
（`BcLoop.run_one_round` → `BcWait.poll_once`）共用这份会话，故它必须能被**两条驱动路径**同时依赖。

依赖面 = stdlib（`json`/`time`/`dataclasses`/`pathlib`）+ `common.protocol` + `remote.hub_http`
（`_request` **函数内导入**，测试/故障注入打的就是它）+ `biz.bc_config`/`biz.bc_eval`/`biz.bc_ledger`/`biz.log`。

`trainer/bc_loop.py` 保留 `X as X` 门面，历史 import 与 monkeypatch 点（`bc_loop.time` / `bc_loop.wait_bc_round`）
一行不改（「名字是契约，位置不是」）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from common.log import log
from common.protocol import decode_weights_json
from worker.archive import backup_weights
from worker.bc_config import BcCourseConfig
from worker.bc_eval import BcEvalError, dispatch_bc_eval
from worker.bc_ledger import append_ledger

#: 无进展告警阈值（秒）：等 job 时超过这么久没有新 epoch 入账 ⇒ 提示检查云机 worker（一次）。
IDLE_WARN_SEC = 900.0

#: job 结果轮询与退避（秒）——与 `wait_job` 同款。
POLL_SEC = 5.0
POLL_MAX_SEC = 60.0

# ---- 账本（事件写入；读法在 biz/bc_ledger.py） -------------------------------


def ledger_bc_epoch(jsonl_path: Path, it: int, row: dict) -> None:
    """bc_epoch 账本事件（控制台 epoch 指标面板数据源）。"""
    append_ledger(
        jsonl_path,
        {
            "event": "bc_epoch",
            "it": int(it),
            "epoch": int(row.get("epoch", 0) or 0),
            "train_loss": row.get("train_loss"),
            "val_loss": row.get("val_loss"),
            "move_acc": row.get("move_acc"),
            "fire_acc": row.get("fire_acc"),
            #: v6 第三头（#11）：legacy 行没有该键 ⇒ None（旧账本仍可读）。
            "hold_acc": row.get("hold_acc"),
            #: B2：与训练同口径的加权 val（诊断列）；`val_loss` 仍保持未加权。
            "val_loss_weighted": row.get("val_loss_weighted"),
            "lr": row.get("lr"),
            "ts": time.time(),
        },
    )


def audit_bc_ledger(epochs: list[int], expected: int) -> dict:
    """账本完整性审计（B8，2026-11-05）：缺号 / 重复 / 末轮必达。

    事故（报告 §6）：107 条 `bc_epoch` 只覆盖 77 个 epoch，缺 5/25/80、30 条重复 ——
    面板照样把点连成曲线，看不出中间是空的。纯函数 ⇒ 可在轮末入账一条
    `bc_ledger_audit`，面板/报告读它而不是自己猜。

    `expected` = 课程声明的 epoch 总数（0 = 未知，只报 observed）。
    """
    seen = sorted(set(int(e) for e in epochs if int(e) > 0))
    have = set(seen)
    missing = [e for e in range(1, int(expected) + 1) if e not in have] if expected > 0 else []
    duplicates = len([e for e in epochs if int(e) > 0]) - len(seen)
    return {
        "event": "bc_ledger_audit",
        "expected": int(expected),
        "observed": len(seen),
        "missing": missing,
        "duplicates": duplicates,
        "complete": bool(not missing and duplicates == 0 and (expected <= 0 or len(seen) >= expected)),
    }


def _archive_epoch_weights(
    hub_url: str,
    token: str,
    jid: str,
    it: int,
    epoch: int,
    course: BcCourseConfig,
    logger=log,
) -> str | None:
    """把 hub 上的 ep{N} 快照归档到课程 `backup_dir`（#12）。

    - **只在该课程配了 `backup_dir` 时做**：没配时默认会落到 RL 的
      `nn-training/weights/`，80 个 epoch 会污染 RL 归档面；轮末那份 `it` 归档仍保留。
    - 幂等：`backup_weights(label=epN)` 已有同名归档 ⇒ 直接复用，不产生第二份。
    - 失败只 WARN：归档是观测面，不得打断训练等待。
    """
    if not course.backup_dir:
        return None
    import tempfile

    from remote.hub_http import _request

    prefix = course.resolve_backup_prefix(course.name)
    try:
        st, body = _request(
            hub_url, token, f"/jobs/{jid}/epoch-weights/{int(epoch)}", timeout=30.0
        )
        if st == 404:
            logger(f"[run_bc] it{it} ep{epoch}: hub 无该 epoch 快照——跳过归档")
            return None
        if st != 200:
            raise RuntimeError(f"HTTP {st}")
        d = json.loads(body.decode("utf-8"))
        raw = decode_weights_json(str(d["weights"]))
        fd, tmp = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            dst = backup_weights(
                tmp,
                it,
                prefix=prefix,
                backup_dir=str(course.backup_dir),
                label=f"ep{int(epoch)}",
            )
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        if dst:
            logger(f"[run_bc] it{it} ep{epoch}: 权重归档 {dst}")
        return dst
    except Exception as e:
        logger(f"[run_bc] it{it} ep{epoch}: 权重归档失败（不致命）: {type(e).__name__}: {e}")
        return None


def run_epoch_eval(
    hub_url: str,
    token: str,
    jid: str,
    jsonl_path: Path,
    it: int,
    epoch: int,
    course: BcCourseConfig,
    cfg: dict | None,
    log=log,
) -> None:
    """从 hub 取**该 epoch** 的不可变快照 → 多地图干净评估 → bc_eval 事件。

    B6：不能用 `/resume`（它可能已经是更晚的权重）——迟到补发行按行序贴标签会得到假指标。
    """
    import json as _json

    from remote.hub_http import _request

    try:
        st, body = _request(
            hub_url, token, f"/jobs/{jid}/epoch-weights/{int(epoch)}", timeout=30.0
        )
        if st == 404:
            log(f"[run_bc] it{it} ep{epoch}: 无该 epoch 快照——本边界跳过 eval")
            return
        if st != 200:
            log(f"[run_bc] it{it} ep{epoch}: 快照读取失败（HTTP {st}）——本边界跳过 eval")
            return
        d = _json.loads(body.decode("utf-8"))
        weights = decode_weights_json(str(d["weights"]))
        result = dispatch_bc_eval(
            weights_bytes=weights,
            eval_levels=list(course.eval.levels),
            games_per_stage=int(course.eval.games_per_stage),
            it=it,
            epoch=epoch,
            # B4：课程声明的面板与实际关卡值并存入行，跨面板不可相减。
            course_max_ticks=int(course.max_ticks),
            cfg=cfg,
            course=course,
            course_env=bool(course.eval.course_env),
            log=log,
        )
        append_ledger(jsonl_path, {"event": "bc_eval", "it": int(it), **result})
    except BcEvalError as e:
        log(f"[run_bc] it{it} ep{epoch}: eval 放弃（{e}）")
    except Exception as e:  # eval 失败绝不打断训练等待
        log(f"[run_bc] it{it} ep{epoch}: eval 异常 {type(e).__name__}: {e}")


# ---- 等待（非阻塞探针 + 阻塞包装） -----------------------------------------

#: 探针结论：`ready`（结果已到）/ `pending`（正常排队中）/ `transient`（网络错或 5xx，退避重问）。
READY = "ready"
PENDING = "pending"
TRANSIENT = "transient"


@dataclass
class BcWait:
    """一次「等某个 job 回传」的会话（阻塞版 `wait_bc_round` 与让位版 `BcLoop` **共用记账**）。

    为什么是对象而不是循环里的局部变量：等待带三份跨调用必须保留的记账 —— 已入账的 epoch 数
    （防重复写账本/重复派 eval）、上次有进展的时刻（无进展告警）、错误连击（退避）。单进程
    supervisor 下「等」会被切成「让位 → 过一会儿回来问」，那三份记账必须活过这个断裂。
    """

    #: 在等的 job（结果按 jid 回家，所以它是这段等待的身份）。
    jid: str
    #: 已入账的 epoch 行数（增量游标）。
    seen_metrics: int = 0
    #: 已入账的**最新** epoch（读面判「迟到补发行」的基准：`ep < last_epoch` ⇒ 重放的旧 epoch）。
    last_epoch: int = 0
    #: 上一次有进展（有新 epoch 入账）的时刻。
    last_progress: float = field(default_factory=time.time)
    #: 无进展告警是否已打（只打一次，避免刷屏）。
    idle_warned: bool = False
    #: 连续网络错/5xx 次数（退避用；404 = 正常排队，清零）。
    err_streak: int = 0
    #: 已入账的 epoch（B8：同 epoch 重发只记一次；轮末用它做缺号审计）。
    ledger_epochs: list[int] = field(default_factory=list)

    def backoff_sec(self) -> float:
        """瞬时错误后的退避（2 的幂，封顶 `POLL_MAX_SEC`）。"""
        factor = float(2 ** max(self.err_streak - 1, 0))
        return min(POLL_SEC * factor, POLL_MAX_SEC)

    def poll_once(
        self,
        *,
        hub_url: str,
        token: str,
        jsonl_path: Path,
        it: int,
        course: BcCourseConfig,
        cfg: dict | None,
        logger=log,
    ) -> tuple[str, dict | None]:
        """问一次结果（**不睡、不重试、不对「还没好」抛错**）。

        分类与 hub 上的 RL 探针同规（`probe_job_result`）：`ready` / `pending`(404) /
        `transient`(网络错、5xx)；**其余状态码与坏结果抛错**——「还没好」与「永远好不了」
        必须分开，混在一起就是一个静默挂死的等待环。

        `_request` **函数内导入**（原 `run_bc.wait_bc_round` 同款）：它是 HTTP 面的私有
        助手，按名字导入会把绑定钉死在 import 时刻——测试/故障注入靠 `monkeypatch`
        `remote.hub_http._request` 换掉整条 HTTP 链路，顶层导入会让补丁失效（症状：单测真去连
        hub 并挂满超时）。
        """
        from remote.hub_http import _request

        try:
            status, body = _request(hub_url, token, f"/jobs/{self.jid}/result", timeout=30.0)
        except Exception as e:
            self.err_streak += 1
            logger(
                f"wait_bc_round: {self.jid} 轮询网络错误 ({type(e).__name__})"
                f"——退避 {self.backoff_sec():.0f}s"
            )
            return TRANSIENT, None
        if status == 200:
            loaded = json.loads(body.decode("utf-8"))
            if isinstance(loaded, dict):
                # B8：轮末审计（缺号/重复/末轮必达）入账，面板读它而不是猜曲线完整性。
                # 冒烟回显（`{"smoke": True}`）不是真实训练结果：本轮作废、零 epoch，
                # 审计只会把空账本写成「缺全部」——空轮不落任何账本事件。
                if not loaded.get("smoke"):
                    # 末轮 flush（2026-11-08）：READY 之后不会再走下面的 PENDING 分支，
                    # 短 job（短于一个轮询间隔）会一次 drain 都没有。审计前必须把最后
                    # 一批 /bc-metrics 入账，否则末轮（常是唯一一轮）的 bc_epoch 永久丢失。
                    ingest_bc_metrics(
                        self,
                        hub_url=hub_url,
                        token=token,
                        jsonl_path=jsonl_path,
                        it=it,
                        course=course,
                        cfg=cfg,
                        logger=logger,
                    )
                    try:
                        audit = audit_bc_ledger(self.ledger_epochs, int(course.train.epochs))
                        append_ledger(
                            jsonl_path,
                            {**audit, "it": int(it), "jid": self.jid, "ts": time.time()},
                        )
                        if not audit["complete"]:
                            logger(
                                f"[run_bc] it{it}: 账本不完整——缺 ep{audit['missing']}"
                                f"、重复 {audit['duplicates']} 条（expected={audit['expected']}）"
                            )
                    except Exception as e:  # 审计失败绝不打断收尾
                        logger(f"[run_bc] WARN 账本审计失败: {type(e).__name__}: {e}")
                return READY, loaded
            raise RuntimeError(f"wait_bc_round: job {self.jid} 结果非对象")
        if status == 404:
            self.err_streak = 0  # 还没回 = 正常排队
        elif status >= 500:
            self.err_streak += 1
            logger(
                f"wait_bc_round: {self.jid} HTTP {status}（瞬时）——退避 {self.backoff_sec():.0f}s"
            )
            return TRANSIENT, None
        else:
            raise RuntimeError(
                f"wait_bc_round: HTTP {status}: {body[:200].decode('utf-8', 'replace')}"
            )
        # ---- job 在跑：拉每 epoch 指标增量（控制台可见）+ eval 边界 ----
        ingest_bc_metrics(
            self,
            hub_url=hub_url,
            token=token,
            jsonl_path=jsonl_path,
            it=it,
            course=course,
            cfg=cfg,
            logger=logger,
        )
        return PENDING, None


def ingest_bc_metrics(
    wait: BcWait,
    *,
    hub_url: str,
    token: str,
    jsonl_path: Path,
    it: int,
    course: BcCourseConfig,
    cfg: dict | None,
    logger=log,
) -> int:
    """拉一次 job 的每 epoch 指标增量 → `bc_epoch` 账本事件 + eval 边界（返回新游标）。

    失败**不抛**（指标/评估是观测面，绝不打断训练等待）；无进展超时只告警一次。
    """
    from remote.hub_http import _request  # 同 `poll_once`：可被 monkeypatch 替换

    eval_cfg = course.eval
    try:
        mstatus, mbody = _request(hub_url, token, f"/jobs/{wait.jid}/bc-metrics", timeout=15.0)
        if mstatus == 200:
            rows = json.loads(mbody.decode("utf-8")).get("rows") or []
            new_rows = rows[wait.seen_metrics :]
            if new_rows:
                wait.seen_metrics = len(rows)
                wait.last_progress = time.time()
                wait.idle_warned = False
                # 迟到补发行（2026-11-04）：隧道 530 丢掉的那个 epoch 由 worker 的 backlog 在
                # 最新 epoch **之后**重放 ⇒ 这里会拿到一行「回头行」（…ep6, ep5）。账本要照收，
                # 但它**不能派 eval**：`run_epoch_eval` 取 hub `/resume`（此刻已是 ep6+ 的权重），
                # 拿新权重冒充旧 epoch 的结果比缺一行更糟。
                late: set[int] = set()
                dup = 0
                for r in new_rows:
                    ep = int(r.get("epoch", 0) or 0)
                    # B8：同 epoch 重发（旧链路的 30 条重复行）只入账一次。
                    if ep and ep in wait.ledger_epochs:
                        dup += 1
                        continue
                    if ep:
                        wait.ledger_epochs.append(ep)
                    if ep and ep < wait.last_epoch:
                        late.add(ep)
                    ledger_bc_epoch(jsonl_path, it, r)
                    if ep:
                        _archive_epoch_weights(hub_url, token, wait.jid, it, ep, course, logger)
                    if ep > wait.last_epoch:
                        wait.last_epoch = ep
                logger(
                    f"[run_bc] it{it}: epoch 指标 +{len(new_rows) - dup} 行"
                    f"（最新 ep{wait.last_epoch}）入账"
                    + (f"；跳过重复 {dup} 行" if dup else "")
                )
                if late:
                    logger(
                        f"[run_bc] it{it}: 其中 {len(late)} 行是迟到补发"
                        f"（ep{','.join(str(e) for e in sorted(late))}）——只入账，不派 eval"
                    )
                if eval_cfg.enabled:
                    for r in new_rows:
                        ep = int(r.get("epoch", 0) or 0)
                        if not ep or ep in late:
                            continue
                        if ep % int(eval_cfg.every_epochs) == 0:
                            run_epoch_eval(
                                hub_url, token, wait.jid, jsonl_path, it, ep, course, cfg, logger
                            )
    except Exception as e:
        logger(f"[run_bc] WARN 指标轮询失败（不致命）: {type(e).__name__}: {e}")
    # 无上限模式下唯一需要人介入的情形：云机没起来 ⇒ 永远等不到 epoch。
    # 只提示一次（避免刷屏），进程继续等。
    if not wait.idle_warned and time.time() - wait.last_progress > IDLE_WARN_SEC:
        wait.idle_warned = True
        logger(
            f"[run_bc] ⚠ job {wait.jid} 已 {int(IDLE_WARN_SEC / 60)} 分钟无新 epoch 入账"
            "——确认云机 GPU worker 是否已启动并连上 hub："
            "`python -m remote.worker --poll <hub_url> --token <token> --device cuda`"
        )
    return wait.seen_metrics


def wait_bc_round(
    *,
    hub_url: str,
    token: str,
    jid: str,
    jsonl_path: Path,
    it: int,
    course: BcCourseConfig,
    cfg: dict | None,
    wait_sec: float,
    log=log,
) -> dict:
    """BC 专用结果等待环（**阻塞包装**：内部是 `BcWait.poll_once` + 退避；让位版见 `BcLoop`）。

    404 = 正常等待；5xx/网络错误按 2 的幂退避（wait_job 同款）；轮询间隙拉
    /jobs/{id}/bc-metrics 增量行 → bc_epoch 账本事件（控制台可见）；新 epoch 命中
    `eval.every_epochs` 边界 → hub resume 权重快照 → 多地图干净评估 → bc_eval 事件。

    `wait_sec <= 0` = **无上限**（默认，见 DEFAULT_WAIT_SEC）。超时是**错误**（不是"算了"）：
    重发 job 会让 bc-resume 失效 ⇒ 从头训。

    单进程 supervisor 驱动的 BC 课**不走本函数**（它一次只问一句就把执行权让出去）——
    两者共用 `BcWait`/`poll_once`，所以轮询语义只有一份。
    """
    wait = BcWait(jid=jid)
    deadline = None if wait_sec <= 0 else time.time() + wait_sec
    while deadline is None or time.time() < deadline:
        status, result = wait.poll_once(
            hub_url=hub_url,
            token=token,
            jsonl_path=jsonl_path,
            it=it,
            course=course,
            cfg=cfg,
            logger=log,
        )
        if status == READY:
            return result or {}
        time.sleep(wait.backoff_sec() if status == TRANSIENT else POLL_SEC)
    raise RuntimeError(f"wait_bc_round: job {jid} 超时（>{wait_sec}s）未完成")
