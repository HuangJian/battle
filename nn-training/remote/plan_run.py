"""remote/plan_run.py —— 半离线自主段（kind=run）的**执行引擎**（2026-09-23 从 `run_loop.py` 下沉）。

**为什么单独一层**：`remote/` 内部的依赖账本（`tests/helpers/remote_dag.py`）里，唯一被登记的
延迟环是 `run_loop ⇄ worker`——`worker.run_job` 在 kind=run 的尾巴上要 `verify_plan_file` /
`run_plan_job`，而 `run_loop` 每跑一轮又要回头调 `worker.run_job`。前者只能靠**下沉**消除：这两个
函数的传递闭包就是**整个执行引擎**（`RunContext` + 单轮执行 + 主循环，约 1100 行），所以引擎整块
搬到本模块，落在 `http` / `push_client` 同层的 L2。

**本模块不 import `remote.worker`**（守卫钉住）。「一轮怎么跑」通过 `RunContext.run_job_fn`
**注入**——原来那个 `run_loop._real_run_job` 兜底已删除：引擎不再替调用方决定用谁的 job 执行器。
调用方两侧各自注入自己那份：

  * `remote/worker.py`（kind=run 尾巴）：`run_plan_job(..., run_job_fn=run_job)`——把自己传进去；
  * `remote/run_loop.py`（CLI / 独立续跑）：传 `_real_run_job`（延迟 import `worker.run_job`）。

引擎语义与拆分前逐字一致（只改了这一处注入 + 块的位置）：hub 在交接时给一次（课程 + 初始权重 +
代码 + `plan.json`），此后云机自主把计划里的轮次跑完，每轮合成一个与 kind=iter **逐字段同构**的
job 再喂回 `run_job`——离线轮与 hub 监管轮走的是字面同一条代码路径。

链条的三个衔接点：① 对集 `biz.plan.pairs_for`；② argv `biz.plan.iter_spec`；③ 权重与 opt
（上一轮结果 → 下一轮 payload/`opt_sha`，Adam 动量不丢）。产物（`remote/artifacts.py`）是**唯一**
长期记录；补传（`remote/offline_deliver.py`）永远 best-effort、不阻塞、不抛。

★ 注入点：本模块里读的模块全局（`iter_spec` / `pairs_for` / `time` / `DRAIN_FLUSH_SEC` …）都是
**本模块命名空间**的——测试要拦它们请 patch `remote.plan_run.*`，patch `remote.run_loop.*` 打不着
（`run_loop` 只保留门面转发名，见 `tests/remote/test_plan_run_split.py`）。

★ **计划交接面（校验 / 取包播种 / 上下文 / 评估装配）已于 S5 第十三刀逐字节下沉
`remote/plan_handoff`**：本模块只余驱动引擎 + 18 名 `X as X` 门面；要 patch 交接面的
模块全局请去新家（patch 门面不生效）。
"""


from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import remote.land_provider as land_provider
import remote.opt_standard as opt_standard
import remote.weights_standard as weights_standard
from common import progress_hook
from common.log_bundle import LogBundle
from common.platform_utils import (
    cores_note,
)
from common.protocol import (
    PAYLOAD_NAME,
    ProtocolError,
    RetryableError,
    decode_opt_tar,
    decode_weights_json,
    encode_opt_tar,
    encode_weights_json,
    iter_expected_data_fp,
    job_seed,
    normalize_manifest,
    pack_payload,
    validate_result,
)
from common.protocol import (
    job_id as make_job_id,
)
from remote.artifacts import (
    ArtifactStore,
    metrics_row,
    sha256_bytes,
    sha256_file,
)
from remote.offline_deliver import DRAIN_FLUSH_SEC

# ---- 计划交接面（校验 / 取包播种 / 上下文 / 评估装配）已下沉 `remote/plan_handoff.py`（S5 第十三刀，2026-09-27）-----
# 实现搬家、名字留门面：`run_loop` 的 13 个 import 块与全仓调用点一行不改（「名字是契约，位置不是」）。
# 交接面读的模块全局解析在 `remote.plan_handoff.*`——要 patch 请去新家（patch 门面不会生效）。
from remote.plan_handoff import (
    EVAL_ALTERNATE_WAIT_SEC as EVAL_ALTERNATE_WAIT_SEC,
)
from remote.plan_handoff import (
    TS_CODE_ZIP_NAME as TS_CODE_ZIP_NAME,
)
from remote.plan_handoff import (
    TS_TREE_DIR as TS_TREE_DIR,
)
from remote.plan_handoff import (
    RunContext as RunContext,
)
from remote.plan_handoff import (
    _blob_roots as _blob_roots,
)
from remote.plan_handoff import (
    _carry_ts_tree as _carry_ts_tree,
)
from remote.plan_handoff import (
    _eval_job_builder as _eval_job_builder,
)
from remote.plan_handoff import (
    _eval_round_done as _eval_round_done,
)
from remote.plan_handoff import (
    _log_default as _log_default,
)
from remote.plan_handoff import (
    _opt_bytes_from_manifest as _opt_bytes_from_manifest,
)
from remote.plan_handoff import (
    _read_opt_file as _read_opt_file,
)
from remote.plan_handoff import (
    _seed_demo_blob_cache as _seed_demo_blob_cache,
)
from remote.plan_handoff import (
    _seed_opt_blob_cache as _seed_opt_blob_cache,
)
from remote.plan_handoff import (
    _seed_ref_blob_cache as _seed_ref_blob_cache,
)
from remote.plan_handoff import (
    _seed_start_checkpoint as _seed_start_checkpoint,
)
from remote.plan_handoff import (
    _setup_cloud_eval as _setup_cloud_eval,
)
from remote.plan_handoff import (
    _stored_opt_sha as _stored_opt_sha,
)
from remote.plan_handoff import (
    _ts_tree_root as _ts_tree_root,
)
from remote.plan_handoff import (
    open_run_context as open_run_context,
)
from remote.plan_handoff import (
    verify_plan_file as verify_plan_file,
)
from worker.plan import (
    iter_spec,
    pairs_for,
    plan_all_stages,
    rotation_event,
    rotation_recipe,
    volume_for_round,
)

#: 单轮的瞬时失败重试上限（自主模式没有 hub 兜底：重试够了就干净停下留产物）。
ITER_RETRIES = 2

# ------------------------------------------------------------------ 单轮执行


def with_rollout_workers(spec: dict, workers: int) -> dict:
    """把 rollout 规格的并行局数换成**本机**规模（`workers <= 0` = 原样不动）。

    为什么要换：计划里钉着的 `plan.workers` 是**导出那台机器**的规模（常在 8~16 核的本机导出，
    却要在 96 vCPU 的云机上整段跑），而 rollout 与云机 eval 是交替的 ⇒ 两者用同一个
    `common.platform_utils.cpu_worker_slots()`（用户 2026-09-22）。

    安全性：`workers` **不进** `data_fp`（`iter_declared_entries` 只取每条 argv 的 stage/seed），
    所以换并行度不会动摇任何指纹、也不改声明集。
    """
    n = int(workers or 0)
    if n <= 0 or int(spec.get("workers") or 0) == n:
        return spec
    return {**spec, "workers": n}


#: 固定对集课的「加样本」种子步长：原计划种子都落在 `[1, 2**30)`（`build_pairs` 的抽签
#: 上界）⇒ 第 k 份副本的 seed + k×本步长必然与原集及其它副本不重叠，且仍是确定性纯函数。
SAMPLE_SEED_STRIDE = 1 << 30


