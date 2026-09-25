"""loop_remote_job —— **一个远端 PPO job 的四步** mixin（2026-09-25 S4 第二十二刀拆出）。

判据同源：「**一份远端 PPO job 的完整生命周期**」——打包发布（`_remote_ppo_publish`）→
探活（`_remote_ppo_probe`）→ 取回（`_remote_ppo_fetch`）→ 三重校验落位（`_remote_ppo_land`），
外加**组合入口** `_remote_ppo`（发布 → 等结果 → 落位；四个既有调用点的行为不变）。

依赖方向：`class TrainingRemoteJob(TrainingRemotePush)`——发布相位用 `_push_submit_first`、
取回相位用 `_push_fetch`（直推腿）；`TrainingRemoteDrive` 继承本模块。

本模块的**延迟 import** 刻意逐方法自带（`_remote_ppo_publish` 里的 `remote.hub_client` /
`rl.config` / `rl.reward_library` / `rl.queue` / `remote.bundle`；`_remote_ppo_land` 里的
`remote.hub_client` / `rl.collect_only`）——它们各自服务一段，合成一份清单会重新长出拆相前的形状。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from common.protocol import coef_active
from rl.log import log
from rl.loop_remote_push import TrainingRemotePush
from rl.loop_round import RemotePpoJob
from rl.loop_transport import (
    BundleExportedError,
    SmokeVoidRoundError,
    _course_cf_tunnel,
    _gate_round_shards,
    _gpu_push_nodes,
    _hub_push_opt_in,
    _kickstart_ref_payload,
    _remote_forward_agg,
    _wire_from_result,
    kickstart_coef,
    kickstart_warn_kind,
    require_remote_transport,
    resolve_hub_push,
    resolve_transport,
)


class TrainingRemoteJob(TrainingRemotePush):
    """一个远端 PPO job 的四步 + 组合入口。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 「有意并存」的理由同 `rl/loop_remote_push.py`；`args` / `_code_zip_path` 由基类声明。
    _traj_dir: Any
    _jsonl_path: Any
    _ppo_sec: float
    #: 云端 worker **自报**的真训练秒（load+chunk+update，不含上传/排队/下载）。
    #: 与 `_ppo_sec`（往返墙钟）分开记——后者打包传输与排队，用于诊断/配额，
    #: 不应当作"训练量"（排队越久越"达标"是错的，且本地采样期间云端空转它看不到）。
    _ppo_cloud_sec: float
    _total_steps: int
    _chunks_n: int
    _agg: Any
    _kl_cum: Any
    # 仅由方法体内自赋值产生（从未显式声明）；跨方法读到时需要类型可见。
    _bundle_index: Any
    _code_sha256: Any
    _collect_child: Any
    _demo_raw: Any
    _wire: Any

    #: 由其它 mixin 提供、在本模块里被**调用**的助手（组合实例上动态解析）。
    #: 声明为 `Any` 的理由同 `rl/loop_steps.py` 里的 `_ledger_apply`：混入间互调的方法必须
    #: 在每个文件里类型可见，否则 mypy 报 attr-defined。
    _commit_journal: Any
    _ensure_ts_code: Any
    #: `_remote_ppo_publish` 里以 `hasattr`/`getattr` 动态访问（EvalBoard 让位属于编排层）——
    #: 声明只为让「借用声明闭集 = 派生集」这条不变式成立；注解不产生类属性，`hasattr` 语义不变。
    _evalboard_idle: Any
    _forensics: Any
    _per_stage_quota: Any

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
        # ★ 2026-09-25（plan/online-offline-role-routing §7）：kind=run 的**队列项**退役。
        # 生产端唯一的发布者（发一份带段长的队列项、随后等 8h 的那个方法）已删，
        # 取包链（`--export-bundle` ⇒ 任务包）是它的完整替代（原方法名见 plan §7.6）。
        #
        # 谁还带 plan_bytes 而不带 export_path，就是复活那条腿——而队列里**没有消费者**
        # （`remote/worker.py` 对 kind=run 响亮拒收）⇒ 本机会白等一整段（老代码是 8h），
        # 症状是「队列不降、没人报错」。所以在这里**当场**拒，并把该走哪条路写进消息里
        # （这是 kind=run 的唯一咽喉：`--export-bundle` 是唯一合法调用者，它带 export_path）。
        if plan_bytes is not None and export_path is None:
            raise SystemExit(
                "[run_rl] kind=run（离线队列项）这条腿已于 2026-09-25 退役——离线课不再经"
                " hub 队列执行：云机用 battle.offline.ipynb 取任务包接手"
                "（/offline/tasks 清单 → /offline/task-pack 取包 → 跑完回传，"
                "控制台「导入产物」推进本机账本）。本机此刻要发的是**任务包**："
                "控制台「切离线」会自动跑 --export-bundle，或手工 `--export-bundle <zip>`。"
                "（本拒绝点住 `_remote_ppo_publish`：任何新调用者只要不带 export_path 就撞上"
                "这里，不必等谁去读代码。）"
            )
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
        # 起始分布（P3.5）：开了 state_init 时，缺 initTick 的 shard 不进 payload——
        # 一并进 job 里就等于云端训的是另一个起始分布，而 data_fp 账面对得上（静默换实验）。
        from rl.resume import state_init_enabled

        local_shards = iter_shard_dirs(
            args.traj,
            it,
            log=(lambda _m: None) if (rollout_spec or export_path is not None) else log,
            course_fp=course_fp,
            corpus_fp=corpus_fp,
            state_init=state_init_enabled(args),
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
            #
            # kind=run 的**形状**保留：今天唯一带 `plan_bytes` 的调用者是 `--export-bundle`
            # （它同时带 `export_path` ⇒ `register=False`：只建 job 目录当打包源、不进待领池）。
            # 「发一份 kind=run 队列项、本机等 8h」那条腿 2026-09-25 退役（plan §7）——
            # 所以谁若**不带** export_path 传 plan_bytes，就是复活了退役腿。
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
        from rl.resume import state_init_enabled

        verify_and_land(
            result,
            sess.manifest,
            init_weights_path=args.out,
            traj_dir=args.traj,
            it=sess.it,
            out_weights=args.out,
            # data_fp 重算必须用与发布**同一条**过滤（P3.5）：否则本地算出的集合
            # 与云端收到的集合分叉，三重校验会响亮拒收一份本来正常的 job。
            state_init=state_init_enabled(args),
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
                # M3：记**实测**在哪采集（node = 本轮整轮上云；local = 本机），不是
                # args 字面量（auto 会被 _rollout_source 解析成 local/node——原样记
                # auto 等于没记）。离线课（`run`）不再产生 iteration 事件（本机不发活），
                # 所以这里只可能是这两档。
                "rollout_src": ("node" if sess.rollout_spec else "local"),
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
