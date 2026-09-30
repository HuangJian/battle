"""common/manifest —— **job manifest 的契约**：声明什么、怎么校验、怎么指纹（S5 第九刀，2026-09-27）。

从 `common/protocol.py` 整块搬出（**逐字节不动**）。这里是「一份 job 的规格」的唯一实现：

* **词汇**：`PROTO`（协议版本）· 角色 `ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/`ROLE_FIELD`
  （`manifest.role` 的合法值域，与请求方角色两处共用）· `MANIFEST_KINDS`（kind 全集）·
  `KIND_ROLES`（kind → 归属角色，**唯一**映射）+ `role_of`（manifest.role 优先、kind 兜底）；
* **schema**：`MANIFEST_REQUIRED`（必填，缺失 fail fast）· `MANIFEST_OPTIONAL_DEFAULTS` ·
  `MANIFEST_BC_EXEMPT`/`MANIFEST_BC_EXTRA` · `MANIFEST_ITER_EXTRA`/`MANIFEST_RUN_EXTRA`
  + `normalize_manifest`（校验 + 归一化）；
* **产物契约**：`ROLLOUT_SCRIPT`/`EVAL_SCRIPT`/`ROLLOUT_SCRIPTS`（TS 导出器路径 = TS↔Python 的产物契约）·
  `ITER_OUT_REL`/`ITER_NODE_LABEL` · `PLAN_NAME`/`PLAN_PROTO` · `RUN_NODE_LABEL`/`RUN_MAX_ITERS_HARD_CAP`；
* **rollout 规格与语料指纹**：`validate_rollout_spec`（argv 白名单 / 相对路径不越界）·
  `shard_name`/`parse_shard_name` · `data_fp_entries`/`data_fp`/`d14_corpus_match`（数据集身份）·
  `iter_declared_entries`/`iter_expected_data_fp`。

**为什么单独成家**：这是「这份活**要什么**」——声明面与校验面；与「字节怎么编解码」（`wire_codec`）、
「这份活是谁」（`job_identity`）、「语料怎么打包」（`payload`）、「结果合不合法」（`protocol.validate_result`）
都是不同的所有者。读者要加一个 manifest 字段 / 一种 kind，只需看这一个文件。

⚠ **角色词汇只搬了「值域」**：`ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/`ROLE_FIELD` 住这里（它们是
`manifest.role` 的词）；`ROLE_HEADER`/`ROLE_HEADER_VALUE`（HTTP 头名）与 `role_from_header`（会话角色解析）
仍住 `protocol`——那是**请求头**面，不是 manifest。

依赖面 = stdlib（`hashlib` / `json` / `os` / `re` / `collections.abc` / `pathlib`）+ `common.errors`。
**不** import `common.protocol`（无环）。`common/protocol.py` 保留 `X as X` 门面 ⇒ 历史 import 一行不改。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from common.errors import ProtocolError

PROTO = 1  # 协议版本：未知字段忽略，缺失必填 fail fast（D1）

ROLE_OFFLINE = "offline"
ROLE_ONLINE = "online"
#: 两个角色（`manifest.role` 与请求方角色的合法值域，两处共用）。
ROLES: tuple[str, ...] = (ROLE_ONLINE, ROLE_OFFLINE)
#: job manifest 里的归属键名。
ROLE_FIELD = "role"

#: manifest 必填字段（附录 A；缺失任一 → 校验失败）
MANIFEST_REQUIRED = (
    "proto",
    "runId",
    "it",
    "job_id",
    "commit",
    "code_sha256",  # Python 源码 zip sha256（hub 启动时打包，替代 git 同步）
    "course",  # 课程 jsonc 全文快照（reward_spec 重建输入，D6）
    "course_fp",  # 课程文件 sha256（语料血缘，D14）
    "reward_formula",
    "formula_hash",
    "metrics_version",
    "gamma",
    "lam",
    "mode",
    "seed",  # per-job numpy 种子 = hash(runId,it,init_weights_fp)（D5）
    "epochs",
    "mb",
    "lr",
    "init_weights_fp",
    "data_fp",
    "payload_sha256",
)
#: kind=bc（行为克隆 job）免除的 PPO 专有必填（BC 无 reward/γ/λ 语义）；
#: 追加必填 `arch`（bc|student）。plan/bc-cloud-integration.plan.md §1。
MANIFEST_BC_EXEMPT: tuple[str, ...] = (
    "reward_formula",
    "formula_hash",
    "metrics_version",
    "gamma",
    "lam",
)
MANIFEST_BC_EXTRA: tuple[str, ...] = ("arch",)
#: 任务类型（2026-09-13 BC 整合）：缺省 "ppo" = 既有 per-tick PPO job（wire 兼容——
#: 旧 hub 产出的 manifest 无此键，一律按 ppo 校验）。"bc" = 行为克隆 job（语料 npy
#: shard 进 payload，云端跑 train/bc.py，回传 BC 权重；无 reward/γ/λ 语义）。
#: plan/bc-cloud-integration.plan.md §1。
MANIFEST_OPTIONAL_DEFAULTS: dict[str, object] = {
    "kind": "ppo",
    "kl_coef": 0.0,
    "kl_cap": None,  # None = 不覆盖，由 policy.streamKlCap 决定
    "ent_coef": None,  # 2026-09-11：None = 用引擎常量 ENT_COEF（0.01）；0.0 是合法值
    "adv_norm": "auto",
    "normalize_ret": False,  # R5：ret 归一；缺失（旧 hub）= 关
    "kickstart_kl": 0.0,  # §363：BC 缰绳系数（已衰减）；0 = 关
    "ref_weights_b64": "",  # §363：BC ref 权重 base64；空 = 无
    "ref_weights_fp": "",  # §363：上者 sha256（有字节时必对上）
    "shuffle": True,
    "schedule_raw": [],  # ppo_schedule 解析前原始表（审计）
    "opt_init": "",  # base64 tar（model/opt/numpy RNG）；空 = 无（首轮）
    # 严格样本量配额（target_transitions 路线）：训练侧逐关只收前 ceil(target/关数) 步。
    # 0 = 历史行为（全收）；缺失（旧 hub 产出的 manifest）= 0 ⇒ wire 兼容。
    "per_stage_quota": 0,
    # ---- M2（plan/remote-wire-remediation §4.2）内容寻址 blob ----
    #: raw（编码前）opt tar 的 sha256。有它时 `opt_init` 可为空——节点按 sha 去
    #: blob_cache 取，未命中才下载。**必须进 OPTIONAL**（wire 兼容红线：缺失必填会
    #: 把旧 hub/旧 worker 的 job 全部拒收）。
    "opt_sha": "",
    #: raw ref 权重的 sha256（= ref_weights_fp；同义别名，便于按名寻址）。
    "ref_sha": "",
    #: raw 字节数（预检/日志；0 = 未知）。
    "opt_bytes": 0,
    "ref_bytes": 0,
    # ---- demo 混 batch（x20 后续）：demo bank 内容寻址 + BC 系数 ----
    #: demo bank npz 的 sha256；空 = 关（worker 走 getattr 缺省，老行为）。
    "demo_sha": "",
    #: demo BC 辅 loss 系数；0.0 = 关。
    "demo_bc_coef": 0.0,
    #: 每 PPO minibatch 步抽的 demo 样本数；0 = 关。
    "demo_per_mb": 0,
    #: 本轮是否走瘦身路径（审计/A-B；False = 内联老字段，逐字节回到旧行为）。
    "slim": False,
}

# ------------------------------------------------------------------ 归属（role）
# job 的「该由哪块盘执行」是 **job 自己的属性**（2026-09-25，本节头的 plan）：发布时定死、
# 此后不随课程 mode 漂移。为什么必须落成字段而不是每次现算：mode 是易变的（hub 内存表、
# 控制台可热切），用它当判据 ⇒ 切一次模式，历史 job 的归属就跳一次（事故现场：两小时前
# 缺 bun 被拒的那个 `kind=run` job 在切成在线后被 tailscale 盘领走）。
#: 合法 kind 全集（`validate_manifest` 与 `KIND_ROLES` 共用一份；穷举由用例钉住）。
MANIFEST_KINDS: tuple[str, ...] = ("ppo", "bc", "iter", "run")
#: kind → 归属角色（**唯一**映射）：`run`（整段自主）= 离线盘的活；其余（逐轮/整轮/BC）
#: 都是在线盘的活。加新 kind 必须同时给出角色，否则 `test_role_routing` 当场红。
KIND_ROLES: dict[str, str] = {
    "run": ROLE_OFFLINE,
    "iter": ROLE_ONLINE,
    "ppo": ROLE_ONLINE,
    "bc": ROLE_ONLINE,
}


def role_of(manifest: Mapping[str, object]) -> str:
    """job 归属角色：`manifest.role` 优先；缺失/非法 ⇒ 按 `kind` 兜底。

    **不拒单**：旧 job（发布早于本字段）不能因为缺字段变孤儿（它们的 kind 就已经说明了
    归属）；未知 kind 也回落 online（`validate_manifest` 已在入口挡掉未知 kind）。
    """
    raw = str(manifest.get(ROLE_FIELD) or "").strip().lower()
    if raw in ROLES:
        return raw
    return KIND_ROLES.get(str(manifest.get("kind") or "ppo"), ROLE_ONLINE)


# ------------------------------------------------------------------ M3: kind=iter
# plan/remote-wire-remediation.plan.md §5.2：新 job kind =「一整轮」——节点自己跑
# rollout（bun 调 exporter 产 shard）→ 接着跑既有 PPO 链路 → 只回传权重/report。
# 与 BC 同一条 kind 通道（照 MANIFEST_BC_* 的先例），mode 红线互斥。
#: kind=iter 追加必填：TS 运行时 zip 的 sha256 + rollout 规格。
MANIFEST_ITER_EXTRA: tuple[str, ...] = ("ts_code_sha256", "rollout")
# ------------------------------------------------------------------ TS 导出器路径
# `tools/sim/*.ts` 的真实文件名 —— 这是 **TS↔Python 的产物契约**，故与 wire 协议同住一层：
# 长驻池按**同质入口**建（`worker/serve_pool.py`）、本机停等 cmd 由此拼（`biz/cmd.py`）、
# 云机找 TS 根时按它探路（`remote/run_loop.py`）—— 各处抄一份字面量就等着谁先漂。
#: 逐局 rollout 导出器（kind=iter / kind=run）。
ROLLOUT_SCRIPT = "tools/sim/export-rl-rollout.ts"
#: 离线评估导出器（云机评估；`biz/eval_local.py` 建 cmd 时也用它）。
EVAL_SCRIPT = "tools/sim/export-eval-game.ts"
#: goal / intent 两个半 MDP 导出器（本机 `biz/cmd.py` 按模式选它们；节点侧 argv 白名单不放行）。
GOAL_SCRIPT = "tools/sim/export-goal-rollout.ts"
INTENT_SCRIPT = "tools/sim/export-intent-rollout.ts"

#: 长驻池的**同质入口**（2026-09-28）：一个 worker 按每行首个 **mode token** 分派到任一导出器。
#:
#: 为什么（用户点名，`docs/nn/runtime-opt.md` §27.10/§28）：原先池按导出器分「腿」——worker 的入口在
#: 起进程时就烧死（`bun <exporter>.ts --serve`），于是「用哪个导出器」成了池的**形状**，进而长出
#: 「预热得猜腿 / 换腿要退役空闲 worker / 混模式的轮拿不到池」这一整类问题。而这些导出器都是
#: `runServe(main)` 的**无状态外壳**（每局新建 World），分家是历史包袱 ⇒ 把「选哪个导出器」从
#: **起进程时的 argv** 挪到 **每行的 token**。
SERVE_ANY_SCRIPT = "tools/sim/serve-any.ts"

#: 导出器 → mode token —— `tools/agent/persist-pool.ts::PERSIST_MODE_BY_ENTRY` 的 Python 镜像。
#: 值域必须与 `tools/sim/serve-any.ts::SERVE_MODES` 的键集逐字相同（那是**协议面**：改一侧=
#: 改另一侧）；TS 侧有对拍用例（`tests/serve-any.test.ts`），Python 侧由
#: `tests/test_remote_serve_pool.py` 钉住本表被真的送进了 worker 的 stdin。
SERVE_MODE_BY_SCRIPT: Mapping[str, str] = {
    ROLLOUT_SCRIPT: "rollout",
    EVAL_SCRIPT: "eval",
    GOAL_SCRIPT: "goal",
    INTENT_SCRIPT: "intent",
}


def serve_mode_for(argv0: str) -> str | None:
    """这一局的 argv[0] 该送哪个 mode token；`None` = 这个脚本不进池（未知/不支持的导出器）。

    按**规范化路径**比（正/反斜杠、前导 `./`、绝对路径都认）——与 `serve_pool` 原来的 `owns()`
    同一套容错，只是判据从「是不是本池那个脚本」换成「是不是池认识的导出器」。
    """
    key = str(argv0).replace("\\", "/").lstrip("./")
    mode = SERVE_MODE_BY_SCRIPT.get(key)
    if mode is not None:
        return mode
    base = key.rsplit("/", 1)[-1]
    for script, token in SERVE_MODE_BY_SCRIPT.items():
        if script.rsplit("/", 1)[-1] == base:
            return token
    return None

#: TS 源码 zip 里允许出现的 exporter（argv[0] 白名单）。**只**放行 rollout 采集器：
#: argv 来自 hub（可信方），但白名单让「协议字段被误当命令执行」不可能发生。
ROLLOUT_SCRIPTS: tuple[str, ...] = (ROLLOUT_SCRIPT,)
#: rollout 规格里 argv 内嵌路径的允许前缀（job 目录内的相对路径，防越界）。
#: 端口无关：worker 一律以 job 目录为 cwd 执行 argv。
ITER_OUT_REL = "w"
#: 上云 rollout 写进 shard manifest 的 `node` 标签（hub 侧 argv 里的字面量）。
#: 与本地 `local` 区分（可溯源），同时是 §5.5① 逐位对拍的基准——验收时本地也用
#: 同一 argv（同标签）重跑，逐字节比对才成立。
ITER_NODE_LABEL = "node"

# ------------------------------------------------------------------ 半离线: kind="run"
# 「云端整段自主」job：hub 只在交接时给一次（课程 + 初始权重 + 代码 + **计划**），
# 节点从此不依赖 hub —— 自己按计划把剩余轮次跑完（rollout + PPO 全在节点），逐轮
# 把权重/指标写进本地产物目录（Kaggle /kaggle/working、Colab Drive），可打包下载、
# 可跨会话续跑。
#
# 与 kind=iter 的关系（**不是 fork，是延长**）：本 job 自己的那一轮（`it`）与 kind=iter
# **逐字段同构**（`rollout` + `ts_code_sha256` 必填、data_fp = 该轮声明集），节点走的
# 也是同一条执行链；区别只在尾巴——跑完本轮到 `plan.json` 继续把后续轮次自己跑完。
# 于是「离线轮」与「hub 监管轮」的行/产物/校验口径完全一致。
#: 计划文件名（payload 内，与 init_weights.json 同层；sha 进 manifest 而**不是**全文——
#: argv 模板 + 逐轮对集可达百 KB 量级，不该让 hub 每轮轮询都解析一遍）。
PLAN_NAME = "plan.json"
PLAN_PROTO = 1
#: kind=run 追加必填：kind=iter 的两项 + 计划的 sha256。
MANIFEST_RUN_EXTRA: tuple[str, ...] = (*MANIFEST_ITER_EXTRA, "plan_sha256")
#: 半离线轮写进 shard manifest 的 `node` 标签（与 `node`/`local` 区分：可溯源到
#: 「这一批局是云端自主段跑的」）。
RUN_NODE_LABEL = "run"
#: 计划的**硬上界**（防一份手写/损坏的计划把节点按在机上一整天）。命令行可用
#: `--run-max-iters` 再降；计划的 end_it 一律按其与 iters_total 的交集钳制。
RUN_MAX_ITERS_HARD_CAP = 500

def normalize_manifest(m: dict) -> dict:
    """校验 + 归一化 job manifest（proto=1：缺失必填 fail fast，未知字段忽略）。

    返回浅拷贝的 manifest（必填齐全、可选字段带默认值）。校验失败抛
    `ProtocolError`，错误信息指明缺失字段。
    """
    if not isinstance(m, dict):
        raise ProtocolError(f"manifest 必须是对象，收到 {type(m).__name__}")
    kind = str(m.get("kind", "ppo") or "ppo")
    if kind not in MANIFEST_KINDS:
        # 允许列表与 `KIND_ROLES` 同一份（`MANIFEST_KINDS`）——加 kind 而忘了给角色会当场红。
        raise ProtocolError(
            f"kind={kind!r} 未知（只认 {'|'.join(repr(k) for k in MANIFEST_KINDS)}）——拒收"
        )
    required = [
        k for k in MANIFEST_REQUIRED if not (kind == "bc" and k in MANIFEST_BC_EXEMPT)
    ]
    if kind == "bc":
        required += list(MANIFEST_BC_EXTRA)
    if kind == "iter":
        required += list(MANIFEST_ITER_EXTRA)
    if kind == "run":
        required += list(MANIFEST_RUN_EXTRA)
    missing = [k for k in required if k not in m]
    if missing:
        raise ProtocolError(f"manifest 缺失必填字段: {missing}")
    if int(m.get("proto", -1)) != PROTO:
        raise ProtocolError(f"proto={m.get('proto')!r} != {PROTO}（协议版本不匹配）")
    out = dict(m)
    for k, v in MANIFEST_OPTIONAL_DEFAULTS.items():
        out.setdefault(k, v)
    # 标量类型校验（fail fast，防拼错/串位）——按 kind 实际持有的键校验
    if not isinstance(out["runId"], str) or not out["runId"]:
        raise ProtocolError("runId 必须是非空 str")
    for k in ("it", "epochs", "mb", "metrics_version"):
        if k not in out:
            continue
        if not isinstance(out[k], int) or isinstance(out[k], bool):
            raise ProtocolError(f"{k} 必须是 int，收到 {out[k]!r}")
    for k in ("gamma", "lam", "lr"):
        v = out.get(k)
        if v is None:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ProtocolError(f"{k} 必须是 float，收到 {v!r}")
        if float(v) <= 0:
            raise ProtocolError(f"{k} 必须 > 0，收到 {v!r}")
    for k in (
        "commit",
        "code_sha256",
        "course",
        "course_fp",
        "mode",
        "seed",
        "init_weights_fp",
        "data_fp",
        "payload_sha256",
        "job_id",
    ):
        if not isinstance(out[k], str) or not out[k]:
            raise ProtocolError(f"{k} 必须是非空 str")
    # kind 红线（plan/bc-cloud-integration.plan.md §1）：ppo 仅 per-tick（v1 原红线），
    # bc 仅 mode="bc"——两种任务类型在 mode 通道上互斥，杜绝串型。
    if kind == "bc":
        if out["mode"] != "bc":
            raise ProtocolError(f"kind=bc 要求 mode='bc'，收到 {out['mode']!r}（拒收）")
        if out["arch"] not in ("bc", "student"):
            raise ProtocolError(f"bc manifest arch 必须是 'bc'|'student'，收到 {out['arch']!r}")
    else:
        if out["mode"] != "per-tick":
            raise ProtocolError(
                f"mode={out['mode']!r} != 'per-tick'（v1 红线：仅 per-tick 课程支持远程）"
            )
    if kind == "iter":
        # M3 mode 互斥红线：iter 只承载 per-tick rollout（goal/intent 导出器不在
        # 上云范围内——它们的 rollout 语义未在协议里建模）。ts_code_sha256 必须是
        # 非空 str（下面的通用非空串循环已覆盖），rollout 规格逐字段校验。
        if out["mode"] != "per-tick":
            raise ProtocolError(
                f"kind=iter 要求 mode='per-tick'，收到 {out['mode']!r}（M3 只上云 per-tick rollout）"
            )
        out["rollout"] = validate_rollout_spec(out["rollout"])
    # 归属字段（2026-09-25）：**可选**（旧 job 没有它 ⇒ `role_of` 按 kind 兜底），
    # 但一旦存在就必须合法——一个拼错的 role 静默变成 online 正是那种「看不见」的失败。
    role_raw = out.get(ROLE_FIELD)
    if role_raw not in (None, "") and str(role_raw) not in ROLES:
        raise ProtocolError(f"{ROLE_FIELD}={role_raw!r} 未知（只认 {list(ROLES)}）——拒收")
    if not isinstance(out.get("normalize_ret", False), bool):
        raise ProtocolError(f"normalize_ret 必须是 bool，收到 {out.get('normalize_ret')!r}")
    if not isinstance(out.get("kickstart_kl", 0.0), (int, float)) or isinstance(
        out.get("kickstart_kl", 0.0), bool
    ):
        raise ProtocolError(f"kickstart_kl 必须是 number，收到 {out.get('kickstart_kl')!r}")
    if float(out.get("kickstart_kl", 0.0)) < 0:
        raise ProtocolError("kickstart_kl 必须 >= 0")
    psq = out.get("per_stage_quota", 0)
    # bool 是 int 的子类 ⇒ 必须先挡 bool（True/False 混进来会让配额变成 1/0 而不报错）。
    if isinstance(psq, bool) or not isinstance(psq, int) or psq < 0:
        raise ProtocolError(f"per_stage_quota 必须是非负整数，收到 {psq!r}")
    return out


# ------------------------------------------------------------------ data_fp


#: data_fp 条目 = (shard 目录名, wver, stage, seed)。
DataFpEntries = Sequence[tuple[str, str, int, int]]


def data_fp_entries(entries: DataFpEntries) -> str:
    """data_fp 的核心：对**声明**的 shard 条目集做 sha256（排序后逐字段拼接）。

    单独抽出来是为了 M3：上云 rollout 的 shard 由节点现产，hub 侧没有目录可读，
    只能对「声明集」（argv 里逐局的 stage/seed + 约定的 wver）算期望值。节点跑完后
    对**实产**目录调 `data_fp()`（同一函数、同一拼接顺序）再比对——两侧算法同源，
    所以「相等」严格等价于「实产集 == 声明集」，任一侧漏局/多局都会露出来。
    """
    ents = sorted(entries, key=lambda e: e[0])
    h = hashlib.sha256()
    for name, wver, stage, seed in ents:
        h.update(name.encode("utf-8"))
        h.update(wver.encode("utf-8"))
        h.update(str(stage).encode("utf-8"))
        h.update(str(seed).encode("utf-8"))
    return h.hexdigest()


def d14_corpus_match(job_course_fp: str, job_corpus_fp: str, shard_manifest: dict) -> bool:
    """D14 装载校验的比对规则（DECISIONS §2026-09-13-level-extraction · 全文 → docs/nn/training-stack.md §25）。

    双侧都有 corpus_fp（语料身份 = env+reward 解析值语义哈希）⇒ 比 corpus_fp——
    预算/路径/注释类课程 mid-run 编辑只动 course_fp（文件血缘），不得触发拒收。
    任一侧缺 corpus_fp（legacy shard / 旧 job）⇒ 回退文件血缘 course_fp 逐字比对。

    ★ 为什么它住 protocol 而不是 worker：这条规则有**两个**执行端——云 worker 装载时
    逐 shard 判（拒收），hub 打包时也逐 shard 判（`hub_client.iter_shard_dirs` 只挑
    匹配的进 payload）。两份实现漂开就是一个死循环：发布端放进一个异血缘 shard、云端
    整份 job 拒收、训练轮等一个永不回传的结果（2026-09-20 事故：c6-chip it16 的 payload
    里混进了 21 个旧血缘 shard，云 worker 报 `D14 course_fp 不匹配` 整份退回）。
    同一函数 = 同一判据，打包集恒等于云端会接受的集合。
    """
    s_corpus = str(shard_manifest.get("corpus_fp", "") or "")
    if job_corpus_fp and s_corpus:
        return job_corpus_fp == s_corpus
    return str(shard_manifest.get("course_fp", "")) == job_course_fp


def data_fp(shard_dirs: Sequence[str | Path]) -> str:
    """D1 data_fp：sha256(按字典序排列的 shard 相对路径 + 各 manifest {wver,stage,seed})。

    shard_dirs：本轮应训 shard 目录（绝对/相对路径均可，按 basename 字典序排序——
    排序以 shard 目录名（rl_s{stage}_seed{seed}）为键，与打包/装载口径一致）。
    任一侧重算必须得到同一值（hub 打包时写入、验收时本地重算比对——防 ABA，D12）。
    """
    entries: list[tuple[str, str, int, int]] = []
    for d in shard_dirs:
        p = Path(d)
        mp = p / "manifest.json"
        try:
            with open(mp, encoding="utf-8") as f:
                mm = json.load(f)
        except (OSError, ValueError) as e:
            raise ProtocolError(f"data_fp: 读 {mp} 失败: {e}") from e
        entries.append(
            (
                p.name,  # rl_s{stage}_seed{seed}
                str(mm.get("wver", "")),
                int(mm.get("stage", -1)),
                int(mm.get("seed", -1)),
            )
        )
    return data_fp_entries(entries)


# ------------------------------------------------------------------ M3 rollout 规格
#
# 为什么 argv 是规格的 SSOT（而不是 stages/seeds/difficulty 一堆字段）：
# hub 侧本来就有 `biz/cmd.build_rollout_cmd` 拼装本机 rollout 命令（三导出器 + 课程
# 覆盖 + D14 血缘，单源）。上云时**用同一个函数**、只把路径换成 job 目录内的相对
# 路径，再把它交给节点执行 ⇒ 「节点跑的采集」与「本机跑的采集」逐字节同命令，
# 逐位对拍（计划 §5.5①）是构造性质而不是靠人去对对参数。新增一个字段就等于在
# 协议里复制一份 cmd.py 的知识，迟早漂。

#: argv 里必须携带的相对路径 flag（job 目录为 cwd）。
_ITER_PATH_FLAGS: tuple[str, ...] = ("--out", "--weights")
#: rollout 规格默认值（缺省即旧行为，additive）。
ROLLOUT_SPEC_DEFAULTS: dict[str, object] = {
    "wver": "",
    "workers": 1,
    # 0 = plan 没给 ⇒ **节点兜底硬顶**（`iter_rollout.DEFAULT_GAME_TIMEOUT_SEC`）。
    # 2026-09-22 改口径：旧注释写的是「0 = 不设单局超时（本机历史行为）」，也就是一个卡住的
    # bun 子进程可以永远等下去——it34 实测 rollout 中途停了 651s（10 局卡死、日志只有计数）。
    # 本机 rollout 不受影响（它不看这个字段），云端必须有个上限。
    "game_timeout_sec": 0.0,
    "bun": "bun",  # 节点侧 bun 可执行名（PATH 查找）；空 = 用节点默认
}


def shard_name(stage: int, seed: int) -> str:
    """shard 目录名（与 `export-rl-rollout.ts` 的 `rl_s${si}_seed${seed}` 同源）。"""
    return f"rl_s{stage}_seed{seed}"


def parse_shard_name(name: str) -> tuple[int, int] | None:
    """`rl_s{stage}_seed{seed}` -> (stage, seed)；不匹配返回 None（不抛）。"""
    m = re.fullmatch(r"rl_s(\d+)_seed(\d+)", str(name or ""))
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


#: Windows 盘符前缀（`C:` / `c:`）——绝对值与 drive-relative 都算，跨平台一律拒。
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _iter_flag_value(argv: list[str], flag: str) -> int:
    """取 argv 里 `flag` 的单个整数值；缺失/重复/非整数一律 ProtocolError。

    重复出列（`--stages 0 --stages 1`）也是个坑：导出器只会吃到一个，声明集却可能
    按另一个算——声明集必须就是**实际会执行**的那一组，所以重复直接拒收。
    """
    vals = [argv[i + 1] for i, a in enumerate(argv) if a == flag]
    if len(vals) != 1:
        raise ProtocolError(f"rollout.argv 的 {flag} 必须恰好出现 1 次，实得 {len(vals)} 次")
    try:
        return int(vals[0])
    except (TypeError, ValueError):
        raise ProtocolError(f"rollout.argv 的 {flag} 必须是单个整数，收到 {vals[0]!r}") from None


def _iter_rel_path(value: object, flag: str) -> str:
    """校验 argv 里的路径参数是 job 目录内的相对路径（拒绝对路径 / `..` / 空）。

    跨平台（2026-09-17 修）：`os.path.isabs` / `Path.is_absolute` 只看**当前内核**的
    规则——Linux 上 `C:/weights.json` 两者都判 False，于是 Windows 盘符路径能静默过门，
    到节点上却变成宿主盘上的文件（或直接跑挂）。节点的 cwd 契约不随着 hub 的内核变，
    所以盘符（含 drive-relative `c:x`）与 UNC 在任何平台都在这里拒收。
    """
    s = str(value or "")
    if not s:
        raise ProtocolError(f"rollout.argv 的 {flag} 不能为空")
    if (
        os.path.isabs(s)
        or Path(s).is_absolute()
        or s.startswith(("~", r"\\"))  # ~ 家目录 / UNC（`\\\\host\\share`）
        or _WIN_DRIVE_RE.match(s)  # `C:/x` / `C:\\x` / `C:x`（drive-relative）
    ):
        raise ProtocolError(
            f"rollout.argv 的 {flag}={s!r} 必须是相对路径（节点以 job 目录为 cwd）"
        )
    parts = Path(s).parts
    if ".." in parts:
        raise ProtocolError(f"rollout.argv 的 {flag}={s!r} 不得包含 ..（越界）")
    return s


def validate_rollout_spec(spec: object) -> dict:
    """校验 + 归一化 manifest 的 `rollout` 子字典（kind=iter）。失败抛 ProtocolError。

    归一化后 shape：
      {argv: [[str, ...], ...], wver: str, workers: int, game_timeout_sec: float, bun: str}
    argv 每项 = 一局的完整命令，**不含** bun 路径（worker 用自己的 bun；argv[0] 是
    exporter 脚本，受 `ROLLOUT_SCRIPTS` 白名单约束）。
    """
    if not isinstance(spec, dict):
        raise ProtocolError(f"manifest.rollout 必须是对象，收到 {type(spec).__name__}")
    allowed = set(ROLLOUT_SPEC_DEFAULTS) | {"argv"}
    out = dict(ROLLOUT_SPEC_DEFAULTS)
    for k, v in spec.items():
        if k not in allowed:
            raise ProtocolError(
                f"manifest.rollout 未知字段 {k!r}（允许：{sorted(allowed)}——拒绝，非忽略）"
            )
        out[k] = v
    raw_argv = spec.get("argv")
    if not isinstance(raw_argv, list) or not raw_argv:
        raise ProtocolError("manifest.rollout.argv 必须是非空数组（每局一项）")
    argv_out: list[list[str]] = []
    seen: set[tuple[int, int]] = set()
    for i, item in enumerate(raw_argv):
        if not isinstance(item, list) or len(item) < 2:
            raise ProtocolError(f"manifest.rollout.argv[{i}] 必须是长度 >=2 的字符串数组")
        argv = [str(x) for x in item]
        if not all(isinstance(x, str) for x in item):
            raise ProtocolError(f"manifest.rollout.argv[{i}] 含非字符串元素（拒收）")
        script = argv[0].replace("\\", "/").lstrip("./")
        if script not in ROLLOUT_SCRIPTS:
            raise ProtocolError(
                f"manifest.rollout.argv[{i}][0]={argv[0]!r} 不在白名单 {list(ROLLOUT_SCRIPTS)}（拒收）"
            )
        argv[0] = script
        st = _iter_flag_value(argv, "--stages")
        sd = _iter_flag_value(argv, "--seeds")
        for flag in _ITER_PATH_FLAGS:
            if flag not in argv:
                raise ProtocolError(f"manifest.rollout.argv[{i}] 缺 {flag}（节点无法定位产物）")
            j = argv.index(flag)
            if j + 1 >= len(argv):
                raise ProtocolError(f"manifest.rollout.argv[{i}] 的 {flag} 没有取值")
            argv[j + 1] = _iter_rel_path(argv[j + 1], flag)
        if (st, sd) in seen:
            raise ProtocolError(f"manifest.rollout.argv 重复声明同一局 (stage={st}, seed={sd})")
        seen.add((st, sd))
        argv_out.append(argv)
    out["argv"] = argv_out
    out["wver"] = str(out["wver"] or "")
    # workers：缺席 = 1（默认值）；**显式 0 拒收**（不静默改成 1——那会让「配错了」
    # 与「没配」长得一样，而并发配错正是那种「跑起来了但完全不是你要的」错误）。
    _w: object = 1 if out["workers"] is None else out["workers"]
    if isinstance(_w, bool):
        raise ProtocolError(f"manifest.rollout.workers 必须是整数，收到 {_w!r}")
    if isinstance(_w, int):
        _wn = _w
    elif isinstance(_w, str) and _w.isdigit():
        _wn = int(_w)
    else:
        raise ProtocolError(f"manifest.rollout.workers 必须是整数，收到 {_w!r}")
    if _wn < 1:
        raise ProtocolError("manifest.rollout.workers 必须 >= 1（显式 0 拒收，不静默改成 1）")
    out["workers"] = _wn
    _t: object = 0.0 if out["game_timeout_sec"] is None else out["game_timeout_sec"]
    if isinstance(_t, bool) or not isinstance(_t, (int, float)):
        raise ProtocolError(f"manifest.rollout.game_timeout_sec 必须是数字，收到 {_t!r}")
    if float(_t) < 0:
        raise ProtocolError(
            "manifest.rollout.game_timeout_sec 必须 >= 0（0 = 节点兜底硬顶，不是不限）"
        )
    out["game_timeout_sec"] = float(_t)
    out["bun"] = str(out["bun"] or "bun")
    return out


def iter_declared_entries(spec: dict) -> list[tuple[str, str, int, int]]:
    """rollout 规格 → 声明的 data_fp 条目集（与 argv 一一对应，不可能漂）。"""
    wver = str(spec.get("wver") or "")
    ents: list[tuple[str, str, int, int]] = []
    for argv in spec["argv"]:
        st = _iter_flag_value(list(argv), "--stages")
        sd = _iter_flag_value(list(argv), "--seeds")
        ents.append((shard_name(st, sd), wver, st, sd))
    return ents


def iter_expected_data_fp(spec: dict) -> str:
    """kind=iter 的 manifest.data_fp = 对**声明集**的 data_fp（hub 算、节点复算比对）。"""
    return data_fp_entries(iter_declared_entries(spec))