def _scaled_pairs(
    pairs: list[tuple[int, int]], mult: float
) -> list[tuple[int, int]]:
    """固定对集课的样本倍数：补 `ceil(n×mult) − n` 局**同分布的新种子**。

    不复用原 `(stage, seed)`：同一局重跑只会得到逐字节相同的 shard（确定性），是白烧墙钟
    而不是加样本。副本按原对集顺序轮转，第 k 份把 seed 平移 `k×SAMPLE_SEED_STRIDE`；
    `mult=1.0` 逐字返回原列表（不分配、不改计划身份）。
    """
    n = len(pairs)
    m = float(mult)
    if n == 0 or m == 1.0:
        return pairs
    want = max(1, math.ceil(n * m))
    if want < n:
        return pairs[:want]  # 倍数 <1：按原顺序截短（仍是确定性的）
    out = list(pairs)
    for idx in range(n, want):
        stage, seed = pairs[idx % n]
        copy = idx // n
        out.append((int(stage), int(seed) + copy * SAMPLE_SEED_STRIDE))
    return out


def _scaled_volume(volume: Mapping[str, Any], mult: float) -> dict:
    """带 `volume` 块的课：把 `target_transitions` 与逐关配额/首批局数一起放大。

    三者必须**同源重算**（`validate_runtime_volume` 会逐关核对）：只改 target 会让
    `per_stage_quota`/`games_per_stage_by_stage` 与它不一致，节点侧第一道门就拒收。
    这里直接用与计划导出同一个纯函数族重解，`widen_width`（若有）照旧复算。
    """
    from worker.volume_alloc import alloc_games_by_stage, est_hi, widen_by_width
    from worker.volume_quota import target_per_stage

    m = float(mult)
    stages = [int(s) for s in volume["stages"]]
    target = max(1, math.ceil(int(volume["target_transitions"]) * m))
    # 显式逐关硬顶跟着目标一起抬（否则放大后的配额会被旧硬顶卡在半路）；0 = 无顶，保持 0。
    topup = dict(volume["topup"])
    cap = int(topup.get("max_games_per_stage", 0) or 0)
    if cap > 0:
        topup["max_games_per_stage"] = max(cap, math.ceil(cap * m))
    out = {
        **volume,
        "target_transitions": target,
        "per_stage_quota": target_per_stage(target, len(stages)),
        "topup": topup,
    }
    per_stage = volume.get("games_per_stage_by_stage")
    if not isinstance(per_stage, dict) or not per_stage:
        # 老五字段块（在飞/手写计划）：没有分关估算可复算 ⇒ 只放大标量目标与初波局数，
        # 形状逐字不变（`pairs_for` 的 `initial_wave_pairs` 分支照旧读它）。
        out["games_per_stage"] = max(1, math.ceil(int(volume.get("games_per_stage", 1) or 1) * m))
        return out
    ests = {int(k): int(v) for k, v in volume["est_s_by_stage"].items()}
    factor = float(volume["est_hi_factor"])
    games = alloc_games_by_stage(
        stages, target, ests_hi={s: est_hi(ests[s], factor=factor) for s in stages}
    )
    widen = int(volume.get("widen_width", 0) or 0)
    if widen > 0:
        games = widen_by_width(games, widen)
    return {
        **out,
        "games_per_stage_by_stage": games,
    }


