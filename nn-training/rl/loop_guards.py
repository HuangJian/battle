"""loop_guards —— TrainingGuards mixin：训练护栏（2026-09-02 从 rl/loop_core.py 拆出）。

训练护栏的**组合根**。四簇护栏各自住一个模块（S4 第二十三刀，判据同源）：
  · `rl/loop_guards_trip.py::TrainingGuardsTrip`  —— **过程面**硬边界（F4 熔断 / 止损）；
  · `rl/loop_guards_leg.py::TrainingGuardsLeg`    —— **结果面**停腿（干烧回锚 / 配对杀臂）；
  · `rl/loop_guards_gate.py::TrainingGuardsGate`  —— **课程结束门**（M1 第四守卫，
    plan/course-exit-and-shutdown.md §4）求值 + 判决落地 + 预算硬断；
  · `rl/loop_guards_sweep.py::TrainingGuardsSweep` —— keepIters 目录轮转与目录回收。
判据本体全在纯函数模块（`rl/breaker.py` / `rl/stop_loss.py` / `rl/kickstart_burn.py` /
`rl/paired_kill.py` / `rl/gate_check.py`）；四簇只做「读训练状态 → 判 → 落日志/事件」。

由 TrainingLoop(TrainingSteps, TrainingGuards) 混入；依赖的实例属性（_agg、
_report、_kl_streak、_tripped 等）在 TrainingLoop.__init__/迭代方法中赋值，
此处仅声明类型。

S4 第二十三刀把 13 个成员按**判据同源**切成四簇（trip / leg / gate / sweep），本文件
**留作组合根**，余下四人恰好是 DAG 上的**共享 sink**（提供者留根、调用者出包）：
  · `_ledger_apply`     —— 账本视图增量（3 簇共用：trip / leg / gate）；
  · `_sync_cloud_halt`  —— 判决 → 云机达令（2 簇共用 + 外部 `loop_lifecycle.finish_course`）
                           连同 `_is_soft_verdict` / `_gate_halt_mode` 与三个判决词表常量。
★ 二者留根还有一条硬理由：它们是 `rl.loop_guards` **patch 锚点**（`set_cloud_halt` /
`dist_common` 被四个测试文件以 `monkeypatch.setattr(..., raising=True)` 打桩）——搬走会
让桩静静失效。四簇彼此零互调 ⇒ 基类元组顺序恒惰性（零重名、零 `super()`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import dist_common
from remote.hub_client import set_cloud_halt
from rl.log import log
from rl.loop_guards_gate import TrainingGuardsGate
from rl.loop_guards_leg import TrainingGuardsLeg
from rl.loop_guards_sweep import TrainingGuardsSweep
from rl.loop_guards_trip import TrainingGuardsTrip


class TrainingGuards(
    TrainingGuardsTrip, TrainingGuardsLeg, TrainingGuardsGate, TrainingGuardsSweep
):
    """训练护栏 mixin（组合根）：共享 sink —— 账本视图增量 + 判决 → 云机达令。

    四簇（trip / leg / gate / sweep）各自住一个模块；本类只留**被多簇共用**的提供者，
    基类元组顺序惰性（四簇互不调用、零重名、零 `super()`）。只有 `TrainingLoop` 会被
    实例化——同一对象、同一把锁。
    """

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _agg: Any
    _report: dict
    _jsonl_path: Any
    _kl_streak: int
    _ent_streak: int
    _ent_peak: float | None
    _tripped: Any
    _prev_entropy: Any
    _stop_loss_streak: int
    _traj_root: Any
    _traj_dir: Any
    _course_fp: Any
    #: 真实现在 `rl/loop_dispatch.py::TrainingDispatch`（S4 第二十刀，之前住 loop_core）；
    #: 占位（MRO 破检测）在 `rl/loop_eval.py::TrainingEval`（S4 第十七刀）。
    _eval_on_round: Any
    #: 云端停机达令当前置位态（门判决 → set_cloud_halt 联动；重启即复位）。
    _cloud_halted: bool
    #: R2a 账本视图（`loop_lifecycle._setup_common` 建立；缺失时各使用点退化，不阻断训练）。
    _ledger: Any

    def _ledger_apply(self, event: dict | None) -> None:
        """把刚写入账本的事件并入本进程的 `LedgerView`（R2a，2026-09-18）。

        视图只在开课/续跑时扫一遍盘（用户 2026-09-18 裁决），之后靠这条增量路径
        保持同步——于是任何时刻「视图 == 盘上账本」，且没有第二次全文件扫描。
        视图缺失（老测试直接构造 mixin、未走 `_setup`）时静默跳过：观测永不阻断训练。
        """
        view = getattr(self, "_ledger", None)
        if view is None or not event:
            return
        try:
            view.apply_event(event)
        except Exception as e:  # 观测量失败不得影响训练主链（同 _gate 的兜底风格）
            log(f"[run_rl] ledger view advance skipped（{type(e).__name__}: {e}）")

    #: 需要下发云端停机达令的门判决（不包含预算 STOP——预算到顶是真正结束，走硬断）。
    CLOUD_HALT_VERDICTS = frozenset({"REMEDIATE", "PAUSE", "ABORT"})

    #: ★ 只提示、不该杀云机的门种类（2026-09-13 P0 止血）。
    #: `plateau`(G4) 的 REMEDIATE 语义是"**边际收益枯竭**"，不是"课程失效"——它每 5 轮
    #: 必然复现一次（平台期本来就长），下发 cloud halt 就会反复杀掉云端 PPO worker。
    #: 实测事故：`c6-pickup3` it35–it60 共 6 次、`c6-bonus` it25–it70 共 **10 次**
    #: cloud halt，全部由 G4 触发 ⇒ 两条腿后半程（c6-bonus 是 60% 的轮次）都在
    #: "PPO worker 被反复杀"的环境下训练，且 G4 用**训练内** win_rate 判，
    #: 根本看不见配对口径下的退化（c4-dodge 同一个判错轴教训）。
    #: ⇒ plateau 只记录 verdict，不下达停机令；真需要停的场景由 G7/G13/PAUSE/ABORT 覆盖。
    NO_CLOUD_HALT_KINDS = frozenset({"plateau"})

    #: 门禁触发的动作模式（控制台顶部「触发门禁：停机/提示」开关，2026-09-13）。
    #: halt = 下发 cloud halt（默认，历史行为）；notify = 只提示不停机。
    GATE_HALT_MODES = ("halt", "notify")

    def _is_soft_verdict(self, verdict: str, readings: Any) -> bool:
        """REMEDIATE 是否**只**由提示类门（plateau）触发 ⇒ 不该下发 cloud halt。

        保守原则：readings 缺失/没有任何门 released 时返回 False（维持既有停机行为），
        避免因读不到明细而漏停真正需要干预的情况。
        """
        if verdict != "REMEDIATE" or not readings:
            return False
        released = [r for r in readings if getattr(r, "released", False)]
        if not released:
            return False
        return all(getattr(r, "kind", "") in self.NO_CLOUD_HALT_KINDS for r in released)

    def _gate_halt_mode(self) -> str:
        """门禁动作模式：**标志文件 > 启动参数 > 默认 halt**。

        标志文件 = `<traj>/gate-halt-mode.txt`（内容 halt|notify），由控制台顶部开关写。
        放在文件里是为了**运行时可热切**：训练中改主意不必重启（每轮门判定只读一次，
        一轮 ~100s，开销可忽略）。读不到/内容非法一律回退启动参数，再回退 halt（保守）。
        """
        try:
            root = self._traj_root
            if root is not None:
                p = Path(root) / "gate-halt-mode.txt"
                v = p.read_text(encoding="utf-8").strip().lower()
                if v in self.GATE_HALT_MODES:
                    return v
        except OSError:
            pass
        except Exception:  # 任何意外（属性缺失/权限）都退化到启动参数，绝不影响训练
            pass
        v = str(getattr(self.args, "gate_halt_mode", "") or "halt").strip().lower()
        return v if v in self.GATE_HALT_MODES else "halt"

    def _sync_cloud_halt(self, it: int, verdict: str, readings: Any = None) -> None:
        """§386 联动：门判决只作用于远端云机，TrainingLoop 永不停车。

        REMEDIATE/PAUSE/ABORT → 向 hub 下发停机达令（能自停的云机（Colab）
        释放，停不掉的（Kaggle）照常干活）；HOLD/ADVANCE → 停机条件消失下发
        resume。任何失败（tunnel 抖动、hub 没起）只记日志，绝不断训练。
        local/push 模式无 hub（remote_hub_url 空）→ 直接短路，零行为。

        2026-09-13 修正：`REMEDIATE` 若**只**由提示类门（G4 plateau）触发则**跳过**停机
        （见 `NO_CLOUD_HALT_KINDS`）——否则平台期每 5 轮杀一次云 worker。
        """
        args = self.args
        hub_url = str(getattr(args, "remote_hub_url", "") or "")
        token = str(getattr(args, "remote_token", "") or "")
        if not hub_url or not token:
            return
        want_halt = verdict in self.CLOUD_HALT_VERDICTS
        if want_halt and self._is_soft_verdict(verdict, readings):
            kinds = sorted(
                {
                    str(getattr(r, "kind", "?"))
                    for r in (readings or [])
                    if getattr(r, "released", False)
                }
            )
            log(
                f"[run_rl] gate it{it}: {verdict} 仅由提示类门 {kinds} 触发 "
                f"→ 不下发 cloud halt（避免杀掉云端 PPO worker）"
            )
            # 提示类判决不改变停机态：既不下达，也不主动 resume。
            return
        if want_halt and self._gate_halt_mode() == "notify":
            # 操作员把顶部开关拨到「提示」：只记录 verdict（上面已落账），不停云机。
            log(
                f"[run_rl] gate it{it}: {verdict} —— 门禁动作为 notify（控制台开关）"
                f"→ 只提示，不下发 cloud halt"
            )
            return
        args = self.args
        hub_url = str(getattr(args, "remote_hub_url", "") or "")
        token = str(getattr(args, "remote_token", "") or "")
        if not hub_url or not token:
            return
        want_halt = verdict in self.CLOUD_HALT_VERDICTS
        halted = bool(getattr(self, "_cloud_halted", False))
        if want_halt == halted:
            return  # 状态已一致（停机持续期/已恢复），幂等
        set_cloud_halt(
            hub_url,
            token,
            want_halt,
            log=lambda m: log(f"[run_rl] gate it{it}: {m}"),
            # 共享 hub（2026-09-18）：达令必须按课程下发——本课门禁 ABORT 只停本课云机，
            # 否则并行训练的其它课程会跟着被停。课程身份就是进程级那个（apply_course 挂上）。
            course=dist_common.course_name_of(),
        )
        self._cloud_halted = want_halt
