"""BC 语料留用与固定语料校验（plan/bc-single-course.plan.md §16.9）。

本模块只做两件事，保持 np/torch-free 之外的依赖最小：

1. **ratio**：按 `target{win,loss,timeout}` 计算每关（或全局）配额，胜局全留、
   非胜局只裁不补；未选中的 shard 删除载荷、保留 manifest 作为 skip marker
   （`landed_pairs` 认它，`scan_shards` 不认）。
2. **fixed**：只校验 shard 形状/长度/mask 宽度，并算 `data_fp`；不改写语料。

`all` 仍会清掉 error/aborted 这类无效结局（它们不是训练样本），但不会按比例裁。
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worker.bc_config import BcCorpusTarget
from worker.bc_outcome import (
    LOSS as LOSS,
)
from worker.bc_outcome import (
    OUTCOME_CLASSES as OUTCOME_CLASSES,
)
from worker.bc_outcome import (
    TIMEOUT as TIMEOUT,
)
from worker.bc_outcome import (
    WIN as WIN,
)
from worker.bc_outcome import (
    classify_outcome as classify_outcome,
)

SKIP_MARKER = "corpus_skipped"
SKIP_REASON = "corpus_skip_reason"


@dataclass(frozen=True)
class CorpusShard:
    """一个已落盘 shard 的 outcome 视图（只读 manifest，不读张量）。"""

    path: Path
    stage: int
    seed: int
    outcome: str
    klass: str | None


def _read_manifest(shard: Path) -> dict[str, Any]:
    p = shard / "manifest.json"
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"[bc-corpus] shard manifest 读不到：{p}：{e}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"[bc-corpus] shard manifest 必须是对象：{p}")
    return obj


def load_shards(data_dir: str | Path) -> list[CorpusShard]:
    """扫描有 `obs.npy` 的 shard（skip marker 没有载荷，天然被排除）。"""
    from worker.data.npyio import scan_shards

    paths = scan_shards(str(data_dir))
    if not paths:
        raise FileNotFoundError(f"[bc-corpus] 没有可用 shard（obs.npy）：{data_dir}")
    out: list[CorpusShard] = []
    for raw in paths:
        path = Path(raw)
        manifest = _read_manifest(path)
        try:
            stage = int(manifest["stage"])
            seed = int(manifest["seed"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"[bc-corpus] shard 缺 stage/seed：{path}") from e
        outcome = str(manifest.get("outcome") or "")
        out.append(CorpusShard(path, stage, seed, outcome, classify_outcome(manifest)))
    return out


def selection_key(corpus_fp: str, it: int, stage: int, seed: int, outcome: str) -> str:
    """同类超额时的确定性排序键（与到达顺序无关）。"""
    raw = f"{corpus_fp}|{int(it)}|{stage}|{seed}|{outcome}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _quota(frac: float, base: float) -> int:
    """非负半入整数配额（不用 Python 的 banker's rounding）。"""
    return max(0, math.floor(float(frac) * float(base) + 0.5))


def _target_dict(target: BcCorpusTarget | Mapping[str, float] | None) -> dict[str, float]:
    if target is None:
        raise ValueError("[bc-corpus] ratio 必须提供 target")
    if isinstance(target, BcCorpusTarget):
        out = target.model_dump()
    else:
        out = {k: float(target.get(k, 0.0)) for k in OUTCOME_CLASSES}
    if abs(sum(out.values()) - 1.0) > 1e-9 or out[WIN] <= 0:
        raise ValueError(f"[bc-corpus] target 非法（和须为 1 且 win>0）：{out}")
    return out


def _counts(shards: list[CorpusShard]) -> dict[str, int]:
    return {k: sum(1 for s in shards if s.klass == k) for k in OUTCOME_CLASSES}


