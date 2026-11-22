"""remote/job_shell.py — `run_job` 的**作业壳相位**（remote，L4）。

2026-11-18（长函数存量第三十五刀）：`remote/worker.py::run_job` 439 行里，与
「轮级执行」（kind=iter 的 rollout、训练核调用、热替换护栏）无关的七段搬到这里；
宿主按**原顺序**编排，日志 / 判据 / 写盘顺序逐条未动：

```
precheck_job      离线腿拒收 / 后端解析 / 结果复用 / bun 自检（任何下载之前）
land_materials    payload + code.zip 取字节并摆好（细节住 `remote/download.py`）
bc_or_none        kind=bc 的独立执行链分叉
resolve_job_weights  权重五源（W0–W4；必须早于 rollout 与 build_ppo）
run_iter_phase    kind=iter：TS 运行时 + 节点侧 rollout + 轮级资源收线
check_mode_and_d14    per-tick 红线 + 语料血缘
echo_smoke_result --echo：不跑 PPO，原样回显 init 权重
```

状态住 `JobState`（一次 job 建一个）；`collect_round` 由宿主**注入**（值引用），
所以本模块不 import `remote.offline_greedy`(L4)——拓扑秩因此是 **L4**（最深依赖
`remote.download` / `remote.bc_job`，均 L3），`worker`(L5) 站在上面。

⚠ 两段刻意**不搬**：热替换护栏（`_CODE` 是 worker 的进程级单例，
`test_worker_state_contract` 钉住宿主）与训练核调用（`remote.train_core` 是 L4，
搬来会把本模块顶到 L5）。
"""

from __future__ import annotations

import json
import os
import tarfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from common.log_bundle import LogBundle
from common.logutil import secs
from common.protocol import (
    ProtocolError,
    d14_corpus_match,
    encode_opt_tar,
    encode_weights_json,
    pack_result_v2,
    validate_result,
)
from remote.cloud_pool import acquire_rollout, release_rollout
from remote.download import (
    WEIGHT_SOURCES,
    CodeAsk,
    WeightsAsk,
    _ensure_code,
    _ensure_payload,
    _ensure_ts_code,
    _resolve_weights,
)
from remote.job_fs import _persist_result
from remote.job_spec import BcJobSpec
from remote.wire import _wire_block
from worker.iter_rollout import RoundContext, resolve_bun
from worker.stream_collect import StreamCollector


@dataclass
class JobState:
    """一次 job 的壳状态：`run_job` 建一次，相位函数按原顺序就地更新。"""

    base_url: str
    token: str
    jid: str
    manifest: dict
    work_dir: Path
    device: str
    backend: str
    echo: bool
    preloaded: dict | None
    code_cache_dir: Path | None
    ts_code_cache_dir: Path | None
    lease_token: str
    torch_threads: int
    prep_b: LogBundle
    log: Callable[[str], None]
    # ---- 相位产物（默认值只在对应相位之前可见，宿主不在那时读）----
    job_dir: Path = Path()
    shard_dirs: list[Path] = field(default_factory=list)
    payload_nbytes: int = 0
    payload_dl_sec: float = 0.0
    unpack_sec: float = 0.0
    pruned_dirs: int = 0
    #: D10（plan/replay-corpus.plan.md）：replay pre-pass 的读数（full 路径 None）。
    replay: Mapping[str, object] | None = None
    code_root: Path = Path()
    blob_root: Path = Path()
    init_w: Path | None = None
    weights_src: str = ""
    weights_wire_bytes: int = 0
    blob_hits: int = 0
    blob_miss_bytes: int = 0
    iter_info: dict | None = None
    rctx: RoundContext | None = None
    ts_code_bytes: int = 0
    ts_code_hit: bool = False


