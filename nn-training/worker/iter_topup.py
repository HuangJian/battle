"""iter_topup.py — 云机侧**有界多批补差**（plan/rollout-stage-balance.plan.md §4.4）。

## 病

全离线腿（`kind=run`）一轮只跑一批：`pairs_for` 按（过去的）全局 est 反解「每关同一个局数」，
而达标线是**分关** quota。关间 `samples/局` 差 1.75× 时同一轮里既有浪费（长局关溢出被
`per_stage_quota` 裁掉）又有缺口（短局关欠采），而节点**没有第二次机会**——一轮一个 job、
PPO 在 job 里跑完（x21-psh-b it168 实测：缺 4405 样本、同时丢 3333 样本）。

## 修法（每条都为「不静默」服务）

* **每批一个自己的 spec**：`run_iter_rollout` 的「实产 == 声明」（`verify_shards` +
  `iter_expected_data_fp(spec)`）**一个字不改**——补差批有自己的 argv，就用自己的声明集。
  （代替方案「放宽 verify_shards 为闭集」被否决：hub 侧的 `data_fp` 是**回显**，
  节点这条谓词是整条链上唯一的语料完整性判据，放宽 = 净放松；见评审 P0-3。）
* **批目录隔离**：第 k 批跑在 `job_dir/topup{k}/`——否则 `scan_shard_dirs` 会把前几批
  一起扫进来，逐位对账立刻失真（"多局"）。
* **同一条连续流续抽**：`continuous_pairs(..., start_idx=已产局数)`；首批
  （`initial_wave_pairs_by_stage`）就是这条流的前缀（`wave_pairs` 同函数、同 0x5EED 键），
  所以「第 k 批接得上」是构造性的，不是约定。
* **同关取模板**：argv 模板只从**本关**的既有行里取——`retarget_argv` 对 `--stage-json` 的
  语义是「空 ⇒ 整对删除」，跨关取模板会把自定义关的 JSON 删掉/带错。重定向原语从
  `worker.rollout_argv`（L0 叶子）取，**不从 `worker.plan`**：后者是 L4，本模块（L2）
  import 它会成上向边，而 `remote/worker.py`（L5）顶层 import 本模块 ⇒ 图会破。
* **缺口/浪费分开记账**：缺口是硬指标（不可恢复），浪费是软指标（有上界）；触 `game_cap`
  与「批数用尽仍有缺口」都在返回块的 `capped_stages` / `shortfall_by_stage` 里**可见**，
  由调用方写进轮报。静默短采正是本模块要防的事。

常量与标定（1.15 / ≤3 补差批 / 末批 ×0.85）住 `worker/volume_alloc.py`（纯函数 + 单测）。
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.payload import INIT_WEIGHTS_NAME
from common.protocol import ProtocolError, validate_rollout_spec
from worker.volume_alloc import (
    VOLUME_ALLOC_RULE,
    default_game_caps,
    shortfall_by_stage,
    stage_totals,
    topup_games_by_stage,
    validate_runtime_volume,
    wasted_samples,
)

#: 补差批的 job 子目录前缀（`topup1` / `topup2` …）——与首批同目录会让逐位对账失真。
BATCH_DIR_PREFIX = "topup"


def _flag_value(argv: list[str], flag: str) -> str:
    """argv 里 `flag` 的下一个 token（缺席/结尾 ⇒ 空串；与 `worker.plan._flag_value` 同语义）。"""
    try:
        i = argv.index(flag)
    except ValueError:
        return ""
    return str(argv[i + 1]) if i + 1 < len(argv) else ""


def _stage_of(argv: list[str]) -> int:
    raw = _flag_value(argv, "--stages")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ProtocolError(
            f"补差批模板缺 --stages 或非整数（{raw!r}）——声明集无法与关卡对齐，拒收"
        ) from None


def _out_base(argv: list[list[str]], fallback: int) -> int:
    """下一批 `--out` 的起始序号：沿用首批 `w{i}` 编号的最大值 +1（不覆盖、不重号）。

    编号是 `data_fp` 条目的一部分（`data_fp_entries` 哈希 shard 目录名），所以两批
    **必须**不相交——单测钉住这一点。
    """
    top = -1
    for row in argv:
        txt = _flag_value(list(row), "--out")
        if txt.startswith("w") and txt[1:].isdigit():
            top = max(top, int(txt[1:]))
    return max(int(fallback), top + 1)


def next_batch_spec(
    spec: dict,
    games_by_stage: dict[int, int],
    *,
    start_idx: dict[int, int],
    out_base: int,
    rotate_seed: int,
    it: int,
) -> dict:
    """本批 spec：同一条流上 `start_idx → start_idx+n` 的连续抽签，逐局重定向 argv。

    模板按 **stage** 取（`spec["argv"]` 里该关的第一行）；`--stage-json` 用模板自己的值
    （`retarget_argv` 的 `stage_json=None` 会**删掉**那一对，跨关取模板就会踩这个坑）。
    """
    from worker.rollout_argv import retarget_argv
    from worker.volume_quota import continuous_pairs

    rows = list(spec["argv"])
    by_stage: dict[int, list[str]] = {}
    for row in rows:
        by_stage.setdefault(_stage_of(list(row)), list(row))
    missing = [s for s in games_by_stage if int(s) not in by_stage]
    if missing:
        raise ProtocolError(
            f"补差批拿不到这些关的 argv 模板: {sorted(missing)}（声明集里没有该关 ⇒ 无法重定向）"
        )
    pairs = continuous_pairs(
        int(rotate_seed),
        int(it),
        {int(s): int(n) for s, n in games_by_stage.items()},
        {int(s): int(v) for s, v in start_idx.items()},
    )
    argv: list[list[str]] = []
    for stage, seed in pairs:
        base = by_stage[int(stage)]
        argv.append(
            retarget_argv(
                base,
                stage=int(stage),
                seed=int(seed),
                out=f"w{int(out_base) + len(argv)}",
                wver=str(spec.get("wver") or ""),
                stage_json=_flag_value(base, "--stage-json") or None,
            )
        )
    return validate_rollout_spec({**spec, "argv": argv})


def merge_iter_reports(base: dict, extra: list[dict]) -> dict:
    """把补差批的轮报并进首批轮报（`games`/`totalSamples`/`outcomes` 等**必须相加**）。

    `combine_reports` 管聚合字段；`shards`/`elapsedSec`/`perGame`/`perGameSecs` 是
    `run_iter_rollout` 自己的收尾字段（不在 `combine_reports` 的键集里）⇒ 这里显式相加。
    """
    from worker.reports import combine_reports

    reports = [base] + [r for r in extra if r]
    out = combine_reports(reports)
    # 保留 base 的收尾/溯源字段（combine_reports 不认识它们），再逐项相加。
    for k, v in base.items():
        if k not in out:
            out[k] = v
    out["shards"] = sum(int(r.get("shards") or 0) for r in reports)
    out["elapsedSec"] = round(sum(float(r.get("elapsedSec") or 0.0) for r in reports), 3)
    per_game: list[Any] = []
    per_game_secs: list[Any] = []
    for r in reports:
        per_game.extend(r.get("perGame") or [])
        per_game_secs.extend(r.get("perGameSecs") or [])
    out["perGame"] = per_game
    out["perGameSecs"] = per_game_secs
    return out


def topup_rollout(
    *,
    job_dir: str | Path,
    spec: dict,
    shard_dirs: list[str],
    ts_dir: str | Path | None = None,
    log: Callable[[str], None] = lambda msg: print(f"[topup] {msg}", flush=True),
    run_fn: Callable[..., dict] | None = None,
) -> dict | None:
    """按 spec 里的 `volume` 块补差；返回 `{shard_dirs, reports, volume}` 或 **None**（无需补差）。

    `shard_dirs` = **首批**的产出目录（调用方已经拿到）——本函数只从这里读账本，
    自己新产的批落在 `job_dir/topup{k}/` 并向返回值里追加，绝不重扫首批目录。
    `run_fn` 注入时为单测用（默认 = `worker.iter_rollout.run_iter_rollout`）。
    """
    raw = spec.get("volume")
    if not isinstance(raw, dict):
        return None
    try:
        vol = validate_runtime_volume(raw)
    except ValueError as e:
        raise ProtocolError(f"rollout.volume 块非法：{e}") from e
    topup = dict(vol["topup"])
    max_batches = int(topup["max_batches"])
    if not bool(topup.get("enabled")) or max_batches <= 0:
        return None
    from worker.iter_rollout import collect_shard_manifests, run_iter_rollout

    def _totals(dirs: list[str]) -> tuple[dict[int, int], dict[int, int]]:
        """目录（字符串，来自轮结果）→ 逐关 `(collected, games)`（**产出集**口径）。"""
        return stage_totals(collect_shard_manifests([Path(d) for d in dirs]))

    run = run_fn or run_iter_rollout
    stages = [int(s) for s in vol["stages"]]
    target = int(vol["target_transitions"])
    ests = {int(k): int(v) for k, v in vol["est_s_by_stage"].items()}
    caps = default_game_caps(
        stages, target, ests=ests, explicit=int(topup["max_games_per_stage"])
    )
    rotate_seed = int(vol["rotate_seed"])
    it = int(vol["it"])
    collected, games = _totals(shard_dirs)
    out_base = _out_base(list(spec["argv"]), len(spec["argv"]))
    new_dirs: list[str] = []
    reports: list[dict] = []
    batches = 0
    while batches < max_batches:
        plan_g = topup_games_by_stage(
            stages=stages,
            collected=collected,
            games_done=games,
            target_transitions=target,
            ests=ests,
            game_caps=caps,
            batches_left=max_batches - batches,
            last_lo_factor=float(topup["last_lo_factor"]),
        )
        if not plan_g:
            break
        batches += 1
        spec2 = next_batch_spec(
            spec,
            plan_g,
            start_idx={s: int(games.get(s, 0)) for s in stages},
            out_base=out_base,
            rotate_seed=rotate_seed,
            it=it,
        )
        sub = Path(job_dir) / f"{BATCH_DIR_PREFIX}{batches}"
        sub.mkdir(parents=True, exist_ok=True)
        # 子目录隔离的**代价**：`--weights init_weights.json` 与 `--out w{i}` 都是相对
        # **job 目录**解析的（`iter_rollout._exec_argv`），而 `--out` 要的就是子目录；
        # `--weights` 必须就位，否则每一局都起不来。文件是同一份字节（不重算、不改内容），
        # mtime/内容都与打包时一致（没有 `--weights` 的 sha 校验环节，只有存在性）。
        src_w = Path(job_dir) / INIT_WEIGHTS_NAME
        if not src_w.exists():
            raise ProtocolError(
                f"补差批需要 {INIT_WEIGHTS_NAME}（rollout argv 以 job 目录为相对根），"
                f"但 {src_w} 不存在——拒收（宁可不补，也不能跑一批读不到权重的局）"
            )
        shutil.copyfile(src_w, sub / INIT_WEIGHTS_NAME)
        log(
            f"it{it} 补差批 {batches}/{max_batches}: {len(spec2['argv'])} 局 "
            f"（games={plan_g} → {sub.name}/，est_s={ests} cap={caps}）"
        )
        info = run(sub, spec2, ts_dir=ts_dir, log=log)
        dirs_b = [str(d) for d in info["shard_dirs"]]
        added, games_b = _totals(dirs_b)
        for s, v in added.items():
            collected[s] = collected.get(s, 0) + int(v)
        for s, v in games_b.items():
            games[s] = games.get(s, 0) + int(v)
        out_base += len(spec2["argv"])
        new_dirs.extend(dirs_b)
        reports.append(dict(info.get("report") or {}))
    if batches <= 0:
        return None
    short = shortfall_by_stage(stages=stages, collected=collected, target_transitions=target)
    capped = sorted(s for s in short if int(caps.get(s, 0)) > 0 and int(games.get(s, 0)) >= int(caps[s]))
    volume_block = {
        "rule": VOLUME_ALLOC_RULE,
        "batches": int(batches),
        "est_hi_factor": float(vol["est_hi_factor"]),
        "collected_by_stage": {int(s): int(collected.get(s, 0)) for s in stages},
        "games_by_stage": {int(s): int(games.get(s, 0)) for s in stages},
        "shortfall_by_stage": {int(s): int(v) for s, v in short.items()},
        "wasted_samples": int(
            wasted_samples(stages=stages, collected=collected, target_transitions=target)
        ),
        "capped_stages": capped,
    }
    if short:
        log(
            f"WARN it{it} 补差批用尽仍有缺口: {short}（capped={capped}；batches={batches}/{max_batches}）"
            "——缺口会带进训练，这是**硬指标**（事件 volumeTopup 里有账）"
        )
    else:
        log(
            f"it{it} 补差完成: batches={batches} collected={volume_block['collected_by_stage']} "
            f"wasted={volume_block['wasted_samples']}"
        )
    return {"shard_dirs": new_dirs, "reports": reports, "volume": volume_block}
