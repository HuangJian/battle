"""零落盘守卫：dispatch 腿回传的 shard **从不落盘**，元数据走内存账（2026-10-28）。

用户口径：「不是删！！！是根本就**不需要保存到硬盘**！！！」——所以判据必须是
**目录从未被创建**，而不是「事后删掉了」。

七条判据，逐条对应一条被替代的「读盘」路：

1. `write_shard(materialize=False)` 一个字节都不写（连目录都不建）；
2. `iter_shard_dirs(mem=…)` 把内存 shard 列进本轮集（并跑同一套 mver 过滤）；
3. `data_fp(…, mem=…)` 与「盘上真目录」算出的指纹**逐字相同**；
4. `_trim_shards_to_quota(…, mem=…)` 能按内存 manifest 裁；
5. `settled_stage_totals(…, mem=…)` 把补波的账算上（否则白烧到波次上限）；
6. `metrics_stats(extra_metrics=…)` 把内存 `metrics.npy` 算进逐维统计；
7. `finalize_round_shards` 只写 `stage_est.json`，est 读取端立刻可用。

判据全部读返回值 / 文件事实，不撒 `assert "…" in 文本`（形态口径零命中）。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np

import common.manifest as cm
from biz.reward_library import METRICS
from common.shard import SHARD_FILES, write_shard
from remote.hub_client import iter_shard_dirs
from remote.payload_pack import _pack_segmented, compress_segment, shard_segment
from trainer.loop_remote_job import _trim_shards_to_quota
from trainer.zero_disk import PER_GAME_NAME, finalize_round_shards, write_iter_actuals
from worker.metrics_stats import metrics_stats
from worker.resume import (
    MEM_SHARD_NODE,
    STAGE_EST_NAME,
    settled_stage_totals,
    trailing_stage_samples_per_game,
)

_JID = "a" * 16


def _manifest(stage: int, seed: int, n: int, *, wver: str = "w1") -> dict:
    return {"wver": wver, "stage": stage, "seed": seed, "nSamples": n}


def _mem() -> dict[str, dict]:
    return {
        "rl_s3_seed11": _manifest(3, 11, 100),
        "rl_s3_seed12": _manifest(3, 12, 200),
        "rl_s4_seed13": _manifest(4, 13, 999),
    }


def _rich_manifest(stage: int, seed: int, n: int, *, outcome: str = "stage_clear") -> dict:
    """真实 rollout shard 画像的子集：覆盖控制台四列所需的全部 `_PER_GAME_FIELDS`。

    `mem`（`ShardSegments._manifests`）里存的正是这份字典（与落盘 `manifest.json` 同源），
    `compact_per_game` / 读方 `entryFromManifest` 都按这些字段名取数。
    """
    return {
        "stage": stage,
        "seed": seed,
        "nSamples": n,
        "kills": 12,
        "ticks": 400,
        "outcome": outcome,
        "powerUpsCollected": 3,
        "playerDamageTaken": 50,
        "playerDeaths": 0,
        "puGotTank": 1,
        "startLives": 1,
        "enemyTotal": 20,
        "puSpawnStar": 1,
        "dims": {"loot": 0.7},
        "score": 1234,
    }


def _rich_mem() -> dict[str, dict]:
    return {
        # 同 (stage,seed) 两份（重试）→ 全采集：两份都写，去重交给读方 pickBest。
        "rl_s3_seed11": _rich_manifest(3, 11, 300),
        "rl_s3_seed11_retry": _rich_manifest(3, 11, 280),
        "rl_s3_seed12": _rich_manifest(3, 12, 200, outcome="base_destroyed"),
        "rl_s4_seed13": _rich_manifest(4, 13, 999),
        # 缺 (stage,seed) 的脏行：compact_per_game 必须丢弃（不能污染配对）。
        "rl_s9_bad": {"nSamples": 50, "kills": 5},
    }


def test_write_shard_materialize_false_writes_nothing(tmp_path: Path) -> None:
    """零落盘的核心判据：目录**从未存在**（不是「存在过又被删」）。"""
    out = tmp_path / "it5" / "dist" / "a97" / "rl_s3_seed11"
    files = {name: b"x" for name in SHARD_FILES}
    got: list[tuple[str, dict, dict]] = []

    write_shard(
        files,
        _manifest(3, 11, 100),
        str(out),
        lambda name, fs, mm: got.append((name, fs, mm)),
        materialize=False,
    )

    assert not out.exists()
    assert not out.parent.exists(), "连节点目录都不该建"
    assert [name for name, _fs, _mm in got] == ["rl_s3_seed11"]
    assert got[0][1] is files, "交出去的就是同一份内存字节（不是复制后再落盘）"


def test_iter_shard_dirs_sees_memory_shards(tmp_path: Path) -> None:
    """内存 shard 必须进「本轮应训集」——否则发布端看不见它们。"""
    (tmp_path / "it5").mkdir()
    dirs = iter_shard_dirs(tmp_path, 5, mem=_mem())
    assert [p.name for p in dirs] == sorted(_mem())
    assert all(p.parent.name == MEM_SHARD_NODE for p in dirs)
    assert not any(p.exists() for p in dirs), "占位路径不该被创建"

    # 行宽版本门与磁盘腿同一条：缺 metrics_version 的 shard 在不匹配时被剔除。
    assert iter_shard_dirs(tmp_path, 5, mem=_mem(), metrics_version=9) == []


def test_data_fp_memory_and_disk_are_identical(tmp_path: Path) -> None:
    """同一份 manifest，内存账与盘上目录算出的 data_fp 必须**逐字相同**（D12 的前提）。"""
    mem = _mem()
    disk_dirs: list[Path] = []
    for name, mm in mem.items():
        d = tmp_path / "it5" / "dist" / "a97" / name
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps(mm), encoding="utf-8")
        disk_dirs.append(d)
    placeholders = [tmp_path / "it5" / "dist" / MEM_SHARD_NODE / n for n in mem]

    assert cm.data_fp(disk_dirs) == cm.data_fp(placeholders, mem=mem)


def test_trim_uses_memory_manifests(tmp_path: Path) -> None:
    """配额裁剪必须能只靠内存 manifest 做（盘上没有 manifest.json 可读）。"""
    mem = _mem()
    dirs = [tmp_path / "it5" / "dist" / MEM_SHARD_NODE / n for n in mem]

    kept, trimmed, kept_samples = _trim_shards_to_quota(dirs, 100, mem=mem)

    # stage3：seed11(100) 留、seed12(200) 裁（acc 已 ≥ 配额）；stage4：999 单独一关照留。
    assert [p.name for p in kept] == ["rl_s3_seed11", "rl_s4_seed13"]
    assert trimmed == 1
    assert kept_samples == 100 + 999


def test_settled_totals_counts_memory_shards(tmp_path: Path) -> None:
    """补波账：内存 shard 也要计入「本波已采多少」，否则永远不达标。"""
    (tmp_path / "it5").mkdir()
    totals = settled_stage_totals(tmp_path, "w1", mem=_mem())
    assert totals == {3: (2, 300), 4: (1, 999)}

    # 异血缘（wver 不同）⇒ 一份都不算（与 `_scan_shards` 同一条过滤）。
    assert settled_stage_totals(tmp_path, "other", mem=_mem()) == {}


def test_metrics_stats_accepts_memory_blobs(tmp_path: Path) -> None:
    """逐维统计的唯一来源改成内存字节后，shards 计数必须仍然对。"""
    arr = np.zeros((3, len(METRICS)), dtype=np.float32)
    buf = io.BytesIO()
    np.save(buf, arr)
    rec = metrics_stats(str(tmp_path), it=5, extra_metrics=[buf.getvalue()], write=False)
    assert rec["shards"] == 1
    assert rec["decision_steps"] == 2, "终局行必须被剔掉（m[:-1]）"


def test_finalize_writes_stage_est_and_estimator_reads_it(tmp_path: Path) -> None:
    """轮末只写几百字节的分关账；`est_agg=latest` 立刻能读到（盘上没有 shard 目录）。"""
    it_dir = tmp_path / "it5"
    it_dir.mkdir()

    finalize_round_shards(it_dir, _mem(), it=5)

    est = json.loads((it_dir / STAGE_EST_NAME).read_text(encoding="utf-8"))
    assert est == {"3": {"games": 2, "samples": 300}, "4": {"games": 1, "samples": 999}}
    assert trailing_stage_samples_per_game(tmp_path, [3, 4], agg="latest", fallback=0) == {
        3: 150,
        4: 999,
    }


def test_write_iter_actuals_writes_full_collection(tmp_path: Path) -> None:
    """零落盘腿的逐局画像落 `it<N>/per-game.json`：读方（dashboard）优先读它出四列。

    验证点：
    · 一个 it 一份文件（全采集，不去重——同 (stage,seed) 重试两份都留，去重交给读方 pickBest）；
    · 字段名与 `entryFromManifest` 逐字相同（`kills`/`ticks`/`playerDamageTaken`/`dims`…）；
    · 缺 (stage,seed) 的脏行被 `compact_per_game` 丢弃，不污染配对。
    """
    it_dir = tmp_path / "it7"
    it_dir.mkdir()

    pg = write_iter_actuals(it_dir, _rich_mem())

    assert pg is not None
    pg_path, pg_n = pg
    assert pg_path == it_dir / PER_GAME_NAME
    assert pg_n == 4
    rows = json.loads(pg_path.read_text(encoding="utf-8"))
    assert len(rows) == 4, "两份重试的 (3,11) + (3,12) + (4,13)；缺身份的脏行被丢"
    by_key = {(r["stage"], r["seed"]): r for r in rows}
    assert (3, 11) in by_key and (3, 12) in by_key and (4, 13) in by_key
    # 全采集：两份 (3,11) 都在（compact_per_game 不去重，读方 pickBest 才去重）。
    assert sum(1 for r in rows if (r["stage"], r["seed"]) == (3, 11)) == 2
    e = by_key[(3, 12)]
    assert e["kills"] == 12 and e["ticks"] == 400 and e["outcome"] == "base_destroyed"
    assert e["playerDamageTaken"] == 50 and e["startLives"] == 1 and e["enemyTotal"] == 20
    # dims.loot 以 {loot:...} 形态保留（读方推道具掉落用）。
    assert e["dims"] == {"loot": 0.7}


def test_write_iter_actuals_skips_when_no_data(tmp_path: Path) -> None:
    """纯离线回传轮 `mem` 为空、盘上无 shard ⇒ 不写（绝不拿空数组覆盖回传腿已写好的文件）。"""
    it_dir = tmp_path / "it3"
    it_dir.mkdir()
    # 模拟回传腿已落好的 per-game.json，不能被空写覆盖。
    existing = it_dir / PER_GAME_NAME
    existing.write_text("[{\"stage\":1,\"seed\":1}]", encoding="utf-8")

    assert write_iter_actuals(it_dir, {}) is None
    assert existing.read_text(encoding="utf-8") == "[{\"stage\":1,\"seed\":1}]"


def test_finalize_also_writes_per_game_for_zero_disk(tmp_path: Path) -> None:
    """`finalize_round_shards` 现在顺带写逐局画像（零落盘腿四列的唯一数据源）。"""
    it_dir = tmp_path / "it8"
    it_dir.mkdir()

    finalize_round_shards(it_dir, _rich_mem(), it=8)

    rows = json.loads((it_dir / PER_GAME_NAME).read_text(encoding="utf-8"))
    assert len(rows) == 4


def test_pack_segmented_never_reads_the_placeholder_dir(tmp_path: Path) -> None:
    """打包只拼 `presegmented`：占位目录不存在也必须打出与盘上**同一份字节**。"""
    name = "rl_s3_seed11"
    seg = shard_segment(name, {"obs.npy": b"obs", "metrics.npy": b"met"})
    pre = {name: compress_segment(seg)}
    zip_path = tmp_path / "payload.tar.xz"

    _pack_segmented(
        [tmp_path / "it5" / "dist" / MEM_SHARD_NODE / name],
        zip_path,
        extra_files=[],
        perturb=None,
        member_bytes=None,
        shard_extra=None,
        chunks=1,
        presegmented=pre,
    )

    import lzma

    assert zip_path.read_bytes() == pre[name] + compress_segment(bytes(1024))
    assert lzma.decompress(pre[name])
