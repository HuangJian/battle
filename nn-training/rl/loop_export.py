"""loop_export —— **产物出包** mixin（2026-09-25 从 rl/loop_steps.py 拆出，S4 第二十一刀）。

这一簇 4 个成员共享一个判据：**「这一轮要给出去的东西」**——把产物打成能被别人拿走 / 上传的东西。

- `_ensure_ts_code`：把 rollout 用的 TS 运行时打成 `ts_code.zip`（内容寻址，同源码只传一次）。
- `_volume_plan_block`：离线计划里必须带上的**动态采集块**——节点没有 hub 的 jsonl，est 只能被钉在
  计划里（`--stages` 不可解析 / est ≤ 0 一律**响亮退出**，静默降级回老口径正是要防的事）。
- `_export_offline_bundle`：`--export-bundle`——it..it+n-1 打成**可上传云机**的全离线任务包。
- `_export_weights`：权重归档（只归档不自动清理）。

**本文件里唯一一条方法间调用链**就在这一簇：`_export_offline_bundle` → `_volume_plan_block`
（实测连通分量）。`loop_steps` 其余 8 个成员彼此零互调（全是叶子），搬走后那边**零方法间调用**。

## 宿主：`TrainingSteps` 的**末位**基类（追加不插队）

调用者两处，都在 MRO 上更靠前：

  · `RoundSteps`（`step_export_offline_bundle` / `step_export_weights`）——组合根的第一个基类；
  · `TrainingRemote`（`_remote_ppo_publish` → `_ensure_ts_code`；`_remote_run_segment` →
    `_volume_plan_block`）——`TrainingSteps` 的第一个基类。

⇒ `class TrainingSteps(TrainingRemote, TrainingEval, TrainingExport)`，`TrainingLoop.__bases__`
一行不改。依据（实测）：4 个成员名在既有混入里**零同名定义** ⇒ 末位追加不会被 MRO 遮罩。

## 依赖方向

本模块**不 import** `rl.loop_steps` / `rl.loop_remote`（调用者依赖被调用者，反向即环）。它调用的
兄弟方法 `_remote_ppo` 只在**组合实例**上动态解析（混入常态，理由同 `rl/loop_volume.py` 头注）。

## DI seam / 模块全局

`dist_common`（`weights_fingerprint`）· `log` · `backup_weights` / `_MODE_BACKUP_PREFIX` 是**本模块**的
模块全局。搬家前它们是 `rl.loop_steps` 的；实测**没有任何测试 patch 过它们**（`rl.loop_steps` 命名
空间被 patch 的只有 `log`，而那处测的是留守的 `_write_iter_stats`）⇒ 无 patch 点迁移；旧家删掉这些
import 后，陈旧的 `rl.loop_steps.<名字>` 会**响亮** `AttributeError`，不是静默空操作。
三处**延迟** import（`rl.iter_job` / `rl.plan` / `remote.hub_client`）是原有注入面，保留在方法体内。

## 跨模块槽位（写-读手）

`_ts_code_sha256` / `_ts_code_zip_path`：**本模块写**（`_ensure_ts_code` 懒建缓存），
`TrainingRemote._remote_ppo_publish` **读**（决定 payload 里要不要带那份 zip）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dist_common
from rl.archive import backup_weights
from rl.log import log
from rl.modes import _MODE_BACKUP_PREFIX


class TrainingExport:
    """产物出包（4 方法；见本模块头注）。"""

    # 依赖的 `TrainingLoop` 实例属性（声明类型供 mypy/阅读；实际赋值在 `TrainingLoop.__init__`
    # 与各兄弟混入）。与其它混入里的同类声明**有意并存**：混入的状态契约必须在**每个**文件里对
    # mypy 可见，运行期的唯一真相仍是同一实例上的那一份。
    args: Any
    _rotate_seed: Any
    #: 本簇自己的懒建缓存：写在这里、被 `TrainingRemote` 读（见头注「跨模块槽位」）。
    _ts_code_sha256: Any
    _ts_code_zip_path: Any

    #: 兄弟混入的方法（混入常态：在组合实例上解析）——声明类型，理由同 `rl/loop_volume.py`。
    _remote_ppo: Any

    def _ensure_ts_code(self, job_root: str, *, log: Any) -> None:
        """M3：打包 rollout 用的 TS 运行时 zip（一次，缓存在 self 上）。

        为什么在训练侧打而不是节点侧 `bun install`：`tools/sim/export-rl-rollout.ts`
        的链路**零第三方运行时依赖**（非相对 import 只有 node 内建 `fs`/`path`），所以
        打包即可，云机不必装依赖（plan §5.3）。内容固定时间戳 + 内容寻址 sha，
        同源码反复跑只传一次。
        """
        if str(getattr(self, "_ts_code_sha256", "") or ""):
            return
        from remote.hub_client import pack_ts_code_zip

        repo_root = Path(__file__).resolve().parents[2]  # nn-training/rl/x.py -> 仓根
        zp = Path(job_root) / "ts_code.zip"
        self._ts_code_sha256 = pack_ts_code_zip(repo_root, zp, log=log)
        self._ts_code_zip_path = zp

    def _volume_plan_block(self) -> dict | None:
        """计划要带上的**动态采集块**（`target_transitions > 0` 时；见 `rl/volume_waves`）。

        为什么必须进计划：全离线/半离线腿（kind=run）的逐轮语料由 `rl/plan.pairs_for` 重放
        而成，而 `build_pairs` 根本不认 `target_transitions` —— 不带这块，云机采多少局就由
        课程里那个 `seed_rotate` 数字决定（配小了就是**静默少采**：训练的样本量低于目标，
        而云机没有本地集群那种实时补救机制——一轮一个 job，PPO 在 job 里跑完）。

        两个口径细节：
          * **est 用当前估计**（trailing 均值，回退课程声明值）——与 `_volume_est_samples`
            同函数同 window，所以计划里的 G0 就是**导出那一刻**本地循环会用的那个数；节点
            没有 hub 的 jsonl，est 只能被钉在计划里（这也是「同一计划跨机器逐字节一致」的
            前提：现算会让两侧解出不同的语料指纹）。
          * **mode 门与 `_per_stage_quota` 同源**：`target_transitions` 只对 per-tick 有意义
            ⇒ 非 per-tick 返 None（= 老口径，逐字节不变）。

        `--stages` 缺失/不可解析、est ≤ 0 一律**响亮退出**：静默降级回老口径正是要防的事。
        """
        args = self.args
        if str(getattr(args, "mode", "")) != "per-tick":
            return None
        if int(getattr(args, "target_transitions", 0) or 0) <= 0:
            return None
        from rl.resume import trailing_samples_per_game
        from rl.volume_waves import volume_block

        declared = int(getattr(args, "est_samples_per_game", 0) or 0)
        jsonl = getattr(self, "_jsonl_path", None)
        est = (
            int(trailing_samples_per_game(jsonl, window=5, fallback=declared) or declared)
            if jsonl
            else declared
        )
        try:
            return volume_block(args, est_samples_per_game=est)
        except ValueError as e:
            raise SystemExit(
                f"[run_rl] 动态采集无法写进离线计划（{e}）——修好课程/参数再导出："
                "盘里没有的采集量规则，云机无法自行补上"
            ) from e

    def _export_offline_bundle(self, it: int, pairs: list[tuple[int, int]], n: int) -> None:
        """`--export-bundle`：把 it..it+n-1 打成**可上传云机**的全离线任务包（本轮不训练）。

        用户需求（2026-09-17）：「hub 支持打包导出训练任务（课程、初始权重、代码），以
        kaggle/colab 官方方式上传云机后，云机全程自主完成训练」。与半离线的差别：包一旦
        写出，hub 就可以关机——任务信息（课程/超参/血缘/计划/代码）全在包里。

        轮次对齐（整条第 N 个容易错的地方）：本轮的 `it` **就是**包里要跑的第一轮（loop 的
        `it` = last_completed+1），而包里 `plan.start_it` 必须 = `it - 1`（计划区间是
        `start_it+1 .. end_it`，起点权重 = `args.out` = W(it-1) 的产物）。所以
        `max_iters = n`（不是 n-1：这里没有「job 自己那一轮」要扣）。

        没跑过任何一轮（`args.out` 无权重）就拒导——包里没有起点的任务等于没任务。
        """
        args = self.args
        # ★ 2026-09-21（§3）：原先这里拒绝「非 remote」——`--ppo` 删除后该判据恒真、会把
        #   整条导出路径误拒。单一 PPO 路径下「PPO 在节点上跑」是唯一形态，导出天然成立。
        iters_total = int(getattr(args, "iters", 0) or 0)
        if iters_total <= 0:
            raise SystemExit(
                "[run_rl] --export-bundle 需要课程声明 iters（包里的计划必须有终点——「跑到哪停」"
                "是任务定义的一部分，不能靠云机猜）"
            )
        if it <= 1 and not dist_common.weights_fingerprint(args.out):
            raise SystemExit(
                f"[run_rl] --export-bundle: 没有起点权重（{args.out}）——先跑至少一轮，"
                "或把已有权重放到 --out 指向的位置"
            )
        from rl.iter_job import build_iter_spec
        from rl.plan import RUN_NODE_LABEL, build_plan, dump_plan, planned_iters

        wver = dist_common.weights_fingerprint(args.out)
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
        plan = build_plan(
            args,
            it=it - 1,
            iters_total=iters_total,
            rotate_seed=int(self._rotate_seed),
            max_iters=0 if n < 0 else n,
            workers=workers,
            game_timeout_sec=game_timeout,
            budget_sec=float(getattr(args, "run_budget_sec", 0.0) or 0.0),
            volume=self._volume_plan_block(),
            log=log,
        )
        log(
            f"[run_rl] 全离线导出：it{it} → it{plan['end_it']}"
            f"（{len(planned_iters(plan))} 轮）——本轮不训练、不等待"
        )
        # export_path 非空 ⇒ `_remote_ppo` 只建 job 目录（打包源）+ 写包 + 抛 BundleExportedError。
        self._remote_ppo(it, spec, plan_bytes=dump_plan(plan), export_path=str(args.export_bundle))

    def _export_weights(self, it: int) -> None:
        """权重归档（只归档不自动清理）。

        ★ 2026-09-21（§3 单一 PPO 路径）：weights_json 恒由**认领到 job 的 worker** 产出、
        经 `_remote_ppo` 三重校验落位到 args.out（D2/D12）——本机不再有 torch 导出路径
        （goal/intent 导出分支随本机 PPO 一并退役），这里只归档 + 日志。
        """
        args = self.args
        # 课程声明 backup_prefix/backup_dir 时优先（D6 课程单一事实来源）；缺省
        # 退回按模式前缀 + 默认 nn-training/weights（旧行为）。
        bak_prefix = str(getattr(args, "backup_prefix", "") or "") or _MODE_BACKUP_PREFIX[args.mode]
        bak_dir = str(getattr(args, "backup_dir", "") or "") or None
        bak = backup_weights(args.out, it, prefix=bak_prefix, backup_dir=bak_dir)
        log(
            f"[run_rl] ppo it{it}: weights already landed by the claimed worker "
            f"(D12) -> {args.out}"
        )
        if bak:
            log(f"[run_rl] weights archived -> {bak}")
