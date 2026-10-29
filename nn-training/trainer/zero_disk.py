"""零落盘：sampler 回传的 shard **根本不落盘**（2026-10-28）。

用户口径（2026-10-28）：「不是删！！！是根本就**不需要保存到硬盘**！！！」——`plan/payload-zero-disk`
的 S1/S2 只做到「收到就压」（`write_shard(on_receive=)` → `ShardSegments` 压段），但 `write_shard`
仍把整份 npy 写盘，于是 y36 it91 攒出 **2912 个 npy / 820 MB**（`obs.npy` 759 MB）。

**S3（本模块 + `write_shard` 的「有段账就不落盘」）**：dispatch 腿的字节在 `fetch_task` 的 `files`
里就已经全在内存，打包用 `presegmented` 按名拼接 ⇒ 磁盘上从头到尾不需要存在过。

磁盘上的「目录」被三样东西替代：

1. **段**：`ShardSegments._parts`（已压好的 tar 段，打包时原样拼接）；
2. **元数据**：`ShardSegments._manifests`（发布端 `iter_shard_dirs(mem=)` / `data_fp(mem=)` /
   配额裁剪 / 分关 est 都读它）；
3. **统计**：`ShardSegments._metrics`（`metrics_stats(extra_metrics=)` 的唯一来源）。

本模块只负责轮末的**分关账落盘**：`it<N>/stage_est.json`（`worker.resume.STAGE_EST_NAME`）——
`trailing_stage_samples_per_game` 在磁盘上没有 shard 目录时靠它，`est_agg=latest` 才不会静默
退回全局 est。这份 JSON 是**元数据**（几百字节），不是采样结果。

**边界**：local 兜底腿 / 预采子进程的 shard 由本机 bun 直接写盘（不经过 `on_shard`）⇒ 照旧落盘，
也照旧被扫盘读到。课程显式声明 `archive_keep_shards: true` 时 dispatch 腿也落盘（课程自己要留语料）。

**resume 的固有代价**：dispatch 腿的 shard 不在盘上 ⇒ 崩溃重启后本轮按「没采到」重采。方向安全
（不会错数据），只白跑一次 rollout。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from common.log import log
from worker.resume import MANIFEST_NAME, STAGE_EST_NAME, walk_shard_dirs

#: 逐局压缩画像的落盘名（与 `remote/artifacts.py::ArtifactStore.PER_GAME_NAME`、
#: `dashboard/src/server/iters.ts::PER_GAME_NAME` 三处必须逐字相同：读方按文件优先，
#: 在线/dist 零落盘腿原本不写它 ⇒ 控制台「耗时/击杀/残血/道具」四列恒空，见 z2-r3 it20+）。
PER_GAME_NAME = "per-game.json"


def _add_row(rows: dict[int, tuple[int, int]], mm: Mapping[str, Any]) -> None:
    """把一份 manifest 累加进 `{stage: (games, samples)}`（量纲与 `worker.resume` 同源）。"""
    st = mm.get("stage")
    n = mm.get("nSamples")
    if not isinstance(n, int):
        n = mm.get("totalSamples")
    if not isinstance(st, int) or not isinstance(n, int) or n <= 0:
        return
    games_n, samples_n = rows.get(int(st), (0, 0))
    rows[int(st)] = (games_n + 1, samples_n + int(n))


def stage_est_rows(
    it_dir: Path, *, mem: Mapping[str, dict] | None = None
) -> dict[int, tuple[int, int]]:
    """本轮**全部** shard 的分关账：盘上目录 + 内存 manifest（同名以盘上为准）。

    量纲与 `worker.resume.trailing_stage_samples_per_game` / `settled_stage_totals` 逐字相同
    （只认 `nSamples` / `totalSamples`，不读 ticks）：三处口径必须同源，否则删盘/落盘两态
    的 est 会跳。
    """
    rows: dict[int, tuple[int, int]] = {}
    seen: set[str] = set()
    for d in walk_shard_dirs(it_dir, with_manifest=True):
        seen.add(d.name)
        try:
            mm = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(mm, dict):
            _add_row(rows, mm)
    for name, mm in (mem or {}).items():
        if name not in seen:
            _add_row(rows, mm)
    return rows


def write_stage_est(it_dir: Path, rows: Mapping[int, tuple[int, int]]) -> Path:
    """原子写 `it<N>/stage_est.json`（返回路径）。空账也写 —— 「有账但为空」≠「没有账」：
    前者让 `latest` 继续往前找，后者会退回扫盘（零落盘轮没有目录可扫 ⇒ 静默取全局 est）。"""
    path = it_dir / STAGE_EST_NAME
    doc = {str(st): {"games": g, "samples": s} for st, (g, s) in sorted(rows.items())}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def write_iter_actuals(it_dir: Path, mem: Mapping[str, dict]) -> tuple[Path, int] | None:
    """轮末把本轮**全部** shard 的逐局压缩画像写成 `it<N>/per-game.json`（一个 it 一份）。

    返回 `(path, 局数)`；无数据可写时返回 `None`（不在盘上留空文件）。

    在线/dist 零落盘腿的 shard 从不落盘（`common.shard.write_shard` 见到段账即只交字节、不写盘），
    而控制台的「耗时/击杀/残血/道具」四列是逐局聚合出来的，依赖单局 manifest —— 它只活在回传进程的
    `ShardSegments._manifests` 内存里，随进程回收。`finalize_round_shards` 恰好在本轮回传账还在时调用
    （裁剪 `_trim_shards_to_quota` 在随后的 publish 阶段才发生），所以在此把它压成只读画像落盘，
    读方（`dashboard/src/server/iters.ts::readPerGameFileEntries`）优先读这个文件，与离线回传腿同口径、
    零改动。

    收集范围 = **全采集**（盘上 shard 目录 + 内存 manifest，两份源与 `stage_est_rows` 同）：
    `mem` 在调用时仍是裁剪前的全量 ⇒ 四列统计覆盖所有被采样的局，不止 PPO 实际装载的保留集
    （用户口径 2026-10-29「写全采集」）。同 (stage,seed) 重复不去重（读方 `pickBest` 才去重）。

    只在有数据时才写：纯离线回传轮 `mem` 为空且盘上无 shard ⇒ 跳过，绝不拿空数组覆盖回传腿
    已写好的 `per-game.json`。
    """
    from worker.reports import compact_per_game

    all_mm: list[dict] = []
    # 盘上 shard（非零落盘 / local 兜底腿）：直接扫目录。
    for d in walk_shard_dirs(it_dir, with_manifest=True):
        try:
            mm = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(mm, dict):
            all_mm.append(mm)
    # 内存回传 manifest（零落盘腿）：与盘上合并。
    for mm in (mem or {}).values():
        if isinstance(mm, dict):
            all_mm.append(mm)
    if not all_mm:
        return None
    rows = compact_per_game(all_mm)
    if not rows:
        return None
    path = it_dir / PER_GAME_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, len(rows)


def finalize_round_shards(
    it_dir: Path,
    mem: Mapping[str, dict],
    *,
    it: int = -1,
    emit: Callable[[str], None] | None = None,
) -> Path:
    """轮末收尾：把本轮分关账 + 逐局画像写盘（**唯一**落盘的东西，几百字节 + 压缩画像）。

    必须在 `_record_iteration` / `_write_iter_stats` / 巡检**之后**调（它们还要读内存账与
    local 腿的盘上 shard）。`mem` 为空（纯 local 轮）⇒ 只写账，不喊「零落盘」。
    """
    say = emit if emit is not None else log
    rows = stage_est_rows(it_dir, mem=mem)
    path = write_stage_est(it_dir, rows)
    if mem:
        say(
            f"[zero-disk] it{it}: {len(mem)} 个回传 shard 全程未落盘"
            f"（打包走 presegmented 段），分关账 → {path}"
        )
    pg = write_iter_actuals(it_dir, mem)
    if pg is not None:
        pg_path, pg_n = pg
        say(
            f"[zero-disk] it{it}: 逐局画像 → {pg_path}"
            f"（全采集 {pg_n} 局，控制台的耗时/击杀/残血/道具列据此出数）"
        )
    return path