def precheck_job(st: JobState, *, resolve_backend) -> dict | None:
    """离线腿拒收 / 后端解析 / 结果复用 / bun 能力自检——全部在**任何下载之前**。"""
    jid, manifest, log = st.jid, st.manifest, st.log

    # ---- 离线腿已退役（2026-09-25，plan/online-offline-role-routing §7）：kind=run 拒收 ----
    # 「hub 发一份 kind=run 整段 job、本机等 8h」那条腿**不再有生产端**：离线课由云机
    # **取任务包**接手（`battle.offline.ipynb` → `/offline/tasks` → `/offline/task-pack`）。
    # 为什么拒收而不是「当成 kind=iter 跑一轮」：半跑一轮会产出权重、让控制面看着像在推进，
    # 而 hub 侧早就不等了 —— 那正是最难查的那类静默分叉（plan §7.2 的「不许白等」）。
    # 位置在**最前**（连结果复用/能力自检之前）：拒单不发一号指令、不下载一个字节。
    if str(manifest["kind"]) == "run":
        raise ProtocolError(
            f"job {jid}: kind=run（半离线整段）这条腿已于 2026-09-25 退役 —— 离线课由云机"
            "取任务包接手：battle.offline.ipynb（/offline/tasks 清单 → /offline/task-pack 取包）。"
            "本 worker 拒收这份活。"
        )

    # ---- 训练后端（2026-10-27，Paddle 双生）：**节点本地能力开关**，hub 不可见（用户口径②）----
    # 2026-11-06（BC #10）：Paddle 后端现在跑 kind=ppo + kind=bc；节点侧 rollout 仍是
    # torch/bun 语义。在**任何下载之前**拒单 —— 别让 AI Studio 机器传完 payload 才炸
    # "No module named torch"。载荷是**入参**（`--backend` 一路传下来）；空串 = auto
    # （看本机，torch 优先，逐字节旧行为）。
    st.backend = resolve_backend(st.backend)
    if st.backend == "paddle" and str(manifest["kind"]) not in ("ppo", "bc"):
        raise ProtocolError(
            f"job {jid}: 本 worker 是 Paddle 后端，只跑 kind=ppo/bc；这份活是 "
            f"kind={manifest['kind']}——拒收（节点侧 rollout 仍是 torch/bun 语义）"
        )

    # ---- 结果复用（2026-09-05）：上次已算完但回传失败 → 本地缓存直接重传，不重算 PPO ----
    cached_path = st.work_dir / jid / "_result.json"
    if cached_path.exists():
        try:
            cached: dict = json.loads(cached_path.read_text(encoding="utf-8"))
            validate_result(cached, manifest, commit_echo_must_match=False)
            log(f"job {jid}: 复用上次算完的结果（上次回传失败）——直接重传")
            return cached
        except (OSError, ValueError, ProtocolError):
            pass  # 缓存缺失/跨 manifest/损坏 → 清场走全流程
        # 其余异常照抛：`validate_result` 自己的 bug（TypeError/AttributeError…）若被当成
        # 「缓存坏了」，现场就是**静默重算一次 PPO**（几十分钟 + 权重不同），真因永不出现。

    # ---- 能力自检（plan/train-mode-hot-switch.plan.md L3.2，2026-09-24）------
    # 缺 bun 在**零下载**时就拒单：`kind=iter` 的活要节点自己跑 rollout
    # （`run_iter_rollout` → `resolve_bun`），而现状是先把 payload + code.zip + ts_code.zip
    # 三件全下完（实测 3.42MB / 12.1s）才在 `run_iter_rollout` 里发现没 bun —— 全白传。
    #
    # ★ 位置很关键：必须在**结果复用块之后**。它前面那个 `_result.json` 命中是纯重传
    #   （上次算完了、只是回传失败），重传不需要 bun —— 放在复用块之前会把这种情况误拒。
    #
    # 失败类型与原来一模一样（`resolve_bun` 抛 `ProtocolError`）：调用方按「确定性拒结」
    # 处理（`worker_loop` 的 `except ProtocolError` 分支注释里就有「节点能力缺失如 bun
    # 装不上」），hub 落终局 failed、训练侧停腿 —— 不动分类，只把判定前移。
    # `echo` 走「只验传输链」（下方 kind 分叉里整个跳过 rollout）⇒ 不该要求 bun。
    if str(manifest["kind"]) == "iter" and not st.echo:
        resolve_bun(str((manifest.get("rollout") or {}).get("bun") or ""))
    return None


