"""remote/worker_session.py — worker 轮询壳的**会话级外件**（装配 / 重试 / 收线）。

从 `remote/worker.py` 拆出（2026-11-18，长函数存量第三十三/三十四刀，LOC 预算 1000）：
`worker_loop` 的 while 体是轮询语义本身，必须留在宿主；而它周围的四段——多 hub/缓存根
装配、acquire 退避、让路自适应复查、退出收线——只服务**一次会话**、不含轮询判据，
搬到这里。

不搬的三件（**接缝**在宿主命名空间，搬走会让既有 patch 静默失效）：
`_setup_bulk_yield`（宿主持有还原账）、`_ensure_pf_store`（`W.make_prefetcher` 是 patch 点）、
`_poll_halt`（`W._release_cloud_machine` 是 patch 点）。
"""

from __future__ import annotations

from pathlib import Path

from remote.bulk_sched import yield_worth_it
from remote.cloud_pool import CLOUD_ROLLOUT_POOL
from remote.lifecycle import Lifecycle
from remote.payload_proc import PayloadProcess
from remote.prefetch import PrefetchStore
from remote.result_upload import UploaderLike
from remote.wire import _BULK


def hub_plan(
    hub_urls: list[str] | None, base_url: str, work_dir: Path, cache_dir: Path | None
) -> tuple[list[str], bool, Path, Path | None]:
    """多 hub 列表 / 是否多 hub / 缓存根 / code_cache 实参（单 hub 且无 cache_dir 保持 None）。"""
    # 多 hub 轮询（P3b §3.8）：None/空 → 单 hub（默认行为零变化）；多 hub round-robin。
    hubs = [u for u in (hub_urls or [base_url]) if u]
    if not hubs:
        hubs = [base_url]
    multi = len(hubs) > 1
    # A3 持久缓存根（plan/aistudio-transfer-hardening §3.3）：`cache_dir` 给了就把**内容寻址
    # 缓存**（code / ts_code / blob）挂到它下面（跨会话复用），**job 工作目录仍走 work_dir**
    # （`/tmp`，每轮清场重建的语义不变）。缺省 None ⇒ 逐字旧行为（`work_dir/code_cache`）。
    cache_root = Path(cache_dir) if cache_dir is not None else work_dir
    shared_code_cache = cache_root / "code_cache"
    if cache_dir is not None:
        cache_root.mkdir(parents=True, exist_ok=True)
    # 单 hub 且无 cache_dir 时**故意仍传 None**（保持改造前的调用形状）；否则显式给根。
    code_cache_arg = shared_code_cache if (multi or cache_dir is not None) else None
    return hubs, multi, cache_root, code_cache_arg


def acquire_retry(fails: int, e: Exception, poll_sec: float, log) -> tuple[int, float]:
    """iii-2：首败立即重试（12s 超时已经等过），连败才退避 poll_sec。"""
    fails += 1
    wait_s = 0.0 if fails < 2 else poll_sec
    tail = "立即重试" if wait_s <= 0.0 else f"retry in {wait_s}s"
    log(f"acquire failed: {e} — {tail}（连续 {fails} 次）")
    return fails, wait_s


def yield_self_check(yield_live: bool, log) -> bool:
    """A2：每轮收尾复查控制面分位；不值得让路就一次性关掉（会话内不回升）。"""
    if not yield_live:
        return False
    _sd = _BULK.stats()
    # ★ 直接**下标**取（不是 `.get(..., 0.0)`）：键名被改时这里当场 KeyError，
    #   而不是静默拿到 0.0 ⇒「分位为 0 = 没数据」⇒ 自适应永不触发、无人察觉。
    #   （两个键都恒在，见 `BulkScheduler.stats()`；对应用例
    #   `test_the_check_reads_the_quantiles_stats_exposes`。）
    _p50 = float(_sd["p0_rt_ms_p50"])
    _p95 = float(_sd["p0_rt_ms_p95"])
    if yield_worth_it(p50_ms=_p50, p95_ms=_p95, n=int(_sd["p0_count"])):
        return True
    _BULK.configure_yield(after_sec=float("inf"), total_budget_sec=0)
    log(
        f"让路自适应关闭：p0_p95={_p95 / 1000:.1f}s ≫ p50={_p50 / 1000:.1f}s"
        f"（n={int(_sd['p0_count'])}，控制面尾部不是被 bulk 挤的）"
        "——让路只拖长传输；回退档 --bulk-yield always"
    )
    return False


def close_worker(
    prev_yield: tuple[float, float | None],
    prev_p1_observer: object,
    pf_stores: dict[int, PrefetchStore | PayloadProcess],
    uploader: UploaderLike,
    life: Lifecycle,
    done: int,
    log,
) -> None:
    """收线：还原让路策略/观察者 → 收预取腿 → 回传 drain → 池收线 → 生命周期收线上报。"""
    # 让路策略还原（W1/§3.5 ★）：库层缺省必须回到老语义，否则同进程后续使用者
    # （测试 / 多会话）继承 auto——`test_control_plane_bypass.py` 那类「要求无条件让路」
    # 的既有用例会确定性变红。
    _BULK.configure_yield(after_sec=prev_yield[0], total_budget_sec=prev_yield[1])
    _BULK.set_p1_observer(prev_p1_observer)
    # 下行腿收尾（2026-10-20）：进程模式要收掉填充线程与子进程（否则一个会话漏一个
    # `python -m remote.payload_worker`）。它**不丢任何东西**——暂存区是软持有（无租约），
    # 没被 claim 的候选本来就要蒸发。
    for _pf in list(pf_stores.values()):
        _closer = getattr(_pf, "close", None)
        if _closer is not None:
            _closer()
    # P2.5（2026-09-22）：**每条**退出路径都要过这里——`break`（空闲/停机）、
    # `--once` 的 return、热替换的 SystemExit(86)、以及任何异常。队列里可能还有
    # 没送出去的结果；不等它落定就退出 = 静默丢掉最贵的产物（训练侧要等租约
    # 过期才看得出来，正是「3.5 小时静默」那一类事故）。
    _drained = uploader.close()
    _st = uploader.stats()
    log(
        f"回传收尾: mode={_st['mode']} 提交={_st['submitted']} "
        f"成功={_st['ok']} 失败={_st['failed']} 未落定={_st['pending']} "
        f"同步退化={_st['sync_fallback']}"
    )
    if not _drained or _st['failed']:
        log(
            "★ 回传收尾有未落定/失败的结果——训练侧会在租约过期后才发现，"
            "请按上面的 jid 排查（hub 日志 / 网络）"
        )
    # 收线上报放在**回传收尾之后**：最后一个读点因此包含「结果到底交没交出去」之后的状态。
    CLOUD_ROLLOUT_POOL.close_all()
    life.close(done)
