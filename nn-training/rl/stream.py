"""流式迭代：采集与 PPO 波次重叠（--stream 1）。"""
from __future__ import annotations

import collections
import os
import threading
import time

import numpy as np

import dist_common
import ppo as ppo_mod
from rl.log import log
from rl.queue import run_rollout_queue
from rl.resume import completed_pairs


def wave_params(cum_kl: float, kl_cap: float, wave_games: int, wave_cap: int,
                remaining: int | None = None) -> tuple[int, int]:
    """波次阈值/容量：软降档（R1）+ 残局上限。

    软降档：cum_kl 过 70% 上限后收缩 wave 规模——把熔断过冲从一整个 wave
    （24 局 ≈ +0.03~0.04 kl）压到个位数局。
    残局上限（2026-08-25）：断点续跑轮本轮最多只会到账 remaining 局，阈值超过它
    会让主循环静默空等到收官才在尾巴排水训练（it63：计划 105/已盘 103，只剩 2 局
    却要等满阈值 4）。取 min 后「来多少训多少」；cap 不低于阈值。"""
    if cum_kl > 0.7 * kl_cap:
        thr, cap = max(4, wave_games // 3), max(4, wave_cap // 3)
    else:
        thr, cap = wave_games, wave_cap
    if remaining is not None and remaining >= 0:
        thr = max(1, min(thr, remaining))
        cap = max(thr, min(cap, remaining))
    return thr, cap


def _shard_dir(entry: str) -> str | None:
    """本地局 _dir 指向 rollout 工作目录（w9/rl_s30_seed619823394/*.npy 多一层
    子目录），远程局 _dir 直接就是 shard 目录（obs.npy 平铺）。探测含 obs.npy 的一层。"""
    if os.path.exists(os.path.join(entry, "obs.npy")):
        return entry
    try:
        for sub in os.listdir(entry):
            cand = os.path.join(entry, sub)
            if os.path.isfile(os.path.join(cand, "obs.npy")):
                return cand
    except OSError:
        pass
    return None


def run_rollout_stream(bun: str, rl_path: str, traj_dir, pairs: list[tuple[int, int]],
                       args, cfg: dict, iter_id: str, model, opt, device,
                       on_collect_done=None,
                       backend=None, update_kwargs: dict | None = None) -> dict:
    """流式迭代（--stream 1）：采集与 PPO 重叠。

    backend（工程化共享）：per-tick RL 用 ppo（默认），意图 RL 用 ppo_intent——
    两者都实现 load_episode_from_shard / chunk_episodes / update / load_episodes /
    _ppo_load，本函数不复制第二份加载/更新逻辑。update_kwargs 透传给 backend.update
    （意图 RL：value_warmup_epochs / ref_model / kl_coef / seed）。

    正确性依据：整轮权重冻结为 W(N)（分发只发生在迭代边界），故任意时刻到达的
    语料都出自同一策略版本，on-policy 比率数学不受到达顺序影响；GAE 用采样时
    存储的 value 计算，与装载时机无关。

    机制：collector 线程跑 run_rollout_queue（本机槽压到 max(2, workers//4)
    给 torch 让核），每局结算回调注入待处理队列；主线程每当积压 ≥
    policy.streamWaveGames（默认 12）局就把这批 shard 装载（load_shard + GAE，
    wave 内 advantage 归一化）、chunkify 后按 --epochs 遍更新——每局总更新遍数
    与串行模式一致。轮内累计 KL 过 policy.streamKlCap（默认 0.12）的 70% 后
    软降档收缩 wave；触顶则停止训练、停派发后续采集任务（halt_event 贯穿队列，
    在途局自然收尾），已结算未训练的语料按 dropped 记账。

    干净评估时机（2026-08-25 用户指令修订）：中央派发队列清空——全部采集任务已
    派到节点/本地线程、结果仍在途——即刻派发评估，顺势填收尾空出的节点槽位；
    熔断与 collector 收官仅作兜底再触发点（护栏去重）。线程句柄经
    report["_eval_thread"] 回传主循环，下轮分发前 join。

    与串行模式的语义差异（已记录 nn.progress.md §6.7）：①adv 归一化从"全轮"
    变为"每 wave"；②早期 wave 的更新发生在 θ 漂移更早处（PPO clip 容忍范围）；
    ③PPO epoch checkpoint 流式期间不落盘（崩溃重启该轮重训，语料靠
    completed_pairs 秒回）；④断点续跑轮里此前已落盘的旧局不参与本轮更新。
    """
    pend: collections.deque = collections.deque()
    lock = threading.Lock()
    box: dict = {}
    backend = backend or ppo_mod
    update_kwargs = update_kwargs or {}
    halt_ev = threading.Event()  # 置位 → 队列停止派发新任务（R1 熔断止损）
    # R6 语义：首个 PPO 波次启动即置位 → 本机 dist 槽位让位训练（集群停摆豁免在
    # queue 侧）；PPO 全部收尾后本机转投 eval 尾段（local_gate，主循环置位）。
    ppo_started_ev = threading.Event()
    eval_fired = [False]         # 干净评估一次性护栏：队列清空主触发，熔断/收官兜底
    state = {"cum_kl": 0.0, "steps": 0, "chunks": 0, "waves": 0, "ppo_sec": 0.0,
             "load_sec": 0.0, "dropped": 0, "halted": False, "last_agg": None}

    def _on_result(summary):
        with lock:
            pend.append(dict(summary))

    # 本机直跑槽位：--local-slots 显式指定优先；0 = 自动 max(2, workers//4)
    # （给 torch 让核的历史折中）。这些槽与远端 agent 同队抢任务，保证训练机
    # 自身有保底采样份额——课程起步期每轮仅 12 局，不保底会被先孵化的远端
    # 线程瞬间抢空（2026-08-25 实测 local=0）。
    _ovr = int(getattr(args, "local_slots", 0) or 0)
    local_slots = _ovr if _ovr > 0 else max(2, int(args.workers) // 4)
    policy = cfg.get("policy", {})
    kl_cap = float(policy.get("streamKlCap", 0.12))
    wave_games = max(4, int(policy.get("streamWaveGames", 12)))
    # 残局感知：本轮最多会到账多少新结算（计划 − 已在盘）。断点续跑常剩个位数
    # 缺口（it63：103/105），波次阈值以此为上限，避免静默空等收官。
    try:
        wver_start = dist_common.weights_fingerprint(rl_path)
        remaining_games = max(0, len(pairs) - len(completed_pairs(traj_dir, wver_start)))
    except OSError:
        remaining_games = None

    def _fire_eval_once(tag: str) -> None:
        """干净评估一次性触发：派发队列清空为主触发点（节点进入收尾空转，
        评估局顺势填槽），熔断 / collector 收官仅作兜底再触发点。"""
        if eval_fired[0] or on_collect_done is None:
            return
        eval_fired[0] = True
        log(f"[stream] clean-eval dispatched ({tag}) — frozen weights on nodes, "
            f"running parallel to collection")
        try:
            box["eval_thread"] = on_collect_done()
        except Exception as cb_err:  # noqa: BLE001 — 评估旁路不拖垮采集
            log(f"[stream] on_collect_done error: {str(cb_err)[:120]}")

    def _collector():
        try:
            box["report"] = run_rollout_queue(
                bun, rl_path, traj_dir, pairs, args, cfg, iter_id,
                on_result=_on_result, local_slots_max=local_slots,
                tail_dispatch=False, halt_event=halt_ev,
                local_suspend=ppo_started_ev,
                on_queue_drained=lambda: _fire_eval_once("dispatch queue drained"))
            # 兜底：本地回退路径不会触发队列清空回调，收官时补触发（护栏幂等）。
            _fire_eval_once("collector done")
        except Exception as e:  # noqa: BLE001 — 主线程统一上报
            box["err"] = str(e)
        finally:
            box["t_end"] = time.time()  # rollout_sec 锚点：collector 真实退出时刻

    def _load_wave(summaries: list[dict]) -> list[dict]:
        t_load = time.time()
        eps = []
        for s in summaries:
            d = s.get("_dir")
            if not d:
                continue
            shard = _shard_dir(d)
            if shard is None:
                continue
            try:
                ep = backend.load_episode_from_shard(shard)
            except Exception as e:  # noqa: BLE001 — 单局坏 shard 跳过
                log(f"[stream] skip bad shard {shard}: {str(e)[:100]}")
                continue
            if ep is None:
                continue
            eps.append(ep)
        if eps:
            all_adv = np.concatenate([e["adv"] for e in eps])
            mean, std = all_adv.mean(), all_adv.std() + 1e-8
            for e in eps:
                e["adv"] = ((e["adv"] - mean) / std).astype(np.float32)
        state["load_sec"] += time.time() - t_load
        return eps

    def _drain(final: bool, cap: int | None = None) -> None:
        took: list[dict] = []
        with lock:
            # cap 限制单波规模：it15 教训——无上限 drain 曾一口吞 90 局，
            # 单波 376 步算了 20 分钟，流水线碎度全毁、KL 曲线也变粗。
            while pend and (cap is None or len(took) < cap):
                took.append(pend.popleft())
        if not took:
            return
        if state["halted"]:
            state["dropped"] += len(took)
            log(f"[stream] KL cap reached — dropped {len(took)} settled games from training")
            return
        eps = _load_wave(took)
        if not eps:
            return
        chs = backend.chunk_episodes(eps, args.mb)
        t_p = time.time()
        if not ppo_started_ev.is_set():
            ppo_started_ev.set()  # 首个梯度步 = 「PPO 启动」→ 本机 dist 槽位让位
            log("[stream] PPO phase started — local dist slots suspending "
                "(auto-resume if cluster stalls)")
        agg_w = backend.update(model, opt, chs, args.epochs, device, **update_kwargs)
        state["ppo_sec"] += time.time() - t_p
        state["cum_kl"] += float(agg_w["kl"])
        state["steps"] += len(chs) * args.epochs
        state["chunks"] += len(chs)
        state["waves"] += 1
        state["last_agg"] = agg_w
        log(f"[stream] wave: {len(took)} games -> {len(chs)} chunks x{args.epochs}ep "
            f"kl={agg_w['kl']:.4f} cum_kl={state['cum_kl']:.4f} "
            f"ent={agg_w['entropy']:.3f}")
        if state["cum_kl"] > kl_cap:
            state["halted"] = True
            halt_ev.set()  # R1：预算耗尽，队列停止派发新任务（在途局自然收尾）
            _fire_eval_once("kl-cap halt")
            log(f"[stream] cumulative KL {state['cum_kl']:.4f} > cap {kl_cap} — "
                f"collect-only for the rest of this round; task dispatch stopped")

    th = threading.Thread(target=_collector, daemon=True)
    t0 = time.time()
    log(f"[stream] collector started (local_slots={local_slots}, "
        f"wave={wave_games} games, kl_cap={kl_cap})")
    wave_cap = max(wave_games * 2, 24)
    th.start()
    while True:
        with lock:
            n_pending = len(pend)
        w_thr, w_cap = wave_params(state["cum_kl"], kl_cap, wave_games, wave_cap,
                                   remaining=remaining_games)
        if n_pending >= w_thr:
            _drain(False, cap=w_cap)
            continue
        if not th.is_alive():
            break
        th.join(timeout=3.0)
    # rollout_sec 锚点改为 collector 线程真实退出时刻（box["t_end"]）：此前在
    # 主线程测量，最后一个 in-flight wave 的更新时长会被误计入采集窗口。
    collect_done = box.get("t_end") or time.time()
    th.join(timeout=5.0)
    with lock:
        n_left = len(pend)
    log(f"[stream] collector done in {collect_done - t0:.0f}s — "
        f"draining {n_left} settled-but-untrained games")
    while True:
        with lock:
            n_pending = len(pend)
        if n_pending == 0:
            break
        _, tw_cap = wave_params(state["cum_kl"], kl_cap, wave_games, wave_cap,
                                remaining=remaining_games)
        _drain(True, cap=tw_cap)
    if "err" in box:
        raise RuntimeError(f"stream collector failed: {box['err']}")
    report = box.get("report")
    if report is None:
        raise RuntimeError("stream collector produced no report")
    # 断点续跑轮可能零结算（全部秒回）
    if state["chunks"] == 0 and int(getattr(args, "epochs", 0)) > 0:
        eps_done = backend._ppo_load(str(traj_dir / "ppo_ckpt"), model, opt)
        if eps_done >= args.epochs:
            # 该轮 PPO 已在先前进程中完整跑完：权重以当前状态收尾即可，
            # 重复调用 ppo_update 会走"剩余 0 epoch"路径（空聚合）。
            log(f"[stream] no fresh settles + PPO checkpoint already complete "
                f"({eps_done}/{args.epochs} epochs) — weights final, skipping update")
        else:
            log("[stream] no fresh settles this round — falling back to full-disk update")
            episodes = backend.load_episodes(str(traj_dir))
            chunks = backend.chunk_episodes(episodes, args.mb)
            t_p = time.time()
            state["last_agg"] = backend.update(model, opt, chunks, args.epochs, device,
                                               ckpt_path=str(traj_dir / "ppo_ckpt"),
                                               **update_kwargs)
            state["ppo_sec"] += time.time() - t_p
            state["steps"] = sum(e["obs"].shape[0] for e in episodes)
            state["chunks"] = len(chunks)
    rollout_sec = round(collect_done - t0, 1)  # 采集窗口（collector 真实退出锚点）
    tail_sec = round(time.time() - collect_done, 1)
    log(f"[stream] done: games={report['games']} kl_cum={state['cum_kl']:.4f} "
        f"steps={state['steps']} chunks={state['chunks']} waves={state['waves']} "
        f"halted={state['halted']} dropped={state['dropped']} "
        f"collect_wall={rollout_sec}s tail_update={tail_sec}s "
        f"ppo_cpu={state['ppo_sec']:.0f}s load_cpu={state['load_sec']:.0f}s")
    # agg=None 表示本轮没有发生任何梯度步（checkpoint 已在先前进程完整跑完）。
    # 指标不伪造为 0——jsonl 写 null，报告显示 '—'，健康判定自动忽略该轮。
    last = state["last_agg"]
    # pure_collect_sec 由 run_rollout_queue 实测（末局结算 − 权重分发完毕），
    # 经 box["report"] 顶层透传——此处不做任何公式派生。
    report["_stream"] = {"rollout_sec": rollout_sec, "tail_drain_sec": tail_sec,
                         "ppo_sec": round(state["ppo_sec"], 1),
                         "load_sec": round(state["load_sec"], 1),
                         "steps": state["steps"], "chunks": state["chunks"],
                         "waves": state["waves"], "kl_cum": round(state["cum_kl"], 4),
                         "halted": state["halted"], "dropped_games": state["dropped"],
                         "agg": last}
    # 评估线程句柄随报告回传主循环（R4）：下轮权重分发前必须 join——否则提前触发
    # 或长尾的评估会被下一轮异 sha 权重的原子清场杀掉（eval_log 记 dropped）。
    if box.get("eval_thread") is not None:
        report["_eval_thread"] = box["eval_thread"]
    return report
