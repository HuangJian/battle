"""stream_collect.py — 云机侧的**逐局流式采集**调度器（2026-10-27，用户口径）。

## 病（这是第 N 次改这条需求，前面的形态都错在同一处）

用户口径从 2026-10-07 起一直是同一句话：

> 采完一局，立即判断差额最大的关卡，立即用空出来的 worker 采集该差额关卡的样本，
> 直到所有关卡样本量满额。**不需要一轮一轮的补。**

前面几版的形态是「**批**」：初波（`games_per_stage_by_stage` 预先算好）→ 整批跑完 → 重算
缺口 → 再一批（`worker/iter_topup.topup_rollout` 的 `while batches < max_batches`）。批的
粒度决定了三件事都做错：

* **不是「采完一局就判」**：批里最慢的那一局（经典 35 关 6–15s、偶尔 30s 撞硬顶）决定下一
  次决策的时刻，短局关交出的槽位只能干等；
* **est 只能事后校准**：批尾才知道这一轮真实的 `samples/局`，于是批次越补越小、越补越偏
  （现场 41–47 批，一轮撞满 `OFFLINE_LEASE_TTL_SEC=900`）；
* **账目按批走**（`topup{k}/`、逐批 spec/data_fp/报告合并）⇒ 每批都要重扫盘、重新聚合。

## 修法

**一个调度器 + 一个池**，整轮的采集只有一段：

```
槽位空出 → 问调度器「下一局派哪一关」→ 派一局 → 它结算 → 立刻回到第一步
```

* 判据是**差额（samples）**：`quota − collected`，最大的先派；同额按 stage 升序（确定性）。
  一局回来就把它的 `nSamples` 记进账本，所以「谁最缺」每结算一局都重新算 —— 这正是用户
  说的那句话。
* **在飞的局也算账**（`collected + unsettled × est ≥ quota` ⇒ 本关先不派）：否则宽槽位会在
  同一关上叠派、把过采浪费放大到「槽位数 × 局均样本」。没有局均估计时只留一局在飞
  （它一回来就有估计）。
* **不猜局数**：本模块不回答「这一关要采几局」（那是 est 反解，误差 ±40% ⇒ 多派/少派）。
  它只回答「**下一局**是哪一关」；满额是每局结算后重算出来的，不是预先算出来的。
* **满额即停**：所有关 `collected ≥ quota`（或撞硬顶）⇒ 返回 `None`，池里没派的候选
  **从不运行**。
* **硬顶兜底**：显式 `max_games_per_stage` → 计划 est 反解 → 本关**实测** est 反解（先到先
  冻，之后 est 怎么跳都不再改这条顶）→ 都没有时 `DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN`
  （防「一局零样本」把一轮拖成无限）。触顶的关进 `capped_stages`（响亮，不静默短采）。
* **同一条连续流**：第 `k` 局 = `stage_seeds(rotate_seed, it, stage, start=k, n=1)` —— 与
  `worker.volume_waves.initial_wave_pairs_by_stage` / `worker.volume_quota.continuous_pairs`
  同一个 `0x5EED` 流，所以「接着抽」是构造性的，不是约定。`--out` 全局续号（`w{i}` 不与
  计划里任何一条重号）。

## 与计划的 argv 的关系

计划的 `argv` 在本模块里只当**模板**（每关一条：自定义关的 `--stage-json` 只在自己的模板
里）。整轮采哪几局由调度器现场决定 ⇒ 轮末的声明集 = 实际派出去的那一组（`iter_rollout`
按它重算 `data_fp` 再逐位对账）。「实产 == 声明」这条纪律一个字没放宽；变的只是「声明集
不再是打包时预排的那份」。计划里的局数仍在 `volumeTopup.planned_argv` 里可见，供对账。

本模块只有纯逻辑 + 一个轻量账本（无 IO / 无子进程 / 无随机）：真正跑局、收产物、逐位对账
在 `worker/iter_rollout.run_iter_rollout`（它每结算一局就调这里的 `next_game` / `observe`）。
"""

from __future__ import annotations

from collections.abc import Callable