def _run_iteration(
    ctx: RunContext, it: int, *, prev_it: int, on_ppo_start: Callable[[], None] | None = None
) -> dict:
    """跑第 `it` 轮：合成 kind=iter job → 喂回 `run_job`（生产链路的字面复用）。

    `prev_it` = 上一轮（其产物权重就是本轮 init_weights）。返回本轮 result。
    """
    weights = ctx.store.weights_path(prev_it)
    if not weights.exists():
        raise ProtocolError(f"第 {it} 轮的 init 权重不在产物里: {weights}（产物被删/状态损坏？）")
    # ★ 2026-10-15（离线 delta 事故的**本机链**面）：产物里那份就是**我们回传给 hub 的** delta
    #   容器（hub 原样收发）。它既是「上一轮的结果」也是「本轮的 init」⇒ 装载与打包前先在本机
    #   落地成整份；用**旁路缓存**（`standard_weights_copy`）而不是就地改 —— 产物本体保持 delta，
    #   deliver 腿发送的字节、hub 侧 duplicate/conflict 判据、账本指纹***全都不变***，而本轮拿到的
    #   是可直接装载的整份。基准从本机产物链里逐字节找（找不到即响亮拒，不猜）。
    #   这条路径本身就是「不重传」：Init 用本机自己的字节重建，**零字节日下传输**。
    # ★ 2026-10-22（`plan/offline-full-weights-backup` P0-1）：基准优先走**内容寻址缓存**
    #   （`_cas_hit`，键就是 base_sha，O(1)）；不传它就退回「按名序扫 `.weights-standard`」——
    #   链长了就是 O(n) 次 394KB 读。缓存里没有时 `_candidates` 那条老路照旧在（两态都对）。
    weights = weights_standard.standard_weights_copy(
        weights,
        ctx.store.root,
        blob_root=next((p for p in _blob_roots(ctx) if Path(p).is_dir()), None),
        log=ctx.log,
    )
    init_fp = sha256_file(weights)
    # ★ 2026-10-15（离线 opt delta 的读侧落地，与上面 weights 同一条口径）：产物里的
    #   `it-N/opt.tar` 就是**我们回传出去的** BOD1 容器（现场 `tmp/y5-term` 的 it-237，300945B，
    #   头 4 字节 = BOD1；消费者一解包就是 `ReadError: invalid header`）。它既是「上一轮的结果」
    #   也是「本轮的 init」⇒ 整份落成旁路缓存（`.opt-standard/`，产物本体保持容器给 deliver 腿），
    #   并**种子进内容寻址缓存**后再声明 `opt_sha` —— 声明什么就必须拿到什么（`train_core` 只认
    #   `blob_cache/<sha>`，而离线段没有下载通道）。下一轮的落地基准也随之是这份整份（链上一步，
    #   而它在缓存里 = O(1) 命中）。找不到基准 ⇒ 响亮记一行、本段按**无动量**跑（与
    #   `plan_handoff._stored_opt_sha` 同口径：动量是可选件，首轮本来就没有 ⇒ 不让整段死在 init）。
    #   ⚠ **不**退回 `ctx.last_opt_sha`（那是上一个调用在下面 `:279` 写下的「回传出去的那份
    #   delta 自己的 sha」）—— 那正是一个**拿不到的地址**（容器解不开、离线段也没有下载通道），
    #   声明它 = 把这次要修的形态又从后门放回来。
    try:
        ctx.last_opt_sha = opt_standard.landed_opt_sha(
            ctx.store.opt_path(prev_it),
            ctx.store.root,
            blob_roots=_blob_roots(ctx),
            log=ctx.log,
        )
    except ProtocolError as e:
        ctx.log(f"WARN: it{prev_it} 的 opt 是 delta 容器且本机找不到基准（{e}）——本段按无动量跑")
        ctx.last_opt_sha = ""
    # ★ 2026-10-22（用户口径「有几个 worker 铺多少局」）：自主机自己 rollout ⇒ 宽度就是
    #   `ctx.rollout_workers`（缺省 = 本机 `cpu_worker_slots()`，见 plan_handoff 的装配），
    #   **不 ping 任何人、不看 hub 的 plan 声明**。plan 里的 `games_per_stage_by_stage` 是
    #   打包时的缺口形状（est 反解），本机核数比它多就按宽度重铺；派发侧逐关满额即停，
    #   多出来的候选不会白跑。无 `volume` 的老课程逐字走 `pairs_for`。
    # ★ 2026-10-22 补（同源面的那一刀）：加宽必须**连 `widen_width` 一起写进块**——块里的
    #   `games_per_stage_by_stage` 要过 `validate_runtime_volume` 的同源复算（首批局数 =
    #   `widen_by_width(est 反解值, widen_width)`），只改 games 不声明宽度会被节点/`iter_spec`
    #   响亮拒收（现场：y30-stack it156，9 局/关被铺到 24 局/关 ⇒ ProtocolError 整段死）。
    # ★ 2026-10-28（云机轮换，plan/it-stage-ratation §9.5）：本轮采哪些关 = **当轮窗口**。
    #   `ctx.plan["volume"]` 是导出那一轮的那一份（一份块），开了轮换后每轮都要按配方
    #   重解（`worker/plan.volume_for_round`）——否则第 2 轮起还在采第 1 轮的关集。
    #   笔记本开关（`ctx.rotation`，三态）在这里落地：
    #     auto = 跟随计划；on = 计划没带轮换就拒跑（不猜）；off = 把窗口降级成全关（记账）。
    _policy = str(getattr(ctx, "rotation", "auto") or "auto")
    window_override, rotation_override = rotation_window_for_round(ctx.plan, it, _policy)
    if rotation_override:
        recipe = rotation_recipe(ctx.plan) or {}
        ctx.log(
            f"WARN it{it}: 笔记本 --rotation off —— 本段把计划里的轮换"
            f"（mode={recipe.get('mode')!r} per_it={recipe.get('per_it')}）降级为**全关采样**；"
            "语料分布与导包时不同（账本 rotation.override=off 留痕）"
        )
    vol_probe = volume_for_round(ctx.plan, it, window_override=window_override)
    if isinstance(vol_probe, dict) and ctx.sample_mult != 1.0:
        # 先放大再走本机宽度的「加宽」逻辑：两张牌都是纯函数，顺序不影响同源校验。
        vol_probe = _scaled_volume(vol_probe, ctx.sample_mult)
    if isinstance(vol_probe, dict):
        from worker.volume_alloc import widen_by_width

        _per = vol_probe.get("games_per_stage_by_stage")
        _width = int(ctx.rollout_workers or 0)
        if isinstance(_per, dict) and _per and _width > 0:
            _wide = widen_by_width(_per, _width)
            if sum(_wide.values()) > sum(int(v) for v in _per.values()):
                ctx.log(
                    f"it{it} 首批按本机宽度重铺：{sum(int(v) for v in _per.values())} → "
                    f"{sum(_wide.values())} 局（workers={_width}）"
                )
                vol_probe = dict(
                    vol_probe, games_per_stage_by_stage=_wide, widen_width=_width
                )
    _plan = dict(ctx.plan, volume=vol_probe) if isinstance(vol_probe, dict) else ctx.plan
    if window_override is not None:
        # 降级轮：块已经按全关解好，**不要再**让 `pairs_for` 按配方派生一次（那会解回窗口）。
        _plan.pop("rotation", None)
    pairs = pairs_for(_plan, it)
    if not isinstance(vol_probe, dict):
        # 固定对集课没有「训练侧配额」可放大 ⇒ 直接补同分布的**新种子**（不是重跑同一局）。
        pairs = _scaled_pairs(pairs, ctx.sample_mult)
    spec = iter_spec(_plan, it, pairs, wver=init_fp, course=ctx.course)
    spec = with_rollout_workers(spec, int(ctx.rollout_workers or 0))
    # 日志面用**重铺后的** plan（否则打出来的还是打包时的旧 G_s，与实采对不上）
    vol = vol_probe
    epochs_eff = int(ctx.manifest.get("epochs") or 0)
    if ctx.epoch_mult != 1.0:
        # PPO 遍数是整数（`train_core` 直接拿去喂 `ppo_update`）：向上取整，最少 1。
        epochs_eff = max(1, math.ceil(epochs_eff * ctx.epoch_mult))
    # ★ 2026-09-24（日志节食）：这几行与 `run_job` 自己的入口/设备/装载读数是**同一个阶段**
    # （本轮上云从拼 spec 到 PPO 开算），所以攒进同一个 bundle，由 `run_job` 在装载完成时
    # 打**一行**（原来这里是 1 行 + 它那边 9 行）。
    prep_b = LogBundle(ctx.log)
    prep_b.add(
        f"it{it}",
        f"{len(pairs)} 局（{len({s for s, _ in pairs})} 关）wver={init_fp[:12]}…"
        + (
            f" 动态采集：每关初波 {vol['games_per_stage']} 局，训练侧配额 "
            f"{vol['per_stage_quota']}/关"
            if isinstance(vol, dict)
            else ""
        ),
    )
    if ctx.sample_mult != 1.0 or ctx.epoch_mult != 1.0:
        prep_b.add(
            "放大",
            f"样本×{ctx.sample_mult:g} ⇒ {len(pairs)} 局；"
            f"PPO epochs×{ctx.epoch_mult:g} ⇒ {epochs_eff}",
        )
    prep_b.add(
        "动量", "带 Adam 动量" if ctx.last_opt_sha else "无 opt：Adam 从头"
    )
    # ---- 合成第 it 轮 manifest：与 hub 发布的 kind=iter 逐字段同构 ----
    m = dict(ctx.manifest)
    m.update(
        {
            "kind": "iter",
            "it": int(it),
            "epochs": epochs_eff,
            "rollout": spec,
            "init_weights_fp": init_fp,
            "data_fp": iter_expected_data_fp(spec),
            # opt 走内容寻址：上一轮 PPO 已把原始 tar 写进 blob_cache ⇒ 命中即零传输
            "opt_init": "",
            "opt_sha": str(ctx.last_opt_sha or ""),
            "opt_bytes": 0,
            "seed": job_seed(str(m["runId"]), int(it), init_fp),
        }
    )
    # 动态采集的**训练侧一半**：逐关严格样本量配额（`ceil(target/关数)`）必须逐轮随
    # manifest 走。hub 发布的头一份 manifest 里通常已经有它（`loop_steps._remote_ppo` 发布时
    # 算好），但计划才是自洽契约（包里/manual 导入的那份可能更旧）——按**计划**的值走。
    # ★ 2026-10-29（日志瘦身）：漂移**不再单打一行 WARN**（值恒定 ⇒ 390 轮 390 条一样的话），
    #   改成拼进 prep 那行的「动态采集」字段（同一句读数里既有生效值也有被覆盖的原值）。
    drift = 0
    want = 0
    if isinstance(vol, dict):
        want = int(vol["per_stage_quota"])
        have = int(m.get("per_stage_quota", 0) or 0)
        drift = have if (have and have != want) else 0
        m["per_stage_quota"] = want
    if drift:
        # 与 prep 那行同一个 bundle（装载完成时一次性打），不再每轮一条 WARN。
        prep_b.add("配额漂移", f"manifest {drift}/关 → 按计划 {want}/关")
    # 回传形态按轮定（`plan/anchor-full-rounds` §4）：锚点轮弹 `w_fmt`（worker 回整份权重），
    # 其余写 `W_DELTA_FMT`；`opt_fmt` 一律弹掉（opt 的形态在**写完盘之后**由 `after_write` 定，
    # 那里才有「盘上最近一份整份」当基准）。`None` = 旧行为（manifest 原样）。
    if ctx.round_forms is not None:
        ctx.round_forms.prepare_manifest(m, it)
    m["job_id"] = make_job_id(m)
    m = normalize_manifest(m)
    # ---- 本轮 payload：只有 init_weights.json（shard 由节点现场产，与 kind=iter 一致）----
    it_dir = ctx.work_dir / m["job_id"]
    it_dir.mkdir(parents=True, exist_ok=True)
    zip_path = it_dir / PAYLOAD_NAME
    payload_init = it_dir / "init_weights.json"
    payload_init.write_bytes(weights.read_bytes())
    m["payload_sha256"] = pack_payload([], m, zip_path, extra_files=[payload_init])
    m = normalize_manifest(m)
    run_job = ctx.run_job_fn
    if run_job is None:  # 引擎不替调用方决定用谁跑一轮（原先这里兜底 worker.run_job）
        raise RuntimeError(
            "run_job_fn 未注入：半离线引擎必须由调用方给出 job 执行器"
            "（worker 侧传 run_job 自己；CLI 侧传 remote.run_loop._real_run_job）——"
            "见 remote/plan_run.py 头部"
        )
    # 代码快照与 TS 运行时：**有随包字节就用字节**（全离线：节点无仓、不联网），没有就
    # 交给 `run_job` 走它本来的路（命中 code_cache / 向 hub 下载——半离线轮就是这条路：
    # worker 自己那一轮已把 code.zip 落进内容寻址缓存）。两个来源都存在时优先字节。
    preloaded: dict = {"payload_zip": zip_path.read_bytes()}
    if ctx.code_zip_bytes:
        preloaded["code_zip"] = ctx.code_zip_bytes
    if ctx.ts_code_zip_bytes:
        preloaded["ts_code_zip"] = ctx.ts_code_zip_bytes
    result = run_job(
        "",
        "",
        {"job_id": m["job_id"], "manifest": m},
        work_dir=ctx.work_dir,
        device=ctx.device,
        torch_threads=ctx.torch_threads,
        preloaded=preloaded,
        code_cache_dir=ctx.code_cache_dir,
        ts_code_cache_dir=ctx.ts_code_cache_dir,
        prep=prep_b,
        # ★ 2026-10-29（用户口径「eval 藏进 PPO 墙钟」）：PPO **开跑那一刻**是提交上一轮评估的
        #   唯一好时机——rollout 已经收工（不与它抢 94 个 CPU 槽），PPO 在 TPU 上跑 26s，
        #   评估（~2.5s）整个藏在里面。`on_ppo_start` 由 `train_core` 在 PPO 之前一刻回调。
        on_ppo_start=on_ppo_start,
        log=ctx.log,
    )
    validate_result(result, m, commit_echo_must_match=False)
    # ★ 2026-10-28（云机轮换）：本轮窗口读数随结果带回落账（`_checkpoint` 写进行）。
    #   云机轮此前只有 `volume_stage_stats` 的键可看（而它本身还没进行账）⇒ 账本里
    #   看不出「这段在轮换、这一轮采了哪 12 关」；`override` 非空 = 笔记本开关改过语料面。
    _rot = rotation_event(
        ctx.plan,
        int(it),
        window=[int(s) for s in (vol_probe or {}).get("stages", ())],
        override=rotation_override,
    )
    if _rot is not None:
        result["rotation"] = _rot
    raw = _opt_bytes_from_result(result)
    ctx.last_opt_sha = sha256_bytes(raw) if raw else ""
    return result