def land_materials(st: JobState) -> None:
    """payload 与 code.zip 的「取字节 + 摆好」——实现住 `remote/download.py`，这里只收读数。"""
    jid, manifest, log, prep_b = st.jid, st.manifest, st.log, st.prep_b

    # ---- 物料落地（S4 第十二刀）：payload 与 code.zip 的「取字节 + 摆好」整段在
    # `remote/download.py`（与 kind=iter 的 `_ensure_ts_code` 并列成三兄弟）。
    # 这一刀之后 `run_job` 不再认识缓存树 / 解包 / 清场细节——它读三个 `_ensure_*` 的**返回值**，
    # 那些返回值（`PayloadLanded` / `CodeLanded`）就是「摆到哪儿了」的完整答案。
    # E1（plan/aistudio-transfer-hardening §4）：准备段的**计时**拆出来。
    # 为什么值得：慢链路上「准备 95s」分不清是**在传**还是在**本地处理**；解包/blob 解析
    # 是纯 CPU，把它们从准备合计里切出来，剩下的差额就是链路（而链路正是本 plan 的病灶）。
    # ★ 2026-10-30（用户口径「准备完成行只留必要读数」）：「物料落地」**合计**段删掉——
    #   它是「下载 + 解包 + code」三项的加总，而三项各自都已单独报（解包/blob 解析/payload），
    #   每轮再打一遍加总只是重复。分项秒数仍进 wire 账（机器可读的那份没少）。
    landed = _ensure_payload(
        st.base_url,
        st.token,
        jid,
        manifest,
        st.work_dir,
        preloaded=st.preloaded,
        log=log,
        bundle=prep_b,
    )
    st.job_dir = landed.job_dir
    st.shard_dirs = landed.shard_dirs
    st.payload_dl_sec = landed.dl_sec
    st.unpack_sec = landed.unpack_sec
    st.payload_nbytes = landed.payload_nbytes
    st.replay = landed.replay
    # ★ 2026-10-30：prune 计数**不进人读的那一行**（每轮一句家务事 ⇒ 纯重复），
    #   改由 wire 账带着（`wire.prune_dirs`）。
    st.pruned_dirs = landed.pruned_dirs
    # 解包这半场的读数照打（别写 0.0——那是诊断读数）；staged 时它**不在关键路径上**
    # （关键路径的诚实性由 wall 保证：`other = wall − in − out − ppo`，解包已不在 wall 里）。
    # ★ 2026-10-30（用户口径「准备完成行只留必要读数」）：括号里的解释删掉——秒数本身
    # 就是读数，「不含传输」这类口径说明属于注释（代码里已写清），不该每轮重打一遍。
    prep_b.add(
        "解包",
        f"{secs(st.unpack_sec)}s → {len(st.shard_dirs)} shard",
    )
    # `code_cache_dir` 是 run_job 的**入参**（共享根提示），落地后换成 per-sha 目录。
    code = _ensure_code(
        CodeAsk(st.base_url,
        st.token,
        jid,
        manifest,
        st.job_dir,
        st.work_dir,
        code_cache_root=st.code_cache_dir,
        preloaded=st.preloaded,
        log=log,
        bundle=prep_b),
    )
    st.code_root = code.code_root
    st.blob_root = code.blob_root


def bc_or_none(st: JobState, *, run_bc) -> dict | None:
    """kind=bc 走独立执行链（语料 npy shard，无 reward/γ/λ/init-weights 语义）。"""
    # ---- kind 分叉（BC 整合，plan/bc-cloud-integration.plan.md §3）：bc 任务走独立
    # 执行链（语料 npy shard 训练，无 reward/γ/λ/init-weights 语义）；ppo 任务继续
    # 走下方 per-tick 红线 + PPO 链路（默认行为零变化）。
    if str(st.manifest["kind"]) != "bc":
        return None
    result: dict | None = run_bc(
        BcJobSpec(
            jid=st.jid,
            manifest=st.manifest,
            job_dir=st.job_dir,
            shard_dirs=st.shard_dirs,
            device=st.device,
            torch_threads=st.torch_threads,
            echo=st.echo,
            base_url=st.base_url,
            token=st.token,
            lease_token=st.lease_token,
            backend=st.backend,
            log=st.log,
        )
    )
    return result


