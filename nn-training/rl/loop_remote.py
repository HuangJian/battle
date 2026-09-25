"""loop_remote —— **远端 PPO 腿** mixin（2026-09-23 从 rl/loop_steps.py 拆出，S4 第二步）。

这一簇 13 个方法共享同一条链：**发布 job → 领取/等待 → 三重校验落位 → 节点 failover →
事件落账**，覆盖两种上云形态（`kind=run` 半离线段 / `kind=iter` 整轮上云）。它们原先混在
`TrainingSteps`（1715 行）里，与评估/报告/日志那几个方法职责分明——S4 第一步先搬走了模块级
的传输/发布自由函数（→ `rl/loop_transport.py`），这一步搬方法。

## 依赖方向：调用者依赖被调用者

`rl/loop_steps.py` 里是 `class TrainingSteps(TrainingRemote, …)`——因为这一簇的**唯一入口**
`_remote_ppo` 是被 TrainingSteps 的其余方法调用的（`run_training` 主链）。元组此后只**追加**
（S17 加 `TrainingEval`、S21 加 `TrainingExport`，`__mro__[1]` 恒为 `TrainingRemote`）。
反方向靠 5 个小助手，它们在**组合实例**上动态解析（混入的常态；`TrainingLoop(RoundSteps,
TrainingSteps, TrainingGuards, TrainingLifecycle)` 是唯一被实例化的类）：三个仍住 `TrainingSteps`
（`_commit_journal` / `_forensics` / `_per_stage_quota`），两个随产物出包簇搬到基类
`TrainingExport`（`_ensure_ts_code` / `_volume_plan_block`，S4 第二十一刀）——对本模块而言都是
`self.*`，落点不影响解析。

这样选而不是「给 `TrainingLoop` 加一个基类」：后者要改组合类 + 4 个「继承真混入」的测试宿主
（`_Stub(TrainingSteps)` 等），而本方案 **`TrainingLoop` 的基类不变、测试宿主一行不改**。

## ⚠ DI seam 是本模块**自己的**

`dist_common` / `_push_submit` / `_push_wait_result` 被测试以**模块全局**注入；方法搬到这里，
它们就在**本模块**的命名空间解析 ⇒ 测本模块方法的用例必须 patch `rl.loop_remote.*`
（测 `TrainingSteps` 其余方法的用例仍 patch `rl.loop_steps.*`；S4 第二十一刀搬走的那 4 个方法
则 patch `rl.loop_export.*`——同名 seam 在三个命名空间里是**三个各自真实的注入点**）。同名 seam 在两处并存是
**两个各自真实的注入点**，不是重复定义——见 tests/test_loop_transport_split.py。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, cast

import dist_common
from common.protocol import (
    JobFailedError,
    ProtocolError,
    coef_active,
    find_payload,
)
from remote.push_client import submit_job as _push_submit
from remote.push_client import wait_result as _push_wait_result
from rl.events import write_event, write_gate_verdict
from rl.log import log
from rl.loop_round import RemotePpoJob, RoundContext, StepResult, wait_for
from rl.loop_transport import (
    BundleExportedError,
    SmokeVoidRoundError,
    _course_cf_tunnel,
    _gate_round_shards,
    _gpu_push_nodes,
    _hub_push_opt_in,
    _kickstart_ref_payload,
    _push_over_nodes,
    _remote_forward_agg,
    _run_wait_sec,
    _wire_from_result,
    fatal_remote_http,
    kickstart_coef,
    kickstart_warn_kind,
    remote_retryable_exceptions,
    require_remote_transport,
    resolve_hub_push,
    resolve_transport,
)


class TrainingRemote:
    """远端 PPO 腿 mixin：发布 → 领取 → 三重校验落位 → failover → 事件落账。

    被 `TrainingSteps` 继承（调用者依赖被调用者）；只有组合类 `TrainingLoop` 会被实例化。
    """

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 与 rl/loop_steps.py 的同类声明**有意并存**：混入的状态契约必须在**每个**文件里对
    # mypy 可见（否则本文件里的 `self._report` 会被判成未声明属性），而运行期的唯一真相
    # 是那个被实例化的组合类。
    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _traj_dir: Any
    _jsonl_path: Any
    _report: dict
    _rollout_sec: float
    #: M3：本轮节点侧采集墙钟；None = 本轮不在节点采集（本地轮）。必须在这里声明类型
    #: ——只在 _remote_iter 里赋值会被 mypy 推成 float，子类的 `float | None` 就冲突。
    _node_rollout_sec: float | None
    _ppo_sec: float
    #: 云端 worker **自报**的真训练秒（load+chunk+update，不含上传/排队/下载）。
    #: 与 `_ppo_sec`（往返墙钟）分开记——后者打包传输与排队，用于诊断/配额，
    #: 不应当作"训练量"（排队越久越"达标"是错的，且本地采样期间云端空转它看不到）。
    _ppo_cloud_sec: float
    _total_steps: int
    _chunks_n: int
    _agg: Any
    _kl_cum: Any
    #: 远端连续失败 → 写 ABORT 后停腿（loop 在 PPO 步后检查）。
    _leg_abort: bool
    #: 远端连续失败计数（成功即复位）。
    _remote_fail: int
    _rotate_seed: int
    # 仅由方法体内自赋值产生（从未显式声明）；跨方法读到时需要类型可见。
    _bundle_index: Any
    _code_sha256: Any
    _code_zip_path: Any
    _collect_child: Any
    _demo_raw: Any
    _ts_code_zip_path: Any
    _wire: Any

    #: 由 `TrainingSteps` 提供、在本模块里被**调用**的 5 个助手（组合实例上动态解析）。
    #: 声明为 `Any` 的理由同 `rl/loop_steps.py` 里的 `_ledger_apply`：混入间互调的方法必须
    #: 在每个文件里类型可见，否则 mypy 报 attr-defined。
    _commit_journal: Any
    _ensure_ts_code: Any
    _forensics: Any
    _per_stage_quota: Any
    _volume_plan_block: Any

    def _abort_node_failure(self, it: int, e: BaseException, *, where: str) -> None:
        """节点已回报原因的**确定性**失败 → 写 ABORT 判决 + 停腿标记。

        2026-09-17（plan/remote-wire-remediation §5.3 缺口）：此前这类失败（bun 装不上 /
        TS 运行时取不到 / argv 非法）在训练侧只表现为 `wait_job` 25 分钟超时——
        「能力缺失」被写成「网络/排队问题」，而且每次重试再白烧一个超时窗口。现在原因
        随 `JobFailedError` 直接到达，判决里写的是真原因（人一眼能修）。

        不重试、不降级：节点缺的是运行时能力，换机/换轮都一样；修完节点重跑同课即可
        （重发同 job 会清失败标记，见 hub_client.publish_job）。
        """
        reason = str(e)[:300]
        write_gate_verdict(
            self._jsonl_path,
            it,
            "ABORT",
            f"{where} 节点确定性失败（原因已随 /jobs/{{id}}/fail 回传）：{reason}",
            decider="loop",
        )
        log(f"[run_rl] GATE ABORT it{it}: {where} 节点报确定性失败——不重试，立即停腿：{reason}")
        self._leg_abort = True

    def _handle_remote_failure(self, it: int, e: BaseException) -> bool:
        """远端失败的**唯一**处置策略：`True` = 调用方原样上抛（本轮失败）。

        ★ 2026-09-21（§3 单一 PPO 路径）：**没有**"降级到本机"这一档 —— 返回值恒为 True，
        连败 3 次即 ABORT 停腿（原先的 `False = 已降级本机` 分支已连同旗标一并删除）。
        要本机算，操作员在控制台起本机 worker（与云机同一认领协议）。

        为什么要抽出来：三相拆分之后，「发布失败」「取结果失败」「落位失败」是**同一类**
        失败，必须过同一份判决——三段各自演化出不同的连败计数/停腿口径，正是本仓最贵的
        一类分叉（x3-step 事故就是「专为远端失败写的停腿判决一行没写」）。
        """
        if isinstance(e, JobFailedError):
            # 节点已回报原因的**确定性**失败（2026-09-17）：不消耗连败配额、不重试
            # ——重试只会再派给另一台同样干不了的机器，或等回同一个 410。
            self._abort_node_failure(it, e, where="远端 PPO")
            return True
        # 401/403/400：token 不对、IP 被 hub 闭锁、或请求本身有问题——**重试多少次都不会
        # 自愈**，继续消耗连败配额只是重复 publish 同一 job 并把停腿拖后（x3-step 事故：
        # 5×30s 空转 + 账本 5 条同 id job_pending，最后照样死）。
        fatal = fatal_remote_http(e)
        if fatal:
            write_gate_verdict(
                self._jsonl_path,
                it,
                "ABORT",
                f"远端 PPO 不可重试失败 HTTP {fatal}——检查 --remote-token 与 hub 日志 "
                f"AUTH FAIL / BLOCKED 行：{str(e)[:200]}",
                decider="loop",
            )
            log(
                f"[run_rl] GATE ABORT it{it}: 远端 HTTP {fatal}（鉴权/闭锁类，非网络抖动）"
                f"——不再重试，立即停腿"
            )
            self._leg_abort = True
            return True
        # 只对**可重试**失败计账（确定性失败在上面两档已提前 return——它们不消耗配额）。
        self._remote_fail += 1
        log(
            f"[run_rl] remote ppo it{it} FAILED ({type(e).__name__}: {str(e)[:200]}) — "
            f"consecutive={self._remote_fail}"
        )
        # ★ 单一 PPO 路径（2026-09-21 §3）：**没有**就地降级——连败 3 次即 ABORT 停腿。
        # loop 自己不具计算能力；想要本机算，操作员在控制台起本机 worker（与云机同一认领
        # 协议），而不是训练进程偷偷把 job 算在自己身上（C 腿事故的根）。
        if self._remote_fail >= 3:
            write_gate_verdict(
                self._jsonl_path,
                it,
                "ABORT",
                f"远端 PPO 连续失败 {self._remote_fail} 次（单一 PPO 路径：无本机降级）",
                decider="loop",
            )
            log(f"[run_rl] GATE ABORT it{it}: 远端不可用（无本机降级）——停腿")
            self._leg_abort = True
        return True

    def _remote_ppo(
        self,
        it: int,
        rollout_spec: dict | None = None,
        *,
        plan_bytes: bytes | None = None,
        wait_timeout_sec: float = 0.0,
        export_path: str | Path | None = None,
    ) -> dict:
        """远端 PPO（单一发布-等待路径，D11/D12）——**组合入口**：发布 → 等结果 → 落位。

        R2c-3（2026-09-18）把原来那 400 行顺序函数拆成三相（`_remote_ppo_publish` /
        `_remote_ppo_probe` / `_remote_ppo_fetch` / `_remote_ppo_land`），本函数保持拆分前
        的**组合语义**（打包 → 发布 → 阻塞等待 → 三重校验落位），四个既有调用点（本机轮 /
        节点轮 / 半离线整段 / 全离线导出）行为不变。

        差别只在**谁驱动**：细粒度路径（`step_ppo` + `Supervisor`）不调本函数，而是自己串
        三相——中间那次「问一句」允许让位，于是「等云机回传」不再是阻塞点（`_remote_ppo_step`）。
        """
        sess = self._remote_ppo_publish(
            it,
            rollout_spec,
            plan_bytes=plan_bytes,
            wait_timeout_sec=wait_timeout_sec,
            export_path=export_path,
        )
        return self._remote_ppo_land(sess, self._remote_ppo_fetch(sess))

    def _remote_ppo_publish(
        self,
        it: int,
        rollout_spec: dict | None = None,
        *,
        plan_bytes: bytes | None = None,
        wait_timeout_sec: float = 0.0,
        export_path: str | Path | None = None,
    ) -> RemotePpoJob:
        """远程 PPO（单一发布-等待路径，D11/D12）：打包 → 发布 job → 轮询等待 → 三重校验落位。

        `rollout_spec` 非空 = **M3 整轮上云**（kind=iter）：本轮不发本地 shard（payload
        只有 init 权重 + 可选 blob），改随 job 发 rollout 规格 + TS 运行时；节点自己跑
        exporter 产 shard 再跑 PPO，回传里带采集报告。除「发什么/收什么」外，发布/传输/
        三重校验/落位/埋点全走同一条链（不复制一份会漂的第二实现）。
        返回 result（调用方 _remote_iter 需要里面的 report）。

        - 打包：本轮 traj it{it} 下 wver 匹配的 shard 集 + init 权重 + 上轮 opt tar；
        - 发布：磁盘 IPC（job 目录 + jsonl job_pending 事件）→ 旁路 hub-server；
        - 等待：阻塞轮询 server 结果（发布-等待是**唯一** PPO 路径）；
        - 校验：init_weights_fp == 当前 args.out 指纹 + data_fp == 本地重算 + commit 一致；
        - 落位：weights_json → args.out（原子 replace）+ opt tar → it{it}/ppo_ckpt_remote。
        hub 全程免 torch（D2）：只做文件搬运 + sha256。
        """
        args = self.args
        it_dir = self._traj_dir
        t_ppo = time.time()
        # 半离线（kind="run"）：plan_bytes 非空 = 本 job 之后还要节点自主把计划跑完。
        if plan_bytes is not None and rollout_spec is None:
            raise SystemExit(
                "[run_rl] kind=run 必须同时带 rollout_spec（本 job 自己那一轮的采集规格）"
            )
        # I1 WAL：远端提交序列（打包→发布→等待→校验→落位）的 started/done 台账；
        # 重启后见 pending ⇒ 按同 it 重发 job（publish 幂等键 = run_id+it+wver，
        # verify_and_land 三重校验防错位落盘）。
        self._commit_journal().start("ppo_remote", str(it))
        self._forensics(f"remote_pre it{it}")
        # 本相位只做「打包 + 发布」：等结果与校验落位各在自己的相位里（三相拆分后
        # 三处的导入也各自独立——一份 import 清单服务三段，是拆相前那种形状的残留）。
        from remote.hub_client import (
            git_head,
            iter_shard_dirs,
            pack_code_zip,
            publish_job,
        )

        hub_url = str(getattr(args, "remote_hub_url", "") or "")
        token = str(getattr(args, "remote_token", "") or "")
        # Push 优先解析（纯 push 不再依赖本地 hub-server / cloudflared）：
        # 有 gpu_push 节点或 REMOTE_PUSH_NODE 时，payload/code 直推云机隧道，
        # hub_url 可缺省。token 仍要（pull 回落 / env 节点鉴权）；配置节点自带 authKey。
        transport = str(getattr(args, "remote_transport", "auto") or "auto")
        gpu_nodes = resolve_transport(transport, hub_url, token, _gpu_push_nodes(token))
        # hub 中介推送（2026-09-18）：发布带 manifest.dispatch="push"，由 hub 按登记表
        # 推给空闲 GPU worker——训练侧不直连节点，于是「队列顺序/空闲判定/超时回落/
        # 多课程公平」全住在一处。与 gpu_nodes 互斥（resolve_transport hubpush 恒返空）。
        hub_push = resolve_hub_push(transport, hub_url, token, _hub_push_opt_in())
        require_remote_transport(hub_url, token, gpu_nodes)
        log(
            f"[run_rl] remote ppo transport={transport} push_nodes={len(gpu_nodes)} "
            f"hub_push={hub_push} hub={hub_url or '-'}"
        )
        job_root = str(getattr(args, "remote_job_root", "") or "") or str(
            Path(args.traj) / "remote-jobs"
        )
        # 课程快照（D13/D14）：课程文件全文 + course_fp = sha256(文件字节)
        # ★ 顺序：**先**算血缘（course_fp/corpus_fp）、**再**扫 shard——扫描要用它们过滤。
        course = getattr(args, "course_obj", None)
        if course is None:
            raise SystemExit(
                "[run_rl] PPO 走 hub 队列需要课程（--course <name>，D13 课程指针）——"
                "reward 公式/超参/关卡由课程单一事实来源"
            )
        course_path = Path(getattr(args, "course_path", "") or "")
        if not course_path.exists():
            course_path = Path(getattr(args, "course", "") or "")
        if not course_path.exists():
            from rl.config import resolve_course

            course_path = resolve_course(course.name)
        # 课程全文快照 + course_fp = sha256(**启动冻结字节**)——与
        # rl/cmd.course_fp_for_args / rl/loop_lifecycle._course_file_fp 同算法同字节源（D14），
        # 否则 CRLF 换行下 read_text 的通用换行翻译会使指纹不一致、血缘断裂。
        # 冻结 = 热加载编辑（含被拒的语料身份改动）永不进 D13 快照/指纹（不泄漏云端）。
        frozen = getattr(args, "course_frozen_bytes", None)
        course_bytes = frozen if frozen else course_path.read_bytes()
        course_text = course_bytes.decode("utf-8")
        course_fp = hashlib.sha256(course_bytes).hexdigest()
        # D14 语义版：corpus_fp = 语料身份（env+reward 解析值哈希，rl/config.corpus_identity_fp）。
        # 与 course_fp（文件血缘）并存进 manifest；worker 装载校验优先比 corpus_fp——
        # 预算/路径类 mid-run 课程编辑只动 course_fp，不再触发整轮 shard 拒收。
        from rl.config import corpus_identity_fp

        corpus_fp = corpus_identity_fp(course)
        # 本轮应训 shard 集（与 _serial_ppo load_episodes 装载口径一致）；
        # 三条分支的判定抽在 `_gate_round_shards`（纯函数，回归见 test_remote_ppo_gate_fields）。
        # D14 血缘过滤（2026-09-20 事故）：`it{it}` 是累积目录，课程文件被编辑过/换过
        # runId 时里面会躺着旧血缘 shard；云端 worker 逐 shard 拒收 ⇒ 整份 job 退回，
        # hub 侧永远等不到结果（训练轮空转 + worker 反复领同一份死活）。过滤判据
        # 与云端同源（`common.protocol.d14_corpus_match`），故「打进 payload 的集合」
        # 恒等于「云端会接受的集合」；`verify_and_land` 用同样的两个 fp 重算 data_fp。
        local_shards = iter_shard_dirs(
            args.traj,
            it,
            log=(lambda _m: None) if (rollout_spec or export_path is not None) else log,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
        )
        shard_dirs = _gate_round_shards(
            local_shards=local_shards,
            rollout_spec=rollout_spec,
            exporting=export_path is not None,
            it=it,
            it_dir=str(it_dir),
        )
        # ppo_schedule 解析后值（执行用）——_course_iter 已按 it 折算进 args
        from rl.reward_library import METRICS_VERSION

        commit = git_head()
        # 启动时一次打包源文件 code.zip（避免后继并行修改干扰云端代码一致性）
        if not hasattr(self, "_code_sha256"):
            nn_root = Path(__file__).resolve().parent.parent
            code_zip_path = Path(job_root) / "code.zip"
            cs = pack_code_zip(nn_root, code_zip_path, log=log)
            self._code_sha256 = cs
            self._code_zip_path = code_zip_path
        # code.zip 已包含当前源码快照（含未提交修改），无需 git commit-pin 检查。
        from rl.queue import RUN_ID

        run_id = RUN_ID
        ckpt_remote_path = Path(args.traj) / f"it{it - 1}" / "ppo_ckpt_remote"
        ckpt_remote: Path | None = ckpt_remote_path if ckpt_remote_path.exists() else None
        # BC-anchored kickstart（§363）：缰绳系数走 update_kwargs 衰减（ref 传 None——
        # 系数是纯数学，不需模型）；ref 权重读课程 bc 文件（一次，base64 进 manifest）。
        kick_on = bool(getattr(args, "kickstart_ref", False))
        kick_kl = kickstart_coef(args, it) if kick_on else 0.0
        # 原先只看 kick_on 开关 ⇒ 缰绳早已松开、ref 权重还在每轮空运（~0.36 MB 原始，
        # 是 payload 里可观的一块）。系数退火到阈值以下就不再附字节。
        kick_live = kick_on and coef_active(kick_kl)
        ref_b64, ref_fp = _kickstart_ref_payload(args) if kick_live else ("", "")
        # demo 混 batch（x20 后续）：bank 静态（同 run 内逐轮同字节），首轮读一次缓存。
        # 三键半开即拒（缺 bank 发 coef = 静默纯 PPO，不可接受；缺 coef 发 bank = 空运）。
        demo_bank_path = str(getattr(args, "demo_bank", "") or "")
        demo_coef = float(getattr(args, "demo_bc_coef", 0.0) or 0.0)
        demo_per_mb = int(getattr(args, "demo_per_mb", 0) or 0)
        demo_raw: bytes | None = None
        if demo_bank_path or demo_coef > 0 or demo_per_mb > 0:
            if not (demo_bank_path and demo_coef > 0 and demo_per_mb > 0):
                raise SystemExit(
                    "[run_rl] demo 三键须齐全（demo_bank/demo_bc_coef/demo_per_mb）——半开拒发"
                )
            if not hasattr(self, "_demo_raw"):
                bp = Path(demo_bank_path)
                if not bp.exists():
                    raise SystemExit(f"[run_rl] demo_bank 不存在：{demo_bank_path!r}——检查课程路径")
                self._demo_raw = bp.read_bytes()
                log(f"[run_rl] demo bank 已装载：{demo_bank_path} {len(self._demo_raw) / 1e6:.1f}MB")
            demo_raw = self._demo_raw
        # I1 取证：publish 前的临终对照点（上传大 payload 前的 RSS/磁盘基线）。
        self._forensics(f"remote_pre_publish it{it}")
        # M0：打包（tar.xz + 编码）/ 落盘墙钟——iteration 事件的 wire.pack_sec。
        t_pack = time.time()
        # M2：协议瘦身开关（rl.slim / --remote-slim；默认开）。关 → 逐字节旧行为。
        slim = bool(int(getattr(args, "remote_slim", 1) or 0))
        if rollout_spec:
            # M3：TS 运行时 zip（一次打包，缓存在 self 上——sha 不变就不重打）。
            self._ensure_ts_code(job_root, log=log)
        manifest = publish_job(
            job_root=job_root,
            jsonl_path=str(self._jsonl_path),
            run_id=run_id,
            it=it,
            traj_dir=args.traj,
            shard_dirs=shard_dirs,
            init_weights_path=args.out,
            ckpt_remote_dir=ckpt_remote,
            commit=commit,
            code_sha256=self._code_sha256,
            code_zip_path=self._code_zip_path,
            course=course_text,
            course_fp=course_fp,
            # P4-W2 归属：课程短名（args.course_name，apply_course 挂上）；无课程为 ""。
            course_name=str(getattr(args, "course_name", "") or ""),
            corpus_fp=corpus_fp,
            reward_formula=course.reward.formula,
            formula_hash=course.reward_spec().identity(),
            metrics_version=METRICS_VERSION,
            gamma=float(getattr(args, "gamma", 0.995)),
            lam=float(getattr(args, "lam", 0.95)),
            mode=args.mode,
            epochs=int(args.epochs),
            mb=int(args.mb),
            lr=float(args.lr),
            kl_coef=float(getattr(args, "_kl_coef", 0.0) or 0.0),
            kl_cap=getattr(args, "_kl_cap", None),
            ent_coef=getattr(args, "_ent_coef", None),
            adv_norm=getattr(args, "adv_norm", "auto"),
            normalize_ret=bool(getattr(args, "normalize_ret", 0)),
            kickstart_kl=kick_kl,
            ref_weights_b64=ref_b64,
            ref_weights_fp=ref_fp,
            demo_bank_bytes=demo_raw,
            demo_bc_coef=demo_coef,
            demo_per_mb=demo_per_mb,
            shuffle=True,
            schedule_raw=course.ppo_schedule_dicts(),
            # 严格样本量配额（target_transitions 路线）：与 _serial_ppo 同一个来源，
            # 保证 remote 与本机两条 PPO 路径装载口径一致。0 = 全收（历史行为）。
            per_stage_quota=self._per_stage_quota(),
            # M2：瘦身开关 + 冒烟轮强制带 init_weights.json（echo 回显要用）。
            slim=slim,
            keep_init_weights=bool(getattr(args, "smoke", False)),
            # M3：kind=iter 的三件套（rollout 规格 + TS 运行时 sha/文件）；
            # 非 iter 轮恒为默认（kind="ppo"，manifest 不含这两个键 —— 逐字节不变）。
            kind="run" if plan_bytes is not None else ("iter" if rollout_spec else "ppo"),
            rollout_spec=rollout_spec,
            plan_bytes=plan_bytes,
            # 全离线导出：只建 job 目录（拿它当打包源），不记账本也不进待领池——
            # 云机不在网络上，记一条 `job_pending` 只会让控制台看到一条永远等不到工人的任务。
            register=export_path is None,
            # hub 中介推送的意图（hub 读它决定「这份活由我推」；缺席 = pull，字节不变）。
            dispatch="push" if hub_push else "",
            ts_code_sha256=(
                str(getattr(self, "_ts_code_sha256", "") or "") if rollout_spec else ""
            ),
            ts_code_zip_path=getattr(self, "_ts_code_zip_path", None) if rollout_spec else None,
            log=log,
        )
        jid = manifest["job_id"]
        # R2b（plan/r2-loop-task-queue §2.3）：把刚发布的 job 补进 WAL 的**在飞集**——
        # job_id 是 publish 的返回值（start 时还没有），只能上这条 attach。重启后
        # `_commit_journal` 就能报出「在等哪个 job、推给了谁」，而不是只报一个 round 号。
        # ★ 2026-09-22（§course-error-isolation-loud）：`--export-bundle` 是只读快照且
        # **不与训练抢 per-course 锁**——此时**不得**往 journal 写 attach：这份在飞集属于
        # 真正服务的训练循环，导出并行写会让重启后「在等什么」报出一条永远等不到的幽灵
        # job（与上方 `register=export_path is None` 同一个门）。
        if export_path is None:
            self._commit_journal().attach(
                "ppo_remote",
                str(it),
                jid=jid,
                dispatch="push" if hub_push else "pull",
                ts=time.time(),
            )
        pack_sec = round(time.time() - t_pack, 3)
        if export_path is not None:
            # 全离线：不等待、不发 job——把这一段任务打成能上传 Kaggle/Colab 的任务包。
            from remote.bundle import export_bundle

            assert plan_bytes is not None  # 调用方保证（kind=run）
            index = export_bundle(
                export_path,
                manifest=manifest,
                plan_bytes=plan_bytes,
                init_weights_path=args.out,
                code_zip_path=self._code_zip_path,
                job_dir=Path(job_root) / jid,
                hub_url=hub_url,
                note=f"run_rl --export-bundle（it{it} 之后整段；course={getattr(args, 'course_name', '') or '-'}）",
            )
            log(
                f"[run_rl] 全离线任务包已导出：{export_path}"
                f"（{index['run_id']} it{index['it']} → it{index['end_it']}，"
                f"{sum(int(p['bytes']) for p in index['parts'].values()) / 1e6:.1f} MB）—"
                "上传 Kaggle/Colab 后：remote.bundle import + remote.run_loop"
            )
            self._bundle_index = index
            raise BundleExportedError(str(export_path))
        # ---- 会话成型：三相之间**唯一**的载体（发布 → 等结果 → 落位） ----
        # 超时预算在这里算定：同一份 job 的等待预算不该因为「谁先问了一句」而变
        # （细粒度路径会让位后再回来，那时 `wait_timeout_sec` 已经不在作用域里了）。
        timeout_sec = float(wait_timeout_sec or 0.0) or 30 * 60.0
        sess = RemotePpoJob(
            it=it,
            jid=jid,
            manifest=manifest,
            transport="push" if gpu_nodes else "hub",
            nodes=list(gpu_nodes),
            job_root=job_root,
            hub_url=hub_url,
            hub_token=token,
            timeout_sec=timeout_sec,
            t_ppo=t_ppo,
            pack_sec=pack_sec,
            kick_on=kick_on,
            kick_kl=kick_kl,
            rollout_spec=rollout_spec,
            segment=plan_bytes is not None,
            hub_push=hub_push,
        )
        # remote PPO 等待期集群空闲 —— 立即开 evalboard 窗领批（含等待期间新入队的）。
        # 否则「rollout 后才 enqueue」的批要等 PPO 收官后的第二次 idle，卡数十分钟。
        # **必须落在发布相位**：窗口要在「开始等」那一刻就开——细粒度路径会先让位，
        # 等到结果再开窗就永远错过那段空闲（那正是它要服务的窗口）。
        if hasattr(self, "_evalboard_idle"):
            self._evalboard_idle(it, getattr(self, "_last_dist_cfg", None))
        # 直推节点链路：**发布即提交**（三相拆分的关键约定）。探针要问「那份 job 现在怎么
        # 样了」，而节点上还没有这份 job 时它只会一直答「还没回」⇒ 提交必须落在发布相位，
        # 等待相位才可能真让位。提交本身是**有界**的上传（几十 MB），不是那 25 分钟的等待。
        if gpu_nodes:
            self._push_submit_first(sess)
        return sess

    def _remote_ppo_probe(self, sess: RemotePpoJob) -> dict | None:
        """相位②的**非阻塞**版：问一句就走（就绪 → 结果；未就绪 / 瞬时错 → None）。

        终局失败（410）照抛：「还没好」与「永远好不了」必须分开——后者要立刻停腿，而把它
        当成「还没好」正是 x3-step 事故把「bun 缺失」写成 25 分钟超时的原因。
        """
        from remote.hub_client import poll_job
        from remote.push_client import poll_result

        if sess.transport == "push":
            return poll_result(sess.probe_base_url, sess.probe_token, sess.jid)
        return poll_job(sess.probe_base_url, sess.probe_token, sess.jid)

    def _remote_ppo_fetch(self, sess: RemotePpoJob) -> dict:
        """相位②：**阻塞**等到结果（组合路径 / 节点轮 / 半离线整段用它）。

        细粒度驱动器不走这里：它先 `_remote_ppo_probe` 问一句，未就绪就 `WAIT` 让位
        ——让位点因此落在「已经发布、只是还没回」这个**真状态**上（`_remote_ppo_step`）。
        """
        from remote.hub_client import wait_job

        if sess.transport == "push":
            return self._push_fetch(sess)
        return wait_job(
            sess.hub_url, sess.hub_token, sess.jid, timeout_sec=sess.timeout_sec, log=log
        )

    def _remote_ppo_land(self, sess: RemotePpoJob, result: dict) -> dict:
        """相位③：三重校验 + 落位 + 记账 + 结算字段（`_remote_ppo` 的返回就是它）。

        `result` 由相位②给（阻塞或非阻塞取到的是同一件东西），本相位因此对驱动方式
        完全无感——这也是把「校验落位」单独切出来的理由：它既不该被等法影响，也不该
        自己再去碰网络。
        """
        args = self.args
        from remote.hub_client import mark_job_completed, verify_and_land

        # 三重校验 + 落位（D12）：任一不等响亮拒绝，不落盘
        verify_and_land(
            result,
            sess.manifest,
            init_weights_path=args.out,
            traj_dir=args.traj,
            it=sess.it,
            out_weights=args.out,
            log=log,
        )
        mark_job_completed(self._jsonl_path, sess.jid)
        # I1：远端提交序列完成（校验落位 + job 记账）——WAL 收口。冒烟作废轮也算
        # 完成（commit 本身成功了；作废轮由 _prepare_iter_dir 清场后重试新轮）。
        self._commit_journal().finish("ppo_remote", str(sess.it), jid=sess.jid)
        self._forensics(f"remote_post it{sess.it}")
        if result.get("smoke"):
            # 冒烟回显（worker --echo）：全链路已验证，但权重 = init 回显非真 PPO——
            # 作废本轮。job_completed 已记账（审计链完整）；落位的 out 权重与
            # 发布时逐字节相同（init 回显），无需回滚；重试轮 _prepare_iter_dir 清场。
            log(f"[run_rl] remote ppo it{sess.it}: job {sess.jid} 是冒烟回显（result.smoke）——本轮作废")
            raise SmokeVoidRoundError(sess.jid)
        # H7（review-hy）：--remote-precollect 1 → 在 PPO 等待窗口后 spawn 下一轮首波
        # 预采（θ_N 快照，复用 spawn_collect_next 双缓冲机制）。默认 0（Q10 测后开）
        # 时不可达。stale 分数上限 30% 的筛选（S5/F4）属 §6-D3 后续项，未在此实现。
        # M3：上云轮不得预采——节点已经在跑本轮的 rollout，hub 再 spawn 一个本地预采
        # 就成了双份采集（且下一轮又会被 rollout_src=node 拒绝发布）。
        if (
            sess.rollout_spec is None
            and int(getattr(args, "remote_precollect", 0) or 0)
            and (args.iters <= 0 or sess.it < args.iters)
        ):
            from rl.collect_only import spawn_collect_next

            # H7（review-hy）：预采子进程句柄必须存入 self._collect_child，
            # 否则主循环的 join_precollect_child（下一轮开头）接收 None 跳过
            # 等待，预采首波可能尚未落盘即被 _prepare_iter_dir 清场。
            self._collect_child = spawn_collect_next(args, sess.it)
            if self._collect_child is not None:
                log(
                    f"[run_rl] remote precollect: next-round first-wave spawned (pid={self._collect_child.pid})"
                )
        # 结算字段（下游 breaker / stop-loss / events 账本原样消费，D4）
        self._agg = _remote_forward_agg(result.get("agg", {}))
        self._chunks_n = int(result.get("agg", {}).get("chunks", 0))
        self._total_steps = int(result.get("agg", {}).get("steps", 0))
        self._kl_cum = self._agg["kl"]
        # 往返墙钟（含打包/上传/排队/下载）：分母是**发布时刻**，跨步也认同一份
        self._ppo_sec = round(time.time() - sess.t_ppo, 1)
        # 真训练秒：云端 worker 自报的 load+chunk+update（旧 worker / echo 无此字段 → 回落往返）
        self._ppo_cloud_sec = float(result.get("ppo_sec") or 0.0) or self._ppo_sec
        # M0 统一计量：传输层实测（字节/秒）汇总进 iteration 事件的 wire 子字典。
        # protocol/edge_ip/slim 由 M1/M2 的配置面注入（未配 = None，旧行为）。
        _cf_tunnel = _course_cf_tunnel(args)
        self._wire = _wire_from_result(
            result,
            # hub 中介推送也是「推」：hub 侧记的是 submit 实测（body_bytes/upload_sec），
            # 与直推同一套读数——两种 push 的可观测性不该一个有一个无。
            is_push=bool(sess.nodes) or sess.hub_push,
            pack_sec=sess.pack_sec,
            cfg={
                # M1：记**真正生效**的隧道选项（CLI > courses.<stem>.cf_* > rl.cf_* > None）。
                # 不能用 `getattr(args, "remote_cf_protocol", None)`——控制台写的是 rl-config，
                # 不在 CLI 参数里时那个读法永远是 None（2026-09-17 查出的真缺口）。
                "protocol": _cf_tunnel[0],
                "edge_ip": _cf_tunnel[1],
                "slim": bool(int(getattr(args, "remote_slim", 1) or 0)),
                # M3：记**实测**在哪采集（node = 本轮整轮上云；run = 整段自主），不是
                # args 字面量（auto 会被 _rollout_source 解析成 local/node——原样记
                # auto 等于没记）。
                "rollout_src": (
                    "run"
                    if sess.segment
                    else ("node" if sess.rollout_spec else "local")
                ),
            },
        )
        # 启动协议补丁（2026-09-08 vk1 事故）：kickstart_ref 已要求时，it1 校准把
        # 「缰绳真实落地」做进循环——云端 agg 无 kickstart 键或值恒 0 = worker 没跑
        # 缰绳（旧代码/模块钉住），响亮警示而非静默裸奔；正常值应为 0.1~0.6 量级。
        # 2026-09-14 x2-start it31 豁免：系数按几何衰减到期归零后（kick_kl 不活跃、
        # 训练侧不再附 ref），worker 回 0 是预期行为，不得误报（kickstart_warn_kind）。
        _kick_kind = kickstart_warn_kind(
            kick_on=sess.kick_on,
            smoke=bool(result.get("smoke")),
            agg_kickstart=float(self._agg.get("kickstart", 0.0)),
            kick_coef=sess.kick_kl,
        )
        if _kick_kind == "warn":
            log(
                f"[run_rl] WARN remote it{sess.it}: kickstart_ref 已要求（kk 衰减调度激活）"
                "但云端结果 kickstart=0——worker 未执行缰绳？查 worker 代码/会话新鲜度"
            )
        elif _kick_kind == "expired":
            log(
                f"[run_rl] remote it{sess.it}: kickstart 系数已衰减到期（kk={sess.kick_kl:g}）——"
                "worker 未上报距离属预期，不告警"
            )
        log(
            f"[run_rl] remote ppo it{sess.it}: job {sess.jid} accepted — "
            f"steps={self._total_steps} chunks={self._chunks_n} "
            f"kl={self._agg['kl']:.5f} entropy={self._agg['entropy']:.4f} "
            + (f"kickstart={self._agg['kickstart']:.4f} " if sess.kick_on else "")
            + (f"demo_bc={self._agg.get('demo_bc', 0.0):.4f} " if self._agg.get("demo_bc") else "")
            + f"({self._ppo_sec}s round-trip) -> {args.out}"
        )
        return result

    def _push_submit_node(self, sess: RemotePpoJob, i: int) -> None:
        """向第 i 个直推节点提交 job（**发布**动作），并把传输读数记进会话。

        payload / code / blob 一律**从 job 目录重读盘**：会话刻意不持有几十 MB 的字节
        （见 `RemotePpoJob` 的文档），换节点重发时正好也重读一次。
        """
        node = sess.nodes[i]
        job_dir = Path(sess.job_root) / sess.jid
        _pl = find_payload(job_dir)
        if _pl is None:
            raise ProtocolError(f"job {sess.jid}: payload 不在盘上（push 无法发送）")
        from common.protocol import BLOB_NAMES, blob_path

        blobs = {
            n: bp.read_bytes()
            for n in BLOB_NAMES
            if (bp := blob_path(job_dir, n)).exists()
        }
        # M0：submit_job 返回本轮实测传输账（body/payload/code 字节 + 上传秒）
        sess.submit_wire = _push_submit(
            str(node["url"]),
            str(node.get("authKey", "")),
            sess.manifest,
            _pl.read_bytes(),
            self._code_zip_path.read_bytes(),
            blobs=blobs,
            ts_code_zip=(
                Path(self._ts_code_zip_path).read_bytes() if sess.rollout_spec else None
            ),
            echo=bool(getattr(self.args, "smoke", False)),
            log=log,
        )
        sess.node_i = i
        log(f"[run_rl] push: job {sess.jid} 已提交 -> {node['url']}（等待 GPU 完成）")

    def _push_submit_first(self, sess: RemotePpoJob) -> None:
        """按序找第一个收下这份 job 的节点；全失败时把**确定性原因**原样上抛。

        failover 判决在 `_push_over_nodes`（与 `_push_job_round` / `_push_fetch` 同一份）。
        """

        def step(i: int, _node: dict) -> None:
            self._push_submit_node(sess, i)

        _push_over_nodes(sess.nodes, 0, step, log)

    def _push_fetch(self, sess: RemotePpoJob) -> dict:
        """等已提交的节点回结果；该节点失败就换下一个（**重提交**），全失败照旧上抛。

        与组合入口 `_push_job_round` 同序（逐节点「提交 → 等」，任一环节失败即换人），
        差别只在「第一个节点的提交已经发生在发布相位」⇒ 本函数从 `sess.node_i` 起走，
        且换到新节点时要先补提交（新节点从没见过这份 job）。
        """

        def step(i: int, node: dict) -> dict:
            if sess.node_i != i:
                self._push_submit_node(sess, i)
            result = _push_wait_result(
                str(node["url"]),
                str(node.get("authKey", "")),
                sess.jid,
                timeout_sec=sess.timeout_sec,
                log=log,
            )
            if isinstance(sess.submit_wire, dict) and isinstance(result, dict):
                # 挂在结果上随返回一路上浮（_wire_from_result 消费）——不改 result 的
                # 校验字段，纯 additive。
                result["wire_hub"] = sess.submit_wire
            return result

        return cast(dict, _push_over_nodes(sess.nodes, sess.node_i, step, log))

    def _remote_ppo_step(self, ctx: RoundContext) -> StepResult | None:
        """远端 PPO 的**三相驱动**（细粒度路径；R2c-3）。

        返回 `None` = 本轮远端 PPO 已收口（成功，或已降级给本机）；返回 `StepResult`
        = 本轮结束（让位 / 停车）。

        与组合路径（`_remote_ppo`）共用同一批相位方法与同一份失败判决
        （`_handle_remote_failure`），唯一差别在这里**允许让位**：`ctx.resumable` 为真时
        未就绪就 `wait_for`，执行权交给别的课程；为假则退化成阻塞取结果（组合语义）。
        """
        it = ctx.it
        sess = ctx.remote
        if sess is None:
            try:
                sess = self._remote_ppo_publish(it)
            except remote_retryable_exceptions() as e:
                if self._handle_remote_failure(it, e):
                    raise
                return None  # 已降级：调用方改走本机
            ctx.remote = sess
        try:
            result = self._remote_ppo_probe(sess)
        except remote_retryable_exceptions() as e:
            if self._handle_remote_failure(it, e):
                raise
            return None
        if result is None:
            if ctx.resumable:
                # ★ 让位点：job 已经发布，只是还没回。本机没在替它干活（云机在跑）
                # ⇒ 票还掉，机器让给别的课（`hold=False` 见 `LoopRunner`）。
                return wait_for(f"等远端 PPO 回传（job {sess.jid}）")
            try:
                result = self._remote_ppo_fetch(sess)
            except remote_retryable_exceptions() as e:
                if self._handle_remote_failure(it, e):
                    raise
                return None
        try:
            self._remote_ppo_land(sess, result)
        except remote_retryable_exceptions() as e:
            if self._handle_remote_failure(it, e):
                raise
            return None
        ctx.remote = None
        self._remote_fail = 0
        return None

    def _remote_iter(self, it: int, pairs: list[tuple[int, int]]) -> None:
        """M3：**整轮上云**（kind=iter）——节点跑 rollout + PPO，hub 只发规格、收结果。

        与 `_remote_ppo` 共享整条发布/传输/三重校验/落位/埋点链（只换「发什么、收什么」）：
          * 发：rollout 规格（逐局 argv，job 目录内相对路径）+ TS 运行时 + init 权重；
          * 收：权重/opt/agg（同旧）+ **采集报告**（本机此时无 shard 可算）。

        与动态采集（target_transitions）互斥：那套语义要求训练侧反复读本地 shard 补波，
        而这里 shard 在节点上（跑完即毁）。配错就响亮失败，不静默降级。
        """
        args = self.args
        if int(getattr(args, "target_transitions", 0) or 0) > 0:
            raise SystemExit(
                "[run_rl] rollout_src=node 与 --target-transitions（动态采集）互斥："
                "补波需要训练侧反复读本地 shard，而上云轮的 shard 在节点上（跑完即毁）。"
                "要动态采集就保持 rollout_src=local"
            )
        from rl.iter_job import build_iter_spec

        wver = dist_common.weights_fingerprint(args.out)
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0),
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
        )
        log(
            f"[run_rl] rollout_src=node it{it}: {len(pairs)} 局上云采集"
            f"（node workers={workers}，wver={wver[:12] if wver else '-'}…）"
        )
        t_roll = time.time()
        try:
            result = self._remote_ppo(it, rollout_spec=spec)
        except remote_retryable_exceptions() as e:
            # 上云轮走的是 `_remote_ppo` 组合入口（loop_core 在 _node_rollout 时
            # 跳过 _serial_ppo），所以那条路的「鉴权/闭锁类失败立即 ABORT」得在这里
            # 补上：否则 401/403 会走通用兜底 5×30s 重发同一 job 再死（x3-step 事故
            # 的同一个浪费）。只贴判决，不在这里降级——上云轮没有本地 shard 可训练。
            if isinstance(e, JobFailedError):
                # 与上面同规：节点已回报原因（如 bun 装不上 / TS 运行时取不到）——
                # 这是确定性能力缺失，重试无益，立即带原因停腿。
                self._abort_node_failure(it, e, where="rollout_src=node 采集+PPO")
                raise
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"rollout_src=node 不可重试失败 HTTP {fatal}——检查 --remote-token "
                    f"与节点隧道：{str(e)[:200]}",
                    decider="loop",
                )
                log(
                    f"[run_rl] GATE ABORT it{it}: 上云 node 轮远端 HTTP {fatal}"
                    f"（鉴权/闭锁类，非网络抖动）——不再重试，立即停腿"
                )
                self._leg_abort = True
            raise
        rep = dict(result.get("report") or {})
        rep.pop("perGameSecs", None)  # 逐局秒数只用于诊断，不进 iteration 账本
        self._report = rep
        # 节点侧采集墙钟（本机口径的 self._rollout_sec 在这里无意义——整轮都在云上）。
        self._node_rollout_sec = float(rep.get("elapsedSec") or 0.0)
        self._rollout_sec = self._node_rollout_sec
        log(
            f"[run_rl] remote iter it{it}: 节点采集 {rep.get('games')} 局 "
            f"（{self._node_rollout_sec}s），往返 {round(time.time() - t_roll, 1)}s"
        )

    def _remote_run_segment(self, it: int, pairs: list[tuple[int, int]], n: int) -> int:
        """半离线：把 it..end_it **整段**交给云机自主跑（kind=run），返回段尾 it。

        用户需求（2026-09-17）：「云机领到任务（课程 + 初始权重 + 代码）后，即使本机 hub
        一直失联，也能全程自主完成训练，并以 kaggle/colab 官方方式提供产物打包下载」。

        与本机、kind=iter（逐轮上云）的差别只有一条：**hub 不再逐轮决策**。计划
        （`rl/plan.build_plan`）把「后面每轮跑哪些局 + argv 长什么样 + 到哪停」一次性写成
        文件随 payload 下发；节点用同 commit 的代码重放（`pairs_fp` 两侧对账，不符就
        一局不跑），逐轮权重/指标写进产物目录（`remote/artifacts.py`），末尾才回传合并结果。

        本函数只做三件 hub 侧的事：① 组装计划并发布；② 用**放大的**等待预算阻塞（整段
        墙钟量级）；③ 把节点回的逐轮明细落成 `run_segment` 事件（给控制台画曲线），并把
        段尾的报告/指标交给本轮结算（iteration 事件复用 `_record_iteration`）。

        中间轮没有本机 eval：它们不在本机跑，归档里也没有它们的权重（拿活指针去充数就是
        P0 修过的「标签超前一轮」）。所以调用方在本轮**跳过** `_dispatch_delayed_eval`。
        """
        args = self.args
        # ★ 2026-09-21（§3）：原先这里有「要求 --ppo remote」的闸——旗标删除后它恒真、
        # 会把整段误拒。单一 PPO 路径下「整段 rollout + PPO 都在节点上」本就是唯一形态。
        from rl.iter_job import build_iter_spec
        from rl.plan import RUN_NODE_LABEL, build_plan, dump_plan

        wver = dist_common.weights_fingerprint(args.out)
        if not wver:
            raise SystemExit(f"[run_rl] 半离线 it{it}: 本机无权重（{args.out}）——无起点不发段")
        workers = int(getattr(args, "remote_iter_workers", 0) or 0) or int(
            getattr(args, "workers", 1) or 1
        )
        game_timeout = float(getattr(args, "remote_iter_game_timeout", 0.0) or 0.0)
        spec = build_iter_spec(
            args,
            pairs,
            wver=wver,
            workers=workers,
            game_timeout_sec=game_timeout,
            hub_bun=str(getattr(self, "bun", "bun") or "bun"),
            node_label=RUN_NODE_LABEL,
        )
        iters_total = int(getattr(args, "iters", 0) or 0)
        if iters_total <= 0:
            if n < 0:
                raise SystemExit(
                    "[run_rl] --run-iters<0（跑到课程末尾）需要课程声明 iters——"
                    "没有终点就不叫整段，节点会一直跑下去"
                )
            iters_total = it + n
        plan = build_plan(
            args,
            it=it,
            iters_total=iters_total,
            rotate_seed=int(self._rotate_seed),
            # n-1：计划里的 argv 模板是给**下一轮**用的，段尾 = it + (n-1)。
            max_iters=0 if n < 0 else n - 1,
            workers=workers,
            game_timeout_sec=game_timeout,
            budget_sec=float(getattr(args, "run_budget_sec", 0.0) or 0.0),
            volume=self._volume_plan_block(),
            log=log,
        )
        end_it = int(plan["end_it"])
        log(
            f"[run_rl] rollout_src=run it{it}: 半离线段 it{it} → it{end_it}"
            f"（节点自主跑 {end_it - it} 轮；hub 失联不影响，产物在节点工作目录）"
        )
        t0 = time.time()
        try:
            result = self._remote_ppo(
                it,
                spec,
                plan_bytes=dump_plan(plan),
                wait_timeout_sec=_run_wait_sec(args),
            )
        except remote_retryable_exceptions() as e:
            # 与 `_remote_iter` 同规：节点已回报的确定性失败（bun 装不上 / TS 拿不到）
            # 与鉴权/闭锁类 HTTP 都**不重试**——重发同一段只是再白烧一个巨大等待预算。
            if isinstance(e, JobFailedError):
                self._abort_node_failure(it, e, where="半离线段 rollout+PPO")
                raise
            fatal = fatal_remote_http(e)
            if fatal:
                write_gate_verdict(
                    self._jsonl_path,
                    it,
                    "ABORT",
                    f"半离线段不可重试失败 HTTP {fatal}——检查 --remote-token 与节点隧道："
                    f"{str(e)[:200]}",
                    decider="loop",
                )
                log(f"[run_rl] GATE ABORT it{it}: 半离线段远端 HTTP {fatal}——不再重试")
                self._leg_abort = True
            raise
        rows = [r for r in (result.get("iters") or []) if isinstance(r, dict)]
        last = rows[-1] if rows else {}
        rep = dict(last.get("report") or result.get("report") or {})
        rep.pop("perGameSecs", None)  # 逐局秒数只用于诊断，不进 iteration 账本
        self._report = rep
        # 结算口径用**段尾那一轮**（本轮的 PPO 已在节点跑完）：采集墙钟同理。
        self._node_rollout_sec = float(rep.get("elapsedSec") or 0.0)
        write_event(
            self._jsonl_path,
            {
                "event": "run_segment",
                "iter_start": int(it),
                "iter_end": end_it,
                "iters": rows,
                "run_state": result.get("run_state"),
                "plan_sha256": str(result.get("plan_sha256", "") or ""),
                "artifacts": result.get("artifacts") or {},
                "wall_sec": round(time.time() - t0, 1),
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        got_end = int(result.get("it_end") or end_it)
        if got_end != end_it:
            # 节点自报的段尾与计划不符：结果仍然可信（协议已校验严格递增 + it_end == 末轮），
            # 但“我们以为跑到哪”必须按**实际**改，否则下一轮会重跑已训过的轮。
            log(
                f"[run_rl] WARN 半离线段实际跑到 it{got_end}（计划 it{end_it}）——"
                f"按实际推进（run_state={result.get('run_state')}）"
            )
        log(
            f"[run_rl] 半离线段收回：it{it} → it{got_end}（{len(rows)} 轮明细，"
            f"往返 {round(time.time() - t0, 1)}s，state={result.get('run_state')}）"
        )
        return got_end