from common.protocol import ProtocolError
from worker.rollout_argv import retarget_argv
from worker.volume_alloc import (
    DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN,
    VOLUME_ALLOC_RULE,
    default_game_caps,
    shortfall_by_stage,
    validate_runtime_volume,
    wasted_samples,
)
from worker.volume_quota import default_game_cap, stage_seeds, target_per_stage

__all__ = ["STREAM_RULE", "StreamCollector"]

#: 采集调度规则版本（进轮报 `volumeTopup.rule`；与 `VOLUME_ALLOC_RULE` 同值 —— 配额口径
#: 没变，变的是「谁来决定下一局」，那一层由 `mode` 字段说清）。
STREAM_RULE = VOLUME_ALLOC_RULE

#: 本模块的调度形态（`volumeTopup.mode`）：一局一拍，不是一批一轮。
STREAM_MODE = "per-game-stream"


def _flag_value(argv: list[str], flag: str) -> str:
    """argv 里 `flag` 的下一个 token（缺席/结尾 ⇒ 空串；与 `worker.plan._flag_value` 同语义）。"""
    try:
        i = argv.index(flag)
    except ValueError:
        return ""
    return str(argv[i + 1]) if i + 1 < len(argv) else ""


def _stage_of(argv: list[str]) -> int:
    """argv 的 `--stages`（协议层已校验「恰好一次、十进制整数」——这里只做形状转换）。"""
    raw = _flag_value(argv, "--stages")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ProtocolError(
            f"rollout.argv 缺 --stages 或非整数（{raw!r}）——流式采集无法把局归到关卡"
        ) from None


def _out_index(argv: list[str]) -> int:
    """argv 的 `--out` 若是 `w{i}` 形状 ⇒ `i`；否则 -1（`--out` 的编号只用于全局续号）。"""
    txt = _flag_value(argv, "--out")
    if txt.startswith("w") and txt[1:].isdigit():
        return int(txt[1:])
    return -1


