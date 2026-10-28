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


def finalize_round_shards(
    it_dir: Path,
    mem: Mapping[str, dict],
    *,
    it: int = -1,
    emit: Callable[[str], None] | None = None,
) -> Path:
    """轮末收尾：把本轮分关账写盘（**唯一**落盘的东西，几百字节）。

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
    return path