def resolve_job_weights(st: JobState) -> None:
    """权重五源（W0–W4）解析：必须早于 rollout 与 `build_ppo`（arch 从权重文件读）。"""
    jid, manifest, log = st.jid, st.manifest, st.log

    # ---- opt-blob-diet（2026-09-24，plan/opt-blob-diet.plan.md §3.2/§3.3）：权重解析 ----
    # 五源：W0 payload → W1 blob_cache → W2 preloaded → W3 GET blob（W4 旧形状 tar 的
    # `model.pt` 由训练核的 restore 段承担）。**位置是本 plan 最容易做错的一处**：
    #   ① 必须早于 `run_iter_rollout` —— kind=iter 的 rollout argv 是
    #      `--weights init_weights.json`（相对 job 目录），权重不在就起不来；
    #   ② 必须早于 `ppo_engine.build_ppo(...)` —— arch 从权重文件里读，缺文件会静默
    #      退回默认 64/8/128（「建了错的模型却照跑」，§3.3-2）；
    #   ③ 必须**晚于** BC 的 early-return —— BC job 的 `init_weights_fp` 是哨兵 `"bc"`，
    #      进五源会给 hub 打一个必然 404 的 `?name=init`（§3.2 的 64-hex 规则）。
    # 传输账的两个计数器在这里建账：后面的 opt/ref/demo blobs 与训练核共用这一手账。
    blob_hits = 0
    blob_miss_bytes = 0
    t_blob = time.time()
    init_w, weights_src, weights_wire_bytes = _resolve_weights(
        WeightsAsk(job_dir=st.job_dir,
        init_weights_fp=str(manifest["init_weights_fp"]),
        # R0（M0）：本机 f32 副本的身份命中面（旧 hub 无 `opt_from` ⇒ 空串 ⇒ 不试）
        course_fp=str(manifest.get("course_fp", "") or ""),
        opt_from=str(manifest.get("opt_from", "") or ""),
        it=int(manifest.get("it", 0) or 0),
        blob_root=st.blob_root,
        jid=jid,
        base_url=st.base_url,
        token=st.token,
        preloaded=st.preloaded,
        log=log),
    )
    st.prep_b.add("blob 解析", f"{secs(time.time() - t_blob)}s src={weights_src}")
    if weights_src in ("cache", "course_cache"):
        blob_hits += 1
    elif weights_src == "download":
        blob_miss_bytes += weights_wire_bytes
    if init_w is None and str(manifest["kind"]) == "iter":
        # kind=iter 的**节点侧 rollout** 必须有权重文件：W4 那条 tar 退路对它无效
        # （旧 hub 对这类 job 恒带 payload 内的 init_weights.json ⇒ 走 W0）。
        # 新 hub 下这里只会在「四源全缺失」时触发 = 真的发错了活。
        raise ProtocolError(
            f"job {jid}: kind={manifest['kind']} 需要 init 权重但四源皆尽"
            f"（试过 {'/'.join(WEIGHT_SOURCES[:4])}）"
            f"；init_weights_fp={str(manifest['init_weights_fp'])[:12]}…——拒收"
        )
    st.init_w = init_w
    st.weights_src = weights_src
    st.weights_wire_bytes = weights_wire_bytes
    st.blob_hits = blob_hits
    st.blob_miss_bytes = blob_miss_bytes