def _opt_bytes_from_result(result: dict) -> bytes:
    """`result.opt_tar_b64` → raw（链式传递的 Adam 动量）。缺/坏 → 空（下场：动量归零）。"""
    b64 = str(result.get("opt_tar_b64", "") or "")
    if not b64:
        return b""
    try:
        return decode_opt_tar(b64)
    except Exception:
        return b""


def _pack_full_for(ctx: RunContext) -> Callable[[Path, int], Mapping[str, Path]]:
    """段末打包的**整份提供者**（`ArtifactStore.finalize(full_for=…)` 的注入实现）。

    实现整体在 `remote/land_provider`（三条出口 + notebook 一键包共一份）；这里只做两件事：
    把本 ctx 的 `blob_cache` 根交过去（`_cas_hit`，O(1) 命中末轮的基准）与绑定日志。

    为什么是注入而不是让 `artifacts` 自己落地（2026-10-22 · `plan/offline-full-weights-backup` W2）：
    `remote/artifacts` 是 L0 且被 `hub.store_offline`(L1) 直接 import，自己去 import 落地器
    (`remote/weights_standard` L1) 就是向上边（`tests/test_remote_dag.py`）。

    为什么几乎不要钱：历史轮命中 `<store>/.weights-standard/`（每轮开跑前落的）＝ 一次 stat；
    只有末轮要现场落（基准在 `blob_cache`）。落不下来 ⇒ 这一件不给 ⇒ 包里保留原件。
    """
    return land_provider.provider(blob_roots=_blob_roots(ctx), log=ctx.log)


def _weight_bytes(result: dict) -> bytes:
    """`result.weights_json` → weights.json 字节。"""
    return bytes(decode_weights_json(str(result.get("weights_json") or "")))


def rotation_window_for_round(
    plan: dict, it: int, policy: str
) -> tuple[list[int] | None, str]:
    """笔记本开关（`--rotation` 三态）→ `(窗口覆盖, 留痕标记)`。

    * `auto`：不覆盖 —— 计划带轮换就按配方逐轮解（`worker/plan.volume_for_round`）；
    * `on`：计划没带配方 ⇒ **拒跑**（`ProtocolError`，不猜「那就不轮换吧」）；
    * `off`：计划带配方 ⇒ 把窗口降级成**全关** + 留痕 `"off"`（账本 `rotation.override`；
      语料分布变了 = 另一个实验面，必须可读）；计划本来就没轮换 ⇒ 不覆盖（没有可降级的），
      老行为逐字节不变。
    """
    recipe = rotation_recipe(plan)
    if policy == "on":
        if recipe is None:
            raise ProtocolError(
                "--rotation on 但计划里没有 rotation 配方（课程没开轮换/包是旧的）——"
                "要么重新导包，要么把开关改回 auto（拒跑而不是静默按全关采）"
            )
        return None, ""
    if policy == "off" and recipe is not None:
        stages = plan_all_stages(plan)
        if not stages:
            raise ProtocolError(
                f"--rotation off 需要计划里的全关集（pair_args.stages）才能降级（it{it}）"
            )
        return stages, "off"
    return None, ""


