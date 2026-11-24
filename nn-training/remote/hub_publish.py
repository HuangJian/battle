"""发布腿（磁盘 IPC）：把一轮的 payload + manifest 落到 `job_root/<job_id>/` 并记 `job_pending`。

**为什么单独一个模块**（2026-11-23）：`remote/hub_client.py` 撞了 999/1000 的 LOC 预算
（`tests/test_python_loc_budget.py`），任何 +1 行都红，而它里面还压着 `verify_and_land` /
`verify_and_land_bc` / `merge_memory_shards` 三处待收的宽签名。发布段是**唯一能整块抬走**
的一段：它只依赖打包好的字节与 `remote/publish_spec.py` 的数据行，与 HTTP 面、作废族、
校验落位之间没有共享的可变状态。

**为什么是 L2 而不是 L1**：它要 `remote.hub_client` 的 `cancel_stale_jobs` 与
`_previous_job_id`（job 身份守卫要读上一轮的 job），所以拓扑秩 = 1 + 1 = **2**。
方向是单向的：`hub_client` **不** import 本模块（否则就是环），因此这里**不留门面** ——
调用点（`trainer/loop_remote_job.py`、`trainer/bc_loop.py`、`hub/smoke_loopback.py`）
直接 `from remote.hub_publish import PublishSpec, publish_job`。

**三相不变**（2026-11-18 拆分时就定的边界，搬动是逐字搬运）：`_publish_facts`（校验 +
指纹 + 切内容寻址的 blob 字节）→ `_publish_stage`（manifest 预建 + job 身份守卫 + 附带文件
落临时区）→ `_publish_commit`（打包 + 落盘 + 账本）。判据、日志、写盘顺序一条未动。

**patch 目标变了**：`_publish_*` 的调用点解析在**本模块** ⇒ 从今天起打 `remote.hub_client.X`
对这些名字是空操作（同 `job_round` 那一族刀的记账）。
"""

from __future__ import annotations

import base64
import json
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from common.fs import append_jsonl
from common.hashing import sha256_bytes, sha256_file
from common.logutil import tag_logger
from common.platform_utils import rmtree_best_effort
from common.protocol import (
    BLOB_DEMO,
    BLOB_INIT,
    BLOB_OPT,
    BLOB_REF,
    FAIL_NAME,
    PAYLOAD_NAME,
    PLAN_NAME,
    ROLE_FIELD,
    ROLES,
    TS_CODE_NAME,
    blob_path,
    collision_rows,
    data_fp,
    iter_expected_data_fp,
    job_seed,
    normalize_manifest,
    role_of,
    validate_rollout_spec,
)
from common.protocol import (
    job_id as make_job_id,
)
from remote.hub_client import _previous_job_id, cancel_stale_jobs
from remote.hub_http import HubClientError
from remote.payload_pack import PackSpec
from remote.payload_pack import pack_payload_zip as pack_payload_zip
from remote.publish_spec import PublishFacts, PublishSpec, PublishStaged

if TYPE_CHECKING:  # 只在类型面（运行时由 wire_diet 分支内延迟 import，见 hub_client 模块头）
    from remote.payload_slim import ShardSlim

#: 与 `remote/hub_client._log` 同一个 tag（"hub"）—— 搬模块不改日志前缀。
_log = tag_logger("hub", lambda: time)


# ------------------------------------------------------------------ 发布（磁盘 IPC）


