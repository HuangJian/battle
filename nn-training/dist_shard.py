"""dist_shard.py — shard 清单 + 远端结果容器校验 + 唯一落盘出口（S5 第十一刀，2026-09-27）。

从 `dist_common.py` 搬出（**逐字节不动**）：per-tick / 意图 / BC 三份 shard 清单、
`BC_COLLECTOR`、`_shard_files_for` / `validate_result` / `write_shard`。
`dist_common.py` 留 `X as X` 门面 ⇒ 全仓 `dist_common.validate_result(...)` 与
`from dist_common import write_shard` 一行不改（容器格式契约见 `dist_common` 头部）。

红线（原样随行）：远端结果必须先过 `validate_result()` 再落进 traj_dir ——
`discover_rl_shards()` 对已落盘目录是无条件递归扫描的，落盘之后没有任何兜底。
"""

from __future__ import annotations

import base64
import json
import os

SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "a_move.npy",
    "a_fire.npy",
    "lp_move.npy",
    "lp_fire.npy",
    "value.npy",
    # plan/rl-training-config.md §4.2：per-tick shard 奖励改由 Python 公式引擎按
    # metrics.npy（[N+1,21] f8）计算 —— TS 侧不再落 reward.npy。
    "metrics.npy",
    "done.npy",
    "mask.npy",
)

# M8 意图 RL shard 清单（export-intent-rollout.ts 产物）——意图步 semi-MDP：
# inject（prev one-hot 8 + duration）与 dt（窗口时长 tick，变步长 GAE 用）替换
# a_move/a_fire/lp_move/lp_fire；mask 为 8 类死类掩码。
INTENT_SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "inject.npy",
    "a_intent.npy",
    "lp_intent.npy",
    "value.npy",
    "reward.npy",
    "done.npy",
    "mask.npy",
    "dt.npy",
)

# BC 语料 shard 清单（export-godai-bc.ts 产物，BC 整合 2026-09-13；与
# nn-training/data/npyio.py SHARD_FILES + OPTIONAL_FILES 同表——bc.py 装载口径）。
# manifest.json 不在此表（write_shard 单独落）。
BC_SHARD_FILES = (
    "obs.npy",
    "scalars.npy",
    "actions.npy",
    "masks.npy",
    "conditions.npy",
    "returns.npy",
)


#: BC 语料任务的 manifest collector 标识（`_shard_files_for` 的清单判据）。
BC_COLLECTOR = "BC-GOD"


# ---------------- 结果校验（先验后落盘的红线所在） ----------------
def _shard_files_for(manifest: dict) -> tuple:
    """BC 语料 shard（collector=BC-GOD）用 BC_SHARD_FILES；意图 RL shard（collector=
    INTENT-RL）用 INTENT_SHARD_FILES；其余 per-tick SHARD_FILES。"""
    if manifest.get("collector") == BC_COLLECTOR:
        return BC_SHARD_FILES
    if manifest.get("collector") == "INTENT-RL" or "a_intent.npy" in manifest:
        return INTENT_SHARD_FILES
    return SHARD_FILES


def validate_result(
    manifest: dict,
    files: dict,
    expected_wver: str,
    expected_pairs: set[tuple[int, int]],
    seen_keys: set[tuple[int, int]],
) -> str | None:
    """返回 None=通过；否则给出拒收原因。"""
    if not isinstance(manifest, dict):
        return "manifest is not an object"
    if manifest.get("wver") != expected_wver:
        return f"wver mismatch: got {manifest.get('wver')!r}"
    key = (manifest.get("stage"), manifest.get("seed"))
    if key not in expected_pairs:
        return f"unexpected (stage,seed)={key}"
    if key in seen_keys:
        return f"duplicate (stage,seed)={key}"
    # BC wins-only 败局：合法"跳过"结果（kept:false 空容器），不是任务失败。
    if manifest.get("collector") == BC_COLLECTOR and manifest.get("kept") is False:
        if files:
            return f"bc loss-skip shard must carry no files (got {sorted(files)})"
        return None
    want = _shard_files_for(manifest)
    if set(files.keys()) != set(want):
        extra = sorted(set(files) - set(want))
        lack = sorted(set(want) - set(files))
        return f"file set mismatch (extra={extra}, missing={lack})"
    for name, val in files.items():
        try:
            # v2 容器值为原始 bytes；v1 旧 agent 值为 base64 str——双模兼容。
            raw = (
                val if isinstance(val, (bytes, bytearray)) else base64.b64decode(val, validate=True)
            )
        except Exception:
            return f"{name}: invalid base64"
        if len(raw) == 0:
            return f"{name}: empty payload"
    return None


def write_shard(files: dict, manifest: dict, out_dir: str) -> None:
    """校验通过后的唯一落盘出口：目录名沿用 rl_s{si}_seed{seed} 布局。

    2026-09-03 修正：补写 manifest.json——M1 metrics 方案下 engine 加载器
    （ppo.engine.load_shard → _reward_from_metrics）需要 outcome/score/metrics_version
    在**落盘目录内**（分布式/self-node 局的单局 manifest 此前只存在于 fetch 返回的
    内存对象，落盘即丢 → 被当成 timeout 错标，奖励错算）。queue_local 路径由 exporter
    直接写盘不受影响；此处补齐 dist 路径两侧同规。
    """
    os.makedirs(out_dir, exist_ok=True)
    for name in _shard_files_for(manifest):
        val = files[name]
        raw = val if isinstance(val, (bytes, bytearray)) else base64.b64decode(val, validate=True)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(raw)
    # 2026-09-05 修复（F8.3 / plan/remote-ppo-architecture.md §11）：此前**双写**
    # manifest.json（先紧凑再 indent=2，第二次覆盖第一次）——冗余 IO + 双写窗口
    # 无谓暴露（中途崩溃留半写文件）。只保留 indent=2 写（与 TS 侧 exporter 同规），
    # 磁盘产物字节不变（旧代码最终落盘的就是 indent=2 版本）。
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