def _row_extra(result: dict, phase: str) -> dict | None:
    """账本行的附加格（additive）：`phase` + 云机轮换/分关读数（plan/it-stage-ratation §9.5）。

    为什么在**行**上：控制台读的是账本（`remote/artifacts.ledger_row_from_metrics`），
    而云机轮此前连 `rotation` 一个字都没有 ⇒ 轮换课在云机段上看起来与全关课一样。
    缺数据一律不给键（不编 0）——老产物行/未开轮换的轮逐字节不变。
    """
    extra: dict = {}
    if phase != "train":
        extra["phase"] = phase
    rot = result.get("rotation")
    if isinstance(rot, dict) and rot:
        extra["rotation"] = rot
    vstats = result.get("volume_stage_stats")
    if isinstance(vstats, dict) and vstats:
        extra["volume_stage_stats"] = vstats
    # ★ 2026-10-29（云机轮墙钟）：产物段（save/pack/identity）的秒数。它是 `wall=` 里除了
    #   `rollout_sec`/`ppo_sec` 之外最大的一块，此前**一行打点都没有**（只有临时开关
    #   `PPO_CUDA_DIAG=1` 才打）——真机 43.4s/轮，占一轮的一半，日志里完全看不见。
    #   缺键（老 worker）就不给，不编 0。
    wire = result.get("wire")
    if isinstance(wire, dict):
        art = wire.get("art_sec")
        if isinstance(art, (int, float)) and float(art) > 0:
            extra["art_sec"] = round(float(art), 2)
    return extra or None


def _checkpoint(ctx: RunContext, it: int, result: dict, *, wall_sec: float, phase: str = "train") -> dict:
    """把一轮结果落进产物目录（权重 + opt + 账本行），返回账本行。"""
    agg = dict(result.get("agg", {}) or {})
    report = result.get("report")
    wire = dict(result.get("wire", {}) or {})
    row = metrics_row(
        agg=agg,
        report=report if isinstance(report, dict) else None,
        wall_sec=wall_sec,
        ppo_sec=float(result.get("ppo_sec", 0.0) or 0.0),
        rollout_sec=float(wire.get("rollout_sec", 0.0) or 0.0),
        steps=int(agg.get("steps", 0) or 0),
        chunks=int(agg.get("chunks", 0) or 0),
        extra=_row_extra(result, phase),
    )
    # 字节先取到外面（`_weight_bytes` / `_opt_bytes_from_result` 对坏载荷抛 `ValueError`——那是
    # 「结果不可用」，不是「盘写不下去」，两者不能混成同一条归因）。
    w_bytes = _weight_bytes(result)
    opt_bytes = _opt_bytes_from_result(result)
    try:
        entry = ctx.store.checkpoint(it, weights_json=w_bytes, opt_tar=opt_bytes, row=row)
    except OSError as e:
        # ★ 2026-10-14（`plan/offline-local-disk-first` B2 / DoD-14）：产物根落本地盘之后，
        #   段中盘满（ENOSPC）是**唯一没有等价逃生门**的失败（scratch 那两个候选盘各有自己的
        #   容量闸与回退档，产物根没有）。裸抛 OSError 的问题不是「不响亮」（traceback 里确实有
        #   `.tmp` 路径），而是**归因难 + 看起来像崩溃**：日志里既没有「产物根」这个可换的东西，
        #   也没说「已收轮次不受影响、换根就能续」。这里当场把三者说清，再转成 `ProtocolError`
        #   ——让它走 `run_loop` 的失败支路（非零退出 + 一行 `[run] 失败`），**不会**碰到
        #   `finalize` 的成功口径（即段末不谎报成功）；`state.json` 仍是上一轮的 last_it
        #   （`checkpoint` 的写序是「先产物后状态」）⇒ 续跑锚点照样成立。
        ctx.log(
            f"WARN: 产物落盘失败（{type(e).__name__}: {e}）——本段就此停下；产物根={ctx.store.root}"
            f"｜已收轮次不受影响（续跑锚点仍是上一轮），换根续跑：`NN_LOCAL_ROOT` / `--artifacts`"
            " 指一个可写、装得下的盘"
        )
        raise ProtocolError(
            f"产物根写失败（{ctx.store.root}）：{type(e).__name__}: {e}——本段已停下，未落盘的轮不算数"
        ) from e
    ctx.log(
        f"it{it} 落盘：kl={agg.get('kl')} mean_ret={agg.get('mean_ret')} "
        f"winRate={(report or {}).get('winRate')} wall={row['wall_sec']}s"
        # 产物段秒数（零新增行：拼在既有的落盘行上）。`wall - rollout - ppo - art`
        # 才是「其余」；没有它，那 40 秒在日志里既不是 rollout 也不是 PPO。
        + (f" art={row['art_sec']}s" if "art_sec" in row else "")
        + f" → {ctx.store.dir_for(it)}"
    )
    ctx.deliver_round(it)  # 补传在落盘之后（先自洽，再尽力出网）
    return entry


def _eval_due(ctx: RunContext, it: int) -> bool:
    """这一轮该不该评（`eval_every` 的判据在 `ctx.eval_plan` 里）。**纯查表，不提交**。"""
    plan = getattr(ctx, "eval_plan", None)
    if not ctx.eval_on_cloud or getattr(ctx, "eval_runner", None) is None or plan is None:
        return False
    try:
        return bool(plan.due(it))
    except Exception as e:  # 评估是旁路：任何意外都不能影响训练
        ctx.log(f"WARN: 云机评估判据异常（忽略）：{type(e).__name__}: {e}")
        return False


def _submit_cloud_eval(ctx: RunContext, it: int) -> None:
    """提交第 `it` 轮评估（**不等待**）。调用点 = **下一轮 PPO 开跑那一刻**（见 `_drive`）。

    为什么不在轮边界提交（2026-10-29 用户口径「eval 要并行藏进 ppo 墙钟里」）：轮边界提交 +
    就地等待 = 评估卡在「下一轮 rollout 之前」，而 rollout 要 94 个 CPU 槽、评估要 80 个
    ⇒ 真重叠会成批踩单局硬顶。放到 PPO 开跑那一刻，rollout 已收工、PPO 在 TPU 上跑几十秒，
    评估（~2.5s）整个藏在它的墙钟里，**轮墙钟一分不涨**。**永不抛**。
    """
    runner = getattr(ctx, "eval_runner", None)
    if not _eval_due(ctx, it) or runner is None:
        return
    try:
        if not runner.submit(it):
            return  # 上一轮还在飞（`submit` 自己已记过一笔）
        # ★ 2026-10-25（用户口径）：进度行要说清这一段在干**什么**。
        games = int((getattr(getattr(ctx, "eval_plan", None), "plan", None) or {}).get("games", 0) or 0)
        progress_hook.report("eval", it=int(it), done=0, total=games, force=True)
    except Exception as e:
        ctx.log(f"WARN: 云机评估提交失败（忽略）：{type(e).__name__}: {e}")


def _drain_cloud_eval(ctx: RunContext, it: int) -> None:
    """轮边界收线：评估已在**上一轮的 PPO** 里跑了几十秒 ⇒ 这里通常零等待。

    仍是**有界**等（`EVAL_ALTERNATE_WAIT_SEC`）：等超时也照常放行（评估永不按住训练）。
    """
    runner = getattr(ctx, "eval_runner", None)
    if runner is None:
        return
    wait_idle = getattr(runner, "wait_idle", None)
    if wait_idle is None:
        return
    try:
        if not wait_idle(EVAL_ALTERNATE_WAIT_SEC):
            ctx.log(
                f"WARN: 云机评估 it{it} 超过 {EVAL_ALTERNATE_WAIT_SEC:.0f}s 未收线 —— 训练继续"
                "（评估仍在后台，会与接下来的 rollout 抢 CPU；真要缩短就降 --eval-slots/语料）"
            )
    except Exception as e:
        ctx.log(f"WARN: 云机评估收线异常（忽略）：{type(e).__name__}: {e}")