def _skip(shard: Path, reason: str) -> None:
    """删除 shard 载荷，只留 manifest（skip marker）；幂等。"""
    manifest_path = shard / "manifest.json"
    manifest = _read_manifest(shard)
    manifest[SKIP_MARKER] = True
    manifest[SKIP_REASON] = reason
    for child in shard.iterdir():
        if child.name == "manifest.json":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def apply_corpus_policy(
    data_dir: str | Path,
    *,
    policy: str,
    corpus_fp: str,
    it: int,
    target: BcCorpusTarget | Mapping[str, float] | None = None,
    scope: str = "stage",
    source: str = "collect",
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """执行 all/ratio 留用，返回 `bc_corpus_mix` 账本事件（调用方 append）。"""
    if policy not in ("all", "ratio"):
        raise ValueError(f"[bc-corpus] 未知 outcome_policy：{policy!r}")
    if scope not in ("stage", "global"):
        raise ValueError(f"[bc-corpus] 未知 scope：{scope!r}")
    shards = load_shards(data_dir)
    valid = [s for s in shards if s.klass is not None]
    invalid = [s for s in shards if s.klass is None]
    selected: set[Path] = set()
    by_group: dict[str, dict[str, int]] = {}
    tgt = _target_dict(target) if policy == "ratio" else None
    if policy == "all":
        selected = {s.path for s in valid}
    else:
        assert tgt is not None
        groups: dict[str, list[CorpusShard]] = defaultdict(list)
        for s in valid:
            groups[str(s.stage) if scope == "stage" else "all"].append(s)
        for key, group in groups.items():
            counts = _counts(group)
            if counts[WIN] > 0:
                base = counts[WIN] / tgt[WIN]
                quotas = {LOSS: _quota(tgt[LOSS], base), TIMEOUT: _quota(tgt[TIMEOUT], base)}
            else:
                # 没有胜局就没有比例锚点：保留全部有效局，只记账不可行。
                quotas = {LOSS: counts[LOSS], TIMEOUT: counts[TIMEOUT]}
            group_selected = [s for s in group if s.klass == WIN]
            for klass in (LOSS, TIMEOUT):
                cand = [s for s in group if s.klass == klass]
                cand.sort(
                    key=lambda s: (
                        selection_key(corpus_fp, it, s.stage, s.seed, s.outcome),
                        str(s.path),
                    )
                )
                group_selected.extend(cand[: quotas[klass]])
            selected.update(s.path for s in group_selected)
            by_group[key] = {
                "collected": len(group),
                "kept": len(group_selected),
            }
    for s in shards:
        if s.path not in selected:
            _skip(s.path, "invalid_outcome" if s.klass is None else "outcome_ratio")
    collected = _counts(valid)
    kept_shards = [s for s in valid if s.path in selected]
    kept = _counts(kept_shards)
    total = sum(kept.values())
    actual = {k: (kept[k] / total if total else 0.0) for k in OUTCOME_CLASSES}
    infeasible: list[str] = []
    if tgt is not None:
        infeasible = [
            k for k in OUTCOME_CLASSES if tgt[k] > 0.0 and actual[k] + 1e-9 < tgt[k]
        ]
    event: dict[str, Any] = {
        "event": "bc_corpus_mix",
        "it": int(it),
        "source": source,
        "policy": policy,
        "scope": scope,
        "target": tgt,
        "collected": collected,
        "kept": kept,
        "actual": actual,
        "infeasible": infeasible,
        "invalid_skipped": len(invalid),
    }
    if scope == "stage" and by_group:
        event["by_stage"] = by_group
    log(
        f"[bc-corpus] it{it} {source}/{policy}: collected={collected} kept={kept} "
        f"invalid={len(invalid)} infeasible={infeasible or '-'}"
    )
    return event


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fixed_data_fp(data_dir: str | Path) -> str:
    """固定语料内容指纹：排序后的相对路径 + 文件字节（不含 `${data_dir}` 前缀）。"""
    root = Path(data_dir)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(_file_digest(p).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _load_v6_array(path: Path, name: str) -> Any:
    import numpy as np

    p = path / name
    if not p.is_file():
        raise ValueError(f"[bc-corpus] fixed v6 shard 缺 {name}：{path}")
    return np.load(p, mmap_mode="r")


def validate_fixed_corpus(data_dir: str | Path, *, action_v6: bool) -> str:
    """校验固定语料并返回 `data_fp`；只读，不裁剪、不改写。"""
    from worker.data.npyio import scan_shards

    paths = scan_shards(str(data_dir))
    if not paths:
        raise FileNotFoundError(f"[bc-corpus] fixed 语料根没有 shard（obs.npy）：{data_dir}")
    if not action_v6:
        from worker.data.npyio import load_shard, verify_shard_schema

        for raw in paths:
            path = Path(raw)
            verify_shard_schema(str(path), load_shard(str(path)))
        return fixed_data_fp(data_dir)

    from common.schema import BOARD, OBS_CHANNELS, POLICY_EXTRA_DIM, SCALAR_DIM

    for raw in paths:
        path = Path(raw)
        obs = _load_v6_array(path, "obs.npy")
        scalars = _load_v6_array(path, "scalars.npy")
        extra = _load_v6_array(path, "extra.npy")
        a_move = _load_v6_array(path, "a_move.npy")
        a_fire = _load_v6_array(path, "a_fire.npy")
        a_hold = _load_v6_array(path, "a_hold.npy")
        mask = _load_v6_array(path, "mask.npy")
        n = int(obs.shape[0]) if obs.ndim >= 1 else 0
        if n <= 0 or tuple(obs.shape[1:]) != (OBS_CHANNELS, BOARD, BOARD):
            raise ValueError(f"[bc-corpus] fixed v6 obs 形状非法：{path} {obs.shape}")
        expected = {
            "scalars": (n, SCALAR_DIM),
            "extra": (n, POLICY_EXTRA_DIM),
            "a_move": (n,),
            "a_fire": (n,),
            "a_hold": (n,),
            "mask": (n, 9),
        }
        got = {
            "scalars": scalars.shape,
            "extra": extra.shape,
            "a_move": a_move.shape,
            "a_fire": a_fire.shape,
            "a_hold": a_hold.shape,
            "mask": mask.shape,
        }
        for name, shape in expected.items():
            if tuple(got[name]) != shape:
                raise ValueError(
                    f"[bc-corpus] fixed v6 {name} 形状非法：{path} "
                    f"{tuple(got[name])} != {shape}"
                )
    return fixed_data_fp(data_dir)