def run_iter_phase(st: JobState, *, collect_round) -> None:
    """kind=iter：TS 运行时 + 节点侧 rollout（shard 由本机现产）+ 轮级资源收线。"""
    manifest, log, jid = st.manifest, st.log, st.jid
    iter_info: dict | None = None
    rctx: RoundContext | None = None
    ts_code_bytes = 0
    ts_code_hit = False

    # ---- M3 kind 分叉：iter = 一整轮上云（节点自己跑 rollout 产 shard）----
    #
    # 位置很关键：必须在 mode 红线 / D14 / echo **之前**——因为本轮真正要校验的 shard
    # 是刚跑出来的（payload 里没有 shard），D14 必须看到它们；echo 轮则整个跳过 rollout
    # （回显冒烟不跑任何重计算，只验 payload/code/ts_code 三样传输）。
    if str(manifest["kind"]) == "iter":
        ts_root = (
            st.ts_code_cache_dir
            if st.ts_code_cache_dir is not None
            else st.code_root.parent / "ts_code_cache"
        )
        _ts_dir, ts_code_bytes, ts_code_hit = _ensure_ts_code(
            st.base_url,
            st.token,
            jid,
            manifest,
            ts_root=ts_root,
            preloaded=st.preloaded,
            log=log,
            bundle=st.prep_b,
        )
        if st.echo:
            log(f"job {jid}: kind=iter + echo——跳过 rollout（只验传输链）")
        else:
            # 轮级资源（落点/TS 树/长驻池）在整轮共用：池在 `finally` 里收（rollout 之后、
            # PPO 之前）——不让 94 个常驻 bun 陪 PPO 吃内存。
            # ⚠ **ctx 的所有权在这里** ⇒ 收池必须罩住**整段**（含 rollout 自己抛的那些路径）：
            #   rollout 失败（D14/verify/超界……）时若没人收，94 个常驻 bun 会一直挂着。
            rctx = RoundContext()
            # ★ 逐局流式采集（2026-10-27，用户口径）：`volume` 块在 ⇒ 本轮采哪几局由调度器
            # **每结算一局重算一次**（差额最大的关先派，槽位空出即补，满额即停）——不再有
            # 「一批跑完再算下一批」。`volume` 缺席（在线 kind=iter / 老计划）= 老行为逐字不变。
            collector = StreamCollector.from_spec(
                manifest["rollout"], log=log, started_at=time.time()
            )
            pool_key = str(manifest.get("ts_code_sha256", "") or "").strip().lower()
            try:
                # ★ 先建 collector 再取池：from_spec 抛错时池还没被取走。
                rctx.pool = acquire_rollout(pool_key, log)
                # 本轮 rollout（普通波 + 可选 greedy 波；后者在 PPO **之前**，见
                # `remote/offline_greedy.collect_round` 与 plan/greedy-offline-autonomous）。
                iter_info = collect_round(
                    st.job_dir,
                    manifest,
                    ts_dir=_ts_dir,
                    log=log,
                    ctx=rctx,
                    it=int(manifest.get("it", 0) or 0),
                    collector=collector,
                )
                # ⚠ 这里是**有意重绑**，不是变量遮蔽的失误（M3 `2160b1da` 的原话：「本轮真正
                #   要校验的 shard 是刚跑出来的（payload 里没有 shard），D14 必须看到它们」）。
                #   自主腿的 payload 就是 `pack_payload([], …)`（`plan_run.py:520`）⇒ 解包出的
                #   `landed.shard_dirs` 是空表；不重绑的话下面那段 D14 变成空转。
                st.shard_dirs = list(iter_info["shard_dirs"])
            finally:
                pool, rctx.pool = rctx.pool, None
                release_rollout(pool_key, pool)
                rctx.close()  # 池已摘走；这里只收落点/临时资源
            if collector is not None:
                # 轮报的 `volumeTopup`（读数面；`rule`/逐关账/缺口/浪费/触顶 与旧形态同键）。
                iter_info["volumeTopup"] = collector.volume()
                # ★ 2026-10-28（plan/it-stage-ratation §9.5）：另带一份**本机腿同形状**的分关账
                #   （`trainer/loop_volume._volume_stage_stats`），随产物行回到账本 ⇒ 控制台的
                #   「各关进度」列/面板在云机轮上也有读数（同一个解析器，不写第二个分支）。
                # ★ 2026-11-11：合并而不是覆盖 —— greedy 波的 `volume_stage_stats["greedy"]`
                #   已经先写进 `iter_info`（ `collect_round` 在 rollout 之后、这里之前跑）。
                iter_info["volume_stage_stats"] = {
                    **(iter_info.get("volume_stage_stats") or {}),
                    **collector.stage_stats(),
                }
                # `collector.summary_line()` 已并进轮末那行（`volume_line=` 入参），不再单打。

    st.iter_info = iter_info
    st.rctx = rctx
    st.ts_code_bytes = ts_code_bytes
    st.ts_code_hit = ts_code_hit