class StreamCollector:
    """逐局调度器：**每结算一局**就判「差额最大的关」，下一局派它。

    调用契约（`worker/iter_rollout.run_iter_rollout` 是唯一生产调用方）：

      * `next_game()` → 下一局的 argv，或 `None`（所有关满额 / 触顶 ⇒ 本轮不再派）；
      * `observe(argv, n_samples)` → 一局结算后的账本更新（`n_samples` = 该局 shard 的
        `nSamples`，掉局 = 0 ⇒ 不计入配额，但仍占一局的序号）。
    """

    def __init__(
        self,
        spec: dict,
        vol: dict,
        *,
        log: Callable[[str], None] = lambda msg: print(f"[stream] {msg}", flush=True),
    ) -> None:
        self.spec = spec
        self.log = log
        self.stages: list[int] = [int(s) for s in vol["stages"]]
        self.target = int(vol["target_transitions"])
        self.quota = target_per_stage(self.target, len(self.stages))
        self.ests: dict[int, int] = {int(k): int(v) for k, v in vol["est_s_by_stage"].items()}
        self.rotate_seed = int(vol["rotate_seed"])
        self.it = int(vol["it"])
        self.est_hi_factor = float(vol["est_hi_factor"])
        topup = dict(vol["topup"])
        self._explicit_cap = int(topup.get("max_games_per_stage", 0) or 0)
        self._plan_caps = default_game_caps(
            self.stages, self.target, ests=self.ests, explicit=self._explicit_cap
        )
        # ---- 模板（每关一条）+ 计划里的局数（读数面）+ `--out` 续号 ----
        self._templates: dict[int, list[str]] = {}
        planned: dict[int, int] = {s: 0 for s in self.stages}
        top_out = -1
        for row in list(spec["argv"]):
            argv = list(row)
            stage = _stage_of(argv)
            self._templates.setdefault(stage, argv)
            planned[stage] = planned.get(stage, 0) + 1
            top_out = max(top_out, _out_index(argv))
        missing = [s for s in self.stages if s not in self._templates]
        if missing:
            raise ProtocolError(
                f"流式采集拿不到这些关的 argv 模板: {sorted(missing)}（声明集里没有该关 ——"
                "自定义关的 --stage-json 会因此带错/丢失，拒收）"
            )
        # ---- 账本（只增不改口径）----
        #: 逐关已结算样本（= **配额**的量纲，与 `target_transitions` 同源）。
        self.collected: dict[int, int] = {s: 0 for s in self.stages}
        #: 逐关**已派发**局数（含在飞）——也是该关连续流的游标（下一局取第 k 个 seed）。
        self.games: dict[int, int] = {s: 0 for s in self.stages}
        #: 逐关**已结算**局数（掉局也计入：它占了一个 seed，且是 est 的分母）。
        self.settled: dict[int, int] = {s: 0 for s in self.stages}
        #: 计划里预排的局数（读数面：证明「计划 46 局 / 实采 52 局」这类差额，不进判据）。
        self.planned_games: dict[int, int] = {s: int(planned.get(s, 0)) for s in self.stages}
        self._out_next = max(len(list(spec["argv"])), top_out + 1)
        #: 冻结的硬顶（见 `_cap`：有实测之后第一条顶就定下来，之后 est 怎么跳都不改）。
        self._caps: dict[int, int] = {}
        self._dispatched = 0

    # ────────────────────────── 构造 ──────────────────────────

    @classmethod
    def from_spec(
        cls,
        spec: dict,
        *,
        log: Callable[[str], None] = lambda msg: print(f"[stream] {msg}", flush=True),
    ) -> StreamCollector | None:
        """spec 带**启用的** `volume` 块 ⇒ 调度器；缺席/关闭 ⇒ `None`（老行为逐字不变）。

        `volume` 的完整形状 + 同源校验仍走 `validate_runtime_volume`（一份公式，不抄第二份）。
        `topup.max_batches <= 0` 的语义是「本轮不做动态采集」（历史口径）⇒ 这里也不开流。
        """
        raw = spec.get("volume")
        if not isinstance(raw, dict):
            return None
        try:
            vol = validate_runtime_volume(raw)
        except ValueError as e:
            raise ProtocolError(f"rollout.volume 块非法：{e}") from e
        topup = vol["topup"]
        if not bool(topup.get("enabled")) or int(topup.get("max_batches", 0)) <= 0:
            return None
        return cls(spec, vol, log=log)

    # ────────────────────────── 账本读数 ──────────────────────────

    def _est(self, stage: int) -> int:
        """本关局均样本的优先序：**本轮实测** → **本关计划 est** → 全池实测 → 无估计。

        为什么计划 est 排在**池化实测**之前：关间 `samples/局` 能差 1.75×（本模块存在的
        理由）——拿别的关的实测去猜这一关，会让「在飞的那一局预计就能补齐」误判成真：
        长局关的真值 25、池化 300 ⇒ 它手里那局被当成能交 300 样本 ⇒ 槽位白等一局。
        计划 est 是**本关**的历史派生量（同一关的上一轮），虽然旧，但量纲对。

        `0` = 完全没有估计（冷启动的第一次派发）——调用方据此只留一局在飞，绝不拿假 est
        去反解局数（plan/rollout-estfree-worksteal §4.1 的反探针③）。
        """
        done = int(self.settled.get(stage, 0))
        got = int(self.collected.get(stage, 0))
        if done > 0 and got > 0:
            return max(1, round(got / done))
        plan_est = int(self.ests.get(stage, 0) or 0)
        if plan_est > 0:
            return plan_est
        tot_done = sum(int(v) for v in self.settled.values())
        tot_got = sum(int(v) for v in self.collected.values())
        if tot_done > 0 and tot_got > 0:
            return max(1, round(tot_got / tot_done))
        return 0

    def _unsettled(self, stage: int) -> int:
        """本关**还没结算**的局数（派出去但没回来的）。"""
        return max(0, int(self.games.get(stage, 0)) - int(self.settled.get(stage, 0)))

    def _cap(self, stage: int) -> int:
        """本关局数**硬顶**（防「一局零样本 ⇒ 无限补」把一轮拖死）。

        来路（先到先用）：显式 `max_games_per_stage` → 计划 est 反解（`default_game_caps`）→
        本关实测 est 反解（`default_game_cap`，4× 缺口局数）→ 都没有时
        `DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN`（§4.1 的防跑飞绝对值）。

        有实测之后**冻住**（`_caps`）：est 逐局会跳（长/短局混采），让它动态重算就能出现
        「顶掉到已派局数以下 ⇒ 配额未满却停」——那正是本模块要防的静默短采。
        """
        frozen = self._caps.get(stage)
        if frozen is not None:
            return int(frozen)
        cap = int(self._plan_caps.get(stage, 0) or 0)
        if cap <= 0:
            est = self._est(stage)
            cap = default_game_cap(self.quota, est) if est > 0 else 0
        if cap <= 0:
            cap = DEFAULT_TOPUP_MAX_BATCHES_UNKNOWN
        cap = max(int(cap), int(self.games.get(stage, 0)))  # 绝不能卡在已派发的基数之下
        if int(self.settled.get(stage, 0)) > 0:
            self._caps[stage] = cap
        return cap

    # ────────────────────────── 调度 ──────────────────────────

    def next_game(self) -> list[str] | None:
        """下一局的 argv。三态（调用方必须分清，混了就是静默短采）：

        * `[argv]` —— 派这一局；
        * **空列表** —— 本拍**没活可派**（在飞的局预计就能补齐），但**供给不关**：下一局
          结算后再问。冷启动（还没有局均估计、每关已有一局在飞）就是这个状态；
        * `None` —— **终局**：所有关都满额（或撞硬顶）⇒ 本轮不再派新局。
        """
        best_key: tuple[int, int] | None = None
        best_stage: int | None = None
        #: 还有没有「未满额且未触顶」的关 —— 有 ⇒ 本拍的空结果只是软停，不是终局。
        work_left = False
        for stage in self.stages:
            got = int(self.collected.get(stage, 0))
            if got >= self.quota:
                continue
            if int(self.games.get(stage, 0)) >= self._cap(stage):
                continue
            work_left = True
            est = self._est(stage)
            unsettled = self._unsettled(stage)
            if est > 0 and got + unsettled * est >= self.quota:
                continue  # 在飞的局预计就够 ⇒ 先等它们回来（别在同一关上叠派）
            if est <= 0 and unsettled > 0:
                continue  # 没有局均估计、手里还有一局在飞 ⇒ 等它回来给出估计
            need = self.quota - got - (unsettled * est if est > 0 else 0)
            key = (int(need), -int(stage))  # 差额最大的先派；同额按 stage 升序（确定性）
            if best_key is None or key > best_key:
                best_key, best_stage = key, stage
        if best_stage is None:
            return [] if work_left else None
        return self._mint(best_stage)

    def _mint(self, stage: int) -> list[str]:
        """按本关连续流的**当前游标**取下一个 seed，重定向模板成一条 argv。"""
        cursor = int(self.games.get(stage, 0))
        seed = stage_seeds(self.rotate_seed, self.it, int(stage), cursor, 1)[0]
        template = self._templates[int(stage)]
        argv = retarget_argv(
            template,
            stage=int(stage),
            seed=int(seed),
            out=f"w{self._out_next}",
            wver=str(self.spec.get("wver") or ""),
            # 模板自己的 `--stage-json`：`retarget_argv` 的 `None` 会**删掉**那一对，
            # 跨关取模板就会踩这个坑（自定义关课程）——所以只从本关模板取。
            stage_json=_flag_value(template, "--stage-json") or None,
        )
        self._out_next += 1
        self.games[int(stage)] = cursor + 1
        self._dispatched += 1
        return argv

    def observe(self, argv: list[str], samples: int) -> None:
        """一局结算：记样本、推进 est 分母、冻硬顶。**绝不抛**（观测腿，不能让训练死在记账上）。"""
        try:
            stage = _stage_of(list(argv))
        except ProtocolError:
            return
        if stage not in self.collected:
            return
        self.settled[stage] = int(self.settled.get(stage, 0)) + 1
        n = int(samples or 0)
        if n > 0:
            self.collected[stage] = int(self.collected.get(stage, 0)) + n

    # ────────────────────────── 读数（轮报）──────────────────────────

    def volume(self) -> dict:
        """轮报里的 `volumeTopup` 块（键与旧补差形态同源，另加 `mode` / 计划局数）。"""
        short = shortfall_by_stage(
            stages=self.stages, collected=self.collected, target_transitions=self.target
        )
        capped = sorted(
            s for s in short if int(self.games.get(s, 0)) >= self._cap(s) > 0
        )
        return {
            "rule": STREAM_RULE,
            "mode": STREAM_MODE,
            "dispatched_games": int(self._dispatched),
            "planned_games": sum(int(v) for v in self.planned_games.values()),
            "est_hi_factor": float(self.est_hi_factor),
            "collected_by_stage": {int(s): int(v) for s, v in self.collected.items()},
            "games_by_stage": {int(s): int(v) for s, v in self.games.items()},
            "planned_by_stage": {int(s): int(v) for s, v in self.planned_games.items()},
            "shortfall_by_stage": {int(s): int(v) for s, v in short.items()},
            "wasted_samples": int(
                wasted_samples(
                    stages=self.stages,
                    collected=self.collected,
                    target_transitions=self.target,
                )
            ),
            "capped_stages": [int(s) for s in capped],
        }

    def stage_stats(self) -> dict:
        """**分关采集账**（与 `trainer/loop_volume._volume_stage_stats` 同一形状）。

        为什么要点这一格（2026-10-28，plan/it-stage-ratation §9.5）：控制台的「各关进度」
        列/面板读的是账本里的 `volume_stage_stats`（本机腿收官时写），而云机轮此前什么都没
        写 ⇒ 云机段那几列恒为 `—`。同一个形状 ⇒ 读面一份解析器适配两条腿
        （`dashboard/src/server/iters.ts::parseIterVolume`），不必在控制台里写第二个分支。

        差异只在两格（且都是读数面，进不了判据）：
          * `est_src` = `"plan"`：云机的分关 est 是**导出时钉进计划**的（节点没有 hub 的
            jsonl 现算）；本机腿的 `learned`/`trailing` 在这里不成立。
          * `halted` = 逐关配额全部达成（= 采集器停止派局的同一个判据）。
        """
        short = shortfall_by_stage(
            stages=self.stages, collected=self.collected, target_transitions=self.target
        )
        return {
            "rule": VOLUME_ALLOC_RULE,
            "est_src": "plan",
            "halted": not short,
            "quota": int(self.quota),
            "stages": {
                str(int(s)): {
                    "collected": int(self.collected.get(s, 0)),
                    "games": int(self.games.get(s, 0)),
                    # `est_s` = 计划钉的本关估计（与训练侧分母同源）；`cap_games` = 同一份硬顶。
                    "est_s": int(self.ests.get(s, 0)),
                    "cap_games": int(self._plan_caps.get(s, 0)),
                }
                for s in self.stages
            },
            "shortfall": {str(int(s)): int(v) for s, v in short.items()},
        }

    def summary_line(self) -> str:
        """**一行**收尾读数（不逐局打：用户口径 2026-10-27「log 要精简要节制」）。

        名字不叫 `summary`：那会与 `biz/course_archive` / `worker/serve_pool` 的同名方法凑成
        新的「同名散落 ≥3」组（`tests/test_reuse_ledger.py` 的基线守卫），而这三处各是各的
        读数体、没有可共享的实现 —— 换名比抬基线便宜（同族先例 2026-10-24 `tick` → `tick_mode`）。
        """
        vol = self.volume()
        got = sum(int(v) for v in self.collected.values())
        short = vol["shortfall_by_stage"]
        capped = vol["capped_stages"]
        return (
            f"流式采集：动态加采 {vol['dispatched_games']} 局"
            f"｜逐关配额 {self.quota}×{len(self.stages)}，已采 {got}/{self.target}"
            f"｜缺口 {sum(int(v) for v in short.values())}"
            + (f"（未满额关 {sorted(short)}，触顶 {capped}）" if short else "")
            + f"｜过采 {vol['wasted_samples']}"
        )