def _publish_facts(spec: PublishSpec) -> PublishFacts:
    """第一相：校验 kind/rollout_spec、算 data_fp/init 指纹、切出内容寻址的 blob 字节。"""
    import remote.opt_delta as opt_delta

    kind = str(spec.kind or "ppo")
    schedule_raw = spec.schedule_raw if spec.schedule_raw is not None else []
    rollout_spec = spec.rollout_spec
    if rollout_spec is not None:
        rollout_spec = validate_rollout_spec(rollout_spec)
        # kind=run 是 kind=iter 的**延长**（同一轮语义 + 计划尾巴）：两者都要
        # rollout 规格与 TS 运行时，校验口径完全共享。
        if kind not in ("iter", "run"):
            raise HubClientError(
                f"rollout_spec 只对 kind='iter'/'run' 有意义，收到 kind={kind!r}"
            )
        if not spec.ts_code_sha256:
            raise HubClientError(f"kind={kind!r} 必须带 ts_code_sha256（节点要它定位 TS 运行时）")
        if spec.shard_dirs:
            raise HubClientError(
                f"kind={kind!r} 不接受本地 shard（实得 {len(spec.shard_dirs)} 个）——"
                "上云轮由节点现产，混着发会双份采集"
            )
    if kind == "run":
        if not spec.plan_bytes:
            raise HubClientError(
                "kind='run' 必须带 plan_bytes（半离线段的唯一输入：没有计划，节点跑完本轮"
                "就不知道下一轮跑哪些局）——拒发"
            )
        if rollout_spec is None:
            raise HubClientError("kind='run' 必须带 rollout_spec（本 job 自己那一轮的采集规格）")
    # 1) data_fp（D1：排序 shard 路径 + manifest {wver,stage,seed}；iter = 声明集）
    fp = (
        iter_expected_data_fp(rollout_spec)
        if rollout_spec
        else data_fp(spec.shard_dirs, mem=spec.mem)
    )
    # 2) init_weights_fp（fencing：云回传的 init_weights_fp 必须等于当前 args.out 指纹；
    #    BC 无 warm-start → 恒 "bc" 占位）
    init_weights_fp = sha256_file(spec.init_weights_path) if spec.init_weights_path else "bc"
    # 3) opt/ref 内容寻址（M2 B3）：raw 字节 sha256；未开瘦身则内联 base64（旧口径）
    opt_raw = opt_delta.opt_tar_bytes(spec.ckpt_remote_dir)
    opt_sha = sha256_bytes(opt_raw) if opt_raw else ""
    use_opt_blob = bool(spec.slim and opt_sha)
    ref_raw = b""
    if spec.slim and spec.ref_weights_b64:
        try:
            ref_raw = base64.b64decode(spec.ref_weights_b64.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            ref_raw = b""
    # ref_sha 口径与 ref_weights_fp 同一（调用方对 raw 权重取 sha）——有字节才寻址。
    use_ref_blob = bool(spec.slim and spec.ref_weights_fp and ref_raw)
    # demo bank 内容寻址：bank 静态（同 run 内逐轮同字节），sha 键天然跨轮复用。
    # 非 slim 下无内联退路（3MB 进 manifest 不可接受）——响亮拒绝，调用方开 slim。
    demo_raw = spec.demo_bank_bytes or b""
    if demo_raw and not spec.slim:
        raise HubClientError("demo bank 要求 slim=1（内容寻址；非 slim 无内联退路）——拒发")
    use_demo_blob = bool(spec.slim and demo_raw)
    demo_sha = sha256_bytes(demo_raw) if demo_raw else ""
    # opt-blob-diet（2026-09-24，§3.1/§3.4）：权重的**内容寻址段**。sha 直接复用
    # `init_weights_fp`——它的定义就是 `sha256_file(args.out)`，与内容寻址的要求逐字一致，
    # 所以 **manifest 不新增字段**（加一个同义的 `init_sha` 只会造第二份真相）。
    # raw 字节 = 该轮 `init_weights.json` 原样（= 上一轮 `result.weights_json` 解码后的字节）。
    init_raw = (
        Path(spec.init_weights_path).read_bytes()
        if (spec.init_weights_path and Path(spec.init_weights_path).exists())
        else b""
    )
    use_init_blob = bool(spec.slim and init_raw)
    opt_init = (
        ""
        if use_opt_blob
        else (base64.b64encode(opt_raw).decode("ascii") if opt_raw else "")
    )
    return PublishFacts(
        kind=kind,
        schedule_raw=schedule_raw,
        rollout_spec=rollout_spec,
        fp=fp,
        init_weights_fp=init_weights_fp,
        opt_raw=opt_raw,
        opt_sha=opt_sha,
        use_opt_blob=use_opt_blob,
        ref_raw=ref_raw,
        use_ref_blob=use_ref_blob,
        demo_raw=demo_raw,
        demo_sha=demo_sha,
        use_demo_blob=use_demo_blob,
        init_raw=init_raw,
        use_init_blob=use_init_blob,
        opt_init=opt_init,
    )


def _stage_manifest(spec: PublishSpec, facts: PublishFacts) -> tuple[dict[str, object], bool]:
    """4) manifest 预建（`payload_sha256` 占位）：纯数据，不碰盘。返回 (m, 是否 replay 语料)。"""
    from remote.replay_corpus import converter_sha

    _is_replay = str(spec.corpus_fmt or "full") == "replay"
    m: dict[str, object] = {
        "proto": 1,
        "runId": spec.run_id,
        "it": spec.it,
        "commit": spec.commit,
        "code_sha256": spec.code_sha256,
        "course": spec.course,
        "course_fp": spec.course_fp,
        "corpus_fp": spec.corpus_fp,
        "mode": spec.mode,
        "epochs": spec.epochs,
        "mb": spec.mb,
        "lr": spec.lr,
        "kl_coef": spec.kl_coef,
        "kl_cap": spec.kl_cap,
        "adv_norm": spec.adv_norm,
        "normalize_ret": bool(spec.normalize_ret),
        "kickstart_kl": float(spec.kickstart_kl),
        "ent_coef": spec.ent_coef,
        "ref_weights_b64": "" if facts.use_ref_blob else spec.ref_weights_b64,
        "ref_weights_fp": spec.ref_weights_fp,
        "shuffle": spec.shuffle,
        "schedule_raw": facts.schedule_raw,
        "per_stage_quota": int(spec.per_stage_quota),
        "init_weights_fp": facts.init_weights_fp,
        "opt_init": facts.opt_init,
        "opt_sha": facts.opt_sha if facts.use_opt_blob else "",
        "ref_sha": str(spec.ref_weights_fp) if facts.use_ref_blob else "",
        "opt_bytes": len(facts.opt_raw) if facts.use_opt_blob else 0,
        "ref_bytes": len(facts.ref_raw) if facts.use_ref_blob else 0,
        "demo_sha": facts.demo_sha if facts.use_demo_blob else "",
        "demo_bc_coef": float(spec.demo_bc_coef),
        "value_anchor": float(spec.value_anchor),
        "demo_per_mb": int(spec.demo_per_mb),
        "slim": bool(spec.slim),
        "data_fp": facts.fp,
        "payload_sha256": "",
        # P2：语料格式 + 转换器契约进身份（full 省略 = 旧 job_id 逐字节不变）。
        **(
            {
                "corpus_fmt": "replay",
                "replay_converter_sha256": converter_sha(spec.replay_converter_path or ""),
            }
            if _is_replay
            else {}
        ),
    }
    # job 身份：幂等键的五个分量在上面那份字面量里就齐了（下面只补**非键**字段），
    # 所以刻意在这里算出来——守卫要在**任何写盘之前**用它。
    m["job_id"] = make_job_id(m)
    return m, _is_replay


def _stage_scaffold(
    spec: PublishSpec,
    m: dict[str, object],
    job_root_p: Path,
    tmp_extra_dir: Path,
    extra_files: list[Path],
    is_replay: bool,
) -> None:
    """4b) 发布端守卫（**任何写盘之前**）+ 临时区 + 转换器 bundle；拒发时不留任何痕迹。"""
    from remote.replay_corpus import attach_converter

    # 4b) **发布端守卫**（2026-09-24 job 身份事故，plan/job-identity-collision.plan.md §3.3）：
    #     同一条 job 身份不许被**别的课程**占用。判据的唯一实现在 `protocol.collision_rows`。
    #     位置：job_id 算完之后、**任何写盘之前**——拒发时不留 job 目录、不留账本行、
    #     连 `.extra_tmp` 都不建（「半份 job」会让控制台显示一条永远等不到工人的 pending）。
    #     `course_fp` 进键之后正常发布撞不出来 ⇒ 这是哨兵：手写 manifest / 回灌历史 job /
    #     跨 hub 搬目录等旁路一旦造出同身份，必须在发布那一刻响亮，而不是等污染被看出来。
    _conflicts = collision_rows(job_root_p, m)
    if _conflicts:
        _who = "、".join(
            f"{r['course'] or '<单课程>'}（job={r['job_id']}）" for r in _conflicts
        )
        raise HubClientError(
            f"job 身份已被别的课程占用——拒发（本课 {job_root_p.parent.name or '<单课程>'} "
            f"course_fp={str(spec.course_fp)[:12]}… job_id={m['job_id']!s}；占用方：{_who}）。"
            "同一个 (runId, course_fp, it, init_weights_fp, data_fp) 只能属于一门课："
            "两门课内容完全相同（course_fp 相同）时会共享一个 job 身份，"
            "而 hub 无法在两者间区分归属（两个训练轮会读到同一份结果）。"
            "处置：给其中一门课不同的课程文件（改注释也算），或换 runId 重启那条腿。"
        )
    # ⚠ 这个 mkdir 刻意**挪到守卫之后**（下方 4b）：守卫要求「拒发时不留任何痕迹」，
    # 而 `.extra_tmp` 也算痕迹（半份 job 目录比没有 job 更危险）。
    tmp_extra_dir.mkdir(parents=True, exist_ok=True)
    # trainer 预构建的转换器 bundle：随 payload 根下发（worker 只跑不建；full = no-op）。
    if is_replay:
        attach_converter(extra_files, tmp_extra_dir, spec.replay_converter_path or "")


def _stage_payload_extras(
    spec: PublishSpec,
    facts: PublishFacts,
    m: dict[str, object],
    extra_files: list[Path],
    tmp_extra_dir: Path,
) -> None:
    """payload 附带文件（ts_code / plan / init_weights）：写进 `m` 与 `extra_files`。"""
    kind = facts.kind
    rollout_spec = facts.rollout_spec

    if rollout_spec is not None:
        m["ts_code_sha256"] = str(spec.ts_code_sha256)
        m["rollout"] = rollout_spec
        # ★ opt-blob-diet（2026-09-24，§3.4）：kind=iter 不再把 payload 撑成「带权重」。
        # 节点侧 rollout 要的那份权重改由 `init` blob 提供（worker 的 `_resolve_weights`
        # 会在 **rollout 之前**把它落到 `job_dir/init_weights.json`）。
        # 代价 = 发布端多一条守卫：没有权重就根本不该发这份活（否则云上必炸在半路）。
        if not (spec.init_weights_path and Path(spec.init_weights_path).exists()):
            raise HubClientError(
                f"kind={kind!r} 必须有 init 权重（rollout 要用它，PPO 也要用它读 arch）："
                f"init_weights_path={spec.init_weights_path!r} 为空或不存在——拒发"
            )
    if kind == "run":
        assert spec.plan_bytes is not None  # 上面已拒发（类型收窄给 mypy）
        # 计划随 payload 走（与 init_weights.json 同层）：节点解包后 `verify_plan_file(job_dir, …)`
        # 就地拿到它。manifest 只记 sha——全文可达百 KB，不该让 hub 每轮轮询都解析一遍。
        plan_copy = tmp_extra_dir / PLAN_NAME
        plan_copy.write_bytes(spec.plan_bytes)
        extra_files.append(plan_copy)
        m["plan_sha256"] = sha256_bytes(spec.plan_bytes)
    # M2（B4）：有 opt blob 时不传 init_weights.json（worker 从 opt 恢复即完整
    # model+Adam）。echo 冒烟要保持回显能力，keep_init_weights 时照旧带上。
    #
    # ★ opt-blob-diet（2026-09-24，§3.4）：「不带」的门是 **`use_opt_blob`**，不是
    #   `use_init_blob`：`use_opt_blob = slim AND 有 opt 字节` ⇒ 有 opt blob 时必然也有
    #   init blob（权重文件存在 ⇒ `init_weights_fp` 是 64-hex ⇒ 落盘）。所以
    #   `not use_opt_blob or keep_init_weights` 已经等价于「两个 blob 都齐才省掉 payload
    #   里那份」，而且把首轮/冷启动（无 opt blob）留成了 W0 —— 那条路是导出包与
    #   `bundle.py` 依赖的（plan §2.2：离线冗余是特性，不是浪费）。
    if spec.init_weights_path and (not facts.use_opt_blob or spec.keep_init_weights):
        init_copy = tmp_extra_dir / "init_weights.json"
        shutil.copyfile(spec.init_weights_path, init_copy)
        extra_files.append(init_copy)
    # M2（B2）：不再打 payload 内 opt_init.tar.b64 —— 全仓无读取方（opt 只从
    # manifest["opt_init"] / opt_sha blob 取）。


def _stage_keys(
    spec: PublishSpec, facts: PublishFacts, job_root_p: Path, m: dict[str, object]
) -> None:
    """5) 两套键（BC / PPO）+ 续接/形态位 + 非身份附加键；`m` 原地改。"""
    import remote.opt_delta as opt_delta
    import remote.weights_delta as weights_delta

    kind = facts.kind
    if kind == "bc":
        # BC manifest：免除 PPO 专有键（与 protocol.MANIFEST_BC_EXEMPT 同表），并入 extra
        for k in ("kl_coef", "kl_cap", "adv_norm", "normalize_ret", "kickstart_kl", "ent_coef"):
            m.pop(k, None)
        m.pop("ref_weights_b64", None)
        m.pop("ref_weights_fp", None)
        m.pop("opt_init", None)
        m.pop("schedule_raw", None)
        for k in ("opt_sha", "ref_sha", "opt_bytes", "ref_bytes", "slim"):
            m.pop(k, None)
        for k in ("demo_sha", "demo_bc_coef", "demo_per_mb"):
            m.pop(k, None)
        for k, v in (spec.extra or {}).items():
            m[k] = v
    else:
        m["reward_formula"] = spec.reward_formula
        m["formula_hash"] = spec.formula_hash
        m["metrics_version"] = spec.metrics_version
        m["gamma"] = spec.gamma
        m["lam"] = spec.lam
    # ---- R0（M0，plan/wire-diet.plan.md §3 R0）：把「上一轮是哪个 job」写进 manifest ----
    # 判据在 worker 侧（`remote/course_cache`）：`manifest.opt_from == 本机 latest.job_id`
    # ⇒ 本机那份 f32 全量副本就是**权威链的延续点**（零字节、零降质），否则回退内容寻址。
    # 为什么由**这里**写而不是让 worker 自己推「it−1 邻接」：身份映射的键就是 job_id，
    # 让读取端再推一遍 = 第二份真相。扫不到（首轮 / 上一轮已 prune / 换机重开）⇒ **不添键**
    # （旧端忽略未知键，但没有键才是“回退内容寻址”的准确表述）。
    _prev = _previous_job_id(job_root_p, spec.run_id, spec.course_fp, spec.it)
    if _prev:
        m["opt_from"] = _prev
    # ---- R3（M4）：opt 回传的**形态位**（2026-10-14，plan/wire-diet.plan.md §3 R3 / §12）----
    # 有 base（`use_opt_blob` = 真能发 delta）才写；否则**不添键** —— 对外发布路径的 manifest
    # 逐字节不变（与 `payload_fmt` 同纪律）。发布端写下、验收端就是它（兼容矩阵 §4，缺键 = 旧形态）。
    if facts.use_opt_blob:
        m["opt_fmt"] = opt_delta.OPT_DELTA_FMT
    # ---- R1（M3）：权重回传的**形态位**（同 opt_fmt 的纪律）----
    # 有 init 字节（= delta 的基准）才写；否则**不添键** —— 对外发布路径的 manifest 逐字节不变。
    # 基准就是本轮发出去的 init 权重（`init_weights_fp` = `sha256(这条字节)`）⇒ 验收端逐字节可对。
    if facts.use_init_blob:
        m["w_fmt"] = weights_delta.W_DELTA_FMT
    m["kind"] = kind
    m["seed"] = job_seed(spec.run_id, spec.it, facts.init_weights_fp)
    if spec.course_name:
        # P4-W2 归属（S9）：在 job_id 计算**之后**注入——幂等键不含短名，旧链字节不变；
        # normalize_manifest 允许未知/可选键，wire 兼容（D1：未知字段忽略）。
        m["course_name"] = str(spec.course_name)
    if spec.dispatch:
        # 同上：job 身份已定，传输腿的意图是**附加语义**不是身份成分。旧 hub/旧 worker
        # 忽略未知键 ⇒ 缺席即 pull，行为逐字节不变。
        m["dispatch"] = str(spec.dispatch)
    # 归属角色（2026-09-25，plan/online-offline-role-routing §2.1）：**发布时定死**的 job
    # 属性——hub 的派发/认领闸读它，不再读易变的「课程当前 mode」。
    # 与 `dispatch` 同位置（job_id 之后）：归属不是身份成分，重发同一轮不会因为归属变化而
    # 变成两个 job。缺省由 kind 推（同一份快照，不引入第二个读盘点）；显式 `role=` 可覆盖。
    # 旧 hub/旧 worker 忽略未知键 ⇒ 没这个字段的旧 job 由 `role_of` 按 kind 兜底。
    m[ROLE_FIELD] = spec.role if spec.role in ROLES else role_of({"kind": kind})


def _publish_stage(spec: PublishSpec, facts: PublishFacts) -> PublishStaged:
    """第二相：manifest 预建 + job 身份守卫 + payload 附带文件（plan/init）落临时区。

    四段（2026-11-24，长函数存量）：`_stage_manifest`（纯数据 + job_id）→
    `_stage_scaffold`（身份守卫 + 临时区 + 转换器）→ `_stage_payload_extras`（附带文件）→
    `_stage_keys`（BC/PPO 键集 + 续接/形态位）。顺序 = 原函数 `# 4)` 起的语句顺序：守卫仍在
    **任何写盘之前**，manifest 的键插入序（= payload 里那份占位副本的字节序）逐条未动。
    """
    job_root_p = Path(spec.job_root)
    extra_files: list[Path] = []
    tmp_extra_dir = job_root_p / ".extra_tmp"
    m, is_replay = _stage_manifest(spec, facts)
    _stage_scaffold(spec, m, job_root_p, tmp_extra_dir, extra_files, is_replay)
    _stage_payload_extras(spec, facts, m, extra_files, tmp_extra_dir)
    _stage_keys(spec, facts, job_root_p, m)
    jid = str(m["job_id"])
    jd = job_root_p / jid
    return PublishStaged(
        m=m,
        jid=jid,
        jd=jd,
        tmp_extra_dir=tmp_extra_dir,
        extra_files=extra_files,
    )


def _publish_commit(
    spec: PublishSpec, facts: PublishFacts, staged: PublishStaged, *, log=_log
) -> dict:
    """第三相：payload 打包 + blob/manifest 落盘 + 账本；返回归一化 manifest。"""
    m = staged.m
    jid = staged.jid
    jd = staged.jd
    rollout_spec = facts.rollout_spec
    # 5) 落盘 job 目录：payload.zip（zip 内 manifest 为占位副本——payload_sha256 尚
    #    未算出）→ 回填真实 sha → 权威 manifest.json（worker 以 job 记录校验，D1）。
    #    normalize_manifest 在回填后调用：payload_sha256 必填非空，占位空串会误拒。
    jd.mkdir(parents=True, exist_ok=True)
    zip_path = jd / PAYLOAD_NAME
    _slim: ShardSlim | None = None
    if spec.wire_diet:
        _slim = cast("ShardSlim | None", spec.diet_slim)
        if _slim is None:
            from remote.payload_slim import build_slim

            _slim = build_slim(
                reward_spec=spec.reward_spec,
                it=spec.it,
                metrics_version=spec.metrics_version,
                formula_hash=spec.formula_hash,
                log=log,
            )
    sha = pack_payload_zip(
        PackSpec(
            spec.shard_dirs, staged.extra_files, zip_path, m,
            member_bytes=(_slim.member_bytes if _slim is not None else None),
            shard_extra=(_slim.shard_extra if _slim is not None else None),
            presegmented=spec.presegmented,
            log=log,
        )
    )
    m["payload_sha256"] = sha
    if spec.presegmented:
        # 验收判据 3（plan §4）：纯分布式腿下两个数该相等（每个 shard 都在 rollout 期压过）。
        _hit = sum(1 for d in spec.shard_dirs if Path(d).name in spec.presegmented)
        log(
            f"[publish] zero-disk it{spec.it}: packed {_hit}/{len(spec.shard_dirs)} presegmented shard"
            "（命中段直接拼接，未读盘/未重压）"
        )
    if _slim is not None:
        # 形态自述（旧端忽略未知键）：读取端以「盘上有哪些文件」为准，这个键是发布端记录/取证面。
        # 什么都没改时**不添键**（对外发布路径的 manifest 逐字节不变）。
        log(f"[publish] {_slim.fmt_line()}（payload_sha256={sha[:12]}…）")
        if _slim.fmt:
            m["payload_fmt"] = _slim.fmt
    # M2 B3：blob 落盘——pull worker 经 /jobs/{id}/blob 取；push 由 push_client 判缓存
    # 后按需随 body 发送（读的就是这两个文件）。
    if facts.use_opt_blob:
        blob_path(jd, BLOB_OPT).write_bytes(facts.opt_raw)
    if facts.use_ref_blob:
        blob_path(jd, BLOB_REF).write_bytes(facts.ref_raw)
    if facts.use_demo_blob:
        blob_path(jd, BLOB_DEMO).write_bytes(facts.demo_raw)
    if facts.use_init_blob:
        blob_path(jd, BLOB_INIT).write_bytes(facts.init_raw)
    m = normalize_manifest(m)
    (jd / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    # 重发同一个 job（同幂等键 → 同 job_id，逐轮重试走的正是这条路）必须清掉上一次的
    # 确定性失败标记（2026-09-17）——否则重发出来的 job 在 hub 侧仍算「已失败」：
    # 可领取池排除它（无人重跑）、wait_job 立刻 410（重试无效）。旧标记只对旧那一次有效。
    stale_fail = jd / FAIL_NAME
    if stale_fail.exists():
        try:
            stale_fail.unlink()
            log(f"republished job {jid}: 清掉上一次的失败标记（重发即重试）")
        except OSError as e:
            log(f"WARN: 失败标记未清掉（{e}）——重发可能被 hub 当成已失败")
    # 复制 code.zip 到 job 目录（hub-server 的 GET /jobs/{id}/code 从此目录服务）
    if spec.code_zip_path:
        czp = Path(spec.code_zip_path)
        if czp.exists():
            shutil.copy2(czp, jd / "code.zip")
    # M3：ts_code.zip 同规（GET /jobs/{id}/ts_code）——内容寻址缓存的前提是节点能拿到
    # 原始字节并与 manifest.ts_code_sha256 对账。
    if rollout_spec is not None and spec.ts_code_zip_path:
        tzp = Path(spec.ts_code_zip_path)
        if not tzp.exists():
            raise HubClientError(f"kind='iter' 的 ts_code.zip 不存在: {tzp}")
        shutil.copy2(tzp, jd / TS_CODE_NAME)
    try:
        rmtree_best_effort(staged.tmp_extra_dir)
    except OSError:
        pass
    # 6) jsonl job_pending（磁盘 IPC；幂等去重——同 job_id 不重复追加）
    if spec.register:
        # 悬空 job 清理（§381）：发布前作废更早迭代/旧 runId 遗留的 pending job——
        # 否则 loop 重启后无 worker 期间滞留的旧 job 会在 GPU 上线时被全部补做。
        _n_cancelled = cancel_stale_jobs(spec.jsonl_path, spec.it, jid)
        if _n_cancelled:
            log(f"cancelled {_n_cancelled} stale job(s) with it ≤ {spec.it}（旧 runId 遗留，不再派发）")
        append_jsonl(
            spec.jsonl_path,
            {
                "event": "job_pending",
                "job_id": jid,
                "runId": spec.run_id,
                "it": spec.it,
                "ts": time.time(),
            },
        )
    else:
        log(f"job {jid} it{spec.it}: 不记账本/不进待领池（打包导出用，`register=False`）")
    log(
        f"published job {jid} it{spec.it}: "
        + (
            f"kind=iter games={len(rollout_spec['argv'])} "
            f"ts_code={str(spec.ts_code_sha256)[:12]}… "
            if rollout_spec
            else f"shards={len(spec.shard_dirs)} "
        )
        + f"data_fp={facts.fp[:12]}… payload={sha[:12]}…"
        + (f" init_blob={facts.init_weights_fp[:12]}…" if facts.use_init_blob else "")
    )
    if facts.use_init_blob:
        # payload diet 读数：这一刀省下的就是 tar 里那份重复的 model.pt
        # （实测 268,996 B/轮，见 plan/opt-blob-diet.plan.md §1.1）。
        log(
            f"it{spec.it}: payload diet —— 权重走 init blob（{len(facts.init_raw)} B raw，"
            f"sha={facts.init_weights_fp[:12]}…）不进 payload；tar 里不再有 model.pt"
        )
    return m


def publish_job(
    spec: PublishSpec,
    *,
    log=_log,
) -> dict:
    """打包 + 发布 job（磁盘 IPC）：job_root/<job_id>/ + jsonl job_pending 事件。

    所有字段走 `spec`（`PublishSpec`，见 `remote/publish_spec.py`）；`log` 只是出口
    回调，不是发布内容。返回已归一化 manifest（含 job_id / payload_sha256）。幂等：同 job_id
    （幂等键相同）已发布 → 覆盖 payload、不重复追加 pending。

    kind="iter"（M3）：payload 里**没有 shard**（只有 init_weights.json + 可选 blob），
    shard 由节点自己跑 rollout 现产；data_fp 改对**声明集**（argv 里的逐局 stage/seed
    + rollout.wver）算——节点跑完后对实产目录复算，两侧同函数，相等 ⇔ 实产集 ==
    声明集（计划 §5.5①）。

    三相拆分（2026-11-18，长函数存量第三十三刀）：`_publish_facts`（校验 + 指纹）→
    `_publish_stage`（manifest + job 身份）→ `_publish_commit`（打包 + 落盘 + 账本）。
    相位边界就是原函数里的 `# 4)` / `# 5)` 段；判据、日志、写盘顺序逐条未动。
    """
    facts = _publish_facts(spec)
    staged = _publish_stage(spec, facts)
    return _publish_commit(spec, facts, staged, log=log)