def check_mode_and_d14(st: JobState) -> None:
    """per-tick 红线 + D14 语料血缘（跨课程语料绝不混训）。"""
    manifest = st.manifest

    # ---- mode 红线（v1：仅 per-tick） ----
    if manifest["mode"] != "per-tick":
        raise ProtocolError(f"mode={manifest['mode']!r} != per-tick——远程 v1 红线，拒收")

    # ---- D14 语料血缘：job.course_fp == 每个 shard 的 manifest.course_fp ----
    # （跨课程语料绝不混训——发布端已保证 shard 集按 course_fp 过滤，这里再校验一次）
    # 读的 `shard_dirs` 按 kind 不同：普通 job = 解包出的 payload shard；kind=iter = 本轮现场
    # 跑出来的（见 iter 分支里那次重绑的注释）。
    _cfp = str(manifest["course_fp"])
    _corpus = str(manifest.get("corpus_fp", "") or "")
    for _sd in st.shard_dirs:
        try:
            with open(os.path.join(_sd, "manifest.json"), encoding="utf-8") as _f:
                _sm = json.load(_f)
        except (OSError, ValueError) as _e:
            raise ProtocolError(f"D14 course_fp: 读 shard manifest 失败 {_sd}: {_e}") from _e
        if not d14_corpus_match(_cfp, _corpus, _sm):
            raise ProtocolError(
                f"D14 course_fp 不匹配：job={_cfp[:12]}… shard={str(_sm.get('course_fp'))[:12]}… "
                f"（{_sd}）——跨课程语料混入，拒收"
            )


def echo_smoke_result(st: JobState) -> dict:
    """--echo：不拉 torch、不跑 PPO——把 payload 携带的 init 权重与指纹原样回传为结果。"""
    jid, manifest, log = st.jid, st.manifest, st.log

    # ---- 冒烟回显（--echo，2026-09-05）：不拉 torch、不跑 PPO——把 payload 携带的
    # init 权重与指纹原样回传为 job 结果（hub-start --smoke-only 的 Kaggle 交互预演，
    # 用户拍板：真课程 + 作废轮，不建虚拟课程）。三重校验按构造必过
    # （init_weights_fp/data_fp/commit_echo 均为 manifest 回显）；消费方
    # （trainer/loop_steps._remote_ppo）见 result["smoke"] 作废本轮（it 不前进）。
    init_w = st.job_dir / "init_weights.json"
    if not init_w.exists():
        raise ProtocolError("payload 缺 init_weights.json——echo 冒烟无法回显")
    # opt tar：manifest 携带则原样回显（Adam 动量 = 发布时状态）；首轮无 tar 时
    # 回空 tar（解包为空目录——作废轮的落位产物会被 _prepare_iter_dir 清场，
    # 真 PPO 轮会重新落位覆盖，永不消费空 tar）
    opt_b64 = str(manifest.get("opt_init") or "")
    if not opt_b64:
        import io as _io

        _buf = _io.BytesIO()
        with tarfile.open(fileobj=_buf, mode="w:"):
            pass
        opt_b64 = encode_opt_tar(_buf.getvalue())
    result = {
        "job_id": jid,
        "data_fp": manifest["data_fp"],
        "init_weights_fp": manifest["init_weights_fp"],
        "weights_json": encode_weights_json(init_w.read_bytes()),
        "opt_tar_b64": opt_b64,
        "agg": {
            "policy": 0.0,
            "value": 0.0,
            "entropy": 0.0,
            "kl": 0.0,
            "kickstart": 0.0,
            "mean_ret": 0.0,
            "value_new_rms": 0.0,
            "policy_new_rms": 0.0,
            "steps": 0,
            "chunks": 0,
        },
        "commit_echo": manifest["commit"],
        "ppo_sec": 0.0,
        "smoke": True,
    }
    result["wire"] = _wire_block(
        payload_bytes=st.payload_nbytes,
        payload_dl_sec=st.payload_dl_sec,
        unpack_sec=st.unpack_sec,
        prune_dirs=st.pruned_dirs,
    )
    # 两遍收敛：result_bytes 自己的十进制位数要算进体长（第一遍以占位 0 计）。
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    result["wire"]["result_bytes"] = len(pack_result_v2(result))
    validate_result(result, manifest, commit_echo_must_match=False)
    _persist_result(st.work_dir, jid, result)
    log(f"job {jid}: ECHO (smoke) — init 权重原样回传（未跑 PPO）")
    return result