def _maybe_cloud_eval(ctx: RunContext, it: int) -> None:
    """提交 + 立刻有界收线（**旧口径**：调用方在轮边界上，等评估落地才走）。

    ⚠ 生产路径（`_drive`）已改成「PPO 开跑时 `_submit_cloud_eval`、轮边界 `_drain_cloud_eval`」；
    本函数留给「调用点自己就在轮边界、且要一次做完」的宿主与既有用例。
    """
    _submit_cloud_eval(ctx, it)
    _drain_cloud_eval(ctx, it)


def runner_timeout(ctx: RunContext) -> float:
    """评估收线时限（秒）：没配就取 `CloudEvalRunner` 的缺省（600）。"""
    return float(getattr(getattr(ctx, "eval_runner", None), "drain_timeout_sec", 600.0) or 600.0)


def _close_eval(ctx: RunContext, *, timeout: float | None = None) -> None:
    """段末收线：给在飞的云机评估有界时间落账（三个出口都要调）。**永不抛**。

    不调的话，段末那一刻还在飞的局随进程一起消失，那一轮没有 summary——白评一轮
    （逐局行可能已落盘，但板子按 summary 画曲线）。
    """
    runner = getattr(ctx, "eval_runner", None)
    if runner is None:
        return
    try:
        cur = runner.inflight_it() or runner.pending_it()
        if cur is not None:
            limit = runner.drain_timeout_sec if timeout is None else float(timeout)
            ctx.log(
                f"段末收线：等云机评估 it{cur} 落账（在飞的局跑完即走，最多等 {limit:.0f}s）"
            )
        runner.drain(timeout)
    except Exception as e:
        ctx.log(f"WARN: 段末评估收线异常（忽略）：{type(e).__name__}: {e}")


# ------------------------------------------------------------------ 主循环


def run_plan_job(
    *,
    job_id: str,
    manifest: dict,
    job_dir: Path,
    work_dir: Path,
    plan: dict,
    plan_sha256: str,
    first_result: dict,
    course: Any = None,
    device: Any = "cpu",
    torch_threads: int = 0,
    code_cache_dir: Path | None = None,
    ts_code_cache_dir: Path | None = None,
    artifacts_dir: str | Path | None = None,
    max_iters: int = 0,
    budget_sec: float = 0.0,
    #: 云机 A 层评估（`eval_on_cloud`；见 `remote/offline_eval.py`）。
    eval_on_cloud: bool = False,
    eval_slots: int = 0,
    eval_game_timeout_sec: float = 0.0,
    #: rollout 并行局数（0 = 按本机核数自动；与 eval 同一口径，见 `RunContext.rollout_workers`）。
    rollout_workers: int = 0,
    #: 产物补传（可选）：给了 hub 地址 + token 就在每轮落盘后尽力推一份上去
    #: （hub 中途失联也不至于「跑完一整段、控制面一无所知」）。
    hub_url: str = "",
    hub_token: str = "",
    #: 本份产物在 hub 里的**归位键**（多课程 hub 的课程键；见 `OfflineDeliverer.course`）。
    #: 领活路径由 hub 在轮询面下发（`remote/worker.py` 传进来），全离线包那条腿
    #: 由 `--hub-course` 给。空 = 单课程 hub。
    hub_course: str = "",
    deliver: bool = True,
    run_job_fn: Callable[..., dict] | None = None,
    log: Callable[[str], None] = _log_default,
) -> dict:
    """从「首轮结果」续跑：`first_result` 已经跑完，接着把计划剩下的轮次跑完。

    ★ 2026-09-25：**今天没有生产调用者**。它曾经是「hub 发一份 `kind=run` 整段 job、
    worker 接着跑完」那条腿的尾巴——那条腿已退役（`plan/online-offline-role-routing.plan.md`
    §7：hub 侧不再发这种活，`remote/worker.py` 对它响亮拒收；离线课走任务包 + `run_standalone`）。
    保留它的理由只有一个，但很实在：它是把 `_drive` **从首轮结果续下去**的唯一入口，而
    `tests/remote/test_run_loop.py` 的 4 组段语义回归（整段逐轮同构 / 锚点续跑不重复记账 /
    `max_iters` 上限 / 轮失败后产物仍可续）全挂在它上面——删它等于把这些回归一起删。

    返回**合并结果**（末轮形状 + `iters` 明细 + `it_end` + `artifacts`）。

    收尾策略：正常跑完、预算到点、还是中途抛错，都 `finalize` 产物（state + 全量 zip）
    之后再返回/抛出——**任何时刻停下，卷上的目录都是自洽可续的**。
    """
    ctx = open_run_context(
        plan=plan,
        plan_sha256=plan_sha256,
        manifest=manifest,
        job_dir=Path(job_dir),
        work_dir=work_dir,
        artifacts_dir=artifacts_dir,
        device=device,
        torch_threads=torch_threads,
        code_cache_dir=code_cache_dir,
        ts_code_cache_dir=ts_code_cache_dir,
        ts_tree=ts_code_cache_dir,
        course=course,
        max_iters=max_iters,
        budget_sec=budget_sec,
        eval_on_cloud=eval_on_cloud,
        eval_slots=eval_slots,
        eval_game_timeout_sec=eval_game_timeout_sec,
        hub_url=hub_url,
        hub_token=hub_token,
        hub_course=hub_course,
        deliver=deliver,
        run_job_fn=run_job_fn,
        log=log,
    )
    anchor_it = int(manifest["it"])
    session: list[dict] = []
    state = ctx.store.read_state() or {}
    # 接续点 = max(锚点, 产物里已到的那一轮)：同 job 重领时产物可能已经跑在前面
    # （会话中途被回收）——从产物接上，**不重跑也不重复记账**（账本是给人看的曲线，
    # 同一 it 两行会让它彻底读不了）。
    start_from = max(anchor_it, int(state.get("last_it", anchor_it)))
    stored = ctx.store.weights_path(anchor_it)
    if start_from > anchor_it:
        ctx.log(f"产物已跑到 it{start_from}——从产物接上（锚点 it{anchor_it} 不重复记账）")
        # ★ 2026-10-04：原来这里会 `or manifest.opt_sha` —— 那是在**声明一个拿不到（或更糟：
        #   属于旧轮次）的动量 sha**。自主段的合成轮用 run_job("", "") 没有下载通道，声明的
        #   sha 必须在本地可兑现（blob_cache / 产物），否则 it 处在 PPO 之前失败。
        ctx.last_opt_sha = _stored_opt_sha(ctx, start_from)
    elif stored.exists() and sha256_file(stored) == sha256_bytes(_weight_bytes(first_result)):
        # 锚点轮产物与本次结果一致——沿用，不重复记账
        ctx.log(f"锚点 it{anchor_it} 已在产物里且与本次结果一致——沿用（不重复记账）")
        ctx.last_opt_sha = _stored_opt_sha(ctx, anchor_it)
    else:
        session.append(_checkpoint(ctx, anchor_it, first_result, wall_sec=0.0, phase="anchor"))
    return _drive(ctx, session=session, start_from=start_from)


