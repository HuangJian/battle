"""remote/train_core.py —— 作业执行的**训练核**：从算子到产物（2026-09-24 从 `remote/worker.py` 下沉）。

原来 `run_job` 是一段 752 行的直线：下载 → 校验 → **训练** → 产物 → 上报。本模块拿走中间那段
**真正的调用链**（约 427 行）：

    课程上下文 / reward_fn → torch + 种子 → 模型构建（`opt_init` 优先）→ opt 内容寻址解析 →
    （cuda-dp 包装）→ BC kickstart ref（那把尺子）→ demo 混 batch（演示银行）→
    PPO（load → chunk → update）→ 产物（weights_json + `_ppo_save` tar）
    → 合成 `result`（含 M0 传输账）

留下的「作业壳」（`remote/worker.py`）只管网络与上报：取 payload/code → 三道校验 → kind 分叉 →
冒烟回显 → **调本模块** → 落盘 / 上报 / 半离线尾巴。

## 为什么是**下沉**而不是「注入作业执行器」

它依赖的东西最深到 L3（`download._resolve_blob` 的 blob 内容寻址、`job_fs` 的作业工作区、
`job_lifecycle.job_body_error`），而 `wire`/`http` 在 L1/L2 ⇒ 本模块的**拓扑秩只能是 L4**
（账本里与 `hub_server` 同层：一个组装训练，一个组装 hub），`worker` 因此升到 L5。
宿主仍只**注入回调**（`should_cancel` / `on_ppo_start`）——「取消信号从哪来」是宿主的事
（`start_cancel_watcher` 在 L3）。

★ 注入点：本模块读的模块全局（`time` / `_resolve_blob` / `pack_opt_tar` / `_wire_time` …）都是
**本模块命名空间**的——测试要拦它们请 patch `remote.train_core.*`。这一刀是 **seam-free** 的：
全仓实测**没有任何**测试 patch 过这一族的名字（`_resolve_blob` / `time` / `pack_opt_tar` /
`unpack_opt_tar` / `job_body_error` / `_wire_block` / `_persist_result` / `should_cancel` … 零命中），
所以 `worker` 侧不必留转发名（只 `run_training_core` 一个是新名字）。

★ **顶层零 torch / 零 numpy / 零 ppo**：三者都是**函数内**延迟 import（`_run_bc_job` 那条纪律的
同款；`tests/remote/test_train_core_split.py` 机械钉住「顶层没有」+「函数体里有」）。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import remote.course_cache as course_cache
import remote.opt_delta as opt_delta
import remote.opt_standard as opt_standard
import remote.paddle_env as paddle_env
import remote.weights_delta as weights_delta
from common.log_bundle import LogBundle
from common.progress_hook import report as _report_progress
from common.protocol import (
    BLOB_DEMO,
    BLOB_OPT,
    BLOB_REF,
    JobCancelledError,
    ProtocolError,
    encode_opt_tar,
    encode_weights_json,
    job_seed,
    pack_result_v2,
)
from remote.download import (
    WEIGHT_SOURCES,
    _cache_blob,
    _cache_produced_weights,
    _resolve_blob,
    resolve_blobs_batch,
)
from remote.job_fs import pack_opt_tar, unpack_opt_tar
from remote.job_lifecycle import job_body_error
from remote.train_common import (
    blob_wanted,
    check_demo_bank,
    course_prep,
    decode_demo_bank,
    kick_kl_value,
    ppo_shards_root,
)
from remote.wire import _wire_block, _wire_time, note_ppo_tail


def _load_opt_state(path: str | Path) -> tuple[Any, int]:
    """装载 opt 状态，并在 `load_state_dict` 之前做 f16 下溢消毒（P0）。

    返回 `(state, raised)`；`raised` = 被抬高的 `exp_avg_sq` **元素数**。
    **不回写** `path`：回传 delta 的 base 必须保持原样（产物段会重新从
    `opt_dir/opt.pt` load 一次当 base）；两侧 base 逐字相同是 delta 语义前提。
    """
    import torch

    state = torch.load(path, map_location="cpu")
    raised = opt_delta.sanitize_adam_sq(state)
    return state, raised


def _resolved(
    *,
    jid: str,
    name: str,
    sha: str,
    inline_b64: str,
    base_url: str,
    token: str,
    blob_root: Path,
    preloaded: dict | None,
    batch: dict | None,
    log,
) -> tuple[bytes, bool, str]:
    """先查本轮批量解析的结果（`resolve_blobs_batch`），查不到再逐字退回 `_resolve_blob`。

    为什么要有这一层而不是直接改 `_resolve_blob`：批量只覆盖「sha 非空 **且** 本轮真需要」
    的件；其余形状（sha 空 + inline、旧 hub 回退、kick 关闭）必须走老路径。**退回而不是
    重写**，才不会把 `_resolve_blob` 那套安全阀分叉成两份（那是静默 warm-start 的入口）。
    """
    got: tuple[bytes, bool, str] | None = (batch or {}).get(name)
    if got is not None:
        return got
    return _resolve_blob(
        blob_root=blob_root,
        name=name,
        sha=sha,
        inline_b64=inline_b64,
        jid=jid,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )


def _resolve_opt_init(
    *,
    jid: str,
    manifest: dict,
    job_dir: Path,
    blob_root: Path,
    base_url: str,
    token: str,
    preloaded: dict | None,
    log: Callable[[str], None],
    rebuild: Callable[..., bytes] | None = None,
) -> tuple[bytes, str, float, dict[str, str], dict, int, int]:
    """opt 初始状态的**字节解析**（身份命中 / 批量 GET / 单取 / BOD1 落地）——两条核共用。

    返回 `(raw, src, kick_kl, wanted, batch, hits, miss)`。★ 它同时是 **B1 那次合成 GET 的
    装配点**（plan/aistudio-transfer-hardening §3.4）：`wanted`/`batch` 还给 ref 段复用，
    ref 不再各发一次 GET。

    判定顺序有硬约束（plan/ppo-worker-wallclock §4.3 W1b）：身份命中必须在批量解析**之前**
    ——`resolve_blobs_batch` 不是惰性的（剩下的当场下载落盘），判晚了即使命中，opt 的下行
    也已经付掉。`rebuild` 只给 Paddle 核：那台机器没有 torch，BOD1 容器要用 numpy 落地。
    """
    opt_sha = str(manifest.get("opt_sha", "") or "")
    # ---- B1：kick 开关是**纯 manifest 读** ⇒ 可以在取字节之前判（ref 进不进本批）----
    raw_kick = float(manifest.get("kickstart_kl", 0.0) or 0.0)
    kick_kl = kick_kl_value(manifest)
    if raw_kick != 0.0 and kick_kl == 0.0:
        log(f"job {jid}: kickstart_kl={raw_kick:g} 低于阈值 —— 按关闭处理（省 ref 加载+预计算）")
    local = course_cache.hit(
        cache_root=course_cache.cache_root_of(blob_root),
        blob_root=blob_root,
        course_fp=str(manifest.get("course_fp", "") or ""),
        opt_from=str(manifest.get("opt_from", "") or ""),
        expect_it=int(manifest.get("it", 0) or 0) - 1,
        log=log,
    )
    # 身份命中 ⇒ opt 不进本批（`skip` 是纯参数；`ref` 照旧进批——kick 打开时它是另一件）。
    wanted = blob_wanted(
        manifest,
        kick_kl=kick_kl,
        skip=frozenset({BLOB_OPT}) if local is not None else frozenset(),
    )
    batch = (
        resolve_blobs_batch(
            wanted=wanted,
            blob_root=blob_root,
            jid=jid,
            base_url=base_url,
            token=token,
            preloaded=preloaded,
            log=log,
        )
        if wanted
        else {}
    )
    hits = miss = 0
    if local is not None:
        opt_raw, src = local["opt"], "course_cache"
        log(
            f"job {jid}: opt 身份命中（course_cache）——本机 f32 副本，零字节"
            "（hub 权威链的延续点，由回传受理时确认）"
        )
        # 身份命中也是命中（`src=course_cache`）——漏记会让 wire 账看不见这一笔。
        hits += 1
    else:
        opt_raw, opt_hit, src = _resolved(
            jid=jid,
            name=BLOB_OPT,
            sha=opt_sha,
            inline_b64=str(manifest.get("opt_init", "") or ""),
            base_url=base_url,
            token=token,
            blob_root=blob_root,
            preloaded=preloaded,
            batch=batch,
            log=log,
        )
        if opt_hit and src == "cache":
            hits += 1
        if src == "download":
            miss += len(opt_raw)
    # 安全阀（plan §4.3）：opt_sha 存在而 blob 不可得 → 响亮失败，绝不静默 warm-start
    # （那会把 D5 的 Adam 动量悄悄归零，日志上却一切正常）。
    if opt_sha and not opt_raw:
        raise ProtocolError(
            f"job {jid}: opt_sha={opt_sha[:12]}… 存在但 blob 不可得——拒收"
            "（不许静默退回 warm-start，D5）"
        )
    if opt_raw and opt_delta.is_container(opt_raw):
        # BOD1 容器（离线 delta 读侧，2026-10-15）：装载前落成整份。基准找不到就**响亮拒**
        # （拿错动量 = 形状对、数值错的静默降质，比拒收危险）。
        opt_raw = opt_standard.land_opt_bytes(
            opt_raw,
            blob_roots=(blob_root,),
            extra_roots=(job_dir, Path(job_dir).parent),
            log=log,
            rebuild=rebuild,
        )
        log(
            f"job {jid}: opt init 是 BOD1 容器 ⇒ 本机读侧落地成整份后再装载"
            + ("" if rebuild is None else "（numpy）")
        )
    return opt_raw, src, kick_kl, wanted, batch, hits, miss


def _fetch_ref(
    *,
    jid: str,
    manifest: dict,
    job_dir: Path,
    blob_root: Path,
    base_url: str,
    token: str,
    preloaded: dict | None,
    batch: dict,
    wanted: dict[str, str],
    kick_kl: float,
    log: Callable[[str], None],
) -> tuple[Path, int, int] | None:
    """kickstart ref 的**字节**获取 + 两道校验（系数 0 ⇒ `None`，零开销）。

    模型构建留在两条核里（各用各的 `build_ppo`）：本函数只保证「同一份字节、同一套安全阀」
    ——有系数没尺子＝静默裸奔，缺字节/指纹不符都**响亮拒**。`sha` 的唯一来源是本轮装批的
    结果（`wanted`），不再读一次 manifest（两份读法 = 未来会分叉）。
    """
    if kick_kl <= 0:
        return None
    ref_raw, ref_hit, ref_src = _resolved(
        jid=jid,
        name=BLOB_REF,
        sha=wanted.get(BLOB_REF, ""),
        inline_b64=str(manifest.get("ref_weights_b64", "") or ""),
        base_url=base_url,
        token=token,
        blob_root=blob_root,
        preloaded=preloaded,
        batch=batch,
        log=log,
    )
    if not ref_raw:
        raise ProtocolError(f"job {jid}: kickstart_kl={kick_kl} 但无 ref_weights——拒收")
    ref_fp = str(manifest.get("ref_weights_fp", "") or "")
    if ref_fp and hashlib.sha256(ref_raw).hexdigest() != ref_fp:
        raise ProtocolError(f"job {jid}: ref_weights 指纹不符——拒收")
    ref_path = job_dir / "ref_weights.json"
    ref_path.write_bytes(ref_raw)
    hit = 1 if (ref_hit and ref_src == "cache") else 0
    miss = len(ref_raw) if ref_src == "download" else 0
    return ref_path, hit, miss


def _fetch_demo(
    *,
    jid: str,
    manifest: dict,
    blob_root: Path,
    base_url: str,
    token: str,
    preloaded: dict | None,
    log: Callable[[str], None],
) -> tuple[float, int, bytes, str, int, int]:
    """demo bank 的**字节**获取（系数/每 mb 任一为 0 ⇒ 全零 = 不加载，零开销）。

    安全阀同 ref：coef>0 而 bank 不可得 ⇒ 响亮失败，绝不静默降级为纯 PPO（那会让 demo 腿
    的整轮更新在日志一切正常下丢失 demo 项）。
    """
    coef = float(manifest.get("demo_bc_coef", 0.0) or 0.0)
    per_mb = int(manifest.get("demo_per_mb", 0) or 0)
    if not (coef > 0 and per_mb > 0):
        return coef, per_mb, b"", "", 0, 0
    demo_sha = str(manifest.get("demo_sha", "") or "")
    if not demo_sha:
        raise ProtocolError(f"job {jid}: demo_bc_coef>0 但无 demo_sha——拒收")
    demo_raw, demo_hit, demo_src = _resolve_blob(
        blob_root=blob_root,
        name=BLOB_DEMO,
        sha=demo_sha,
        inline_b64="",
        jid=jid,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )
    if not demo_raw:
        raise ProtocolError(f"job {jid}: demo_sha 存在但 blob 不可得——拒收")
    hit = 1 if (demo_hit and demo_src == "cache") else 0
    miss = len(demo_raw) if demo_src == "download" else 0
    return coef, per_mb, demo_raw, demo_src, hit, miss


def _ppo_done_log(
    jid: str, t_ppo: float, train_b: LogBundle, chunks: list, total_steps: int, agg: dict
) -> float:
    """PPO 收尾读数（两条核同一组账、同一行措辞）→ 返回 `ppo_sec`（产物账要用）。

    不再单列 `job … PPO done in …` 一行：steps/chunks/kl + engine 折叠读数并入下游 wire
    总账行（`wire._wire_flush` 读取 `_WIRE[jid]["ppo_line"]` 拼到行尾，2026-10-28）。
    """
    ppo_sec = round(time.time() - t_ppo, 1)
    _wire_time(jid, "ppo", time.time() - t_ppo)
    train_b.add("steps", f"{sum(c['obs'].shape[0] for c in chunks)}/{total_steps}（训练/池子）")
    train_b.add("chunks", len(chunks))
    train_b.add("kl", agg.get("kl"))
    ppo_line = train_b.text()
    if ppo_line:
        note_ppo_tail(jid, ppo_line)
    return ppo_sec


def _agg_block(agg: dict, chunks: list, total_steps: int) -> dict:
    """进 `result.agg` 的读数——两条核同一份字段（hub 侧按 backend 无关的同一形状读）。

    value/policy 的 `*_new_rms` 是门控遥测（x26-obs it90 / x27 冻结判决）：旧 worker 无此键
    ⇒ 0.0，additive；`steps` 是 mb 对齐后**实际训练**的步数（池子总量另有 `steps_pooled`）。
    """
    return {
        "policy": float(agg.get("policy", 0.0)),
        "value": float(agg.get("value", 0.0)),
        "entropy": float(agg.get("entropy", 0.0)),
        "kl": float(agg.get("kl", 0.0)),
        "kickstart": float(agg.get("kickstart", 0.0)),
        "demo_bc": float(agg.get("demo_bc", 0.0)),
        "mean_ret": float(agg.get("mean_ret", 0.0)),
        "value_new_rms": float(agg.get("value_new_rms", 0.0) or 0.0),
        "policy_new_rms": float(agg.get("policy_new_rms", 0.0) or 0.0),
        "steps": int(sum(c["obs"].shape[0] for c in chunks)),
        "steps_pooled": int(total_steps),
        "chunks": len(chunks),
    }



def run_training_core(
    base_url: str,
    token: str,
    *,
    jid: str,
    job_dir: Path,
    work_dir: Path,
    manifest: dict,
    iter_info: dict | None,
    blob_root: Path,
    payload_bytes: int,
    payload_dl_sec: float,
    unpack_sec: float,
    ts_code_bytes: int,
    ts_code_hit: bool,
    device: Any,
    torch_threads: int,
    #: ★ 2026-10-26：训练侧放大 —— PPO 每轮更新遍数 × N（缺省 1.0 = manifest 原样）。
    #: 与离线盘 `CFG.epoch_mult` 同义：只改遍数，不改采样量/评估语料，也不改 manifest 身份。
    epoch_mult: float = 1.0,
    #: ★ 2026-10-27（用户口径②③）：本节点用哪个框架跑这段训练核（`"torch"` / `"paddle"`）。
    #: 它是**节点本地事实**（作业壳的 `--backend` 显式参数一路传下来），不进 manifest、
    #: 不上报 hub —— hub 只看到「谁认领、回传了什么」，两类 worker 混跑同一门课。
    backend: str = "torch",
    preloaded: dict | None,
    # opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md §3.2/§3.3）：初始权重由**作业壳**
    # 在 rollout **之前**解析好传进来（`_resolve_weights` 现在住 `remote/download.py`）。
    # 为什么解析不能留在本模块：kind=iter 的 rollout argv 是 `--weights init_weights.json`
    # （相对 job 目录），而本模块在 rollout **之后**才被调到 —— 权重不在就起不来。
    # 另：`blob_hits` / `blob_miss_bytes` 的 init 那一手账也在壳里记，本模块只在其上累加。
    init_w: Path | None,
    weights_src: str,
    #: 权重解析那一段**走网络**的字节数（命中/payload 恒 0）——`wire.weights_bytes` 的取值源。
    weights_wire_bytes: int,
    blob_hits: int,
    blob_miss_bytes: int,
    # 日志节食（2026-09-24）：作业壳把本 job 的「入口」读数先放进 prep 这个 bundle，
    # 本模块再叠加「设备/装载」读数，装载完成时打**一行**（`nn-training/common/log_bundle.py`）。
    prep: Any = None,
    t_prep: float = 0.0,
    should_cancel: Callable[[], bool] | None = None,
    on_ppo_start: Callable[[], None] | None = None,
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[dict, Any]:
    """**训练核**：把一个轮次算出来、把产物落进 `job_dir`，返回 `(result, course)`。

    入参全是「作业壳已经测好的事实」：payload 的**字节数**与两段耗时（`wire` 账要用，但字节
    本身不进来）、TS 代码缓存命中、blob 缓存根、本次 payload 预取命中（`preloaded`）。
    出参只有两个：`result`（拿去做 `validate_result` + 上报 + 半离线尾巴合并）与 `course`
    （半离线补传要用它把课程名带回去）。

    `base_url` / `token` 只为 blob 内容寻址下载（cache miss 时）——命中即零网络。
    """
    if backend == "paddle":
        # ★ Paddle 核是**独立函数**（下面）而不是本函数里的分支：本函数第一段就 `import torch`，
        #   而 AI Studio 的训练任务镜像里根本没有 torch ⇒ 必须在它之前分出去。
        return _run_training_core_paddle(
            base_url,
            token,
            jid=jid,
            job_dir=job_dir,
            work_dir=work_dir,
            manifest=manifest,
            iter_info=iter_info,
            blob_root=blob_root,
            payload_bytes=payload_bytes,
            payload_dl_sec=payload_dl_sec,
            unpack_sec=unpack_sec,
            ts_code_bytes=ts_code_bytes,
            ts_code_hit=ts_code_hit,
            device=device,
            torch_threads=torch_threads,
            epoch_mult=epoch_mult,
            preloaded=preloaded,
            init_w=init_w,
            weights_src=weights_src,
            weights_wire_bytes=weights_wire_bytes,
            blob_hits=blob_hits,
            blob_miss_bytes=blob_miss_bytes,
            prep=prep,
            t_prep=t_prep,
            should_cancel=should_cancel,
            on_ppo_start=on_ppo_start,
            log=log,
        )
    if backend != "torch":
        raise ProtocolError(f"未知训练后端 backend={backend!r}（只认 torch / paddle）")
    prep_b = LogBundle(log) if prep is None else prep
    import numpy as np

    # ---- 课程上下文：快照全文 → CourseConfig → reward_fn（D1/D6/D13）+ 本轮有效遍数 ----
    course, epochs_eff = course_prep(manifest, job_dir, epoch_mult, prep_b)

    # ---- 延迟导入 torch / ppo 后端（B7 同款；本模块顶层零 torch） ----
    import torch

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)
    import worker.ppo.engine as ppo_engine
    from worker.data.weights_io import load_state_into, save_weights_json

    # ---- per-job 确定性种子（D5）：load/chunk/update 前重新播种 ----
    # numpy RandomState 种子必须 < 2^32：sha256 前 8 个 hex 字符（32 bit）
    seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
    np.random.seed(int(seed_hex[:8], 16))

    # ---- 模型构建：opt_init（_ppo_save tar）优先，否则 init 权重 + 新 opt ----
    # hub 侧免 torch（D2）：模型权重/opt 由 tar 或 weights_json 提供，worker 负责
    # 重建——tar 内 model.pt = 上一轮 PPO 终态（含 Adam 动量，D5）。
    # ★ opt-blob-diet（2026-09-24）：`init_w` 由**作业壳**的 `_resolve_weights` 解析（五源，§3.2），
    #   此处不再自行推导路径。**arch 必须从真实权重读**——缺文件会静默退回 per-tick 默认
    #   64/8/128，那是「建了错的模型却照跑」（§3.3-2）。
    # 标注为 torch.nn.Module（而非推断出的 PPOStudent）：多卡分支要把 model 换成
    # DataParallel，且下游 save_weights_json / load_state_into 收的就是 Module。
    t_build = time.time()
    model: torch.nn.Module = ppo_engine.build_ppo(
        str(init_w) if (init_w is not None and init_w.exists()) else None
    )
    prep_b.add("建模型", f"{time.time() - t_build:.1f}s（build_ppo）")
    # 设备解析（2026-09-10 TPU 接力）：--device tpu/xla 走 torch_xla 的 xla_device()，
    # 而不是 torch.device("xla")——后者在部分 torch_xla 版本上拿不到带序号的设备句柄。
    # torch_xla 只在真的选了 TPU 时才 import（未装 torch_xla 的机器行为不变）。
    # `device` 已在 run_job 入口（kind 分叉之前）过 normalize_ppo_device，"auto" 不再可能到达这里。
    dev_str = str(device)
    use_dp = False
    if dev_str in ("tpu", "xla"):
        # 统一走 ppo.common.xla_device()（torch_xla.device() 优先，旧版回退 xm.xla_device()）
        from worker.ppo.common import (
            tpu_backend_missing_reason,
            xla_device,
            xla_device_speed_probe,
            xla_enable_compile_cache,
            xla_fingerprint,
            xla_world_size,
        )

        # 持久化编译缓存：必须在**任何计算之前**（下面 xla_device() 之后的指纹/速度自检就会
        # 产生第一张图）。缓存被挤出时读盘而非重编，不改变任何数值——真机 ragged tail 每轮
        # 多付的 ~14s 就是缓存淘汰后的重编（docs/nn/tpu-perf.md §6）。
        prep_b.add(
            "XLA 编译缓存",
            f"{xla_enable_compile_cache(work_dir / 'xla-compile-cache')}",
        )
        device_t = xla_device()
        # ★ 2026-09-22（Kaggle TPU 实例上离线课程 PPO 单步 8~9s ⇒ 疑似静默跑 CPU）：XLA 的
        #   CPU 插件也返回 `xla:0`，所以「设备字符串」证明不了什么；这里把**后端指纹**与一次
        #   速度自检打出来，并在后端不是 TPU 时**拒跑**（在 CPU 上跑完整段看起来一切正常，
        #   只是慢两个数量级——正是最该响的那类静默降级）。
        _fp = xla_fingerprint(device_t)
        _spd = xla_device_speed_probe(device_t)
        prep_b.add(
            "设备",
            f"{device_t}｜device_type={_fp['device_type']}｜"
            f"XLA 设备数={_fp['global_device_count']}（本进程可见 {_fp['addressable_device_count']}）｜"
            f"replication={_fp['replication_devices']}｜attrs={_fp['attrs']}｜"
            f"world_size={xla_world_size()}（无复制时恒 1，**别拿它当 TPU 判据**）｜"
            f"2048² matmul {(_spd * 1000) if _spd is not None else float('nan'):.1f} ms"
            "（TPU 量级 ~ms；~10ms+ = 后端是 CPU）",
        )
        _why = tpu_backend_missing_reason(_fp)
        if _why:
            # 拒跑是**要看的**：把攒着的设备读数先落下来（否则这行永远不出现）。
            prep_b.emit(f"job {jid}: 后端不是 TPU——拒跑")
            raise ProtocolError(
                f"job {jid}: 要的是 TPU，但 XLA 运行时不是 TPU 后端（{_why}）——**拒跑**。"
                "在 CPU 上跑完整段会看起来完全正常、只慢两个数量级，所以这里宁可停下："
                "① 确认 `PJRT_DEVICE=TPU` 在**任何** torch_xla import/初始化之前就已设置"
                "（XLA 运行时一经初始化就不能再换后端）；"
                "② Kaggle/Colab 上先单独打印 `torch_xla.runtime.device_type()` 与"
                "`global_device_count()` 对账（TPU v5e-8 ⇒ 8）；"
                "③ 若 ①② 都正常而设备属性仍无 TPU 指纹，重开 runtime（设备可能被别的进程占着）。"
            )
    elif dev_str in ("cuda-dp", "dp"):
        # 多卡（2026-09-10 实测 1.92×）：torch.device("cuda-dp") 不是合法设备，
        # 必须显式落到 cuda；真正的包装在 state_dict 装载之后（见下方 use_dp 段）。
        device_t = torch.device("cuda")
        use_dp = torch.cuda.is_available() and torch.cuda.device_count() > 1
    else:
        # 必须用**归一化后**的 dev_str，不是原始 device —— 2026-09-15 二次事故：
        # 上一版只把分派条件换成 dev_str，这里仍写 `torch.device(device)`，
        # 于是 auto 照样被喂进 torch.device（日志上「兜底为 cuda」打了、job 仍炸 auto）。
        device_t = torch.device(dev_str)
    # ---- W0-a（plan/ppo-worker-wallclock §4.0）：把「这一轮到底跑在什么上」按 job 打回来 ----
    # 日志里曾经彻底看不到卡数/形态：boot 写死 `use_multi_gpu=True`，而「DataParallel 生效」
    # 那行 2026-10-06 已退役 ⇒ 「这一轮跑了几张卡」只能靠人告诉。`dp=` 就是把它按 job 补回。
    # CPU-only worker 上 `torch.cuda.*` 会抛 ⇒ 必须 guard（`n=0 (cpu)` 仍给线程账）。
    if dev_str not in ("tpu", "xla"):
        _cu = torch.cuda.is_available()
        try:
            _gpu_name = torch.cuda.get_device_name(0) if _cu else "-"
            _gpu_cap = str(torch.cuda.get_device_capability(0)) if _cu else "-"
        except Exception:  # 驱动/可见性异常不该拖垮 prep
            _gpu_name, _gpu_cap = "?", "?"
        prep_b.add(
            "设备",
            f"{device_t}｜n={torch.cuda.device_count() if _cu else 0}"
            f"{'' if _cu else ' (cpu)'}｜[0]={_gpu_name}｜cap={_gpu_cap}｜"
            f"dp={use_dp}｜threads={torch.get_num_threads()}",
        )
    # ---- M2 B3 / R0 / B1：opt 解析（身份命中 → 批量 GET → 单取 → BOD1 落地）----
    # `blob_hits` / `blob_miss_bytes` 的 init 那一手账在作业壳的权重解析处建；本模块只在它
    # 上面累加 opt/ref/demo，最后一次性交给 `_wire_block`。
    opt_raw, opt_src, _kick_kl, _wanted, _batch, _h, _m = _resolve_opt_init(
        jid=jid,
        manifest=manifest,
        job_dir=job_dir,
        blob_root=blob_root,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )
    blob_hits += _h
    blob_miss_bytes += _m
    opt = None
    opt_restore_sec = 0.0
    # ★ §4.2（2026-09-21）：restore 段的崩溃归确定性失败（带 traceback 摘要）——
    # 它是本段最容易“静默重演”的一处（优化器/模型卷积不兼容会逐一重演到天亮）。
    try:
        if opt_raw:
            t_opt = time.time()
            opt_dir = job_dir / "opt_init"
            unpack_opt_tar(opt_raw, opt_dir)
            # 必须在 model.to(device_t) 之前加载 state_dict，然后统一移到目标设备。
            # 优先级按 §3.2：W0–W3（`init_w`）**先于** W4（tar 里的 model.pt）——两者
            # 在构造上就是同一份权重（tar 的 model.pt = 上一轮终态 = 本轮 init），但按
            # 声明的优先级取值，`weights_src` 才不会在「同时可得」时报出误导人的源。
            legacy_model = opt_dir / "model.pt"
            if init_w is not None and init_w.exists():
                load_state_into(model, str(init_w))
            elif legacy_model.exists():
                # ---- W4：旧形状 tar（改造前的字节 / hub 回退分支重打 / 旧 hub）----
                # 权重在 tar 里 ⇒ 逐字节走今天的老路（§3.2 W4 / §3.5 的兼容面）。
                model.load_state_dict(torch.load(legacy_model, map_location="cpu"))
                weights_src = "legacy_tar"
            else:
                raise ProtocolError(
                    "opt tar 无 model.pt 且权重四源皆尽"
                    f"（试过 {'/'.join(WEIGHT_SOURCES[:4])}）"
                    f"；init_weights_fp={str(manifest['init_weights_fp'])[:12]}…"
                    " —— 拒收（不许静默 warm-start，D5）"
                )
            model.to(device_t)
            opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
            # 统一 map_location="cpu"：Optimizer.load_state_dict 会把载入张量 cast 到
            # param 所在设备，所以 XLA/CPU/CUDA 三条路都靠这一句完成搬迁（原先写死
            # device_t 在 XLA 上会走 torch.load 的设备 hook，跨 runtime 不稳）。
            # ★ 2026-10-17：先经 `_load_opt_state` 做 f16 下溢消毒，再 load_state_dict；
            #   它只改内存副本，盘上 `opt_dir/opt.pt` 保持原样（产物段的 delta base 要用它）。
            opt_state, opt_raised = _load_opt_state(opt_dir / "opt.pt")
            opt.load_state_dict(opt_state)
            opt_restore_sec = round(time.time() - t_opt, 3)
            prep_b.add("opt", f"从 opt_init（{opt_src}）恢复 {opt_restore_sec:.1f}s（Adam 动量延续，D5）")
            if opt_raised:
                prep_b.add(
                    "opt",
                    f"下溢消毒：exp_avg_sq 抬高 {opt_raised} 个元素"
                    "（v=0/m≠0；DECISIONS §2026-10-17-goalnn-opt-delta-f16-underflow）",
                )
        else:
            # 首轮/无 tar：从 init 权重 warm-start（payload / init blob / blobs 下载）
            if init_w is None or not init_w.exists():
                raise ProtocolError(
                    "无 opt_init 且权重四源皆尽"
                    f"（试过 {'/'.join(WEIGHT_SOURCES[:4])}）"
                    f"；init_weights_fp={str(manifest['init_weights_fp'])[:12]}…"
                    " —— 拒收（不许静默 warm-start，D5）"
                )
            t_warm = time.time()
            load_state_into(model, str(init_w))
            model.to(device_t)
            opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
            prep_b.add(
                "opt", f"无 opt_init，从 init_weights warm-start {time.time() - t_warm:.1f}s + 新 Adam"
            )
    except ProtocolError:
        raise  # 上游已判定的确定性失败（缺 blob / 缺 init 权重）原样上抛
    except Exception as e:
        raise job_body_error("restore（model/opt 恢复）", e) from e

    # ---- 多卡（--device cuda-dp）----
    # 位置很重要：**必须在 load_state_dict / load_state_into + .to(device_t) 之后**再包，
    # 否则 ckpt 的键会长出 "module." 前缀。raw_model 始终指向未包装模块，产物落盘用它。
    # ⚠ DP 会改变梯度归约顺序 ⇒ 末位 ulp 变化，与单卡 run 的逐位 A/B 不可比；
    #   它是新开一条实验臂的开关，不是透明加速（plan/ppo-optimization.plan.md §3.4）。
    raw_model = model
    if dev_str in ("cuda-dp", "dp"):
        if use_dp:
            # `job …: DataParallel 生效（… 卡 …）` 行已退役（2026-10-06，用户指令「删除云机 worker
            # 刷屏 log」）：同一进程里 device 不变 ⇒ 它是**每 job 重复**的静态事实，而开机横幅已印
            # `device=cuda-dp`、rl-config 也记着；「DP 数值不可与单卡逐位比」的警告就在上面。
            # 退化告警（下面的 else）**保留**：那是真异常路径，不是复述。
            model = torch.nn.DataParallel(model)
        else:
            log(
                f"job {jid}: 请求了 cuda-dp 但只可见 {torch.cuda.device_count()} 张卡"
                " —— 退化为单卡（行为等同 --device cuda）"
            )

    # ---- BC-anchored kickstart ref（§363）：有系数无尺子＝静默裸奔，不可接受——
    # 缺字节响亮拒绝；系数为 0 直接跳过（零开销，旧 manifest 行为不变）。
    # ★ B1：判据**已上提**到 opt 解析之前（那里要用它决定 ref 进不进批量 GET）；这里只把
    #   上提算出的 `_kick_kl` 落回 `kick_kl`，字节获取走两核共用的 `_fetch_ref`。
    kick_kl = _kick_kl
    ref_model: torch.nn.Module | None = None
    _ref = _fetch_ref(
        jid=jid,
        manifest=manifest,
        job_dir=job_dir,
        blob_root=blob_root,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        batch=_batch,
        wanted=_wanted,
        kick_kl=kick_kl,
        log=log,
    )
    if _ref is not None:
        ref_path, _h, _m = _ref
        blob_hits += _h
        blob_miss_bytes += _m
        # ★ §4.2：ref 装载同属 restore——ref 与 policy 的架构/形状不合会在每一份字节上
        # 重演（而它只会被写成一行云机日志，训练侧看到的是超时）。
        try:
            t_ref = time.time()
            ref_model = ppo_engine.build_ppo(str(ref_path))
            load_state_into(ref_model, str(ref_path))
            for p in ref_model.parameters():
                p.requires_grad = False
            ref_model.eval()
            ref_model.to(device_t)
            if use_dp:
                ref_model = torch.nn.DataParallel(ref_model)
            prep_b.add("ref", f"{time.time() - t_ref:.1f}s（build+load）")
            log(f"job {jid}: kickstart ref 已加载（BC 冻结 master，kl={kick_kl}）")
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（kickstart ref 装载）", e) from e

    # ---- demo 混 batch（x20 后续）：bank 内容寻址 + 装载校验 ----
    # 安全阀同 ref：coef>0 而 bank 不可得 ⇒ 响亮失败，绝不静默降级为纯 PPO
    # （那会让 demo 腿的整轮更新在日志一切正常下丢失 demo 项）。
    demo_coef, demo_per_mb, demo_raw, demo_src, _h, _m = _fetch_demo(
        jid=jid,
        manifest=manifest,
        blob_root=blob_root,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )
    blob_hits += _h
    blob_miss_bytes += _m
    demo_bank: dict | None = None
    if demo_raw:
        try:
            bank = decode_demo_bank(demo_raw)
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（demo bank 装载）", e) from e
        check_demo_bank(jid, bank, demo_coef, demo_per_mb, demo_src, prep_b)
        demo_bank = bank

    # ---- PPO：load → chunk → update（同一 backend 调用链，D4） ----
    # ★ §4.2（2026-09-21）：grad 段（含读 shard / 分块）的崩溃归确定性失败。
    # 为什么连读 shard 一起包：那同样是「这份字节决定的」失败（缺字段/形状不符），
    # 而 OOM 那类真瞬态由 `job_body_error` 原样放回重领路径。
    # ★ 2026-10-11（plan/rollout-topup-fastpath §3.5 / 评审 P0-1）：装载根由**产出方**给出
    # （`iter_info["shards_root"]`）。换根档下 shard 住在 scratch、不再搬回 job 目录 ⇒ 这里若
    # 仍写 `str(job_dir)` 就会以 0 局语料开练（静默训空）。见 `ppo_shards_root`。
    shards_root = ppo_shards_root(job_dir, iter_info)
    if on_ppo_start is not None:
        try:
            # 打点失败不致命：最坏后果是这份 job 的掉队阈值晚起算（多开一份备份）。
            on_ppo_start()
        except Exception:
            pass
    # ★ 2026-10-25（用户口径）：日志必须说清**在干什么** —— 采集（iter）之外，PPO 这一段
    #   此前一个事件都没有，云机上看起来就是「一直停在 it102 的 iter」。报一遍数（不按
    #   epoch 逐条：那要动 `ppo_update` 的回调契约，见 `tests/hub/test_priority_schedule.py:700`）。
    _report_progress(
        "ppo",
        it=int(manifest.get("it", 0) or 0),
        done=0,
        total=epochs_eff,
        force=True,
    )

    def _cancel_at_epoch_boundary(_ep_done: int, _mdl: Any) -> None:
        """epoch 边界查一次取消（R1-6；延迟判据 <20s）——由 hub 的 landed 驱动。"""
        if should_cancel is None or not should_cancel():
            return
        raise JobCancelledError(
            f"job {jid}: 结果已 landed（别人先赢）——epoch {_ep_done} 边界停算丢弃"
        )

    if should_cancel is not None and should_cancel():
        # 下载/解包/装载期间结果就已 landed：**开算前**就丢，别白烧一整轮 PPO。
        # （epoch 边界那个回调只救得了「开算之后才 landed」的情形。）
        raise JobCancelledError(f"job {jid}: 结果已 landed（下载期间）——开算前丢弃")

    t_ppo = time.time()
    # 日志节食（2026-09-24）：PPO 这一段（epoch 行 + 收尾行）攒成一行，未完成时每 60s 心跳一次
    # ——与装载段的 `prep_b` 分开两本账：装载行**必须**在进 PPO 之前落下来（否则卡在 PPO 里
    # 就什么都看不见）。
    train_b = LogBundle(log)
    try:
        episodes = ppo_engine.load_episodes(
            shards_root,
            float(manifest["gamma"]),
            float(manifest["lam"]),
            normalize_adv=str(manifest["adv_norm"]) != "none",
            normalize_ret=bool(manifest.get("normalize_ret", False)),
            # 严格样本量配额（target_transitions 路线）：逐关只收前 N 步，截断在 GAE
            # 之前。0/缺失（旧 hub 产出的 manifest）= 全收，历史行为逐字节不变。
            per_stage_quota=int(manifest.get("per_stage_quota", 0) or 0),
            bundle=prep_b,
        )
        # ★ 装载完成 = 「本 job 准备」这一段结束：**一行**把所有入口/设备/opt/demo/装载
        # 读数一次说清（原来 ~10 行），随后才进 PPO（2026-09-24 日志节食）。
        prep_b.emit(f"job {jid}: 准备完成 {time.time() - t_prep:.1f}s")
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = ppo_engine.chunk_episodes(
            episodes, int(manifest["mb"]), shuffle=bool(manifest["shuffle"])
        )
        agg = ppo_engine.ppo_update(
            model,
            opt,
            chunks,
            epochs_eff,
            device_t,
            kl_coef=float(manifest["kl_coef"]),
            # ent_coef：None（旧 hub / 未配）→ 引擎常量 ENT_COEF；0.0 是合法值，不能 `or` 兜底。
            ent_coef=(
                None if manifest.get("ent_coef") is None else float(manifest["ent_coef"])
            ),
            ref_model=ref_model,
            kickstart_kl=kick_kl,
            demo_bank=demo_bank,
            demo_bc_coef=demo_coef,
            demo_per_mb=demo_per_mb,
            # value 门控牵引（x26-obs it90 诊断；缺席 = 0.0 = 关，老行为逐字节不变）。
            value_anchor=float(manifest.get("value_anchor", 0.0) or 0.0),
            # ★ 取消接线（R2-5）：今天这条调用**没有**传它——不传则取消延迟永远是
            # 「跑完才响应」。训练侧那条（`trainer/stream.py`）传的是双缓冲预采回调，
            # 与这里不是同一个调用点，别去动那一条。
            on_epoch_done=_cancel_at_epoch_boundary if should_cancel is not None else None,
            # 组 3（日志节食）：epoch 行 + PPO 完成行攒成一行，未完成时每 60s 心跳一次。
            progress=train_b,
            progress_head=f"job {jid}: PPO 训练中",
        )
    except ProtocolError:
        raise
    except JobCancelledError:
        # 取消是**正常结局**（备份副本被首写锁定判负）：绝不能落到下面的
        # `job_body_error`（它会把未知异常转成 ProtocolError ⇒ report_job_failure ⇒
        # 训练停腿——把合法放弃报成确定性失败）。
        raise
    except Exception as e:
        raise job_body_error("grad（PPO 更新）", e) from e
    # P0.5：T_ppo 进传输账（与 T_in/T_out 同一条 `wire` 行 ⇒ 占比可复算，不用人肉拼日志）。
    ppo_sec = _ppo_done_log(jid, t_ppo, train_b, chunks, total_steps, agg)
    # ---- P1-4（2026-10-21）：产物段自己的账（save_weights_json / _ppo_save / pack_*）----    # 这一段在关键路径上、**不属于 `ppo=`**（`t_ppo` 在 `ppo_update` 之后就已结束），
    # 但日志的 `other=` 格又被当成「准备段 CPU」解释了 ⇒ 归因不闭合。读数只在
    # `PPO_CUDA_DIAG=1` 时打一行（稳态不增行，日志节食的约束不变）。
    _t_art = time.time()

    # ---- 产物：weights_json（save_weights_json，D12/G1）+ _ppo_save tar（D5） ----
    # XLA：先落图执行边界再物化回主机。否则 state_dict() / save_weights_json 读到的是
    # 尚未执行的惰性图（权重是最新一轮 `mark_step` 时的快照，不是本轮终态）。
    from worker.ppo.common import HostModule, _ppo_save, xla_mark_step

    xla_mark_step(device_t)
    # ⚠ 用 raw_model 而非 model：DP 包装的 state_dict 键带 "module." 前缀（已实证），写出去会让
    #   ckpt 与单卡路径互不兼容（课程 resume 会炸）。raw_model 与 DP 共享参数对象，视图对两者等价。
    # ★ 2026-10-29：原来是 `raw_model.to("cpu")` —— torch_xla 上那是**逐张量**设备同步
    #   （42 参数 + `_ppo_save` 的 126 个 Adam 状态 = 168 次 × ~0.25s ≈ 42s，字节只有 1MB）。
    #   这里取**主机视图**（一次批量 D2H，模块本身不动）：给 Parameter 赋 CPU 张量会被
    #   autograd 拒（`set_data ... incompatible tensor type`，2026-10-29 真机）。
    host = HostModule(raw_model)
    wj_path = job_dir / "weights.json"
    save_weights_json(host, str(wj_path))
    # ★ opt-blob-diet（2026-09-24，§3.2 W1 / §3.6-8）：把本轮产出的 weights.json **原始
    # 字节**也写进 blob_cache（键 = sha256）。下一轮 hub 的 `init_weights_fp` =
    # `sha256(args.out)` = 同一份字节的 sha（`verify_and_land` 落的就是它解码后的原字节）
    # ⇒ 同会话内 W1 100% 命中、下行零字节。**少了这一行**：`init` blob 的键每轮都变、
    # **每轮必 miss** ⇒ 上行省 268,996 B 而下行多付 379,114 B = **净亏 110 KB/轮**（评审 F1）。
    wj_raw = wj_path.read_bytes()
    _cache_produced_weights(blob_root, wj_raw, log)
    # ---- R1（M3）：形态位命中 ⇒ 回传 **权重 delta 容器**（不吐 torch：JSON + numpy）----
    # 基准 = 本轮**实际载入**的那份 init 权重（`init_w` = 解析出来的那个文件；身份命中时它
    # 就是本机那份 f32 全量）。`base_sha` = 它的 sha256 ⇒ hub 侧就是 exact/identity 两档的
    # 判别读数（exact 档与 manifest 的 `init_weights_fp` 相等，可逐字节对账）。
    # 本机那份 JSON 原样（`wj_raw`）**照样**写 blob_cache/身份映射（下一轮的起点就是它）。
    wj_sent_raw = wj_raw
    if weights_delta.w_fmt_of(manifest) == weights_delta.W_DELTA_FMT:
        if init_w is None or not init_w.exists():
            raise ProtocolError(
                "形态位是权重 delta 但本轮没有 base 权重（init_weights.json 不在）——拒发"
            )
        base_w = init_w.read_bytes()
        wj_sent_raw = weights_delta.pack_weights_delta(
            base_w, wj_raw, base_sha=hashlib.sha256(base_w).hexdigest()
        )
        # ★ 2026-10-15（用户口径）：**发出去的那份字节也要留在本机**（键 = sha256(delta)）。
        #   hub 侧下一轮/下一段的 `init_weights_fp` 就是它原样存下来的这份 delta 的 sha ⇒
        #   这台机器再领到「以自己回传的 delta 为 init」的任务时，W1 直接命中本地缓存
        #   （**零字节日下传输**，不再把这份 delta 从 hub 拉回来，随后由读侧落地成整份）。
        #   与 `_cache_produced_weights`（那份是本机 f32 全量）并列：两条都留 ⇒ 不论 hub 声明的
        #   地址是「整份」还是「delta」，本机都不会重传自己已经持有的字节。
        _cache_blob(blob_root, hashlib.sha256(wj_sent_raw).hexdigest(), wj_sent_raw, log)
    ckpt_dir = job_dir / "ppo_final"
    _ppo_save(str(ckpt_dir), host, opt, epochs_eff)
    # 本机那份 f32 全量（身份映射与 blob_cache 都吃它）；R3 下它**不再是**回传字节本身
    opt_tar_raw = pack_opt_tar(ckpt_dir)
    # M2 B3：把刚产出的 raw opt tar 写进 blob_cache（键 = sha256）——下一轮 hub 的
    # opt_sha 由 verify_and_land 落盘的同一份原始字节算出，故同会话内 100% 命中。
    _cache_blob(blob_root, hashlib.sha256(opt_tar_raw).hexdigest(), opt_tar_raw, log)
    # ---- R3（M4）：形态位命中 ⇒ 回传 **delta 容器**（基准 = 本轮**实际载入**的那份 opt）----
    # 基准字节 = `opt_raw`（上面解析出来的那份：exact 档 = hub 发的 blob；identity 档 = 本机
    # 自己那条 f32 链）⇒ `base_sha` 就是「我拿哪份当基准」的记录（hub 侧的档位读数）。
    # 本机那份 f32 全量**照样**写 blob_cache/身份映射（下一轮的起点就是它）。
    opt_sent_raw = opt_tar_raw
    if opt_delta.opt_fmt_of(manifest) == opt_delta.OPT_DELTA_FMT:
        if not opt_raw:
            raise ProtocolError(
                "形态位是 delta 但本轮没有 base opt（manifest.opt_init/opt_sha 为空）——拒发"
            )
        from worker.ppo.common import _to_cpu_state  # torch 面：延迟 import（顶层零 torch）

        base_state = _to_cpu_state(torch.load(opt_dir / "opt.pt", map_location="cpu"))
        opt_sent_raw = opt_delta.pack_opt_delta(
            base_state,
            _to_cpu_state(opt.state_dict()),
            base_sha=hashlib.sha256(opt_raw).hexdigest(),
        )
    opt_tar_b64 = encode_opt_tar(opt_sent_raw)
    # ---- R0（M0）：记下「本机跑出的这份产物在哪」→ 身份映射的 **pending** ----
    # 只记 sha+字节数（两份副本本来就在 `blob_cache` 里，**不**复制字节）。它此时**还不算**
    # 可用起点：要等回传受理（`job_round` 的 settled 回调）才 `confirm` 升成 `latest`——
    # 输了竞速的那台也算出过一份产物，把它的链当 hub 权威链的延续点会静默分叉。
    course_cache.record_pending(
        course_cache.cache_root_of(blob_root),
        str(manifest.get("course_fp", "") or ""),
        job_id=jid,
        it=int(manifest.get("it", 0) or 0),
        run_id=str(manifest.get("runId", "") or ""),
        weights=(hashlib.sha256(wj_raw).hexdigest(), len(wj_raw)),
        opt=(hashlib.sha256(opt_tar_raw).hexdigest(), len(opt_tar_raw)),
        log=log,
    )

    # 产物段的秒数不再靠 `PPO_CUDA_DIAG`（那要人记得开）：随 `wire.art_sec` 进账本行，
    # 由 `plan_run` 拼进既有的落盘行（零新增行、每轮都有读数）。
    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        # 回传的 weights_json：旧形态用的是被缓存的那份字节（同字节 ⇒ 同 sha，别中途再读一次盘）；
        # R1 形态位命中时这里是 delta 容器（本机那份全量仍是 `wj_raw`，见上面产物段）。
        "weights_json": encode_weights_json(wj_sent_raw),
        "opt_tar_b64": opt_tar_b64,
        "agg": _agg_block(agg, chunks, total_steps),
        "commit_echo": manifest["commit"],
        "ppo_sec": ppo_sec,
    }
    if iter_info is not None:
        # M3：节点自己跑的 rollout 的采集口径（协议层必校——hub 侧无本地 shard 可算）。
        # ★ 2026-10-28（云机轮换）：分关采集账一并回传（控制台「各关进度」读的就是它）——
        #   不在这里搬一手，`iter_info` 里那份就随函数返回被丢了（老产物行没这个键 = 留空）。
        #   两条后端腿（本函数 / `_run_training_core_paddle`）各一行：同一份 `iter_info`。
        result.update({k: iter_info[k] for k in ("report", "volume_stage_stats") if k in iter_info})
    result["wire"] = _wire_block(
        payload_bytes=payload_bytes,
        payload_dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
        opt_restore_sec=opt_restore_sec,
        grad_sec=ppo_sec,
        # 产物段（save/pack/identity）自己的账：它不在 `ppo=` 里，是云机轮墙钟最大的一块暗区。
        art_sec=time.time() - _t_art,
        blob_hits=blob_hits,
        blob_miss_bytes=blob_miss_bytes,
        weights_src=weights_src,
        weights_bytes=weights_wire_bytes,
        ts_code_bytes=ts_code_bytes,
        ts_code_hit=ts_code_hit,
        rollout_sec=(iter_info or {}).get("rollout_sec"),
        bun_version=(iter_info or {}).get("bun_version"),
    )
    # 两遍收敛（同 echo 路径）：result_bytes 与自身体长自指，一遍差它的十进制位数。
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    return result, course


def _run_training_core_paddle(
    base_url: str,
    token: str,
    *,
    jid: str,
    job_dir: Path,
    work_dir: Path,
    manifest: dict,
    iter_info: dict | None,
    blob_root: Path,
    payload_bytes: int,
    payload_dl_sec: float,
    unpack_sec: float,
    ts_code_bytes: int,
    ts_code_hit: bool,
    device: Any,
    torch_threads: int,
    epoch_mult: float = 1.0,
    preloaded: dict | None,
    init_w: Path | None,
    weights_src: str,
    weights_wire_bytes: int,
    blob_hits: int,
    blob_miss_bytes: int,
    prep: Any = None,
    t_prep: float = 0.0,
    should_cancel: Callable[[], bool] | None = None,
    on_ppo_start: Callable[[], None] | None = None,
    log: Callable[[str], None] = lambda _msg: None,
) -> tuple[dict, Any]:
    """`run_training_core` 的 **Paddle 双生**（2026-10-27，AI Studio 训练任务镜像无 torch）。

    调用面、产物、结果字典与 torch 版**逐字段相同**：hub 侧不认识框架，落盘/对账/回传只认
    字节。差别只在框架那一半：

    * 模型/权重 JSON：`worker.models.student_paddle`（键名/形状与 torch 模型逐一对应）；
    * opt 的**文件格式仍是 torch 的**（用户口径①）：hub 的 `opt.pt` 由 `common.torch_pickle`
      读成 numpy 叶子，经 `paddle_state_to_torch` 译进 Paddle Adam；写出时
      `checkpoint_paddle.save_ckpt` 再译回 torch 形状 —— 第 1 轮 torch 跑、第 2 轮 Paddle
      跑、第 3 轮 torch 跑，动量与 `step` 一路连续；
    * BOD1 delta 的重建走 `opt_standard.rebuild_opt_tar_np`（同一份容器契约的 numpy 实现）；
    * Paddle 的 Adam 没有 `step`：步数由 `steps_init`（torch ckpt 的 step）+ 本轮
      `epochs_eff × len(chunks)` 记账（引擎每 minibatch +1，见 `engine_paddle.ppo_update`）；
    * 设备字符串由 Paddle 自己归一（`cuda` → `gpu`）；XLA / DataParallel / MPS 是 torch 专属
      实验臂 ⇒ **响亮拒收**，不静默退回 CPU（那会看起来一切正常、只是慢两个数量级）；
    * `torch_threads` 没有 `set_num_threads` 可用（Paddle 3.3 无此 API）⇒ 翻译成
      `OMP_NUM_THREADS` / `MKL_NUM_THREADS`，**在 import paddle/numpy 之前**写入
      （`_apply_thread_env`；已有值不覆盖，平台配额优先）。

    顶层零 torch/numpy/paddle 的纪律同样适用：三个框架都是**函数内**延迟 import。
    """
    prep_b = LogBundle(log) if prep is None else prep
    # `--threads` 在 Paddle 侧只能靠环境变量，且必须早于 import（见 remote/paddle_env.py）。
    paddle_env.apply(torch_threads, prep_b)
    import numpy as np
    import paddle

    # ---- 课程上下文 + 本轮有效遍数（与 torch 版共用同一个实现）----
    course, epochs_eff = course_prep(manifest, job_dir, epoch_mult, prep_b)

    # ---- 设备：XLA / DataParallel / MPS 是 torch 实验臂，Paddle 侧响亮拒 ----
    dev_str = paddle_env.check_device(jid, device)
    try:
        paddle.set_device(dev_str)
    except Exception as e:
        raise ProtocolError(
            f"job {jid}: paddle 设备 {dev_str!r} 不可用（{e}）——"
            "检查 --device 与本机是否编译了 CUDA"
        ) from None
    prep_b.add("设备", f"{paddle.get_device()}｜backend=paddle｜cuda→gpu 由 paddle 归一")

    # ---- per-job 确定性种子（D5）：与 torch 版同一条公式、同一个播种点 ----
    seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
    np.random.seed(int(seed_hex[:8], 16))

    import worker.models.student_paddle as student_paddle
    import worker.ppo.checkpoint_paddle as checkpoint_paddle
    import worker.ppo.engine_paddle as engine_paddle
    import worker.ppo.np_core as ppo_np
    from common import torch_pickle

    t_build = time.time()
    model = student_paddle.build_ppo(
        str(init_w) if (init_w is not None and init_w.exists()) else None
    )
    prep_b.add("建模型", f"{time.time() - t_build:.1f}s（student_paddle.build_ppo）")

    # ---- opt 解析（与 torch 版共用 `_resolve_opt_init`；只是 BOD1 落地换成 numpy 实现）----
    opt_raw, opt_src, _kick_kl, _wanted, _batch, _h, _m = _resolve_opt_init(
        jid=jid,
        manifest=manifest,
        job_dir=job_dir,
        blob_root=blob_root,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
        rebuild=opt_standard.rebuild_opt_tar_np,
    )
    blob_hits += _h
    blob_miss_bytes += _m

    opt = None
    opt_restore_sec = 0.0
    steps_init = 0
    opt_dir = job_dir / "opt_init"
    try:
        if opt_raw:
            t_opt = time.time()
            unpack_opt_tar(opt_raw, opt_dir)
            legacy_model = opt_dir / "model.pt"
            if init_w is not None and init_w.exists():
                student_paddle.load_state_into(model, str(init_w))
            elif legacy_model.exists():
                checkpoint_paddle.load_model_pt(model, legacy_model)
                weights_src = "legacy_tar"
            else:
                raise ProtocolError(
                    "opt tar 无 model.pt 且权重四源皆尽"
                    f"（试过 {'/'.join(WEIGHT_SOURCES[:4])}）"
                    f"；init_weights_fp={str(manifest['init_weights_fp'])[:12]}…"
                    " —— 拒收（不许静默 warm-start，D5）"
                )
            opt = paddle.optimizer.Adam(
                learning_rate=float(manifest["lr"]), parameters=model.parameters()
            )
            steps_init = checkpoint_paddle.load_opt_pt(model, opt, opt_dir / "opt.pt")
            opt_restore_sec = round(time.time() - t_opt, 3)
            prep_b.add(
                "opt", f"从 opt_init（{opt_src}）恢复 {opt_restore_sec:.1f}s（Adam 动量延续，D5）"
            )
        else:
            if init_w is None or not init_w.exists():
                raise ProtocolError(
                    "无 opt_init 且权重四源皆尽"
                    f"（试过 {'/'.join(WEIGHT_SOURCES[:4])}）"
                    f"；init_weights_fp={str(manifest['init_weights_fp'])[:12]}…"
                    " —— 拒收（不许静默 warm-start，D5）"
                )
            t_warm = time.time()
            student_paddle.load_state_into(model, str(init_w))
            opt = paddle.optimizer.Adam(
                learning_rate=float(manifest["lr"]), parameters=model.parameters()
            )
            prep_b.add(
                "opt", f"无 opt_init，从 init_weights warm-start {time.time() - t_warm:.1f}s + 新 Adam"
            )
    except ProtocolError:
        raise
    except Exception as e:
        raise job_body_error("restore（model/opt 恢复）", e) from e

    # ---- BC kickstart ref（同一条安全阀：系数>0 而字节缺失 ⇒ 拒收；字节走共用的 `_fetch_ref`）----
    kick_kl = _kick_kl
    ref_model = None
    _ref = _fetch_ref(
        jid=jid,
        manifest=manifest,
        job_dir=job_dir,
        blob_root=blob_root,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        batch=_batch,
        wanted=_wanted,
        kick_kl=kick_kl,
        log=log,
    )
    if _ref is not None:
        ref_path, _h, _m = _ref
        blob_hits += _h
        blob_miss_bytes += _m
        try:
            t_ref = time.time()
            ref_model = student_paddle.build_ppo(str(ref_path))
            student_paddle.load_state_into(ref_model, str(ref_path))
            for p in ref_model.parameters():
                p.requires_grad = False
            ref_model.eval()
            prep_b.add("ref", f"{time.time() - t_ref:.1f}s（build+load）")
            log(f"job {jid}: kickstart ref 已加载（BC 冻结 master，kl={kick_kl}）")
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（kickstart ref 装载）", e) from e

    # ---- demo 混 batch（bank 内容寻址 + 装载校验，与 torch 版共用同一组 helper）----
    demo_coef, demo_per_mb, demo_raw, demo_src, _h, _m = _fetch_demo(
        jid=jid,
        manifest=manifest,
        blob_root=blob_root,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )
    blob_hits += _h
    blob_miss_bytes += _m
    demo_bank: dict | None = None
    if demo_raw:
        try:
            bank = decode_demo_bank(demo_raw)
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（demo bank 装载）", e) from e
        check_demo_bank(jid, bank, demo_coef, demo_per_mb, demo_src, prep_b)
        demo_bank = bank

    # ---- PPO：load → chunk → update（numpy 装载 + paddle 更新）----
    shards_root = ppo_shards_root(job_dir, iter_info)
    if on_ppo_start is not None:
        try:
            on_ppo_start()
        except Exception:
            pass
    _report_progress(
        "ppo",
        it=int(manifest.get("it", 0) or 0),
        done=0,
        total=epochs_eff,
        force=True,
    )

    def _cancel_at_epoch_boundary(_ep_done: int, _mdl: Any) -> None:
        if should_cancel is None or not should_cancel():
            return
        raise JobCancelledError(
            f"job {jid}: 结果已 landed（别人先赢）——epoch {_ep_done} 边界停算丢弃"
        )

    if should_cancel is not None and should_cancel():
        raise JobCancelledError(f"job {jid}: 结果已 landed（下载期间）——开算前丢弃")

    t_ppo = time.time()
    train_b = LogBundle(log)
    #: 引擎把**它自己数出来的**步数写回这里（`steps_out`）：调用方不许重算，见下面
    #: `steps_total` 那一段。
    steps_box: dict[str, int] = {}
    try:
        episodes = ppo_np.load_episodes(
            shards_root,
            float(manifest["gamma"]),
            float(manifest["lam"]),
            normalize_adv=str(manifest["adv_norm"]) != "none",
            normalize_ret=bool(manifest.get("normalize_ret", False)),
            per_stage_quota=int(manifest.get("per_stage_quota", 0) or 0),
            bundle=prep_b,
        )
        prep_b.emit(f"job {jid}: 准备完成 {time.time() - t_prep:.1f}s")
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = ppo_np.chunk_episodes(
            episodes, int(manifest["mb"]), shuffle=bool(manifest["shuffle"])
        )
        agg = engine_paddle.ppo_update(
            model,
            opt,
            chunks,
            epochs_eff,
            # `ckpt_path=None`：model/opt 已在本函数上半场按「hub 的 opt tar」恢复过；
            # 再传一遍会让引擎按 state.json 二次 resume（那是离线盘的续跑口径）。
            ckpt_path=None,
            kl_coef=float(manifest["kl_coef"]),
            ent_coef=(
                None if manifest.get("ent_coef") is None else float(manifest["ent_coef"])
            ),
            ref_model=ref_model,
            kickstart_kl=kick_kl,
            demo_bank=demo_bank,
            demo_bc_coef=demo_coef,
            demo_per_mb=demo_per_mb,
            value_anchor=float(manifest.get("value_anchor", 0.0) or 0.0),
            on_epoch_done=_cancel_at_epoch_boundary if should_cancel is not None else None,
            progress=train_b,
            progress_head=f"job {jid}: PPO 训练中",
            steps_out=steps_box,
            log=log,
        )
    except ProtocolError:
        raise
    except JobCancelledError:
        raise
    except Exception as e:
        raise job_body_error("grad（PPO 更新）", e) from e
    ppo_sec = _ppo_done_log(jid, t_ppo, train_b, chunks, total_steps, agg)
    _t_art = time.time()

    # ---- 产物：weights JSON + **torch 格式** ckpt（hub 只认 torch 字节，见模块头口径①）----
    wj_path = job_dir / "weights.json"
    student_paddle.save_weights_json(model, str(wj_path))
    wj_raw = wj_path.read_bytes()
    _cache_produced_weights(blob_root, wj_raw, log)
    wj_sent_raw = wj_raw
    if weights_delta.w_fmt_of(manifest) == weights_delta.W_DELTA_FMT:
        if init_w is None or not init_w.exists():
            raise ProtocolError(
                "形态位是权重 delta 但本轮没有 base 权重（init_weights.json 不在）——拒发"
            )
        base_w = init_w.read_bytes()
        wj_sent_raw = weights_delta.pack_weights_delta(
            base_w, wj_raw, base_sha=hashlib.sha256(base_w).hexdigest()
        )
        _cache_blob(blob_root, hashlib.sha256(wj_sent_raw).hexdigest(), wj_sent_raw, log)
    ckpt_dir = job_dir / "ppo_final"
    # Paddle 的 Adam 没有 `step`：本轮步数 = 载入时的 step + 引擎真正走的步数。
    # 起点与 torch 侧同一条（`_load_opt_state` 读到的 torch `step`），所以混跑时它连续递增。
    # ★ 2026-10-27（评审）：**不许**在这里重算 `steps_init + epochs × len(chunks)` ——
    # 那个式子是引擎内部循环的复本，一旦哪天跳 chunk / 从 ckpt 中途起步就静默漂移，
    # 而它要写进 `state.json` 与回传 opt 的 `step`（bias correction 的时基）。
    # 单源 = 引擎经 `steps_out` 回传（`engine_paddle.ppo_update` 的收尾）。
    steps_total = int(steps_box.get("steps_done", steps_init))
    checkpoint_paddle.save_ckpt(
        model, opt, str(ckpt_dir), epochs_done=epochs_eff, steps_done=steps_total
    )
    opt_tar_raw = pack_opt_tar(ckpt_dir)
    _cache_blob(blob_root, hashlib.sha256(opt_tar_raw).hexdigest(), opt_tar_raw, log)
    opt_sent_raw = opt_tar_raw
    if opt_delta.opt_fmt_of(manifest) == opt_delta.OPT_DELTA_FMT:
        if not opt_raw:
            raise ProtocolError(
                "形态位是 delta 但本轮没有 base opt（manifest.opt_init/opt_sha 为空）——拒发"
            )
        # 基准 = **盘上那份**（消毒只发生在活优化器上；base 两边必须逐字节同源），
        # 与 torch 版 `torch.load(opt_dir / "opt.pt")` 读的是同一个文件。
        base_state = torch_pickle.load_torch_pickle((opt_dir / "opt.pt").read_bytes())
        sent_state = checkpoint_paddle.torch_opt_state(
            model, opt, steps_done=steps_total, node_ref=base_state
        )
        if len(sent_state["state"]) != len(base_state["state"]):
            # 缺项补齐会在活优化器里留下「基准里没有的零动量节点」⇒ 回传前按基准投影掉
            # （`drop_vacuous_nodes`；动量非零会拒发）。留一行读数，免得它成为静默动作。
            prep_b.add(
                "opt delta",
                f"节点 {len(base_state['state'])}→{len(sent_state['state'])}"
                "（惰性节点按基准投影）",
            )
        opt_sent_raw = opt_delta.pack_opt_delta(
            base_state,
            sent_state,
            base_sha=hashlib.sha256(opt_raw).hexdigest(),
        )
    opt_tar_b64 = encode_opt_tar(opt_sent_raw)
    course_cache.record_pending(
        course_cache.cache_root_of(blob_root),
        str(manifest.get("course_fp", "") or ""),
        job_id=jid,
        it=int(manifest.get("it", 0) or 0),
        run_id=str(manifest.get("runId", "") or ""),
        weights=(hashlib.sha256(wj_raw).hexdigest(), len(wj_raw)),
        opt=(hashlib.sha256(opt_tar_raw).hexdigest(), len(opt_tar_raw)),
        log=log,
    )

    # 产物段的秒数不再靠 `PPO_CUDA_DIAG`（那要人记得开）：随 `wire.art_sec` 进账本行，
    # 由 `plan_run` 拼进既有的落盘行（零新增行、每轮都有读数）。
    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(wj_sent_raw),
        "opt_tar_b64": opt_tar_b64,
        "agg": _agg_block(agg, chunks, total_steps),
        "commit_echo": manifest["commit"],
        "ppo_sec": ppo_sec,
    }
    if iter_info is not None:
        # 与 torch 腿同一行（M3 的 report 必带；★ 分关采集账可选，缺则不给键）。
        result.update({k: iter_info[k] for k in ("report", "volume_stage_stats") if k in iter_info})
    result["wire"] = _wire_block(
        payload_bytes=payload_bytes,
        payload_dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
        opt_restore_sec=opt_restore_sec,
        grad_sec=ppo_sec,
        # 产物段（save/pack/identity）自己的账：它不在 `ppo=` 里，是云机轮墙钟最大的一块暗区。
        art_sec=time.time() - _t_art,
        blob_hits=blob_hits,
        blob_miss_bytes=blob_miss_bytes,
        weights_src=weights_src,
        weights_bytes=weights_wire_bytes,
        ts_code_bytes=ts_code_bytes,
        ts_code_hit=ts_code_hit,
        rollout_sec=(iter_info or {}).get("rollout_sec"),
        bun_version=(iter_info or {}).get("bun_version"),
    )
    # 两遍收敛（同 echo 路径）：result_bytes 与自身体长自指，一遍差它的十进制位数。
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    return result, course
