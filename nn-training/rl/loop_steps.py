"""loop_steps —— TrainingSteps mixin：单轮结算与梯度步（2026-09-02 从 rl/loop_core.py 拆出）。

run_training 迭代体的「采集之后」各阶段：报告结算（stream 拆解 + 日志）、串行
PPO 更新、权重导出与归档、eval 后台 join（延迟化软等待）、iteration 事件落账。

由 TrainingLoop(TrainingSteps, TrainingGuards) 混入；依赖的实例属性在
TrainingLoop.__init__/迭代方法中赋值，此处仅声明类型。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

from rl.archive import backup_weights
from rl.eval_m1 import read_eval_summary
from rl.events import write_iteration
from rl.log import log
from rl.modes import _MODE_BACKUP_PREFIX


class TrainingSteps:
    """单轮结算与梯度步 mixin。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    ppo_backend: Any
    update_kwargs: Any
    _model: Any
    _opt: Any
    _device: Any
    _ref_model: Any
    _ppo_mod: Any
    _ppo_goal: Any
    _ppo_intent: Any
    _save_weights_json: Any
    _start_it: int
    _traj_dir: Any
    _jsonl_path: Any
    _report: dict
    _stream_meta: dict | None
    _eval_thread: threading.Thread | None
    _eval_gate: threading.Event | None
    _rollout_sec: float
    _ppo_sec: float
    _total_steps: int
    _chunks_n: int
    _agg: Any
    _kl_cum: Any
    _halted_flag: bool
    _dropped_games: Any
    _load_sec: Any
    _tail_drain_sec: Any
    _waves_n: Any
    _eval_join_sec: float

    def _course_iter(self, it: int) -> None:
        """M1c：每 iter 注入课程配置的加载期上下文（holder）与超参 schedule。

        - holder（reward_context）：reward_fn + gamma/lam + it + 血缘——loaders
          （ppo.engine.load_shard）读取，奖励唯一定义源=课程配置公式；
        - ppo_schedule（按绝对 iter 查表）：lr 改 opt.param_groups（保 Adam
          动量）、epochs/mb 改 args（串行/流式每轮读取）、kl_coef 存 args._kl_coef
          供 update 期注入。
        """
        args = self.args
        course = getattr(args, "course_obj", None)
        if course is None:
            from rl.reward_context import reset as _ctx_reset

            _ctx_reset()
            args._kl_coef = 0.0
            return
        if getattr(self, "_course_reward_fn", None) is None:
            from rl.reward_library import build_reward_fn

            self._course_reward_fn = build_reward_fn(course.reward_spec())
            log(f"[course] reward_fn compiled: formula_len={len(course.reward.formula)}")
        spec = course.reward_spec()
        from rl.reward_context import update as _ctx_update

        _ctx_update(
            reward_fn=self._course_reward_fn,
            gamma=float(getattr(args, "gamma", 0.995)),
            lam=float(getattr(args, "lam", 0.95)),
            it=it,
            identity={"course": course.name, "formula_hash": spec.identity()},
        )
        sch: dict = {}
        if course.ppo_schedule:
            from rl.schedule import resolve_ppo_schedule

            sch = resolve_ppo_schedule(course.ppo_schedule_dicts(), it)
        if "lr" in sch and getattr(self, "_opt", None) is not None:
            self._opt.param_groups[0]["lr"] = float(sch["lr"])
        if "mb" in sch:
            args.mb = int(sch["mb"])
        if "epochs" in sch:
            args.epochs = int(sch["epochs"])
        kl_coef = float(sch.get("kl_coef", 0.0) or 0.0)
        args._kl_coef = kl_coef
        args._kl_cap = sch.get("kl_cap")  # None = 不覆盖，由 policy.streamKlCap 决定
        if sch:
            log(
                f"[course] ppo_schedule@it{it}: lr={sch.get('lr')} epochs={sch.get('epochs')} "
                f"mb={sch.get('mb')} kl_coef={kl_coef} kl_cap={sch.get('kl_cap', 'default')}"
            )

    def _write_iter_stats(self, it: int) -> None:
        """M1c：每 iter 落 metrics_stats.jsonl（21 维统计 + 血缘；非致命）。"""
        course = getattr(self.args, "course_obj", None)
        if course is None:
            return
        try:
            from rl.metrics_stats import metrics_stats

            identity = {
                "course": course.name,
                "formula_hash": course.reward_spec().identity(),
            }
            rec = metrics_stats(str(self._traj_dir), it=it, identity=identity)
            log(
                f"[run_rl] metrics_stats it{it}: shards={rec['shards']} "
                f"steps={rec['decision_steps']} elapsed_ms={rec['elapsed_ms']}"
            )
        except Exception as e:  # 统计失败不打断训练（warn-only，评审 P1-4）
            log(f"[run_rl] WARN metrics_stats failed (non-fatal): {e}")

    def _log_report(self, it: int, t_rollout: float) -> None:
        """报告结算：stream 报告拆解（eval 线程句柄 / 阶段耗时 / 遥测）与日志行。"""
        report = self._report
        stream_meta = self._stream_meta
        kl_cum = None
        halted_flag = False
        dropped_games = None
        load_sec = None
        tail_drain_sec = None
        waves_n = None
        if stream_meta is not None:
            # 流式评估线程句柄随报告回传（R4）：jsonl 写回前 join。
            self._eval_thread = report.pop("_eval_thread", None)
            _sm = report.pop("_stream")
            self._rollout_sec = _sm["rollout_sec"]
            self._ppo_sec = _sm["ppo_sec"]
            self._total_steps = _sm["steps"]
            self._chunks_n = _sm["chunks"]
            self._agg = _sm["agg"]
            tail_drain_sec = _sm.get("tail_drain_sec")
            kl_cum = _sm.get("kl_cum")
            halted_flag = bool(_sm.get("halted", False))
            dropped_games = _sm.get("dropped_games")
            load_sec = _sm.get("load_sec")
            waves_n = _sm.get("waves")
        else:
            self._rollout_sec = round(time.time() - t_rollout, 1)
        self._kl_cum = kl_cum
        self._halted_flag = halted_flag
        self._dropped_games = dropped_games
        self._load_sec = load_sec
        self._tail_drain_sec = tail_drain_sec
        self._waves_n = waves_n
        log(
            f"[run_rl] rollout it{it}: games={report['games']} winRate={report['winRate']} "
            f"outcomes={json.dumps(report['outcomes'])} "
            f"samples={report['totalSamples']} ticks={report['totalTicks']}"
        )
        if "scoreStats" in report:
            ss = report["scoreStats"]
            log(
                f"[run_rl] score it{it}: mean={ss['mean']:.4f} std={ss['std']:.4f} "
                f"min={ss['min']:.4f} max={ss['max']:.4f}"
            )
        if "dimMeans" in report:
            log(f"[run_rl] dims it{it}: {json.dumps(report['dimMeans'])}")

    def _serial_ppo(self, it: int) -> None:
        """串行路径（stream_meta 为空）的 PPO 更新：load → chunk → update。

        远程模式（--ppo remote，D11）：改为「打包 → 发布 job → 轮询等待 → 三重校验
        落位」——PPO 本体在云端 worker 执行，hub 只做调度 + 文件搬运（免 torch，D2）。
        语义与 _serial_ppo 完全同构（阻塞等待一轮 PPO 结果后才进入导出/下一轮）。
        """
        if self._stream_meta is not None:
            return
        if getattr(self.args, "ppo", "local") == "remote":
            self._remote_ppo(it)
            return
        args = self.args
        traj_dir = self._traj_dir
        t_ppo = time.time()
        # P1-7：--adv-norm none 时串行路径跳过 global 归一（对照实验）
        episodes = self.ppo_backend.load_episodes(
            str(traj_dir),
            float(getattr(args, "gamma", 0.995)),
            float(getattr(args, "lam", 0.95)),
            normalize_adv=getattr(args, "adv_norm", "auto") != "none",
        )
        total_steps = sum(e["obs"].shape[0] for e in episodes)
        chunks = self.ppo_backend.chunk_episodes(episodes, args.mb)
        # ppo_backend epoch 级断点续跑：崩溃重启后从最近 checkpoint 继续未完成批次
        if args.mode in ("intent", "goal"):
            agg = self.ppo_backend.update(
                self._model,
                self._opt,
                chunks,
                args.epochs,
                self._device,
                ckpt_path=str(traj_dir / "ppo_ckpt"),
                **self.update_kwargs(args, it, self._start_it, self._ref_model),
            )
        else:
            agg = self._ppo_mod.ppo_update(
                self._model,
                self._opt,
                chunks,
                args.epochs,
                self._device,
                ckpt_path=str(traj_dir / "ppo_ckpt"),
                kl_coef=float(getattr(args, "_kl_coef", 0.0) or 0.0),
            )
        self._ppo_sec = round(time.time() - t_ppo, 1)
        self._chunks_n = len(chunks)
        self._total_steps = total_steps
        self._agg = agg
        self._kl_cum = agg["kl"] if agg else None  # 串行：单次大更新，均值即累计口径

    def _remote_ppo(self, it: int) -> None:
        """远程 PPO（--ppo remote，D11/D12）：打包 → 发布 job → 轮询等待 → 三重校验落位。

        - 打包：本轮 traj it{it} 下 wver 匹配的 shard 集 + init 权重 + 上轮 opt tar；
        - 发布：磁盘 IPC（job 目录 + jsonl job_pending 事件）→ 旁路 hub-server；
        - 等待：阻塞轮询 server 结果（语义与 _serial_ppo 同构）；
        - 校验：init_weights_fp == 当前 args.out 指纹 + data_fp == 本地重算 + commit 一致；
        - 落位：weights_json → args.out（原子 replace）+ opt tar → it{it}/ppo_ckpt_remote。
        hub 全程免 torch（D2）：只做文件搬运 + sha256。
        """
        args = self.args
        it_dir = self._traj_dir
        t_ppo = time.time()
        from remote.hub_client import (
            git_head,
            iter_shard_dirs,
            mark_job_completed,
            publish_job,
            verify_and_land,
            wait_job,
        )

        hub_url = str(getattr(args, "remote_hub_url", "") or "")
        token = str(getattr(args, "remote_token", "") or "")
        if not hub_url or not token:
            raise SystemExit(
                "[run_rl] --ppo remote 需要 --remote-hub-url 与 --remote-token "
                "（hub-server 端点 + Bearer token，D9）"
            )
        job_root = str(getattr(args, "remote_job_root", "") or "") or str(
            Path(args.traj) / "remote-jobs"
        )
        # 本轮应训 shard 集（与 _serial_ppo load_episodes 装载口径一致）
        shard_dirs = iter_shard_dirs(args.traj, it)
        if not shard_dirs:
            raise SystemExit(f"[run_rl] remote it{it}: 无完整 shard（traj {it_dir} 空）——无法发布 job")
        # 课程快照（D13/D14）：课程文件全文 + course_fp = sha256(文件字节)
        course = getattr(args, "course_obj", None)
        if course is None:
            raise SystemExit(
                "[run_rl] --ppo remote 需要课程（--course <name>，D13 课程指针）——"
                "reward 公式/超参/关卡由课程单一事实来源"
            )
        course_path = Path(getattr(args, "course_path", "") or "")
        if not course_path.exists():
            course_path = Path(getattr(args, "course", "") or "")
        if not course_path.exists():
            from rl.config import resolve_course

            course_path = resolve_course(course.name)
        # 课程全文快照 + course_fp = sha256(**原始文件字节**)——与
        # rl/cmd.course_fp_for_args / rl/loop_core._course_file_fp 同算法（D14），
        # 否则 CRLF 换行下 read_text 的通用换行翻译会使指纹不一致、血缘断裂。
        course_bytes = course_path.read_bytes()
        course_text = course_bytes.decode("utf-8")
        course_fp = hashlib.sha256(course_bytes).hexdigest()
        # ppo_schedule 解析后值（执行用）——_course_iter 已按 it 折算进 args
        from rl.reward_library import METRICS_VERSION

        commit = git_head()
        # H4（review-hy P1）：commit-pin 的前提是云端 checkout 到 HEAD 拿到与 hub
        # **一致**的代码。工作区有未提交/未跟踪文件时，云端 git pull 后根本没有
        # remote/ 模块或拿到旧行为——commit 校验照样通过，训练却跑了错代码。
        # fail fast：先 commit + push 再跑云。
        import subprocess as _sp

        _dirty = _sp.run(
            ["git", "status", "--porcelain"],
            cwd=str(Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=30,
        )
        _dirty_files = [ln for ln in _dirty.stdout.splitlines() if ln.strip()]
        if _dirty.returncode == 0 and _dirty_files:
            raise SystemExit(
                "[run_rl] --ppo remote 要求干净工作区（云端按 commit-pin checkout 代码）："
                f"{len(_dirty_files)} 个未提交/未跟踪文件（如 {_dirty_files[0][:60]}…）。"
                "先 commit + push 再启动远程训练（review-hy H4）"
            )
        from rl.queue import RUN_ID

        run_id = RUN_ID
        ckpt_remote_path = Path(args.traj) / f"it{it - 1}" / "ppo_ckpt_remote"
        ckpt_remote: Path | None = ckpt_remote_path if ckpt_remote_path.exists() else None
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
            course=course_text,
            course_fp=course_fp,
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
            adv_norm=getattr(args, "adv_norm", "auto"),
            shuffle=True,
            schedule_raw=course.ppo_schedule_dicts(),
            log=log,
        )
        jid = manifest["job_id"]
        # 阻塞等待云 worker 完成（与 _serial_ppo 同构；超时由 hub-server 租约吸收）
        timeout_sec = 30 * 60.0
        result = wait_job(hub_url, token, jid, timeout_sec=timeout_sec, log=log)
        # 三重校验 + 落位（D12）：任一不等响亮拒绝，不落盘
        verify_and_land(
            result,
            manifest,
            init_weights_path=args.out,
            traj_dir=args.traj,
            it=it,
            out_weights=args.out,
            log=log,
        )
        mark_job_completed(self._jsonl_path, jid)
        # H7（review-hy）：--remote-precollect 1 → 在 PPO 等待窗口后 spawn 下一轮首波
        # 预采（θ_N 快照，复用 spawn_collect_next 双缓冲机制）。默认 0（Q10 测后开）
        # 时不可达。stale 分数上限 30% 的筛选（S5/F4）属 §6-D3 后续项，未在此实现。
        if int(getattr(args, "remote_precollect", 0) or 0) and (args.iters <= 0 or it < args.iters):
            from rl.collect_only import spawn_collect_next

            # H7（review-hy）：预采子进程句柄必须存入 self._collect_child，
            # 否则主循环的 join_precollect_child（下一轮开头）接收 None 跳过
            # 等待，预采首波可能尚未落盘即被 _prepare_iter_dir 清场。
            self._collect_child = spawn_collect_next(args, it)
            if self._collect_child is not None:
                log(f"[run_rl] remote precollect: next-round first-wave spawned (pid={self._collect_child.pid})")
        # 结算字段（下游 breaker / stop-loss / events 账本原样消费，D4）
        agg = result.get("agg", {})
        self._agg = {
            "policy": float(agg.get("policy", 0.0)),
            "value": float(agg.get("value", 0.0)),
            "entropy": float(agg.get("entropy", 0.0)),
            "kl": float(agg.get("kl", 0.0)),
            "mean_ret": float(agg.get("mean_ret", 0.0)),
        }
        self._chunks_n = int(agg.get("chunks", 0))
        self._total_steps = int(agg.get("steps", 0))
        self._kl_cum = self._agg["kl"]
        self._ppo_sec = round(time.time() - t_ppo, 1)
        log(
            f"[run_rl] remote ppo it{it}: job {jid} accepted — "
            f"steps={self._total_steps} chunks={self._chunks_n} "
            f"kl={self._agg['kl']:.5f} entropy={self._agg['entropy']:.4f} "
            f"({self._ppo_sec}s round-trip) -> {args.out}"
        )

    def _export_weights(self, it: int) -> None:
        """按模式导出权重（goal/intent/per-tick）并归档（只归档不自动清理）。

        远程模式（--ppo remote）：weights_json 已由云 worker 产出、`_remote_ppo`
        三重校验落位到 args.out——这里只归档 + 日志（不再调 torch 导出，D2/D12）。
        """
        args = self.args
        if getattr(args, "ppo", "local") == "remote":
            bak = backup_weights(args.out, it, prefix=_MODE_BACKUP_PREFIX[args.mode])
            log(
                f"[run_rl] remote ppo it{it}: weights already landed by cloud worker "
                f"(D12) -> {args.out}"
            )
            if bak:
                log(f"[run_rl] weights archived -> {bak}")
            return
        if args.mode == "goal":
            from models.goal_net import GoalNet

            self._ppo_goal.export_goal_weights(cast(GoalNet, self._model), args.out)
        elif args.mode == "intent":
            from models.intent_net import IntentNet

            self._ppo_intent.export_intent_weights(cast(IntentNet, self._model), args.out)
        else:
            self._save_weights_json(self._model, args.out)
        bak = backup_weights(args.out, it, prefix=_MODE_BACKUP_PREFIX[args.mode])
        log(
            f"[run_rl] ppo it{it}: steps={self._total_steps} chunks={self._chunks_n} "
            + (
                f"policy={self._agg['policy']:.4f} value={self._agg['value']:.4f} "
                f"entropy={self._agg['entropy']:.4f} kl={self._agg['kl']:.5f} -> {args.out}"
                if self._agg is not None
                else "metrics n/a — ppo_backend checkpoint completed by previous process"
            )
        )
        if bak:
            log(f"[run_rl] weights archived -> {bak}")

    def _join_eval(self, it: int) -> dict | None:
        """v3.12 eval 延迟化：eval 不阻塞训练主链（后台线程 + 软等待 + wver 键控）。

        门判定读 eval_log 的 eval_summary（iter 字段保留原轮号 + wver），晚入账只
        让判定窗口顺延，判据不变。溢出预算未收官的在途局由下轮异 sha 清场 + 阈值
        熔断兜底（与 v3.10 前语义一致）。
        """
        args = self.args
        if self._eval_gate is not None:
            self._eval_gate.set()
        eval_join_sec = 0.0
        eval_thread = self._eval_thread
        if eval_thread is not None and eval_thread.is_alive():
            budget = float(args.eval_window_sec) + 60.0
            if args.mode in ("intent", "goal"):
                # intent/goal：eval_summary 须在 jsonl 写回前结算（止损判门依赖）。
                log(
                    f"waiting up to {budget:.0f}s for clean-eval round before next "
                    f"weight distribution"
                )
                _t_join = time.time()
                eval_thread.join(timeout=budget)
                eval_join_sec = round(time.time() - _t_join, 1)
            else:
                soft = min(budget, 180.0)  # per-tick 软等待上限：吃尾巴 + 缓存缓冲
                log(
                    f"[run_rl] eval deferred: soft-wait {soft:.0f}s for tail "
                    f"(remaining eval finishes in background, wver-keyed)"
                )
                _t_join = time.time()
                eval_thread.join(timeout=soft)
                eval_join_sec = round(time.time() - _t_join, 1)
        self._eval_join_sec = eval_join_sec
        # intent/goal：回读该迭代 eval_summary（评估线程写入；止损判门的数据源）。
        eval_rec = (
            read_eval_summary(self._jsonl_path, it) if args.mode in ("intent", "goal") else None
        )
        # pace checkpoint（intent/goal 护栏）：iter5 首现通关。
        if args.mode in ("intent", "goal") and it == 5 and self._report["winRate"] <= 0:
            log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")
        return eval_rec

    def _record_iteration(self, it: int) -> None:
        """iteration 事件落账（字段契约在 rl/events.py::write_iteration）。"""
        write_iteration(
            self._jsonl_path,
            self.args,
            it,
            self._report,
            {
                "rollout_sec": self._rollout_sec,
                "ppo_sec": self._ppo_sec,
                "total_steps": self._total_steps,
                "chunks_n": self._chunks_n,
                "agg": self._agg,
                "kl_cum": self._kl_cum,
                "halted": self._halted_flag,
                "dropped_games": self._dropped_games,
                "waves": self._waves_n,
                "load_sec": self._load_sec,
                "tail_drain_sec": self._tail_drain_sec,
                "eval_join_sec": self._eval_join_sec,
            },
        )