def _end_it_reached(plan: dict, *, state: str, it_end: int) -> bool:
    """本段是否跑到了**计划终点**（T6 的唯一判据；纯函数，`plan/auto-offline-handoff §3.6`）。

    为什么判据在云机这一侧：完成态的生产者是**跑过这段的那一侧**——它同时知道
    `state`（怎么停的）与计划终点（`plan["end_it"]`）。控制台/hub 只能转交，不能猜。

    刻意苛刻的两条：
      * 只有 `complete`（跑空 todo）与 `noop`（接续点已在终点之后）算；
        `budget`/`failed` 都不是跑满 —— 半段自报会把这门课提前封成 completed（U6 反例）；
      * `it_end >= end_it`：`max_iters` 截断后的区间跑完也**不算**跑满（计划终点没到）。
    """
    end_it = int(plan.get("end_it", 0) or 0)
    if end_it <= 0 or state not in ("complete", "noop"):
        return False
    return int(it_end) >= end_it


#: 段末三态的取值（`plan/offline-open-ended-iters-and-loud-export` §3.2.3，J2）。
SEG_END_OPEN = "open"
SEG_END_COURSE = "course"
SEG_END_SEGMENT = "segment"


def _seg_end_kind(plan: dict, *, state: str, it_end: int) -> str:
    """本段跑到终点时，这一段的「段末」是哪一种（纯函数；**三态**，J2 §3.2.3）。

    为什么不能只报 `end_it_reached`（v1 的错，评审 P0-3）：`end_it_reached` 只说
    「计划区间跑完了」，而**计划区间**既可以止于课程预算、也可以是开区间段、还可以是
    有限课程被段长截短的一段 —— 三种的**下一步完全不同**（封课 / 滚下一段 / 滚下一段）。
    真判据是「这一段有没有触及课程预算」，它只能从计划里读（`plan` 是唯一事实来源）：

      * `open_ended`（课程 `iters<=0`，J1）⇒ `"open"`：课程没有终点，这是「本段完成」；
      * `end_it >= iters_total` ⇒ `"course"`：**课末**（封课、不再触发下一段）；
      * 其余（有限课程 + `--run-iters N` 截短的段）⇒ `"segment"`：段末。
        ⚠ 这一格**不是**课末 —— v1 的 `end_it_reached ∧ not open_ended` 会把它误判成课末
        （而它正是 `offline-segment-restart` §93 那条「人手动续段」路径，今天也走这条）。

    没跑到终点（失败 / 预算停）⇒ `""`（**没有段末可言**；与「段末但未知」区分开）。
    """
    if not _end_it_reached(plan, state=state, it_end=it_end):
        return ""
    if plan.get("open_ended") is True:
        return SEG_END_OPEN
    iters_total = int(plan.get("iters_total", 0) or 0)
    end_it = int(plan.get("end_it", 0) or 0)
    if iters_total > 0 and end_it >= iters_total:
        return SEG_END_COURSE
    return SEG_END_SEGMENT


def _drive(ctx: RunContext, *, session: list[dict], start_from: int) -> dict:
    """从 `start_from` 之后跑到计划末尾（受预算/上限约束），返回合并结果。"""
    todo = [it for it in ctx.planned_range() if it > start_from]
    if not todo:
        end_it = int(ctx.plan.get("end_it", 0) or 0)
        if start_from >= end_it:
            # G3（plan/offline-rerun-local-first §4.2）：区分「本机段落已完成」（下一步是清目录/
            # 等新包）与「无事可做」。前者原来是同一个词 —— 人看不出该动哪一步。
            ctx.log(
                f"本机段落已完成（it{start_from} ≥ end_it{end_it}）——没有要跑的轮次；"
                f"要用新段请清空 {ctx.store.root} 或等新包（或置 CFG.force_pack=true）"
            )
        else:
            ctx.log("计划内的轮次都已在产物里——无事可做")
        ctx.store.finalize(
            state="complete",
            summary={"last_it": start_from, "rows": len(ctx.store.rows)},
            full_for=_pack_full_for(ctx),
        )
        # 无事可做也可能**有东西要补传**：上次会话断网、这次连上了，积压全在这一步补完。
        # `end_it_reached` 用同一判据重算：接续点已在终点之后 ⇒ 重报一次「跑满」（幂等；
        # 上一个会话跑满但没送达的摘要，由这一拍补上）。
        ctx.deliver_final(
            it_end=start_from,
            state="noop",
            summary={"rows": len(ctx.store.rows)},
            end_it_reached=_end_it_reached(ctx.plan, state="noop", it_end=start_from),
            seg_end_kind=_seg_end_kind(ctx.plan, state="noop", it_end=start_from),
        )
        ctx.close_delivery()
        _close_eval(ctx)
        return _combined(ctx, last_it=start_from, session=session, state="noop")
    # rollout 并行度与段边界同**一行**（2026-10-29 日志瘦身）：它是每段一次的本机规模派生值，
    # 计划里钉着的是**导出机**的值——两者不同时说出来，「明明改大了并发却不见快」没法归因。
    # 口径（`cpu_worker_slots`：≤4 核全给，否则 max(cores−2, cores×0.8)）留在那个函数里，
    # 行上只给「可用几核 + 核数怎么来的」。
    pinned = int(ctx.plan.get("workers", 0) or 0)
    ctx.log(
        f"自主段开始：{len(todo)} 轮待跑（it{todo[0]} → it{todo[-1]}）"
        f"｜rollout 并发={ctx.rollout_workers} 局"
        + (f"（计划钉着 {pinned} = 导出机规模，已按本机覆盖）" if pinned and pinned != ctx.rollout_workers else "")
        + f"｜核数出处 {cores_note()}"
    )
    # ★M3 / Q2：轮边界的第二层打点（第一层在 `worker/iter_rollout.py` 的每结算一局）。
    # 段开工先报一句：一条长轮次（rollout+PPO）里 hub 的 hold 活性全靠这些完成事件。
    progress_hook.report("seg-start", it=todo[0], force=True)
    prev = start_from
    stopped = "complete"
    #: 待评的轮次（**一个**）：本节点拍完这一轮后登记，**下一轮 PPO 开跑那一刻**才提交 ——
    #: 于是评估整个藏在那个 PPO 的墙钟里（它不与 rollout 抢 CPU，也不占轮边界）。
    pending_eval: list[int] = []
    #: 本拍提交给评估的是哪一轮（收线时只用来写日志）。
    submitted_eval: list[int] = []

    def _submit_pending_eval() -> None:
        """`on_ppo_start` 回调：PPO 开跑的那一刻把上一轮的评估丢出去（不等待）。"""
        if not pending_eval:
            return
        eit = int(pending_eval.pop())
        submitted_eval.append(eit)
        _submit_cloud_eval(ctx, eit)

    try:
        for it in todo:
            # ★ 2026-10-23（plan/push-mode-notebooks §6.4 / W4）：**轮边界**看取消哨兵。
            # 位置刻意是「本轮开工之前」且**先于**预算闸：已落盘的轮一条不丢、当前轮不会跑成
            # 半截；人点的取消优先于「预算刚好到点」（两者都停，但 `state` 要报真因），
            # 而 `state="cancelled"` 会随 final 一起回传（hub 据此 release/换段，不当失败）。
            if ctx.stop_file and Path(ctx.stop_file).exists():
                stopped = "cancelled"
                ctx.log(
                    f"收到取消（{ctx.stop_file}）——在 it{it} **开始之前**干净停机"
                    "（产物自洽，可续跑）"
                )
                break
            if ctx.budget_left() <= 0:
                stopped = "budget"
                ctx.log(
                    f"预算用尽（{ctx.budget_sec:.0f}s）——在 it{it} **开始之前**干净停机"
                    "（产物自洽，可续跑）"
                )
                break
            t0 = time.time()
            progress_hook.report("round-start", it=it, force=True)
            result = _run_with_retries(
                ctx, it, prev_it=prev, on_ppo_start=_submit_pending_eval
            )
            session.append(_checkpoint(ctx, it, result, wall_sec=round(time.time() - t0, 2)))
            # 轮边界「上传前」那一句：落盘/补传都已完成（`_checkpoint` 内部先落盘再尽力出网）。
            progress_hook.report("round-done", it=it, force=True)
            # 收线：这一拍提交的评估已经在**本轮 PPO** 里跑了几十秒 ⇒ 通常零等待（有界兜底）。
            if submitted_eval:
                _drain_cloud_eval(ctx, submitted_eval.pop())
            # 本轮该评 ⇒ 登记，**下一轮 PPO 开跑时**提交（那一秒 rollout 已收工、TPU 空转）。
            if _eval_due(ctx, it):
                pending_eval.append(int(it))
            prev = it
    except (ProtocolError, RetryableError) as e:
        fail_summary = {"last_it": prev, "error": f"{type(e).__name__}: {e}"}
        ctx.store.finalize(state="failed", summary=fail_summary, full_for=_pack_full_for(ctx))
        ctx.log(f"自主段在 it{prev + 1} 处失败（{type(e).__name__}: {e}）——产物已收尾，可续跑")
        # 段失败也要报：控制面最需要的就是「这条腿停了、停在哪、为什么」——云机那一侧
        # 的日志人看不到，而失败是**最该被看见**的状态。
        ctx.deliver_final(it_end=prev, state="failed", summary=fail_summary)
        ctx.close_delivery(timeout=min(DRAIN_FLUSH_SEC, 30.0))  # 失败收尾少等：先让人看到失败
        # 失败也要收评估：已评的局是「这条腿到底怎么样」的一手证据（比失败本身更有用）
        _close_eval(ctx, timeout=min(runner_timeout(ctx), 120.0))
        raise
    # 收尾顺序刻意是「评估 → 产物打包 → 补传摘要」：finalize 打的 artifacts.zip 要**包含**
    # 刚收完线的评估账本（eval_log.jsonl），而摘要里报的行数/末轮才与包一致。
    _close_eval(ctx)
    summary = {"last_it": prev, "rows": len(ctx.store.rows), "session": len(session)}
    ctx.store.finalize(
        state="complete" if stopped == "complete" else stopped,
        summary=summary,
        full_for=_pack_full_for(ctx),
    )
    # T6：跑满计划区间才自报 `end_it_reached`（预算/失败停下的段不算——它们还要人接管）。
    ctx.deliver_final(
        it_end=prev,
        state=stopped,
        summary=summary,
        end_it_reached=_end_it_reached(ctx.plan, state=stopped, it_end=prev),
        seg_end_kind=_seg_end_kind(ctx.plan, state=stopped, it_end=prev),
    )
    ctx.close_delivery()
    return _combined(ctx, last_it=prev, session=session, state=stopped)


