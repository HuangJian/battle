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

#: **可选** shard 文件（2026-10-20，B1 v6）：出现了才允许，**不强制要求**。
#:
#: v6 的三头 shard 多落这两列（`hold` 的动作与 logp），legacy 5/2 shard 没有它们。
#: 校验分成两半：
#:   · **本表 = 宽容侧**：`validate_result` 只拒绝"清单外且不在本表"的多余文件；
#:     不要求 v6 shard 必须带这两列 —— 强制要求会让**所有历史 legacy shard 被反向拒收**。
#:   · **强校验在加载端**：`worker/ppo/np_core.py::_check_mask_width` 用
#:     `a_hold.npy` 的存在性推掩码宽度（9 vs 7），缺失/不一致即响亮报错。
#:     ⇒ 这里的宽松不会让"半个 v6 shard"混过去。
OPTIONAL_SHARD_FILES = (
    "a_hold.npy",
    "lp_hold.npy",
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
    got = set(files.keys())
    if got != set(want):
        # 多出来的只允许是 OPTIONAL_SHARD_FILES（v6 的 a_hold/lp_hold）；
        # 缺的仍然一律拒收（必需清单是硬的）。
        extra = sorted(got - set(want) - set(OPTIONAL_SHARD_FILES))
        lack = sorted(set(want) - got)
        if extra or lack:
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


def manifest_bytes(manifest: dict) -> bytes:
    """shard 的 `manifest.json` 字节 —— **唯一**序列化口径（落盘与 payload 段共用）。

    为什么必须只有一处（2026-10-27，plan/payload-zero-disk）：零落盘方案在 dispatch 腿
    收到内存里的 shard 时就打好 tar 段，成员里必须有 `manifest.json`（`unpack_payload`
    只把**带 manifest.json 的子目录**当 shard，`worker.resume.walk_shard_dirs` 同理）。
    那份字节必须与 `write_shard` 落盘的**逐字节相同**，否则「内存段 == 盘段」这条判据只在
    Linux 成立：文本模式的 `\n` 在 Windows 上会翻成 `\r\n`。所以这里显式产出 LF 字节、
    `write_shard` 以二进制写 —— 同一份 manifest 在任何平台字节相同（payload 的 sha 不再随
    打包机漂）；JSON 读取端（resume / metrics_stats / iter_shard_dirs）只看解析值。
    """
    return json.dumps(dict(manifest), indent=2).encode("utf-8")


def write_shard(files: dict, manifest: dict, out_dir: str, on_receive=None) -> None:
    """校验通过后的唯一落盘出口：目录名沿用 rl_s{si}_seed{seed} 布局。

    `on_receive(name, files, manifest)`（2026-10-27，plan/payload-zero-disk §3.1）：在**落盘
    之前**把字节交出去一次（`name` = `os.path.basename(out_dir)` = `rl_s{stage}_seed{seed}`）。
    dispatch 腿的 shard 本来就在内存里（`fetch_task` 的 `files`）——调用方（`ShardSegments.add`）
    可以当场打段+压好，打包时不必再读回来重压。放在本函数而不是调用点：①「唯一落盘出口」
    是「哪份字节进了 shard 目录」的唯一定义处，挂在这里的读者不可能漏掉某条写盘路；
    ② 顺序天然是「先交字节、后写盘」。缺省 `None` = 逐字节旧行为。

    2026-09-03 修正：补写 manifest.json——M1 metrics 方案下 engine 加载器
    （ppo.engine.load_shard → _reward_from_metrics）需要 outcome/score/metrics_version
    在**落盘目录内**（分布式/self-node 局的单局 manifest 此前只存在于 fetch 返回的
    内存对象，落盘即丢 → 被当成 timeout 错标，奖励错算）。
    queue_local 路径由 exporter 直接写盘不受影响；此处补齐 dist 路径两侧同规。

    manifest.json 的字节口径见 `manifest_bytes`（二进制写：跨平台同字节）。
    """
    os.makedirs(out_dir, exist_ok=True)
    # ★ B1 v6（2026-10-20）：**可选项也要落盘**。
    # 原先这里只写 `_shard_files_for(manifest)`（必需清单）⇒ 节点上传的
    # `a_hold.npy`/`lp_hold.npy` 被**静默丢弃**，落盘的 shard 于是有 9 宽 mask
    # 却没有 hold 两列 ⇒ 云 worker 加载时 `_check_mask_width` 直接炸：
    #   `mask.npy 形状 (620, 9) 与动作空间不符：a_hold.npy 不在 ⇒ 期望 (7,)`
    # （那条校验是在**正确报警**，根因在写盘侧 —— 丢文件比报错更隐蔽：
    #  若没有那条校验，worker 会按 7 宽读 9 宽 mask，静默错位。）
    names = list(_shard_files_for(manifest))
    names += [n for n in OPTIONAL_SHARD_FILES if n in files]
    if on_receive is not None:
        on_receive(os.path.basename(out_dir), files, manifest)
    for name in names:
        val = files[name]
        raw = val if isinstance(val, (bytes, bytearray)) else base64.b64decode(val, validate=True)
        with open(os.path.join(out_dir, name), "wb") as f:
            f.write(raw)
    # 2026-09-05 修复（F8.3 / plan/remote-ppo-architecture.md §11）：此前**双写**
    # manifest.json（先紧凑再 indent=2，第二次覆盖第一次）——冗余 IO + 双写窗口
    # 无谓暴露（中途崩溃留半写文件）。只保留 indent=2 写（与 TS 侧 exporter 同规），
    # 磁盘产物字节不变（旧代码最终落盘的就是 indent=2 版本）。
    # 2026-10-27：改二进制写（见 `manifest_bytes`）——Windows 上不再把 `\n` 翻成 `\r\n`，
    # 解析值不变；换来的是「同一份 manifest 在任何平台字节相同」。
    with open(os.path.join(out_dir, "manifest.json"), "wb") as f:
        f.write(manifest_bytes(manifest))
