"""remote/land_provider.py —— 打包时的**整份提供者**（weights + opt）：一份实现，三条出口共用。

## 契约（打包侧只认这个形状）

```
full_for(root: Path, it: int) -> {包内名: 整份路径}
```

缺键 = 这一件**落不下来**（打包方保留原件，包照出）。`ArtifactStore.finalize(full_for=…)` 与
`offline_deliverable.write_deliver_zip(full_for=…)` 的 `full_for` 都是它。

## 为什么是独立模块（不是塞进打包模块）

`remote/artifacts` 与 `remote/offline_deliverable` 都是 **L0**（前者被 `hub.store_offline` L1
直接 import），而落地器 `weights_standard` / `opt_standard` 是 L1 ⇒ 在 L0 里 import 它们就是
向上边（`tests/test_remote_dag.py`）。本模块 **L2**，读者只有 L6 的 `plan_run` 与 standalone 的
`offline_boot`。

## 为什么几乎不要钱（2026-10-22 复核）

  * 云机**每轮开跑前**已经把上一轮落成整份写进 `<store>/.weights-standard/<sha16>.json`
    （`plan_run._run_iteration` 的既有行为），而 `prune_caches` 只扫 `cache_root` 的三棵内容
    寻址缓存、碰不到产物根 ⇒ 历史轮命中 = **一次 `is_file()`**；
  * 只有「还没当过 init 的轮」（末轮）要现场落，它的基准在 `blob_cache`（`_cas_hit`，键就是
    `base_sha`，O(1)）；
  * 抽屉里若本来就是整份（`is_delta_weights_file` 为假），`standard_weights_copy` **原样交回**
    ⇒ 零成本（所以「只剩抽屉」那支也不是 O(n²)）。

## 失败策略（为什么有 `max_failures`）

基准逐字节找不到时，`weights_standard` 会按候选扫一遍（每次调用新 `_SearchContext`、自带
4000 候选 / 2GB 读上界）。链断了通常是**整段一起断**（同一条 init 链），逐轮各扫一遍纯属浪费，
而这个函数可能跑在人点一下坐着等的 notebook cell 里 ⇒ 同一件**连续失败 3 次就停试**（响亮一行
说「后续轮次不再尝试」），包照出、原件不丢。**永不抛**：打包永不因落地失败而失败。
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import remote.opt_standard as opt_standard
import remote.weights_standard as weights_standard
from common.protocol import ProtocolError
from remote.artifacts import ArtifactStore
from remote.offline_deliverable import OPT_TAR_NAME, WEIGHTS_NAME

#: 同一件连续失败几次后停止再试（见模块头「失败策略」）。
MAX_FAILURES = 3

#: 打包侧的 `full_for` 形状（两边模块的形参类型都是它）。
FullFor = Callable[[Path, int], Mapping[str, Path]]


def blob_roots_of(work_dir: str | Path) -> list[Path]:
    """`blob_cache` 的候选根（**唯一实现**；`plan_handoff._blob_roots_of` 转发到这里）。

    顺序 = 已知好用者在前：`<work_dir>/blob_cache`（`seed_opt_blob` 一直写这里）· 其次
    `<work_dir>/code_cache/blob_cache`（worker 侧的形状）。
    """
    wd = Path(work_dir)
    return [wd / "blob_cache", wd / "code_cache" / "blob_cache"]


def provider(
    *,
    course: str = "",
    blob_roots: Iterable[str | Path] = (),
    log: Callable[[str], None] = print,
    max_failures: int = MAX_FAILURES,
) -> FullFor:
    """造一个 `full_for(root, it)`：整份权重 + 整份 opt，各自独立成败（见模块头）。"""
    roots = [p for p in (Path(b) for b in blob_roots) if p.is_dir()]
    failures = {WEIGHTS_NAME: 0, OPT_TAR_NAME: 0}
    gave_up: set[str] = set()

    def one(root: Path, it: int) -> dict[str, Path]:
        out: dict[str, Path] = {}
        for name, land in (
            (WEIGHTS_NAME, functools.partial(_land_weights, course=course, blob_root=roots[:1], log=log)),
            (OPT_TAR_NAME, functools.partial(_land_opt, blob_roots=roots, log=log)),
        ):
            if name in gave_up:
                continue
            try:
                out[name] = land(root, it)
                failures[name] = 0  # 成功一次就重置：断链可能只断在某一段
            except ProtocolError as e:
                failures[name] += 1
                log(f"WARN 打包 it{it}：{name} 落地失败（{e}）—— 包内保留原件")
                if failures[name] >= max_failures:
                    gave_up.add(name)
                    log(
                        f"WARN 打包：{name} 已连续失败 {failures[name]} 轮（基准链断了？）"
                        "—— 后续轮次不再尝试落地，包内保留原件（换机要用它们的整份请先补基准）"
                    )
        return out

    return one


def _round_file(root: Path, it: int, name: str) -> Path:
    return Path(root) / f"{ArtifactStore.IT_PREFIX}{int(it):03d}" / name


def _land_weights(
    root: Path,
    it: int,
    *,
    course: str,
    blob_root: list[Path],
    log: Callable[[str], None],
) -> Path:
    return weights_standard.standard_weights_copy(
        _round_file(root, it, WEIGHTS_NAME),
        Path(root),
        course=course,
        traj_root=Path(root),
        blob_root=blob_root[0] if blob_root else None,
        log=log,
    )


def _land_opt(
    root: Path,
    it: int,
    *,
    blob_roots: list[Path],
    log: Callable[[str], None],
) -> Path:
    return opt_standard.standard_opt_copy(
        _round_file(root, it, OPT_TAR_NAME),
        Path(root),
        blob_roots=blob_roots,
        traj_root=Path(root),
        log=log,
    )
