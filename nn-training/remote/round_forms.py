"""remote/round_forms.py — 轮形态的**唯一实现**（2026-10-23，`plan/anchor-full-rounds` §4）。

## 一句话

`anchor 轮（首轮 ∪ it % eval_every == 0）⇒ weight + opt 都整份；其余 ⇒ 两件都 delta`。
判据只有一条（`remote.deliver_wire.is_full_round`），本模块只负责**把它落到两个面上**：

  * `prepare_manifest(m, it)` —— 驱动侧：锚点轮**弹掉 `w_fmt`**（worker 手里就有整份权重，
    回整份；客户端不需要展开器），其余写 `W_DELTA_FMT`；`opt_fmt` 一律弹掉（opt 的形态在
    **写完盘之后**由下面这条定，那时才有「最近一份整份」当基准）。
  * `after_write(it, it_dir)` —— 落盘侧（`ArtifactStore.checkpoint` 的注入钩子）：非锚点轮
    把盘上那份整份 opt **就地改成 BOD1 容器**（base = 往回扫到的最近一份**整份**）；锚点轮
    若拿到的是容器则就地落地成整份。任一步失败 ⇒ **保留整份** + 一行日志（绝不挡这一轮）。

## 为什么形态在**写盘**时定（而不是投递时）

写盘那一刻「这一轮是不是锚点」由**计划**唯一确定（`it % eval_every`），不需要等 A 层读数；
投递可能在几小时后由另一个进程（补传腿）做，那时再判就有三份口径。投递侧因此**原样发盘上
字节**（`offline_deliver._post_artifact` 一个字都不用改），省量落在写盘那一刻。

## 基准为什么只能是「最近的整份」（评审 P0-1，别改成「上一轮」）

hub 的落地器找基准的判据是 **sha256 逐字节相等**（`remote/opt_standard._base_tar_bytes`），
而**重打出来的 tar 字节随 mtime 变**（`remote/opt_delta.rebuilt_opt_tar`：「同一份输入两次
调用的字节可能因 mtime 不同而相异」）。⇒ 拿「上一轮重建出来的整份」当基准，这个 `base_sha`
在 hub 侧**永远对不上**，第一轮 delta 之后整条链在 hub 上都解不开。双方逐字节共有的整份只有
**盘上那几份**（客户端写下的、hub 原样收到的）⇒ 基准一律从产物目录里往回扫：
锚点轮（和「回落写整份」的那几轮）就是全部候选。

## 层

`deliver_wire`(L0) / `opt_delta`(L0) / `delta_files`(L0) / `opt_standard`(L1) ⇒ 本模块 **L2**。
`remote.artifacts`(L0) **不** import 它（那里收的是注入进来的 `after_write`，见
`tests/test_remote_dag.py`）。torch 一律惰性（顶层零 torch 是本仓硬规）。
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

import remote.artifacts as artifacts
import remote.deliver_wire as deliver_wire
import remote.delta_files as delta_files
import remote.opt_delta as opt_delta
import remote.opt_standard as opt_standard
import remote.weights_delta as weights_delta
from common.errors import ProtocolError
from common.fs import atomic_write_bytes, extract_tar_bytes

__all__ = ["RoundForms"]

#: 产物目录里 opt 的**交付字节源**（= `ArtifactStore.opt_path` 的末名）。非锚点轮它装的是
#: BOD1 容器 —— 名字不跟着变：这条路径同时是 deliver 腿发什么、hub 侧 duplicate/conflict
#: 判据、以及下一轮装载的输入，**内容才是权威**（`delta_files.named_for` 那一族判据）。
OPT_NAME = "opt.tar"
#: 首轮缺 `plan.start_it` 时的兜底（`ArtifactStore.start` 同一条：新段的第 1 轮）。
_FALLBACK_START_IT = 1


def _first_it(root: Path, plan: Mapping | None) -> int:
    """本 run 的**第一轮**：产物目录里最小的 `it-<n>`，没有就是 `plan.start_it`。

    为什么不是「本会话第一轮」：续跑同一目录时，锚点判据要覆盖整条段（`it % eval_every`
    与起点无关）；而「新 hub 上也能单独装载」是 `full` 档 / hub 落地的事，不该靠首轮兜。
    产物目录读不动 ⇒ 退回 `plan.start_it`（不猜、不抛：形态判据不值得挡住一整段）。
    """
    first: int | None = None
    try:
        dirs = list(Path(root).glob(f"{artifacts.ArtifactStore.IT_PREFIX}*"))
    except OSError:
        dirs = []
    for d in dirs:
        try:
            n = int(d.name[len(artifacts.ArtifactStore.IT_PREFIX) :])
        except ValueError:
            continue
        if first is None or n < first:
            first = n
    if first is not None:
        return first
    try:
        return int((plan or {}).get("start_it", _FALLBACK_START_IT))
    except (TypeError, ValueError):
        return _FALLBACK_START_IT

def _eval_every_of(course) -> int:
    """课程的 `eval_every`（对象属性或映射键；缺失/脏值 ⇒ 0 = 不启用）。"""
    if course is None:
        return 0
    raw = course.get("eval_every", 0) if isinstance(course, Mapping) else getattr(
        course, "eval_every", 0
    )
    try:
        return int(raw or 0)
    except (TypeError, ValueError):
        return 0


class RoundForms:
    """一轮发整份还是 delta（`plan/anchor-full-rounds` 的 §3 判据 + §4 的两个落点）。"""

    #: `eval_every` 的下限：**1 等于每轮都是锚点**（权重从 delta 退回整份 = 净亏 ~267KB/轮，
    #: 而 opt 与今天一模一样）⇒ 这种课程**不装**本机制（= 今天的行为），只记一行。
    MIN_EVAL_EVERY = 2

    def __init__(
        self,
        root: str | Path,
        *,
        first_it: int,
        eval_every: int,
        log: Callable[[str], None] = print,
    ) -> None:
        self.root = Path(root)
        self.first_it = int(first_it)
        self.eval_every = int(eval_every)
        self.log = log

    # ---- 装配 ----

    @classmethod
    def for_store(
        cls, root: str | Path, *, plan: Mapping | None = None, course=None, log=print
    ) -> RoundForms | None:
        """按课程判据建一个；**不装**时返回 `None`（= 今天的行为，一个字节都不变）。

        不装的两种情形都要**说出来**（这条链的失败模式是「静默零收益」）：
          * `course` 缺席（调用方没传课程对象）⇒ 无从知道评估间隔；
          * `eval_every < 2`（含 `CourseSpec` 的缺省 1）⇒ 每轮都是锚点 = 权重反而变大。
        绝不把缺省补成 1，也绝不补成 5（猜一个数就是把「不生效」伪装成「生效」）。
        """
        every = _eval_every_of(course)
        if course is None:
            log(
                "WARN: 轮形态未启用（没有课程对象 ⇒ 不知道 eval_every）——"
                "opt 每轮整份（今天的行为）"
            )
            return None
        if every < cls.MIN_EVAL_EVERY:
            log(
                f"WARN: 轮形态未启用（eval_every={every} < {cls.MIN_EVAL_EVERY}：每轮都会是锚点，"
                "权重从 delta 退回整份 = 净亏）——opt 每轮整份（今天的行为）"
            )
            return None
        first = _first_it(Path(root), plan)
        log(
            f"[forms] 轮形态：锚点 = it{first} ∪ it%{every}==0（其余两件都走 delta；"
            "基准 = 盘上最近一份整份）"
        )
        return cls(root, first_it=first, eval_every=every, log=log)

    # ---- 判据 ----

    def is_full(self, it: int) -> bool:
        """这一轮要不要整份（唯一来源 `deliver_wire.is_full_round`）。"""
        return deliver_wire.is_full_round(
            int(it), first_it=self.first_it, eval_every=self.eval_every
        )

    # ---- 驱动侧 ----

    def prepare_manifest(self, m: dict, it: int) -> None:
        """按轮设**回传形态位**（原地改 `m`；调用点 `plan_run._run_iteration`）。"""
        m.pop("opt_fmt", None)
        if self.is_full(it):
            # worker 手里就有整份权重（本轮 init + PPO 后的新权重）⇒ 让它回整份。
            m.pop("w_fmt", None)
        else:
            m["w_fmt"] = weights_delta.W_DELTA_FMT

    # ---- 落盘侧 ----

    def after_write(self, it: int, it_dir: str | Path) -> None:
        """轮目录写完两件之后：把 opt 定形成本轮的形态（幂等；失败**只记一行**）。"""
        opt = Path(it_dir) / OPT_NAME
        if not opt.is_file():
            return
        is_container = delta_files.container_file(opt, opt_delta.OPT_DELTA_MAGIC)
        if self.is_full(int(it)):
            if is_container:
                # 锚点轮拿到容器（续跑/上一段的老行为）⇒ 就地落地成整份（`.delta` 旁存）。
                opt_standard.ensure_standard_opt(opt, extra_roots=(self.root,), log=self.log)
            return
        if is_container:
            return  # 已经是容器：形态对了，一个字节都不动（幂等）
        base = self.nearest_full(it)
        if base is None:
            self.log(
                f"WARN: it{int(it)} 非锚点轮找不到可用的基准整份（锚点被删/段太短）"
                "——本轮保留**整份** opt（下一轮多一份候选，不阻塞）"
            )
            return
        base_path, base_it = base
        base_raw = base_path.read_bytes()
        new_raw = opt.read_bytes()
        container = opt_delta.pack_opt_delta(
            self._state_of(base_raw, what=f"it{base_it} 的整份 opt"),
            self._state_of(new_raw, what=f"it{int(it)} 的整份 opt"),
            base_sha=hashlib.sha256(base_raw).hexdigest(),
        )
        atomic_write_bytes(opt, container)
        self.log(
            f"[forms] it{int(it)} opt → delta 容器：{len(new_raw)}B → {len(container)}B"
            f"（基准 it{base_it}，base_sha 逐字节可对账）"
        )

    def nearest_full(self, it: int) -> tuple[Path, int] | None:
        """`it` 之前最近的**整份** opt（`(路径, 轮)`）；没有 ⇒ `None`。

        只看盘上字节是不是容器（4 字节魔数），不看轮类型：稳态下它**恰好**是最近的锚点轮
        （非锚点轮都已是容器），而「回落写整份」的那几轮也算候选 —— 于是断链只损失一轮的
        省量，不是整条链。`base_sha` 必须是 hub 也逐字节持有的那份，所以**只认盘上文件**，
        绝不拿「重建出来的字节」当基准（见模块头）。
        """
        n = int(it) - 1
        while n >= self.first_it:
            p = self._opt_path(n)
            if p.is_file() and not delta_files.container_file(p, opt_delta.OPT_DELTA_MAGIC):
                try:
                    if p.stat().st_size > 0:
                        return p, n
                except OSError:
                    pass
            n -= 1
        return None

    def _opt_path(self, it: int) -> Path:
        return self.root / f"{artifacts.ArtifactStore.IT_PREFIX}{int(it):03d}" / OPT_NAME

    def _state_of(self, tar_bytes: bytes, *, what: str):
        """整份 opt tar 字节 → `torch.load` 出来的 state_dict（CPU）。"""
        import torch  # 惰性：顶层零 torch 是本仓硬规

        with tempfile.TemporaryDirectory(prefix="round-forms-") as tmp:
            d = Path(tmp)
            extract_tar_bytes(tar_bytes, d)
            p = d / "opt.pt"
            if not p.is_file():
                raise ProtocolError(f"{what} 里没有 opt.pt（{sorted(x.name for x in d.iterdir())}）")
            return torch.load(p, map_location="cpu")