def _run_with_retries(
    ctx: RunContext, it: int, *, prev_it: int, on_ppo_start: Callable[[], None] | None = None
) -> dict:
    """单轮 + 有限重试（自主模式没有 hub 兜底：瞬时失败重试，确定性失败直接上抛）。"""
    last: BaseException | None = None
    for attempt in range(1, ITER_RETRIES + 2):
        try:
            return _run_iteration(ctx, it, prev_it=prev_it, on_ppo_start=on_ppo_start)
        except RetryableError as e:  # 瞬时（单局超时 / rc≠0 / 传输）——重试值得
            last = e
            ctx.log(f"it{it} 第 {attempt} 次失败（瞬时）：{e}")
            # ★M3 / Q2：重试也是「还在干活」的一个事实——报一句（打点层自己节流；没有
            # 打点层时（本机训练）就一次 dict 查找，什么都不做）。
            progress_hook.report("retry", it=it, done=attempt)
    assert last is not None
    raise last


def _combined(ctx: RunContext, *, last_it: int, session: list[dict], state: str) -> dict:
    """合并结果：末轮形状（直接走 hub 既有落位链）+ `iters` 明细 + `artifacts` 元信息。

    为什么保持「末轮形状」而不是发明新结构：hub 侧 `verify_and_land` 逐字段对的是**本
    job 自己**的 data_fp / init_weights_fp / commit_echo，那三个字段这里原样保留 ⇒ 半离
    线段落地**零新代码**。逐轮明细是 additive 的（旧读方忽略未知键）。
    """
    manifest = ctx.manifest
    # 账本一行 = 一轮训练（起点快照不记账，所以这里不需要任何过滤）：`iters` 的形状
    # 校验交给协议层（`validate_result` 要求逐轮 agg + 严格递增 it），这里只负责
    # 「一轮都没有」这个更早的判据——拿空 iters 当成功结果会污染 hub 账本。
    rows = sorted(ctx.store.rows, key=lambda r: int(r["it"]))
    if not rows:
        raise ProtocolError(
            "自主段没有任何一轮的采集报告可回传（iters 为空）——这不是一个成功的结果"
            f"（产物目录 {ctx.store.root}）"
        )
    last_row = rows[-1]
    last_it = int(last_row["it"])
    result: dict = {
        "job_id": manifest["job_id"],
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(ctx.store.weights_path(last_it).read_bytes()),
        "opt_tar_b64": _encode_opt(ctx.store.opt_path(last_it)),
        "agg": dict(last_row.get("agg", {}) or {}),
        "report": dict(last_row.get("report", {}) or {}),
        "commit_echo": manifest["commit"],
        "ppo_sec": float(sum(float(r.get("ppo_sec", 0.0) or 0.0) for r in session)),
        "iters": [
            {
                "it": int(r["it"]),
                "agg": dict(r.get("agg", {}) or {}),
                "report": dict(r.get("report", {}) or {}),
                "wall_sec": float(r.get("wall_sec", 0.0) or 0.0),
                "weights_fp": str(r.get("weights_fp", "")),
            }
            for r in rows
        ],
        "it_end": last_it,
        "run_state": str(state),
        "plan_sha256": ctx.plan_sha256,
        "artifacts": {
            "dir": str(ctx.store.root),
            "zip": str(ctx.store.root / ArtifactStore.ALL_ZIP),
            "rows": len(ctx.store.rows),
            # ★ 2026-10-11 事故同族：这个键曾经叫 `start_it` 而值是 `manifest["it"]`（= 本段
            # 锚点轮 = `plan.start_it + 1`）⇒ 任何按「起点」读它的下游都会拿错一轮。
            # 键名改成它真正的语义（
            # `plan.start_it` 是**计划区间左端点**，不是本段锚点）。零读者，改名无旁开销。
            "anchor_it": int(manifest["it"]),
        },
    }
    return result


def _encode_opt(opt_path: Path) -> str:
    return encode_opt_tar(opt_path.read_bytes()) if opt_path.exists() else ""
