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
同款；`tests/test_train_core_split.py` 机械钉住「顶层没有」+「函数体里有」）。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.protocol import (
    BLOB_DEMO,
    BLOB_OPT,
    BLOB_REF,
    JobCancelledError,
    ProtocolError,
    coef_active,
    encode_opt_tar,
    encode_weights_json,
    job_seed,
    pack_result_v2,
)
from remote.download import _cache_blob, _resolve_blob
from remote.job_fs import pack_opt_tar, unpack_opt_tar
from remote.job_lifecycle import job_body_error
from remote.wire import _wire_block, _wire_time


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
    preloaded: dict | None,
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
        # ---- 课程上下文：快照全文 → CourseConfig → reward_fn（D1/D6/D13） ----
    import numpy as np

    course_text = manifest["course"]
    course_path = job_dir / "course.jsonc"
    course_path.write_text(course_text, encoding="utf-8")
    from rl.config import load_course

    course = load_course(str(course_path))
    if course.reward_spec().identity() != manifest["formula_hash"]:
        raise ProtocolError(
            f"course formula_hash 与快照不符：manifest={manifest['formula_hash']} "
            f"本地算={course.reward_spec().identity()}"
        )
    from rl.reward_context import update as ctx_update
    from rl.reward_library import build_reward_fn

    reward_fn = build_reward_fn(course.reward_spec())
    ctx_update(
        reward_fn=reward_fn,
        gamma=float(manifest["gamma"]),
        lam=float(manifest["lam"]),
        it=int(manifest["it"]),
        metrics_version=int(manifest["metrics_version"]),
        identity={"course": course.name, "formula_hash": manifest["formula_hash"]},
    )

    # ---- 延迟导入 torch / ppo 后端（B7 同款；本模块顶层零 torch） ----
    import torch

    if torch_threads > 0:
        torch.set_num_threads(torch_threads)
    import ppo.engine as ppo_engine
    from data.weights_io import load_state_into, save_weights_json

    # ---- per-job 确定性种子（D5）：load/chunk/update 前重新播种 ----
    # numpy RandomState 种子必须 < 2^32：sha256 前 8 个 hex 字符（32 bit）
    seed_hex = job_seed(manifest["runId"], int(manifest["it"]), manifest["init_weights_fp"])
    np.random.seed(int(seed_hex[:8], 16))

    # ---- 模型构建：opt_init（_ppo_save tar）优先，否则 init 权重 + 新 opt ----
    # hub 侧免 torch（D2）：模型权重/opt 由 tar 或 weights_json 提供，worker 负责
    # 重建——tar 内 model.pt = 上一轮 PPO 终态（含 Adam 动量，D5）。
    init_w = job_dir / "init_weights.json"
    # 标注为 torch.nn.Module（而非推断出的 PPOStudent）：多卡分支要把 model 换成
    # DataParallel，且下游 save_weights_json / load_state_into 收的就是 Module。
    # M2（B4）：有 opt blob 时 payload 不再带 init_weights.json（model+Adam 都在 opt
    # tar 里）——build_ppo 只借它读 arch，缺文件走默认（per-tick 固定 64/8/128）。
    model: torch.nn.Module = ppo_engine.build_ppo(str(init_w) if init_w.exists() else None)
    # 设备解析（2026-09-10 TPU 接力）：--device tpu/xla 走 torch_xla 的 xla_device()，
    # 而不是 torch.device("xla")——后者在部分 torch_xla 版本上拿不到带序号的设备句柄。
    # torch_xla 只在真的选了 TPU 时才 import（未装 torch_xla 的机器行为不变）。
    # `device` 已在 run_job 入口（kind 分叉之前）过 normalize_ppo_device，"auto" 不再可能到达这里。
    dev_str = str(device)
    use_dp = False
    if dev_str in ("tpu", "xla"):
        # 统一走 ppo.common.xla_device()（torch_xla.device() 优先，旧版回退 xm.xla_device()）
        from ppo.common import (
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
        log(
            f"job {jid}: XLA 持久化编译缓存 {xla_enable_compile_cache(work_dir / 'xla-compile-cache')}"
        )
        device_t = xla_device()
        # ★ 2026-09-22（Kaggle TPU 实例上离线课程 PPO 单步 8~9s ⇒ 疑似静默跑 CPU）：XLA 的
        #   CPU 插件也返回 `xla:0`，所以「设备字符串」证明不了什么；这里把**后端指纹**与一次
        #   速度自检打出来，并在后端不是 TPU 时**拒跑**（在 CPU 上跑完整段看起来一切正常，
        #   只是慢两个数量级——正是最该响的那类静默降级）。
        _fp = xla_fingerprint(device_t)
        _spd = xla_device_speed_probe(device_t)
        log(
            f"job {jid}: TPU/XLA 设备 {device_t}｜device_type={_fp['device_type']}｜"
            f"XLA 设备数={_fp['global_device_count']}（本进程可见 {_fp['addressable_device_count']}）｜"
            f"replication={_fp['replication_devices']}｜attrs={_fp['attrs']}｜"
            f"world_size={xla_world_size()}（无复制时恒 1，**别拿它当 TPU 判据**）｜"
            f"2048² matmul {(_spd * 1000) if _spd is not None else float('nan'):.1f} ms"
            "（TPU 量级 ~ms；~10ms+ = 后端是 CPU）"
        )
        _why = tpu_backend_missing_reason(_fp)
        if _why:
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
    # ---- M2 B3：opt 内容寻址解析（cache / preloaded / download / inline）----
    # 安全阀（plan §4.3）：opt_sha 存在而 blob 不可得 → 响亮失败，绝不静默 warm-start
    # （那会把 D5 的 Adam 动量悄悄归零，日志上却一切正常）。
    opt_sha = str(manifest.get("opt_sha", "") or "")
    blob_hits = 0
    blob_miss_bytes = 0
    opt_raw, opt_hit, opt_src = _resolve_blob(
        blob_root=blob_root,
        name=BLOB_OPT,
        sha=opt_sha,
        inline_b64=str(manifest.get("opt_init", "") or ""),
        jid=jid,
        base_url=base_url,
        token=token,
        preloaded=preloaded,
        log=log,
    )
    if opt_hit and opt_src == "cache":
        blob_hits += 1
    if opt_src == "download":
        blob_miss_bytes += len(opt_raw)
    if opt_sha and not opt_raw:
        raise ProtocolError(
            f"job {jid}: opt_sha={opt_sha[:12]}… 存在但 blob 不可得——拒收"
            "（不许静默退回 warm-start，D5）"
        )
    opt = None
    opt_restore_sec = 0.0
    # ★ §4.2（2026-09-21）：restore 段的崩溃归确定性失败（带 traceback 摘要）——
    # 它是本段最容易“静默重演”的一处（优化器/模型卷积不兼容会逐一重演到天亮）。
    try:
        if opt_raw:
            t_opt = time.time()
            opt_dir = job_dir / "opt_init"
            unpack_opt_tar(opt_raw, opt_dir)
            # 必须在 model.to(device_t) 之前加载 state_dict，然后统一移到目标设备
            model.load_state_dict(torch.load(opt_dir / "model.pt", map_location="cpu"))
            model.to(device_t)
            opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
            # 统一 map_location="cpu"：Optimizer.load_state_dict 会把载入张量 cast 到
            # param 所在设备，所以 XLA/CPU/CUDA 三条路都靠这一句完成搬迁（原先写死
            # device_t 在 XLA 上会走 torch.load 的设备 hook，跨 runtime 不稳）。
            opt.load_state_dict(torch.load(opt_dir / "opt.pt", map_location="cpu"))
            opt_restore_sec = round(time.time() - t_opt, 3)
            log(f"job {jid}: model/opt 从 opt_init（{opt_src}）恢复（Adam 动量延续，D5）")
        else:
            # 首轮/无 tar：从 init 权重 warm-start（hub 打包时写入 payload 的 weights_json）
            if not init_w.exists():
                raise ProtocolError("payload 缺 init_weights.json 且无 opt_init/blob——无法构建模型")
            load_state_into(model, str(init_w))
            model.to(device_t)
            opt = torch.optim.Adam(model.parameters(), lr=float(manifest["lr"]))
            log(f"job {jid}: 无 opt_init，从 init_weights warm-start + 新 Adam")
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
            _n = torch.cuda.device_count()
            _mb = int(manifest.get("mb", 0) or 0)
            model = torch.nn.DataParallel(model)
            log(
                f"job {jid}: DataParallel 生效（{_n} 卡"
                + (f"，mb={_mb} -> 每卡 {_mb // _n}" if _mb else "")
                + "）——梯度归约顺序变化，与单卡 run 数值不可逐位比"
            )
        else:
            log(
                f"job {jid}: 请求了 cuda-dp 但只可见 {torch.cuda.device_count()} 张卡"
                " —— 退化为单卡（行为等同 --device cuda）"
            )

    # ---- BC-anchored kickstart ref（§363）：有系数无尺子＝静默裸奔，不可接受——
    # 缺字节响亮拒绝；系数为 0 直接跳过（零开销，旧 manifest 行为不变）。
    kick_kl = float(manifest.get("kickstart_kl", 0.0) or 0.0)
    # 阈值判据（见 common/protocol.NEGLIGIBLE_COEF）：课程按几何衰减永远到不了精确 0，
    # 实测 1.455e-11 时旧判据 `> 0` 仍会加载 ref 并每轮预计算 3 s。用 coef_active 兜底，
    # 也覆盖"旧 hub 产出的、仍带微小系数的在途 manifest"。
    if kick_kl != 0.0 and not coef_active(kick_kl):
        log(f"job {jid}: kickstart_kl={kick_kl:g} 低于阈值 —— 按关闭处理（省 ref 加载+预计算）")
        kick_kl = 0.0
    ref_model: torch.nn.Module | None = None
    if kick_kl > 0:
        import hashlib as _hl

        ref_sha = str(manifest.get("ref_sha", "") or "")
        ref_b64 = str(manifest.get("ref_weights_b64", "") or "")
        ref_fp = str(manifest.get("ref_weights_fp", "") or "")
        # M2 B3：ref 也走内容寻址（ref_sha = sha256(raw 权重) = ref_weights_fp）。
        ref_raw, ref_hit, ref_src = _resolve_blob(
            blob_root=blob_root,
            name=BLOB_REF,
            sha=ref_sha,
            inline_b64=ref_b64,
            jid=jid,
            base_url=base_url,
            token=token,
            preloaded=preloaded,
            log=log,
        )
        if not ref_raw:
            raise ProtocolError(f"job {jid}: kickstart_kl={kick_kl} 但无 ref_weights——拒收")
        if ref_fp and _hl.sha256(ref_raw).hexdigest() != ref_fp:
            raise ProtocolError(f"job {jid}: ref_weights 指纹不符——拒收")
        if ref_hit and ref_src == "cache":
            blob_hits += 1
        if ref_src == "download":
            blob_miss_bytes += len(ref_raw)
        ref_path = job_dir / "ref_weights.json"
        ref_path.write_bytes(ref_raw)
        # ★ §4.2：ref 装载同属 restore——ref 与 policy 的架构/形状不合会在每一份字节上
        # 重演（而它只会被写成一行云机日志，训练侧看到的是超时）。
        try:
            ref_model = ppo_engine.build_ppo(str(ref_path))
            load_state_into(ref_model, str(ref_path))
            for p in ref_model.parameters():
                p.requires_grad = False
            ref_model.eval()
            ref_model.to(device_t)
            if use_dp:
                ref_model = torch.nn.DataParallel(ref_model)
            log(f"job {jid}: kickstart ref 已加载（BC 冻结 master，kl={kick_kl}）")
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（kickstart ref 装载）", e) from e

    # ---- demo 混 batch（x20 后续）：bank 内容寻址 + 装载校验 ----
    # 安全阀同 ref：coef>0 而 bank 不可得 ⇒ 响亮失败，绝不静默降级为纯 PPO
    # （那会让 demo 腿的整轮更新在日志一切正常下丢失 demo 项）。
    demo_coef = float(manifest.get("demo_bc_coef", 0.0) or 0.0)
    demo_per_mb = int(manifest.get("demo_per_mb", 0) or 0)
    demo_bank: dict | None = None
    if demo_coef > 0 and demo_per_mb > 0:
        import io as _io

        import numpy as _np

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
        if demo_hit and demo_src == "cache":
            blob_hits += 1
        if demo_src == "download":
            blob_miss_bytes += len(demo_raw)
        if not demo_raw:
            raise ProtocolError(f"job {jid}: demo_sha 存在但 blob 不可得——拒收")
        try:
            demo_bank = {k: _np.asarray(v) for k, v in dict(_np.load(_io.BytesIO(demo_raw))).items()}
        except ProtocolError:
            raise
        except Exception as e:
            raise job_body_error("restore（demo bank 装载）", e) from e
        need = {"obs", "scalars", "actions", "masks"}
        if not need.issubset(demo_bank.keys()):
            raise ProtocolError(
                f"job {jid}: demo bank 缺字段 {sorted(need - set(demo_bank.keys()))}——拒收"
            )
        log(
            f"job {jid}: demo bank 已加载（N={demo_bank['obs'].shape[0]}"
            f" coef={demo_coef:g} per_mb={demo_per_mb} src={demo_src}）"
        )

    # ---- PPO：load → chunk → update（同一 backend 调用链，D4） ----
    # ★ §4.2（2026-09-21）：grad 段（含读 shard / 分块）的崩溃归确定性失败。
    # 为什么连读 shard 一起包：那同样是「这份字节决定的」失败（缺字段/形状不符），
    # 而 OOM 那类真瞬态由 `job_body_error` 原样放回重领路径。
    shards_root = str(job_dir)
    if on_ppo_start is not None:
        try:
            # 打点失败不致命：最坏后果是这份 job 的掉队阈值晚起算（多开一份备份）。
            on_ppo_start()
        except Exception:
            pass

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
        )
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = ppo_engine.chunk_episodes(
            episodes, int(manifest["mb"]), shuffle=bool(manifest["shuffle"])
        )
        agg = ppo_engine.ppo_update(
            model,
            opt,
            chunks,
            int(manifest["epochs"]),
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
            # ★ 取消接线（R2-5）：今天这条调用**没有**传它——不传则取消延迟永远是
            # 「跑完才响应」。训练侧那条（`rl/stream.py`）传的是双缓冲预采回调，
            # 与这里不是同一个调用点，别去动那一条。
            on_epoch_done=_cancel_at_epoch_boundary if should_cancel is not None else None,
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
    ppo_sec = round(time.time() - t_ppo, 1)
    # P0.5：T_ppo 进传输账（与 T_in/T_out 同一条 `wire` 行 ⇒ 占比可复算，不用人肉拼日志）。
    _wire_time(jid, "ppo", time.time() - t_ppo)
    log(
        f"job {jid}: PPO done in {ppo_sec}s, steps={total_steps} chunks={len(chunks)} kl={agg.get('kl')}"
    )

    # ---- 产物：weights_json（save_weights_json，D12/G1）+ _ppo_save tar（D5） ----
    # XLA：先落图执行边界再物化回主机。否则 state_dict() / save_weights_json 读到的是
    # 尚未执行的惰性图（权重是最新一轮 `mark_step` 时的快照，不是本轮终态）。
    from ppo.common import _ppo_save, xla_mark_step

    xla_mark_step(device_t)
    # ⚠ 用 raw_model 而非 model：DP 包装的 state_dict 键带 "module." 前缀（已实证），
    #   写出去会让 ckpt 与单卡路径互不兼容（课程 resume 会炸）。raw_model 与 DP 共享
    #   同一批参数对象，.to("cpu") 对两者等价。
    raw_model.to("cpu")
    wj_path = job_dir / "weights.json"
    save_weights_json(raw_model, str(wj_path))
    ckpt_dir = job_dir / "ppo_final"
    _ppo_save(str(ckpt_dir), raw_model, opt, int(manifest["epochs"]))
    opt_tar_raw = pack_opt_tar(ckpt_dir)
    opt_tar_b64 = encode_opt_tar(opt_tar_raw)
    # M2 B3：把刚产出的 raw opt tar 写进 blob_cache（键 = sha256）——下一轮 hub 的
    # opt_sha 由 verify_and_land 落盘的同一份原始字节算出，故同会话内 100% 命中。
    _cache_blob(blob_root, hashlib.sha256(opt_tar_raw).hexdigest(), opt_tar_raw, log)

    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(wj_path.read_bytes()),
        "opt_tar_b64": opt_tar_b64,
        "agg": {
            "policy": float(agg.get("policy", 0.0)),
            "value": float(agg.get("value", 0.0)),
            "entropy": float(agg.get("entropy", 0.0)),
            "kl": float(agg.get("kl", 0.0)),
            "kickstart": float(agg.get("kickstart", 0.0)),
            "demo_bc": float(agg.get("demo_bc", 0.0)),
            "mean_ret": float(agg.get("mean_ret", 0.0)),
            "steps": int(total_steps),
            "chunks": len(chunks),
        },
        "commit_echo": manifest["commit"],
        "ppo_sec": ppo_sec,
    }
    if iter_info is not None:
        # M3：节点自己跑的 rollout 的采集口径（协议层必校——hub 侧无本地 shard 可算）。
        result["report"] = iter_info["report"]
    result["wire"] = _wire_block(
        payload_bytes=payload_bytes,
        payload_dl_sec=payload_dl_sec,
        unpack_sec=unpack_sec,
        opt_restore_sec=opt_restore_sec,
        grad_sec=ppo_sec,
        blob_hits=blob_hits,
        blob_miss_bytes=blob_miss_bytes,
        ts_code_bytes=ts_code_bytes,
        ts_code_hit=ts_code_hit,
        rollout_sec=(iter_info or {}).get("rollout_sec"),
        bun_version=(iter_info or {}).get("bun_version"),
    )
    # 两遍收敛（同 echo 路径）：result_bytes 与自身体长自指，一遍差它的十进制位数。
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    return result, course
