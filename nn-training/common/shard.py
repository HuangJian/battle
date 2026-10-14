"""common/shard.py — shard 清单 + 远端结果容器校验 + 唯一落盘出口（S5 第十一刀，2026-09-27）。

从 `common/distribution.py` 搬出（**逐字节不动**）：per-tick / 意图 / BC 三份 shard 清单、
`BC_COLLECTOR`、`_shard_files_for` / `validate_result` / `write_shard`。
`common/distribution.py` 留 `X as X` 门面 ⇒ 全仓 `common.distribution.validate_result(...)` 与
`from common.distribution import write_shard` 一行不改（容器格式契约见 `common.distribution` 头部）。

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
    # v4（plan/policy-spatial-head.plan.md S0-b）：POLICY_EXTRA(9) f4。
    # 严格集合校验 ⇒ 旧节点（无该文件）被**拒收**（MAJOR 4 的云端快拒门）。
    "extra.npy",
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


# ---------------- wire-diet：per-tick RL shard 的传输形态（2026-10-14，plan/wire-diet.plan.md §3）
# 与上面三份清单并存的原因：那三份是**落盘**口径（节点采完写进 traj 目录的形态），这里是
# **payload 里传出去的**形态（打包端可选瘦身后的形态）。两者只差两个面：奖励从哪来、哪些字段降精度。
#
# ⚠ 住 `common/` 而不是读取端（`worker.ppo.np_core`）：**两个所有者**都要逐字一致的名字 ——
# 写端 `remote.payload_slim`（hub 侧，**不得** import `worker.*`，否则 DAG 层号要连坐）与
# 读端 `worker.ppo.np_core`。所以名字只能有一处定义，而唯一两边都够得着的地方就是这里。
#
# `plan/rl-training-config.md` §4.2：per-tick shard 的 reward 原由 TS 落盘改成读取端按公式算
# （TS 只落 `metrics.npy`，[N+1,76] f8：N 个决策快照 + 1 个终局快照）。**若只有** `reward.npy`
# （打包端已算好，wire-diet §3 P1）⇒ 读端走无 metrics 的字段表 + 血缘校验，见
# `worker.ppo.np_core.load_shard` / `_reward_from_npy`。
METRICS_NAME = "metrics.npy"
#: 打包端算好的奖励（f32，形状 (N,)，dtype 与读取端 `astype(f32, copy=False)` 对齐）。
REWARD_NAME = "reward.npy"
#: `reward.npy` 必须带的**血缘声明**（每 shard 一份，~100 B）。奖励在**别处**算，「it 用错」
#: 在数值上与正常 reward 无从区分 —— 只有这份 meta 抓得到（评审 P1-4）。
REWARD_META_NAME = "reward_meta.json"
#: `reward.npy` 的内容口径版本（算式/落盘 dtype 变化时 +1；与 `metrics_version` 正交）。
#: 读端**必须**校验：节点的 code.zip 可能比 hub 旧/新，版本不符的奖励数值上无从分辨。
REWARD_VERSION = 1

#: 奖励面的两条合法形态（`discover_shards` 的 `need_any`：obs **且** 二者之一）。
RL_REWARD_FILES: tuple[str, ...] = (METRICS_NAME, REWARD_NAME)

#: wire-diet §3 P2：**允许**降精度传输（f32 → f16）的 per-tick RL 字段。
#: 入选判据（实测 `tmp/wire-audit6.txt` §3）：绝对误差上界 ≤1e-3 且都是 O(1) 输入。
#: ⛔ `value` **不在表内** —— 它是 GAE 基线，误差经 `δ_t = r + γV' − V` → GAE → `adv_norm`
#: 放大，而只省 3.3%（55,868 B）：收益小、风险不对称。
#: 加字段/改名时：这里 + `plan/wire-diet.plan.md` §3 P2 的误差实测表**必须**同 commit 改
#: （`tests/remote/test_payload_slim.py` 拿本表与读端的 `_RL_SHARD_SPEC` 对账）。
RL_F16_FIELDS: tuple[str, ...] = ("scalars", "extra", "lp_move", "lp_fire")


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
