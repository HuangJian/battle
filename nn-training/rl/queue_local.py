"""queue_local —— 纯本地 rollout（单机线程池路径）。

从 rl/queue.py 拆出（2026-09-02）：run_rollout 只依赖本机并发（ThreadPoolExecutor
spawn bun 子进程），无分布式状态——独立成模块便于单测与职责分离。
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import dist_common
from platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from rl.cmd import build_rollout_cmd
from rl.log import log
from rl.reports import combine_reports

REPO_ROOT = Path(__file__).resolve().parents[2]  # 仓库根 battle2（rl/ 上溯 3 层，修正 2026-09-02）

ROLLOUT_LOG_EVERY = 10  # 本地 rollout 每 N 局结算打一条进度行


def run_rollout(bun: str, rl_path: str, traj_dir: Path, pairs: list[tuple[int, int]], args) -> dict:
    """Run one rollout generation into traj_dir with up to W concurrent bun
    processes over the given (stage, seed) game pairs.

    Each game is one single-threaded bun process writing shards under
    traj_dir/w{i}/ — disjoint by construction, and discover_rl_shards() scans
    recursively, so the PPO side needs no knowledge of the layout. Per-game
    granularity saturates all cores regardless of how few stages/seeds the
    sweep has (bun startup ~300ms is noise vs a 12000-tick game).
    Returns the aggregated report dict.
    """

    # 吞吐 T4：双缓冲复用前提——本地 shard 也必须写 wver（与 run_rollout_queue 的
    # local slot 对齐），否则主进程下一轮 completed_pairs 不命中、预采产物作废。
    wver = dist_common.weights_fingerprint(rl_path)
    workers = max(1, min(args.workers, len(pairs)))

    def run_one(idx: int, si: int, seed: int) -> tuple[int, dict | None]:
        wdir = traj_dir / f"w{idx}"
        wdir.mkdir(parents=True, exist_ok=True)
        log_f = open(wdir / "rollout.log", "w", encoding="utf-8")
        cmd = build_rollout_cmd(
            bun,
            args,
            weights=rl_path,
            out_dir=str(wdir),
            stage=si,
            seed=seed,
            wver=wver,
            node_label="local",
        )
        p = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT, **_POPEN_NO_WINDOW
        )
        rc = p.wait()
        log_f.close()
        report = None
        if rc == 0:
            report = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
        return rc, report

    t0 = time.time()
    results: list[tuple[int, dict | None]] = [(1, None)] * len(pairs)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_one, i, si, sd): i for i, (si, sd) in enumerate(pairs)}
        for done_n, fut in enumerate(as_completed(futures), 1):
            results[futures[fut]] = fut.result()
            if done_n % ROLLOUT_LOG_EVERY == 0 or done_n == len(pairs):
                log(
                    f"[rollout] local {done_n}/{len(pairs)} games settled ({time.time() - t0:.0f}s)"
                )

    failed = [i for i, (rc, _r) in enumerate(results) if rc != 0]
    if failed:
        tail = (traj_dir / f"w{failed[0]}" / "rollout.log").read_text(encoding="utf-8")[-2000:]
        raise SystemExit(f"[run_rl] rollout worker(s) {failed} failed:\n{tail}")

    reports = [r for _rc, r in results if r is not None]
    return combine_reports(reports)


def run_local_rollout(
    bun: str, rl_path: str, traj_dir: Path, idx: int, task: tuple[int, int], args, wver: str
) -> dict:
    si, sd = task
    wdir = traj_dir / f"w{idx}"
    wdir.mkdir(parents=True, exist_ok=True)
    cmd = build_rollout_cmd(
        bun,
        args,
        weights=rl_path,
        out_dir=str(wdir),
        stage=si,
        seed=sd,
        wver=wver,
        node_label="local",
    )
    with open(wdir / "rollout.log", "w", encoding="utf-8") as log_f:
        # 整局墙钟计时，与远端 agent 写入 manifest 的 elapsedSec 同口径——
        # 此前 local 局无耗时数据，巡检「采样机健康」的局均耗时列对 local 恒为 '—'。
        t0 = time.time()
        p = subprocess.Popen(
            cmd, cwd=str(REPO_ROOT), stdout=log_f, stderr=subprocess.STDOUT, **_POPEN_NO_WINDOW
        )
        rc = p.wait()
        elapsed_sec = round(time.time() - t0, 3)
    if rc != 0:
        raise RuntimeError(f"local rollout rc={rc} (see {wdir}/rollout.log)")
    report: dict[str, Any] = json.loads((wdir / "_rl_report.json").read_text(encoding="utf-8"))
    if report.get("wver") != wver:
        raise RuntimeError("local report wver mismatch")
    report["elapsedSec"] = elapsed_sec
    report["_dir"] = str(wdir)
    return report


def rescan_nodes(
    cfg,
    code_hash,
    upgrade_branch,
    dirty_files,
    local_bun,
    spawned_ids,
    alive,
    lock,
    weights_bytes,
    iter_id,
    wver,
    task_timeout,
    status_timeout,
    all_settled,
    deadline,
    rescan_sec,
    worker,
    extra_threads,
    halt_event=None,
    *,
    first_probe_sec: float = 5.0,
    wkind: str = "rollout",
    streaks=None,
    soft_streaks=None,
    fail_streak_max: int = 3,
    soft_streak_max: int = 9,
    rearm_cap: int = 3,
    rearm_warned=None,
) -> None:
    """v3.9 动态节点发现线程主体（dispatch.run() spawn，19 参逐位对应）。

    v3.14b：追加 halt_event —— KL 熔断后 rescan 线程必须立即退出，否则主线程
    join(timeout=max(30, window+taskTimeout)) 会白等满超时（实测 180s/次，
    集成 I2/I4 因此假死）。0 参代价：旧调用方不传 = 永不因 halt 退出（兼容）。

    2026-09-02 OO 拆分回归修复：旧签名带 log/dist_common/node_ping/
    request_upgrade_guarded/is_self_node/mm 六个参数——log/dist_common 本模块
    已顶层导入（拆出后不再需要注入），node_ping/request_upgrade_guarded/
    is_self_node 经 dist_common.xxx 调用（裸参数从未使用），mm 就地内联。
    修复前 dispatch 的 18 个实参会整体错位 → 线程启动即抛
    `missing 6 required positional arguments`（rollout-rescan 线程报废，
    运行中上线的节点永远不被发现）。

    A4（2026-09-19 审计实测）：旧实现 `all_settled.wait(rescan_sec)` 之后紧跟
    「已结算即 return」——而 volume 短波形态下 **234 轮全部 <120s**（p50 7s，max 115s），
    于是本线程每轮都在首个 sleep 里被结算事件唤醒并退出：2.5h 里 **0 次扫描 pass**、
    130+ 次节点排除无一次回场。现在：
      · 首个 pass 提前到 `first_probe_sec`（缺省 5s），之后每 `rescan_sec`；
      · 候选 = 尚未孵化 ∪ **已停派**（真故障熔断 / 连续瞬断软停）的节点 —— 后者原先
        因 `spawned_ids` 命中被永久跳过，熔断即整轮出局；
      · 回场强制重握手（清缓存 + 重发权重，post_weights 自带 cached 探针）并**重置
        失败计数**、补孵 c_n 个采样线程；每节点有 `rearm_cap` 上界（缺省 3），
        用尽后不再补孵并告警一次；
      · ping 并行（与 B/C 层同款）；补 `kind=wkind` —— 旧实现漏传 kind，goal/intent
        腿的中途上线节点会把权重发进 rollout 桶，任务全 409；
      · `nd` 带 `ping`（旧实现漏带 ⇒ stageJson 任务在回场节点上被能力握手拒掉）。
    """
    configured = [n for n in cfg.get("nodes", []) if n.get("enabled", True)]
    streaks = {} if streaks is None else streaks
    soft_streaks = {} if soft_streaks is None else soft_streaks
    rearm_warned = set() if rearm_warned is None else rearm_warned
    rearm_counts: dict[str, int] = {}
    # 首个 pass 不等满 rescan_sec（短波形态下那样等于永不扫描，见 docstring）。
    next_probe_at = time.time() + max(0.0, first_probe_sec)
    while (
        not all_settled.is_set()
        and not (halt_event is not None and halt_event.is_set())
        and time.time() < deadline
    ):
        # 用 all_settled.wait 替代 time.sleep：所有游戏已结算时立即唤醒（不再等满
        # rescan_sec，round done 不滞后——实测 2026-09-05）。
        # 防忙等的地板：50ms（不写死 0.5s——那会让 `recoverPingSec` 这类旋钮在
        # 小值时不生效，配置与实际行为说谎；生产缺省 5s/20s，地板不参与）。
        wait_sec = min(
            max(0.05, next_probe_at - time.time()),
            max(0.05, deadline - time.time()),
        )
        all_settled.wait(wait_sec)
        if (
            all_settled.is_set()
            or (halt_event is not None and halt_event.is_set())
            or time.time() >= deadline
        ):
            return
        # 下个 pass 的间隔：地板必须与上面 wait 的地板同源（0.05s）——写死 1.0s 会让
        # `recoverPingSec` 这类旋钮在小值时不生效（配置与实际行为说谎，与上面注释同一
        # 口径）。2026-09-20 实测代价：tests/test_rollout_dispatch_resilience.py 的回场
        # 用例配 0.05s 却按 1.0s 走 ⇒ 4 轮回场要 >3s 纯等待（pytest-timeout 线程栈可见）。
        # 生产缺省 20s/5s 远大于地板，不受影响；防忙等的下界由循环里的 0.05s wait 担任。
        next_probe_at = time.time() + max(0.05, rescan_sec)

        # 候选 = 尚未孵化 ∪ 已停派（真故障熔断 / 连续瞬断软停）。
        with lock:
            cands: list[tuple[dict, str, str]] = []
            for n in configured:
                nid = str(n.get("id") or n.get("url") or "?")
                if nid not in spawned_ids:
                    cands.append((n, nid, "new"))
                elif (
                    streaks.get(nid, 0) >= fail_streak_max
                    or soft_streaks.get(nid, 0) >= soft_streak_max
                ):
                    cands.append((n, nid, "rearm"))
        if not cands:
            continue
        # 并行 ping（串行 3s/台 会让一个 pass 卡十几秒，与 B/C 层同款修法）。
        pings = dist_common.ping_nodes_parallel(
            [n for n, _nid, _why in cands], timeout=status_timeout
        )
        for (n, nid, why), ping in zip(cands, pings, strict=True):
            if ping is None:
                continue  # 仍未上线，下个 pass 再试
            # 回场上界：每节点每轮最多补孵 rearm_cap 次（防「永远失败的节点」无限起线程）。
            if why == "rearm":
                with lock:
                    capped = rearm_counts.get(nid, 0) >= rearm_cap
                    first_warn = capped and nid not in rearm_warned
                    if first_warn:
                        rearm_warned.add(nid)
                if capped:
                    if first_warn:
                        log(
                            f"[dist] rescan {nid}: 本轮回场上界 {rearm_cap} 次已用尽 — "
                            f"不再补孵（节点持续失败，见日志根因）"
                        )
                    continue
            if ping.get("codeHash") != code_hash:
                # guarded 重启（跨代去重 + 脏树拒发，同 ping 门）；dedup 静默跳过
                # （rescan 周期 ~15s，重复刷屏无信息量）。F2：日志带两侧 hash——
                # 与 ping 门同口径，运维一眼看出差异在哪一侧。
                dist_common.forget_weights_node(nid)
                local_short = code_hash[:8]
                remote_short = str(ping.get("codeHash") or "")[:8] or "none"
                if not upgrade_branch:
                    continue
                ok, reason = dist_common.request_upgrade_guarded(
                    nid,
                    n["url"],
                    n.get("authKey", ""),
                    upgrade_branch,
                    str(ping.get("codeHash")),
                    dirty=dirty_files,
                    expected_hash=code_hash,
                )
                if reason == "restart-requested":
                    log(
                        f"[dist] rescan {nid}: codeHash stale "
                        f"(local={local_short} remote={remote_short}) — requested "
                        f"upgrade to {upgrade_branch} (accepted)"
                    )
                elif reason.startswith("dirty-tree"):
                    log(
                        f"[dist] rescan {nid}: codeHash stale "
                        f"(local={local_short} remote={remote_short}) — remote restart "
                        f"suppressed ({reason}: uncommitted training-tree changes)"
                    )
                elif dist_common.is_self_node(n["url"], nid):
                    log(
                        f"[dist] rescan {nid}: self node stale "
                        f"(local={local_short} remote={remote_short}) — restart-only "
                        f"({reason})"
                    )
                elif reason != "dedup":
                    log(
                        f"[dist] rescan {nid}: codeHash stale "
                        f"(local={local_short} remote={remote_short}) — upgrade "
                        f"request failed ({reason})"
                    )
                continue
            remote_full = str(ping.get("bunVersion", "?"))
            # mm 就地内联（param 已删）：major.minor 一致性红线（确定性，M4）
            if ".".join(remote_full.split(".")[:2]) != ".".join(str(local_bun).split(".")[:2]):
                continue
            c_n = max(1, int(n.get("concurrency") or ping.get("cpus") or 1))
            # 权重：新上线节点走进程内复用缓存；**回场节点强制重握手**（它可能刚重启，
            # 桶里那份也可能已被别的客户端挤掉；post_weights 自带 cached 探针，命中即
            # 'kept'）。kind 必须与 rollout 腿一致——旧实现漏传，goal/intent 腿会把权重
            # 发进 rollout 桶，任务全 409（与 A1 同一类陷阱）。
            if why == "new" and dist_common.weights_already_pushed(
                wver, nid, kind=wkind
            ):
                mode = "kept(cache)"
            else:
                if why == "rearm":
                    dist_common.forget_weights_node(nid)
                try:
                    mode = dist_common.post_weights(
                        n["url"],
                        n.get("authKey", ""),
                        iter_id,
                        wver,
                        weights_bytes,
                        timeout=min(300.0, max(60.0, task_timeout)),
                        kind=wkind,
                    )
                except dist_common.DistError as e:
                    log(f"[dist] rescan {nid}: weights POST failed ({e}) — skip this round")
                    continue
            # nd 带 ping：漏带会让 stageJson 任务在回场/中途上线节点上被能力握手拒掉
            # （旧实现 bug，自定义关课程下等于把这些节点白孵）。
            nd = {
                "id": nid,
                "url": n["url"],
                "key": n.get("authKey", ""),
                "c": c_n,
                "ping": ping,
            }
            with lock:
                spawned_ids.add(nid)
                if why == "new":
                    alive.append(nd)
                # 回场：重置失败计数（否则 worker 在循环顶部立刻又 return）。
                streaks[nid] = 0
                soft_streaks[nid] = 0
                if why == "rearm":
                    rearm_counts[nid] = rearm_counts.get(nid, 0) + 1
            if why == "new":
                log(
                    f"[dist] rescan: node {nid} online mid-run — weights {mode}, "
                    f"spawning {c_n} workers"
                )
            else:
                log(
                    f"[dist] rescan: node {nid} 回场（此前停派）— weights {mode}，"
                    f"重置失败计数并补孵 {c_n} 个采样线程"
                    f"（第 {rearm_counts[nid]}/{rearm_cap} 次）"
                )
            for _ in range(c_n):
                t = threading.Thread(target=worker, args=(nd,), daemon=True)
                t.start()
                extra_threads.append(t)


def register_inflight(inflight: dict[tuple[int, int], int], task: tuple[int, int]) -> None:
    """主副本派发登记（纯函数）：**所有**主副本派发一律入表，即竞速候选。

    早派任务必须可见（it6：a97 积压 3 局、空闲槽无从竞速）。副本数天然上界 =
    节点数（pick_race_target 同节点排除），无 tailFanoutDup。重试 requeue 不出表、
    再派发再登记（计数累加），结算/终局失败路径负责扣减。"""
    inflight[task] = inflight.get(task, 0) + 1


def pick_tail_race(inflight: dict[tuple[int, int], int], dup: int) -> tuple[int, int] | None:
    """v3.10 长尾竞速选择（保留给历史单测；生产路径用 pick_race_target）。

    副本数 < dup 时选字典序最小的 in-flight 任务。"""
    cand: tuple[int, int] | None = None
    for t, c in inflight.items():
        if c < dup and (cand is None or t < cand):
            cand = t
    return cand


def pop_inflight(
    inflight: dict[tuple[int, int], int],
    inflight_nodes: dict[tuple[int, int], set[str]],
    task: tuple[int, int],
    nd_id: str,
) -> None:
    """一个副本出表（结算/失败/丢弃）。计数归零时连同节点集一起清。

    A/B/C 三层共用一处（原先只是 `eval_dispatch` 里的局部闭包，现提到本模块）。
    """
    if task in inflight:
        inflight[task] -= 1
        if inflight[task] <= 0:
            inflight.pop(task, None)
            inflight_nodes.pop(task, None)
        else:
            inflight_nodes.get(task, set()).discard(nd_id)


def clear_inflight(
    inflight: dict[tuple[int, int], int],
    inflight_nodes: dict[tuple[int, int], set[str]],
    task: tuple[int, int],
) -> None:
    """任务已结算（胜者拿到）⇒ 整个出表；在飞的竞速副本回来时按 dup 丢弃。"""
    inflight.pop(task, None)
    inflight_nodes.pop(task, None)


def pick_race_target(
    inflight: dict[tuple[int, int], int],
    nd_id: str,
    inflight_nodes: dict[tuple[int, int], set[str]],
    timeout_blocks: dict[tuple[int, int], set[str]],
) -> tuple[int, int] | None:
    """in-flight race 选靶（纯函数，单测覆盖）：空闲槽复制哪个已在跑的任务。

    规则（2026-09-16 用户裁定：不判节点快慢、无 dup 上限）：
    - nd_id 已持有该任务 → 不派回（每节点每任务最多 1 份）
    - nd_id 在 timeout_blocks[task] → 冷却期内不重抢
    - 返回字典序最小的可竞速任务；无则 None
    副本数上界 = 节点数（同节点排除），先返回者结算、败者丢弃。"""
    for t in sorted(inflight):
        current = inflight_nodes.get(t, set())
        blocked = timeout_blocks.get(t, set())
        if nd_id not in current and nd_id not in blocked:
            return t
    return None


def race_tier_ok(speeds: dict[str, float], nid: str, top_n: int = 3) -> bool:
    """v3.11 竞速派档（纯函数，单测覆盖）：竞速副本只派给**快节点**，避免副本恰好落入
    慢节点、竞速形同虚设（用户 2026-08-31 观察："两个副本都分派到慢速节点，不还是要等"）。

    - 本机（local）：豁免（实测最快、无网络往返），永久参与竞速。
    - 无速度样本（首轮/全空）：乐观放行——没数据时不该设门槛。
    - 其余按 EWMA 耗时（speed 表，即各节点最近任务平均耗时）排序，取 top_n 快档；
      不在快档的节点不参与竞速（慢节点对竞速是负资产）。
    - 节点数 ≤ top_n：全员参与（退化回无门槛，正确）。"""
    if nid == "local":
        return True
    if not speeds:
        return True
    ranked = sorted((v, k) for k, v in speeds.items() if k != "local")
    if not ranked:
        return True
    tier = {k for _v, k in ranked[:top_n]}
    return nid in tier
