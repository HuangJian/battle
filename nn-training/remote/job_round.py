"""remote/job_round.py —— **每 job 一轮**：取活 → 起旁路线程 → 跑 → 交回传 → 收尾（2026-09-24 从 `remote/worker.py` 下沉）。

原来 `worker_loop` 是「轮询 + 每 job 一轮」织在一起的一个 while：轮询壳（多 hub round-robin、
停机感知、空闲退出）与一轮的 177 行体量互相遮挡。本模块拿走**后者**——`worker_loop` 现在只剩
取活前的那些判断，以及 `run_one_round(...)` 之后读 `RoundOutcome`。

    旁路线程组（预取填充 `_prefetch_fill` / 心跳续租 / 取消环）与 `run_job` 重叠 → 交回传 → finally 全收

配套下沉的还有 `_prefetch_fill`（原本只被这一轮启动；它依赖最深到 L3，本来就该在这一层）与
`settle_result`（原本是 `worker_loop` 的闭包，只为这一轮的 async 落定服务）。

## ★ 为什么是**宿主**下沉 + 一个注入点

本模块依赖最深到 L3（`job_lifecycle` / `download`），所以**拓扑秩是 L4**——与 `train_core` 同层：
一个组装训练，一个组装「一轮作业」。宿主 `worker`（L5）站在它上面。

* **`run_job_fn` 是唯一注入点**（`worker_loop` 传 `run_job_fn=run_job`）：`run_job` 住在
  `worker.py`（L5），本模块不能反向 import 它。宿主在**调用点**读 `worker.run_job` 这个**值**
  ⇒ `monkeypatch.setattr(remote.worker, "run_job", ...)` 照旧生效（10 处既有 patch 一行不改）。
  漏注入 = 调用当场 `TypeError`（响亮），不留兜底。
* **`uploader` 由宿主拥有并传入**：它的队列要跨 job 存活（`if once:` 的 drain 与最外层
  `finally` 的 close/stats 都在宿主），本模块只 submit。

## ★ seam 分档（本刀把 4 个 patch 目标从 `worker` 迁到本模块）

| 名字 | 解析在 | 为什么 |
|---|---|---|
| `run_job` | **`remote.worker`** | 宿主在调用点读值 ⇒ patch `worker` 仍生效（10 处不改） |
| `job_ready` · `abandon_job` · `release_job` · `report_job_failure` · `start_cancel_watcher` | **`remote.job_round`** | 调用点 100% 在这一轮里，随块搬走 |
| `peek_jobs` · `download_payload` · `PREFETCH_ROUND_SEC` | **`remote.job_round`** | `_prefetch_fill` 一起搬走（其依赖全 ≤ L3） |
| `uploader` · `post_result` | `remote.worker` | `uploader` 宿主建制并跨 job 存活；`post_result` 在宿主构造 uploader 时按调用时解析 |

`worker.py` 仍以 `X as X` 显式转发这些名字（tests / e2e 直接 import 门面）——**但 patch 门面无效**，
请 patch 本模块。这一条由 `tests/test_job_round_split.py` 双向钉住。

★ **本模块顶层零 torch**（与 `train_core` 同款纪律）：`run_job_fn` 是注入的，本模块连 `train_core`
  都不 import。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.protocol import (
    HEARTBEAT_SEC,
    ROLE_ONLINE,
    CodeChangedError,
    JobCancelledError,
    ProtocolError,
    RetryableError,
    UnreapableChildError,
)
from remote.bulk_sched import BULK_P2_PREFETCH, BulkPreemptError
from remote.download import download_payload
from remote.job_lifecycle import (
    _failure_detail,
    abandon_job,
    heartbeat,
    job_ready,
    job_started,
    peek_jobs,
    release_job,
    report_job_failure,
    start_cancel_watcher,
)
from remote.prefetch import PREFETCH_DEPTH_DEFAULT, PrefetchStore, pick_candidates
from remote.result_upload import Outcome, ResultUploader, UploadTask
from remote.wire import _wire_flush, _wire_start
from remote.worker_proc import _request_reload


@dataclass(frozen=True)
class RoundOutcome:
    """一轮的结局（宿主读它决定计数 / 退出）。

    * `uploaded` —— 走到了「交回传」（P2.5）。宿主据此 `done += 1` + 复位轮询读数。
    * `ok` —— 本轮算成功（成功路径上才为 True）。
    * `stop` —— 循环该整条退出（CodeChangedError + 无监督器 ⇒ 宿主 `return done`）。
      有监督器时 `_request_reload` 自己 SystemExit(86)，不走这里。
    """

    jid: str
    ok: bool
    uploaded: bool
    stop: bool



#: 预取传输账的合成 job id（预取不属于任何在跑的 job，但又必须记字节——
#: 否则「预取花了多少带宽」只能从 hub 侧对账，而 hub 看到的是同一张脸）。
PREFETCH_WIRE_ID = "prefetch"
#: 预取填充的轮询间隔（秒）：一轮填满后等这么久再问下一次 peek。
PREFETCH_ROUND_SEC = 5.0


def _prefetch_fill(
    base_url: str,
    token: str,
    store: PrefetchStore,
    stop: threading.Event,
    *,
    worker_id: str = "",
    role: str = ROLE_ONLINE,
    depth: int = PREFETCH_DEPTH_DEFAULT,
    skip: set[str] | None = None,
    log: Any = None,
) -> None:
    """后台填充软持有队列（P2）：`peek`（控制面，无副作用）→ **P2** 下载 → 暂存。

    与 `run_job` **重叠**运行——这就是预取的全部价值所在（§0：串行把 GPU 饿死在传输上）。
    三条纪律：

      · 下载一律走 `bulk_prio=BULK_P2_PREFETCH`：**可被控制面/关键传输当场打断**（丢半截，
        幂等重下）。预取不该有能力拖慢在跑的 job 或控制环。
      · 失败**不是失败**：被挤走/404/瞬时错误 → 就地丢掉、记一行、下一轮再来。
        **绝不**进 `ProtocolError`/`report_job_failure`（否则网络抖动会被报成节点故障）。
      · 预取只走 `download_payload` 一条路：minimize-payload 的 omit 协商落在那个函数里，
        预取落地后**自动继承**；在这里另写一个「整包 GET」就是把已瘦身的部分又吹回去。
    """
    skip = skip or set()
    log = log or (lambda _m: None)
    while not stop.is_set():
        try:
            peeked = peek_jobs(
                base_url,
                token,
                worker_id=worker_id,
                role=role,
                n=max(1, int(depth)),
                log=None,  # 预取的 peek 不进 poll 告警节流表（同一 url 会互相压报）
            )
        except Exception as e:
            log(f"prefetch: peek 失败（{type(e).__name__}）——{PREFETCH_ROUND_SEC:.0f}s 后再试")
            stop.wait(PREFETCH_ROUND_SEC)
            continue
        cands = list(peeked[0]) if peeked else []
        picked = pick_candidates(cands, held=store.held(), skip=skip, depth=depth)
        for cand in picked:
            if stop.is_set():
                break
            jid = str(cand["job_id"])
            try:
                payload = download_payload(
                    base_url,
                    token,
                    jid,
                    bulk_prio=BULK_P2_PREFETCH,
                    wire_jid=PREFETCH_WIRE_ID,  # 账记在合成 id 上，不污染在跑 job 的账
                    log=lambda m, _j=jid: log(f"prefetch {_j[:8]}: {m}"),
                )
            except BulkPreemptError as e:
                log(f"prefetch {jid[:8]}: {e}")
                continue
            except (ProtocolError, RetryableError, CodeChangedError) as e:
                log(f"prefetch {jid[:8]}: 放弃（{type(e).__name__}）——预取失败不算失败")
                continue
            except Exception as e:
                log(f"prefetch {jid[:8]}: 放弃（{type(e).__name__}: {e}）")
                continue
            if store.store(jid, payload, cand):
                log(f"prefetch {jid[:8]}: 已预取 {len(payload)} bytes（软持有，无租约）")
        _wire_flush(PREFETCH_WIRE_ID, log)  # 每轮一行预取传输账
        stop.wait(PREFETCH_ROUND_SEC)
    _wire_flush(PREFETCH_WIRE_ID, log)  # 收尾：最后一次没有等满一轮的也上账


def settle_result(
    jid: str,
    out: Outcome,
    wall_end: float,
    *,
    uploader: ResultUploader,
    log: Callable[[str], None],
) -> None:
    """回传落定：打结算行 + **把本 job 的传输账收在这一刻**（P2.5）。

    账必须等到这里才收：`out` 的字节/秒是 `post_result` 内部记的，而 async 下它发生
    在关键路径之后 —— 提前 flush 会把回传读成 0s（那正是最该看见的一段）。
    `wall_end` 只在 async 下传（sync = 回传就在关键路径里，口径不变）。
    """
    log(
        f"job {jid} done — "
        + (
            "lost the race (409, 赢家已落账) — 本份丢弃"
            if out.status == 409
            else (
                "backup 副本被拒（403，非本 job 租约持有人）— 本份丢弃，不算失败"
                if out.status == 403
                else "result accepted"
            )
        )
        + (f"  [回传 {out.seconds:.1f}s]" if out.seconds else "")
    )
    _wire_flush(jid, log, wall_end=wall_end if uploader.mode == "async" else None)


def run_one_round(
    base_url: str,
    token: str,
    job: dict,
    *,
    part_dir: Path,
    code_cache_dir: Path | None,
    pf_store: PrefetchStore | None,
    polls_since_accept: int,
    worker_id: str,
    device: str,
    torch_threads: int,
    echo: bool,
    restart_argv: list[str] | None,
    role: str = ROLE_ONLINE,
    prefetch_depth: int,
    uploader: ResultUploader,
    run_job_fn: Callable[..., dict],
    log: Callable[[str], None],
) -> RoundOutcome:
    """取活后的一整轮：旁路线程组 → `run_job_fn` → 交回传 → 收尾。

    **入参分档**（17 个太多？它们全是「宿主已经定好的事实与旋钮」，按角色读）：

      · 作业身份 —— `job` · `part_dir` · `code_cache_dir` · `pf_store` · `polls_since_accept`；
      · 传输/身份 —— `base_url` · `token` · `worker_id`；
      · 训练旋钮 —— `device` · `torch_threads` · `echo` · `role` · `prefetch_depth`；
      · 上报与生命周期 —— `restart_argv`（热替换）· `uploader` · `log`；
      · **注入点** —— `run_job_fn`（不能反向 import `worker`）。

    被明确**排除**在签名外的宿主概念：`multi`（宿主的「多 hub」）—— 它唯一的用处是
    `code_cache_dir=shared_code_cache if multi else None`，所以由宿主算好再传（本模块不知道
    有几个 hub，也不该知道）。`done` / `_polls_since_accept` 是宿主的账，从 `RoundOutcome` 回读。

    失败语义（与搬移前逐字一致）：取消 = 零回传零 fail；瞬时失败 = `release_job` 回池；
    **机器级停滞**（`UnreapableChildError`，2026-09-25）= 同一处置但**自己一行**（「机器级」
    那一档必须能从日志里看出来）；确定性拒绝 = `report_job_failure`（终局）；其它 = 只 log
    （幂等重拉）。
    """

    jid = job["job_id"]
    _wire_start(jid)  # 阶段占比（in/out/ppo/other）的 wall 从 claim 起算
    lease_token = str(job.get("lease_token", "") or "")
    claim_mode = str(job.get("status") or "ok")  # ok（独占）| backup（无租约副本）
    log(
        f"job {jid} claimed [mode={claim_mode}]"
        + ("" if lease_token else "（无租约：先回传者胜，后到者 409 丢弃）")
        + f" — downloading payload ({polls_since_accept} polls since last accepted result)"
    )
    # 心跳线程仅在有租约时启动（P3b 独占 hub 下发 lease_token；无租约
    # （旧 hub/§343 时代）则不续租，结果胜负由首写锁定决定）。
    # job 执行期间 60s 周期续租（长 job 靠它活过 CLAIM_TTL_SEC），job 结束 join。
    # ---- P2 预取填充（后台，与下面的 run_job 重叠）：PPO_A 跑着的时候下载 B ----
    # 命中即零下载开算（`preloaded` 接缝）；未命中就是「先串行下载关键 payload」，
    # 用命中率压掉空转。填充线程与 job 同生命周期（job 结束就停，绝不留常驻线程）。
    preloaded: dict | None = None
    _pf_stop = threading.Event()
    pf_thread: threading.Thread | None = None
    if pf_store is not None:
        got = pf_store.take(jid)
        want_sha = str((job.get("manifest") or {}).get("payload_sha256") or "")
        if got is not None and (not want_sha or str(got.get("blob_sha")) == want_sha):
            preloaded = {"payload_zip": got["payload_zip"]}
        elif got is not None:
            # 暂存副本与 claim 到的这份不是同一字节（hub 换过 job）：丢弃，走关键下载。
            log(f"job {jid}: 预取副本 sha 与 claim manifest 不符——丢弃走关键下载")
        pf_thread = threading.Thread(
            target=_prefetch_fill,
            args=(base_url, token, pf_store, _pf_stop),
            kwargs={
                "worker_id": worker_id,
                "role": role,
                "depth": prefetch_depth,
                "skip": {jid},
                "log": log,
            },
            daemon=True,
            name=f"pf-{jid[:8]}",
        )
        pf_thread.start()
    _hb_stop = threading.Event()
    hb_thread = None
    if lease_token:

        def _hb_loop() -> None:
            while not _hb_stop.wait(HEARTBEAT_SEC):
                heartbeat(base_url, token, jid, lease_token)

        hb_thread = threading.Thread(target=_hb_loop, daemon=True, name=f"hb-{jid[:8]}")
        hb_thread.start()
    job_ok = False
    stop = False  # CodeChangedError + 无监督器 ⇒ 整条循环退出（宿主据此 `return done`）
    uploaded = False  # P2.5：本 job 有没有走到「交回传」（没走到 = finally 里照旧 flush）
    # 取消环（2026-09-22）：唯一硬取消信号 = `landed`（结果已落盘）。backup 副本与
    # 掉队重领者都可能正在算一份**别人已经赢下**的 job——停算的收益是省一张卡的 GPU，
    # 代价是每 1.5s 一个 P0 小包。取消点在 epoch 边界（<20s），实测值进 cancel_latency_s。
    _cancel = threading.Event()
    _watch_stop = threading.Event()
    start_cancel_watcher(base_url, token, jid, _watch_stop, _cancel, log=log)
    _t_ppo0 = time.time()
    try:
        result = run_job_fn(
            base_url,
            token,
            job,
            work_dir=part_dir,
            device=device,
            torch_threads=torch_threads,
            echo=echo,
            code_cache_dir=code_cache_dir,
            lease_token=lease_token,
            preloaded=preloaded,  # P2 命中面：有它则 payload 段零网络
            log=log,
            should_cancel=_cancel.is_set,
            on_ppo_start=lambda: job_started(base_url, token, jid, worker_id=worker_id),
        )
        # 算完待回传（P0 小包）：只降别人的优先级（低档备份保险），**永不**触发取消。
        job_ready(base_url, token, jid, worker_id=worker_id)
        # ★ 关键路径到此为止（P2.5 异步回传）：回传不再占着算力等。交给上传线程，
        #   主循环立刻去领下一份——而下一份的字节多半已被预取到本地（P2），两者
        #   资源不相交（链路 vs CPU/GPU），天然可叠。
        #   用户口径（2026-09-22）：双课程交错已把 rollout/PPO 填满，所以「缩掉关键
        #   路径上的传输」是唯一的胜法；而 `out` 25s > `in` 15s，正是最大的一块。
        _t_ready = time.time()

        def _on_settled(_j: str, _out: Outcome, _t: float = _t_ready) -> None:
            """绑住本 job 的 `wall_end`（默认参数而非闭包变量——B023 的口径）。"""
            settle_result(_j, _out, _t, uploader=uploader, log=log)

        uploader.submit(
            UploadTask(
                jid=jid,
                base_url=base_url,
                token=token,
                result=result,
                lease_token=lease_token,
                claim_mode=claim_mode,
                on_settled=_on_settled,
            )
        )
        uploaded = True
        # 口径微调（P2.5）：async 下此刻还不知道 hub 收没收，所以这一格的含义从
        # 「距上次**被接受**」变成「距上次**产出结果**」（收没收看落定行/收尾行）。
        # 它是存活日志里的诊断读数（是不是在疯狂轮询却不产活），不是判据。
        job_ok = True
    except JobCancelledError as e:
        # ★ 唯一正确的取消处置（§2.4）：不写 _result.json、不 POST、不报 fail、
        # abandon（release 租约 + 零 reclaim），立刻去问 priority 选下家。
        # 绝不能落到下方 except ProtocolError（= 把合法放弃报成确定性失败 ⇒ 训练停腿）
        # 或 except RetryableError（= 把别人已赢下的活 release 回池）。
        log(
            f"job {jid} CANCELLED: {e} — 停算丢弃（零回传/零 fail），"
            f"cancel_latency_s={time.time() - _t_ppo0:.1f}"
        )
        abandon_job(base_url, token, jid, worker_id=worker_id, reason="landed")
    except UnreapableChildError as e:
        # ★ 机器级停滞（子进程 SIGKILL 之后收不了尸：D 状态 / 挂住的挂载点；2026-09-25 二次取证）。
        # **必须排在 `except RetryableError` 之前**——它是 `RetryableError` 的子类，落到那条上
        # 处置虽然也是 release，但日志会退化成「瞬时失败」，现场就看不出「机器级」这一档（那正是
        # 这次事故最难查的地方）。处置 = **立即还租约、立即重领重投**：不睡（云机按分钟计费，
        # 空转就是烧配额）、不报 `report_job_failure`（那会把机器的问题记在内容头上 ⇒ hub 落终局
        # failed ⇒ 训练停腿 ⇒ 反过来把云机停掉）、不计毒包（hub 只对**租约过期**计 reclaims，
        # 主动 release 不算 ⇒ 反复重投永远不会把自己冻死）。
        log(
            f"job {jid} 机器级停滞（子进程收不了尸）: {e} — release 租约立即重领"
            "重投同一份活（不睡/不报失败/不计毒包，云机不停）"
        )
        release_job(base_url, token, jid, lease_token, log=log)
    except RetryableError as e:
        # 瞬时失败（网络/5xx/传输损坏）：主动还租约立即回池——不再付 30min 过期等待
        log(f"job {jid} 瞬时失败: {e} — release 租约回池，立即可重领")
        release_job(base_url, token, jid, lease_token, log=log)
    except CodeChangedError as e:
        # 热替换：本进程 sys.modules 是旧代码，继续跑 = 用旧逻辑产出"看着正常"的
        # 结果。有监督器 → 以 HOT_RELOAD_EXIT 干净退出，由 supervise_worker 用同一
        # 套参数重新拉起子进程（fresh sys.modules → 新代码生效，输出流不断）。
        # 无监督器（裸 worker_loop 直调）→ 降级为提示人工重启。
        log(f"job {jid}: {e}")
        release_job(base_url, token, jid, lease_token, log=log)  # 别占着租约等重启
        if not _request_reload(restart_argv, log=log):
            log(
                "无监督器 —— 请手动重启本进程"
                "（notebook: Runtime → Restart runtime 后重跑步骤 4）"
            )
            stop = True
    except ProtocolError as e:
        log(f"job {jid} REJECTED: {e} — skip (not retried)")
        # 确定性拒绝（commit 不符/模式不符/节点能力缺失如 bun 装不上）不重试——
        # 轮询下一个。
        # 2026-09-17：**必须把原因报给 hub**，否则这条确定性失败在训练侧只表现为
        # 25 分钟超时（能力缺失被读成网络/排队问题，且每次重试白烧一个超时窗口）。
        # 只在这一分支报（CodeChangedError 会重启进程靠租约回池、RetryableError
        # 靠 release 回池，报了就等于把可恢复的 job 钉死）；hub 侧把它落成终局后
        # 该 job 不再回池，重发同 job（同幂等键）会清标记。
        report_job_failure(
            base_url,
            token,
            jid,
            str(e) or type(e).__name__,
            kind=type(e).__name__,
            detail=_failure_detail(e),
            lease_token=lease_token,
            log=log,
        )
    except Exception as e:
        log(f"job {jid} FAILED: {type(e).__name__}: {e} — will re-poll (idempotent)")
        # 瞬态失败（网络/远端关闭）：租约未续会自动回池，重拉同 job 幂等。
        # ★ 2026-09-21（§4）：本分支**只准**装真瞬态。内容决定性失败（解包/校验/
        #   运行时能力缺失）必须在上游就转成 `ProtocolError`（见 unpack_payload_or_fail、
        #   manifest 校验、bun 检测），否则同一份字节会无限重领（事故：40 次 / 3.5h 空转）。
    finally:
        # 每 job 一行传输账：payload/code/blob/result 的 (bytes, sec) + 零字节命中 + 重抽。
        # P2.5：async 且本 job **已交回传**时**不**在这里 flush —— `out` 的账要等回传
        # 落定才记完，过早 flush 会把回传读成 0s（`_result_settled` 负责收）。
        # 没走到回传（取消/失败/backup 丢弃）的话回传永不会落定，必须在这里收。
        if not uploaded:
            _wire_flush(jid, log)
        # 取消环必须每 job 都收（否则一个 job 一个常驻线程，长跑 worker 会漏线程）
        _watch_stop.set()
        _hb_stop.set()
        # 预取填充线程同理：job 结束即停（它最多再跑一轮下载，join 有超时兜底）。
        _pf_stop.set()
        if pf_thread is not None:
            pf_thread.join(timeout=30.0)
        if hb_thread is not None:
            hb_thread.join(timeout=HEARTBEAT_SEC + 5)
    return RoundOutcome(jid=jid, ok=job_ok, uploaded=uploaded, stop=stop)
