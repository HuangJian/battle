"""remote/offline_greedy.py —— 云机轮内的 greedy 采集接线（rollout 之后、PPO 之前）。

为什么要单独一个模块（而不是留在 `remote/worker.py`）：

* `remote/worker.py` 已在 LOC 预算（<1000）的刀口上，作业壳不该再长；
* 这段接线只做两件事——**跑一波**（`greedy_stage`）与**并账**（`merge`）——与作业壳的网络/校验/
  分叉无关，独立后可以单独用假 runner 测（`tests/remote/test_greedy_offline_wiring.py`）。

落点与口径（详见 `plan/greedy-offline-autonomous.plan.md` 与 `docs/nn/remote-transport.md §147`）：

* 云机普通轮走**流式采集**（`worker/stream_collect` 把 argv 当每关模板），往它那份 spec 追加行会被
  静默忽略 ⇒ greedy 必须自己的 spec、自己的产出根（`<job>/greedy/wave{k}/`）；
* `run_iter_rollout` 轮末按「实产 == 声明」对账，一波一个目录才不会互相污染；
* 换根档下产出住轮 scratch，`scratch.batch_rel` 按相对位置镜像 ⇒ 仍落在 PPO 的单一装载根里；
  万一某一波回落成「原地」档，`_under_ppo_root` 把落单的 shard 移进装载根（否则「采了没训」）。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from common.protocol import ProtocolError, parse_shard_name
from worker.greedy_round import GREEDY_DIR_NAME, greedy_spec, run_greedy_round
from worker.iter_rollout import run_iter_rollout
from worker.reports import combine_reports


def collect_round(
    job_dir: str | Path,
    manifest: dict,
    *,
    ts_dir: Any,
    log: Callable[..., None],
    ctx: Any,
    it: int,
    collector: Any = None,
) -> dict:
    """跑本轮 rollout（普通波 + 可选 greedy 波），返回 `iter_info`（已并账）。"""
    info = run_iter_rollout(
        job_dir,
        manifest["rollout"],
        ts_dir=ts_dir,
        log=log,
        ctx=ctx,
        it=it,
        next_game=None if collector is None else collector.next_game,
        on_game_settled=None if collector is None else collector.observe,
        # 收官读数并进轮末那一行（2026-10-29 日志瘦身：原来它自己一行）。
        volume_line=None if collector is None else collector.summary_line,
    )
    out = greedy_stage(
        manifest=manifest,
        iter_info=info,
        shard_dirs=list(info.get("shard_dirs") or []),
        job_dir=Path(job_dir),
        rctx=ctx,
        ts_dir=ts_dir,
        log=log,
    )
    if out is not None:
        merge(info, out, log=log)
    return info


def greedy_stage(
    *,
    manifest: dict,
    iter_info: dict,
    shard_dirs: list[str],
    job_dir: Path,
    rctx: Any,
    ts_dir: Any,
    log: Callable[..., None],
) -> dict | None:
    """跑 greedy 采集波（rollout 之后、PPO 之前）；未声明配额 ⇒ `None`（旧包逐字节不变）。

    产出根 = `<job_dir>/greedy/wave{k}/`：进 `rctx` 后 `scratch.batch_rel` 按**相对位置**镜像进
    轮 scratch（批必须嵌在轮里，否则 PPO 的单一装载根扫不到）——回退档则原地。
    """
    rollout = manifest.get("rollout") or {}
    raw = rollout.get("greedy_quota")
    if raw is None:
        return None
    it = int(manifest.get("it", 0) or 0)
    volume: dict[str, Any] = {}
    if isinstance(rollout.get("volume"), dict):
        volume = dict(rollout["volume"])
    pairs_seen: list[tuple[int, int]] = []
    for d in shard_dirs:
        parsed = parse_shard_name(Path(d).name)
        if parsed is not None:
            pairs_seen.append(parsed)
    stages = volume.get("stages") if isinstance(volume.get("stages"), (list, tuple)) else []
    if not stages:
        stages = sorted({s for s, _sd in pairs_seen})
    est_raw: dict[str, Any] = {}
    if isinstance(volume.get("est_s_by_stage"), dict):
        est_raw = dict(volume["est_s_by_stage"])
    ests = {int(k): int(v) for k, v in est_raw.items() if str(k).lstrip("-").isdigit()}
    greedy_root = Path(job_dir) / GREEDY_DIR_NAME
    shutil.rmtree(greedy_root, ignore_errors=True)  # 重试轮可能留下半截产物（那会让对账红）
    rep: dict[str, Any] = {}
    if isinstance(iter_info.get("report"), dict):
        rep = dict(iter_info["report"])
    sampling_brief = {
        "games": int(rep.get("games") or 0),
        "winRate": float(rep.get("winRate") or 0.0),
        "outcomes": dict(rep.get("outcomes") or {}),
        "samples": int(rep.get("totalSamples") or 0),
    }
    shards_root = Path(iter_info.get("shards_root") or job_dir)
    wave_no = 0

    def _run_wave(pairs: list[tuple[int, int]]) -> dict:
        nonlocal wave_no
        wave_no += 1
        info = run_iter_rollout(
            greedy_root / f"wave{wave_no}",
            greedy_spec(rollout, pairs),
            ts_dir=ts_dir,
            log=log,
            ctx=rctx,
            it=it,
        )
        dirs = [_under_ppo_root(str(d), shards_root) for d in (info.get("shard_dirs") or [])]
        return {"report": info.get("report") or {}, "shard_dirs": dirs}

    return run_greedy_round(
        raw_quota=raw,
        round_target=int(rollout.get("greedy_round_target") or 0),
        stages=stages,
        it=it,
        rotate_seed=int(volume.get("rotate_seed") or 0),
        ordinary=pairs_seen,
        run_wave=_run_wave,
        sampling_brief=sampling_brief,
        ests=ests,
        width=int(getattr(rctx, "workers", 0) or 0),
        log=log,
    )


def _under_ppo_root(d: str, shards_root: Path) -> str:
    """greedy shard 必须落在 PPO 的单一装载根里，否则「采了没训」而账本好看。"""
    p = Path(d)
    try:
        p.resolve().relative_to(shards_root.resolve())
        return str(p)
    except ValueError:
        dest = shards_root / GREEDY_DIR_NAME / f"{p.parent.name}__{p.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        try:
            shutil.move(str(p), str(dest))
        except OSError as e:  # 搬不动就把现场说清（不静默留一份训不到的语料）
            raise ProtocolError(
                f"greedy shard 搬不进 PPO 装载根：{p} → {dest}（{type(e).__name__}: {e}）"
                "——采到的 greedy 语料不进梯度比不采更糟（账本会好看）"
            ) from e
        return str(dest)


def merge(iter_info: dict, out: Mapping[str, Any], *, log: Callable[..., None]) -> None:
    """greedy 一轮结果并进 `iter_info`：主 report 全量、分关账挂增量子键、统计另起一键。

    三个键名与 trainer 腿逐字相同（`trainer/loop_volume._greedy_stats` /
    `volume_stage_stats["greedy"]`）——控制台/`worker/resume` 不写「这条腿」的分支。
    """
    report = out.get("report") or {}
    if report:
        base = iter_info.get("report")
        iter_info["report"] = (
            combine_reports([base, report]) if isinstance(base, dict) and base else report
        )
    vss = iter_info.get("volume_stage_stats")
    if not isinstance(vss, dict):
        vss = {}
    by_stage = out.get("by_stage")
    if isinstance(by_stage, dict):
        vss = {**vss, "greedy": by_stage}
    iter_info["volume_stage_stats"] = vss
    stats = out.get("greedy_quota_stats")
    if isinstance(stats, dict):
        iter_info["greedy_quota_stats"] = stats
    log(
        f"[greedy] 本轮并入：waves={out.get('waves')} total={(stats or {}).get('total')} "
        f"degraded={(stats or {}).get('degraded')}（产出 {len(out.get('shard_dirs') or [])} 片）"
    )
