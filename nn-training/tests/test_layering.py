"""分层契约（S3，2026-09-23）—— 依赖方向必须单一，且靠测试钉住而不是靠自觉。

分层（2026-09-30 重组**完成**；全文 → `plan/nn-training-module-reorg.plan.md`）：

```
L0  common/                                   （stdlib-only，无 torch）
L1  biz/                                      （游戏业务：课程 / 奖励 / 关卡 / 账本）
L2  worker/                                   （本地 torch 训练全栈：算法栈 + 节点侧执行体）
L3  remote/                                   （跨端线路 + 云引导 —— 云机 worker = 本地 worker + remote）
L4  trainer/（编排层 + 六个入口：trainer/run_rl.py … trainer/eval_m1_once.py） · hub/
```

允许 `L4 → L3 → L2 → L1 → L0`；**反向禁止**。

> 2026-09-30（刀 4）：`biz/` 从 `rl/` 出包 —— 纯逻辑（64 个模块）与编排分家。
> 2026-09-30（刀 5）：编排那一半（37 个模块）随整包改名 `rl/` → **`trainer/`**，`rl/` 这个包
> 2026-09-30（刀 7）：六个**入口脚本**（`run_rl` / `run_bc` / `run_rl_cluster` / `train_loop` /
> `eval_course_once` / `eval_m1_once`）从 `nn-training/` 顶层搬进本包 ⇒ 快照 **37 → 43**，
> 「根入口」这个位置从此不存在（`nn-training/` 下只剩 `conftest.py`）。
> **从此不存在**。判据仍是那条机械定义，两边都换了家名：
> `biz/` = 「与 `TRAINER_ORCHESTRATION` 互补的那棵纯逻辑树」，`TRAINER_ORCHESTRATION`
> （见下方快照）= 「`trainer/` 中直接或经包内传递可达 `remote|worker` 的模块」。
> 刀 4 当天这句写作 `biz = rl/*.py − RL_ORCHESTRATION`，刀 5 之后按今天的名字读。
> 于是「谁是纯逻辑、谁是编排」在**包名**上就看得见，而不必逐个模块读 import。
>
> 2026-09-30（刀 6）：**`biz/` 只留游戏业务**（12 个模块），算法栈（`models/` `ppo/`
> `data/` `train/` `scripts/`，39 模块）与 52 个训练侧单体一起并入 **`worker/`**
> （口径：`worker/` = 「支持本地 torch 训练的全部代码」）。于是本文件的 L1 只剩
> `biz/` 一个包：「`biz/` 里没有一个模块达远端」这条断言不变；算法栈内部的先后
> 从本文件挪到 `tests/helpers/remote_dag.py` 的账本（`worker.*` 整族在册，层号 = 拓扑秩）。

> 本文件只判**粗粒度**的「面」：L0/L1 不许碰上层，而「编排」是一个声明式快照。
> `remote/` · `hub/` · `worker/` 三个包**内部**的先后由 `tests/helpers/remote_dag.py` 的
> 全局限号账本管（那才是「谁在谁上面」的权威）——本文件与它刻意不重叠。
> 两条不变量：① `worker/` 坐在 `remote/` **下面**（L2 < L3），因为云机侧（`remote/offline_eval` ·
> `remote/worker`）与 trainer 侧（`trainer/dispatch` · `trainer/queue_local`）都要用它（2026-09-30 刀 3）；
> ② **同层对端互不 import**：`hub/` ↔ `trainer/`（L4）· `worker/` ↔ 同层的其它节点侧包（今天无）。

## 为什么需要守卫

本仓的包级循环曾经就是这么长出来的：`rl/`（今 `trainer/`）有 10 个文件 import `remote/*`，
`remote/` 有 8 个文件 import `rl/*`，靠 `trainer/queue.py` 里一处**函数内延迟 import** 换来表面的平静——那种平静
下一次改动就会破。2026-09-23 把纯逻辑的 `protocol` / `game_watch` 下沉到 `common/`、并把
TS 导出器路径收进 `common/protocol.py`（`EVAL_SCRIPT`）之后，**`trainer`（当日还叫 `rl`）↔
`remote` 的模块级环消失了**（本文件由闭环断言守着）。

## 判据口径：编排层是**结构性**的，不是白名单

`trainer/`（刀 5 前的 `rl/`）里曾有**两类**模块：**纯逻辑**（课程 / 奖励 / 熔断 / 账本 …，不需要
传输层）与**编排**（驱动 rollout / eval / 远端腿，本质是应用层）。刀 4 按机械判据把前者整族
搬去 `biz/`，判据不用人肉列举「允许 import 谁」，而是：

    TRAINER_ORCHESTRATION := 「trainer 中可达 remote|worker 的模块集合」的**声明式快照**

两边对账（`test_trainer_orchestration_set_is_exactly_...`）：**多一个即红**（新模块偷偷碰了
传输层）、**少一个也红**（拆分后该模块已不达远端，把它从集合里删掉）。这样这份清单不会腐烂成
「合法的历史遗留」——那正是这类清单最坏的结局。

另外两条是真正的性质（不是记账）：① **纯逻辑不得 import 编排**（否则纯逻辑会经由编排间接
拖入传输层，「纯」就名不副实）；② **remote 不得（传递地）触及任何编排模块**——这就是
「包循环已断」的机械形式。

扫 **AST 的全部 import 节点**（含函数内的延迟 import）——函数内 import 同样是一条依赖边，
只是它把失败推迟到调用时（本仓就有过「延迟 import 掩盖了循环」的先例）。
`tests/` / `e2e/` 不参与分层（测试可以随便 import）。

⚠ **判据自身的坑（2026-09-23 反向探针实测）**：`from <pkg> import <mod>` 这种写法只给出裸包名，
必须展开成 `<pkg>.<mod>` 才看得见那条边。首版只给 `remote` 展开、没给 `rl` 展开 ⇒
「纯逻辑 → 编排」与「remote → 编排」两条断言在探针下**静默不动**（测试全绿但守卫是瞎的）。
本文件用 `_subpackages()`（按实存的包子目录）统一展开，别再逐个包开小灶。
"""

from __future__ import annotations

from pathlib import Path

from tests.helpers import source_scan

NN_ROOT = Path(__file__).resolve().parent.parent

#: L0 的**顶层单文件模块**。
#:
#: 2026-09-30（刀 2，`common/` 收口）：现在是**空**的 —— L0 曾经散在根下的六个单文件
#: （`dist_common` / `dist_shard` / `dist_weights_ledger` / `schema` / `platform_utils` /
#: `pid_probe`）已全部收进 `common/` 包，`common/` 一个包就是全部 L0。
#:
#: 保留这个**空元组**（而不是把循环删掉）：下面两条「L0 不得依赖 L1/L2」的断言同时扫
#: 「本名单」与「`common/` 目录」，名单是用来接住**下一个**顶层 L0 单文件的 —— 删掉它，
#: 下次有人往根下丢一个 stdlib-only 模块，判据会静默瞎着（本文件头部那条教训的同型）。
L0_TOP_MODULES: tuple[str, ...] = ()
#: L1 包（游戏业务）。
#:
#: 2026-09-30（刀 4）：`biz` 从 `rl/` 出包成 L1 的一员 —— 本名单里的每个包都必须**零**引用
#: 上层包面（见 `test_l1_packages_never_import_the_upper_face`），`biz/` 也不例外。
#: 2026-09-30（刀 6）：算法栈（`models/` `ppo/` `data/` `train/` `scripts/`）整族搬进
#: `worker/` ⇒ **L1 只剩 `biz/`**。算法栈的分层没消失，换账本管（见上：`worker.*` 入账）。
L1_PACKAGES = ("biz",)
#: **上层包**：对 L0 / L1 来说它们都是「不许碰」的上层。
#:
#: 合成一个面（而不是分开列 L2/L3/L4）是有意的：这个文件判的是「面」，先后顺序由账本管；
#: 把 `hub` / `worker` 补进来是因为它们 2026-09-30 才成为顶层包 —— 漏掉它们，
#: `common/` 或 `models/` 里 `import worker` 这类向上边会**静默通过**（本文件头部那类教训）。
#: `trainer`（刀 5）同理：它是 `rl` 改名后的家，**不在这个名单里就会让 `biz/` 里的
#: `import trainer.…` 静默通过** —— 而那正是 `biz` 切线（刀 4）存在的理由。
UPPER_PACKAGES = ("remote", "hub", "worker", "trainer")

#: `trainer/` 里的**编排模块**（应用层：驱动 rollout/eval/远端腿），允许 import `remote`。
#:
#: 这不是「豁免名单」而是**声明式快照**：测试会把它与「trainer 中可达 remote|worker 的模块
#: 集合」逐项对账，多一个 / 少一个都红。2026-09-30（刀 4 + 刀 5）后这份名单**就是 `trainer/`
#: 的全部** —— 纯逻辑那一半（64 个模块）已搬进 `biz/`（刀 6 再把训练侧的 52 个挪进 `worker/`），
#: 剩下的 37 个随整包改名 `rl/` → `trainer/`（刀 7 再把六个入口从 `nn-training/` 顶层
#: 并进来 ⇒ **43 个**，仍然是「trainer/ 的全部」）
#: （判据两边都成立：`trainer/` 里没有「不达远端」的模块，`biz/` 里没有一个达远端）。
#: 2026-09-23 下沉 `EVAL_SCRIPT` 后由 17 个收敛到 11 个——
#: `eval_local` / `eval_dispatch` / `gate_check` / `batch_eval` / `eval_a_once` /
#: `eval_replays_once` 原来只是**经由 `eval_local` 间接**碰到传输层，环一断就回了纯逻辑。
#: 2026-09-23（S4）：`loop_steps` 拆出 `loop_transport`（传输/发布策略的独立实现），
#: 后者成为新的一员（它直接 import `remote.push_client`）——由本快照强制登记。
#: 同日 S4 第二步再拆出 `loop_remote`（远端 PPO 腿 13 个方法的混入），它也直接
#: import `remote.push_client` 与 `trainer.loop_transport`。两次都是本快照先红、再登记。
#: 2026-09-25（S4 第十八刀）：`loop_core` 拆出 `loop_volume`（动态采集编排 9 个方法的混入），
#: 它接过 `trainer.rollout_phase` 的 `dispatch_rollout_phase`（补波/连续配额的派发口）⇒ 与本快照
#: 预期的形状**不同但同因**：它自己不经 remote，只是**经 rl 传递可达**——同样先红、再登记。
#: 同日（S4 第十九刀）：`loop_core` 再拆出 `loop_lifecycle`（主循环骨架 7 方法的混入），
#: 它拿 `trainer.loop_guards`（TrainingGuards）· `trainer.loop_steps`（kickstart_coef）·
#: `trainer.rollout_phase`（join_precollect_child）三处编排 import ⇒ 同样“不同形状但同因”
#: （经 rl 传递可达），先红、再登记。
#: 同日（S4 第二十刀）：`loop_core` 收尾拆出三簇——实测只有两簇“经 rl 传递可达”：
#: `loop_iter_dir`（拿 `trainer.collect_only` 的 `precollect_snapshot_wver`）· `loop_dispatch`（拿
#: `trainer.rollout_phase` 的 `dispatch_rollout_phase`）；**`loop_baseline` 反而回到纯逻辑**（它只拿
#: `common.distribution` / `common.log` / `trainer.queue.RUN_ID`，三者都不达 remote）⇒ 只登记前两个。
#: 同日（S4 第二十一刀）：`loop_steps` 拆出 `loop_export`（产物出包 4 方法的混入），它拿
#: `remote.hub_client.pack_ts_code_zip`（`_ensure_ts_code` 里**延迟** import——本快照的 AST 也看
#: 函数内 import）⇒ 同样先红、再登记（第六次）。
#: 同日（S4 第二十二刀）：`loop_remote` 的 862 行连通分量按判据同源切成四簇——`loop_remote_push`
#: / `loop_remote_job` / `loop_remote_fail` / `loop_remote_drive`。四者**都**经 `trainer.loop_transport`
#: （或直接 `remote.push_client`）传递可达 remote，故全部登记；`loop_remote` 退成组合根后仍经
#: `trainer.loop_remote_drive` 可达 ⇒ 名字不动（第七次）。
#: 2026-09-25（合并 `origin/goal-nn`）：本机 rollout 接长驻池 —— `trainer.queue_local` / `trainer.dispatch`
#: 改直 `import worker.serve_pool`（DECISIONS `§2026-09-23-goalnn-layering-common-sink` 早已把
#: `serve_pool` 列为 rl 允许的 remote 目标），于是它们成为**直接**编排成员；而 `batch_*` 执行面 /
#: `eval_*` / `stream` / `queue` / `loop_baseline`·`loop_control`·`loop_eval` 只是**经 rl 传递可达**
#: （`trainer.queue` → `trainer.dispatch` → remote 这条链把归集器与批执行面一并拽进来）⇒ 同样先红、再登记。
#: 这是一次**特征**（本机长驻池）而非拆分的连带登记，故单列一行说明形状。
#: 2026-09-27（S5 第四刀）：`bc_loop` 拆出 `bc_ingest`（BC job 回传消费：轮询会话/指标·eval 入账
#: + 节奏常量）——它沿用「`_request` 函数内 import」的口径（测试打的就是那个点）⇒ 直接编排成员，
#: 同前先红、再登记。`bc_loop` 仍经它可达 ⇒ 名字不动。
#: ⚠ 2026-09-30（刀 5，`rl/` → `trainer/`）：**成员一个没变**，只是包名换了 —— 但快照的
#: 「经包内传递可达」那一步必须跟着改（`d.startswith("trainer.")`），否则固定点会**静默塌成
#: 「只算直接可达」**：37 个成员里有一批（`batch_*` / `eval_*` / `stream` / `queue` …）正是
#: 只经这条内部链才达远端的 ⇒ 快照会假性 `shrank`（不是真回纯逻辑）。本刀实测到的就是这个。
#: ⚠ 2026-09-30（刀 7，入口归位）：`run_rl` / `run_bc` / `run_rl_cluster` / `train_loop` /
#: `eval_course_once` / `eval_m1_once` 六个**入口脚本**从 `nn-training/` 顶层搬进 `trainer/`
#: ⇒ 本快照 +6（37 → 43）。判据没变（「可达 remote|worker」），这六个本来就是**直接**成员：
#: `run_rl` 延迟 import `remote.push_client` / 顶层 `worker.*`、`run_bc` 与 `train_loop` 顶层
#: `worker.*`、`run_rl_cluster` 顶层 `worker.loop_scheduler`、两个 `eval_*_once` 经 `trainer.*`
#: 传递达 worker。它们搬进来之前**不在本文件视野里**（本文件只扫 `trainer/`）—— 这正是
#: `test_trainer_holds_only_orchestration_modules`（集合相等那条）先红的原因：一搬进来，
#: 「trainer 里每个模块都必须是编排」立刻看见这六个。
TRAINER_ORCHESTRATION = frozenset(
    {
        "batch_eval",
        "batch_plan",
        "batch_runner",
        "batch_store",
        "bc_ingest",
        "bc_loop",
        "collect_only",
        "dispatch",
        "eval_a_once",
        "eval_course_once",
        "eval_dispatch",
        "eval_m1",
        "eval_m1_once",
        "loop",
        "loop_baseline",
        "loop_control",
        "loop_core",
        "loop_dispatch",
        "loop_eval",
        "loop_export",
        "loop_guards",
        "loop_iter_dir",
        "loop_lifecycle",
        "loop_plan",
        "loop_remote",
        "loop_remote_drive",
        "loop_remote_fail",
        "loop_remote_job",
        "loop_remote_push",
        "loop_round_steps",
        "loop_runner",
        "loop_serve",
        "loop_steps",
        "loop_transport",
        "loop_volume",
        "queue",
        "queue_local",
        "rollout_phase",
        "run_bc",
        "run_rl",
        "run_rl_cluster",
        "stream",
        "train_loop",
    }
)


def _imports(path: Path) -> frozenset[str]:
    """该文件里出现的**完整点分模块名**集合（AST，含函数内 import）。

    `from biz.log import log` → `biz.log`；`from remote import hub_client` → `remote` 与
    `remote.hub_client` **两条都记**（裸包与子模块是两种不同的依赖声明）。

    ⚠ 展开必须**对所有子包**生效：只给某个包开小灶，`from trainer import loop_steps` 就会被
    记成裸 `trainer`，环与切线的断言会**静默失效**（2026-09-23 反向探针实测到，见本文件头部）。

    实现搬进 `tests.helpers.source_scan.imports`（带缓存的派生小结果，见该模块头）：本文件有
    一条**固定点**循环（`_trainer_reaching_remote`）会反复问同一批文件，原先每次都重新解析全
    那棵树。
    """
    return source_scan.imports(str(path), str(NN_ROOT))


def _top_modules(path: Path) -> set[str]:
    """顶层模块名（用于「L0 不得依赖 L1/L2」这类粗判）。"""
    return {d.split(".")[0] for d in _imports(path)}


def _py_files(root: Path) -> tuple[Path, ...]:
    return source_scan.py_files(str(root))


def _trainer_modules() -> dict[str, Path]:
    """`trainer/` 的模块（2026-09-30 刀 5 后 = **编排全部**；纯逻辑在 `biz/`）。"""
    return {p.stem: p for p in _py_files(NN_ROOT / "trainer")}


def _trainer_reaching_remote() -> set[str]:
    """`trainer/` 中**直接或经包内传递**可达**传输/执行面**的模块（= 编排层）。

    判据里的「远端」= `remote` **或** `worker`：`worker/` 是 2026-09-30（刀 3）从 `remote/`
    出包的节点侧执行体（`serve_pool` / `iter_rollout`），语义上仍属于同一个面。
    不把 `worker` 算进来，快照会一次失效 14 个模块（它们只是把 `import remote.serve_pool`
    改成了 `import worker.serve_pool`）—— 而 `TRAINER_ORCHESTRATION` 的**成员一个没变**。

    ⚠ 固定点的**内部边前缀**必须随之改名（`trainer.`）：写成旧 `rl.` 就不会错，而是**静默塌成
    「只算直接可达」**，一批只经内部链达远端的成员（`batch_*` / `eval_*` / `stream` / `queue`…）
    会假性失登。这是本仓「改名型搬家」里最典型的一类哑故障（本文件头部的同型教训）。
    """
    transport = {"remote", "worker"}
    trainer = _trainer_modules()
    reach = {
        k: any(d.split(".")[0] in transport for d in _imports(p)) for k, p in trainer.items()
    }
    changed = True
    while changed:
        changed = False
        for k, p in trainer.items():
            if reach[k]:
                continue
            if any(
                d.startswith("trainer.") and reach.get(d[8:], False) for d in _imports(p)
            ):
                reach[k] = True
                changed = True
    return {k for k, v in reach.items() if v}


def _remote_reaching_trainer() -> set[str]:
    """从**传输/执行面包**（`remote/` · `hub/` · `worker/`）出发（含传递）能触达的 `trainer` 模块名集合。

    2026-09-30（刀 1/刀 3）：扫描根从「只有 `remote/`」扩到三个包 —— `hub/` 与 `worker/`
    成为顶层包后，它们内部若长出 `import trainer.<编排>` 的边，原先**不会被扫到**。
    2026-09-30（刀 5）：被扫的名字从 `rl` 换成 `trainer`（同一个家换了门牌）——
    漏改这处 `d.startswith(...)` 就是**哑的**：不再有任何边被记下，断言永真。
    """
    out: set[str] = set()
    seen: set[str] = set()
    stack: list[Path] = []
    for pkg in ("remote", "hub", "worker"):
        root = NN_ROOT / pkg
        if root.is_dir():
            stack += list(_py_files(root))
    while stack:
        p = stack.pop()
        if str(p) in seen:
            continue
        seen.add(str(p))
        for d in _imports(p):
            head = d.split(".")[0]
            if head in {"remote", "hub", "worker"}:
                f = NN_ROOT / (d.replace(".", "/") + ".py")
                if f.exists():
                    stack.append(f)
            elif d.startswith("trainer."):
                out.add(d[8:])
                sub = NN_ROOT / "trainer" / (d[8:] + ".py")
                if sub.exists():
                    stack.append(sub)
    return out


def test_l0_never_imports_l1_or_l2() -> None:
    """L0 是最底层：只许依赖 stdlib（外加同层）。"""
    forbidden = set(L1_PACKAGES) | set(UPPER_PACKAGES)
    offenders: list[str] = []

    for name in L0_TOP_MODULES:
        p = NN_ROOT / f"{name}.py"
        if not p.exists():
            continue
        offenders += [f"{p.name} -> {m}" for m in sorted(_top_modules(p) & forbidden)]

    for p in _py_files(NN_ROOT / "common"):
        offenders += [
            f"common/{p.name} -> {m}" for m in sorted(_top_modules(p) & forbidden)
        ]

    assert offenders == [], f"L0 不得依赖 L1/L2：{offenders}"


def test_l1_packages_never_import_the_upper_face() -> None:
    """L1（游戏业务）不得依赖它的任何上层（`remote/` · `hub/` · `worker/` · `trainer/`）。

    刀 6 之后 L1 只剩 `biz/` 一个包（算法栈已并入 `worker/`）；`biz/` 必须**零**引用。

    2026-09-30（刀 1/刀 3）：判据从「只查 `remote`」改成查**整个上层包面** —— `hub` 与
    `worker` 成为顶层包后，`models/` 里 `import worker` 这种向上边原先会静默通过。

    2026-09-30（刀 5）：`trainer` 补进本判据后，原先「L1 的 `rl/` 只允许编排模块碰上层」
    那段例外**整段删掉**了 —— `rl/` 已不再是 L1 的一员（整包改名 `trainer/`，整包都是 L4
    编排）。少一个子包就少一份例外，这正是「分家」在守卫上的样子。
    """
    upper = set(UPPER_PACKAGES)
    offenders: list[str] = []
    for pkg in L1_PACKAGES:
        root = NN_ROOT / pkg
        if not root.is_dir():
            continue
        for p in _py_files(root):
            for d in sorted(x for x in _imports(p) if x.split(".")[0] in upper):
                offenders.append(f"{pkg}/{p.name} -> {d}")

    assert offenders == [], "L1 不得依赖上层包面：\n  " + "\n  ".join(offenders)


def test_l1_packages_never_import_trainer_orchestration() -> None:
    """L1（刀 6 后 = `biz/`）不得依赖 `trainer/` 的编排模块（否则间接拖入传输层）。

    刀 6 把算法栈并入 `worker/` 后本判据只剩一个包；`worker/` 与 `trainer/` 的关系由
    账本与 `test_remote_never_reaches_trainer_orchestration` 管。
    """
    offenders: list[str] = []
    for pkg in L1_PACKAGES:
        root = NN_ROOT / pkg
        if not root.is_dir():
            continue
        for p in _py_files(root):
            for d in sorted(_imports(p)):
                if d.startswith("trainer.") and d[8:] in TRAINER_ORCHESTRATION:
                    offenders.append(f"{pkg}/{p.name} -> {d}")
    assert offenders == [], (
        "L1 上层不得 import trainer 编排模块（会间接依赖 remote）：\n  " + "\n  ".join(offenders)
    )


def test_biz_never_imports_trainer_orchestration() -> None:
    """**真切线**：`biz/` 一个模块都不许 import 编排态 `trainer/`。

    否则「纯逻辑」可经由编排间接拿到传输层——分层就成了摆设（这条与
    `test_l1_packages_never_import_trainer_orchestration` 一起，才是「切线是真的」的证明）。

    历史：本用例在刀 4 前扫的是 `rl/` 里的**纯逻辑半**（`rl/` 自己不许 import 自己的编排半）。
    那半搬进 `biz/` 后判据一个字没变、只是扫描面跟着搬了家 —— 这正是刀 4 的「同一栋房子
    换个门牌」在守卫上的样子：**跟着搬，不是改写判据**。刀 5 再跟着改一次家名（`rl.` →
    `trainer.`，切片随之从 `[3:]` 变成 `[8:]`）：**前缀与切片必须同改**，只改一半不会报错，
    只会让这条切线**静默变瞎**（本文件头部那条哑故障的同型）。
    """
    offenders: list[str] = []
    for p in _py_files(NN_ROOT / "biz"):
        for d in sorted(_imports(p)):
            if d.startswith("trainer.") and d[8:] in TRAINER_ORCHESTRATION:
                offenders.append(f"biz/{p.name} -> {d}")
    assert offenders == [], "biz/ 不得 import 编排 trainer：\n  " + "\n  ".join(offenders)


def test_trainer_holds_only_orchestration_modules() -> None:
    """★ 2026-09-30（刀 4 + 刀 5）后的形态：`trainer/` **只有编排**（纯逻辑全在 `biz/`）。

    为什么这条不能只靠快照对账：`test_trainer_orchestration_set_is_exactly_the_modules_reaching_remote`
    只看得见「新模块碰了传输层」（`grew`）与「登记的模块不再碰」（`shrank`）—— 往 `trainer/`
    里丢一个**不达远端**的纯逻辑模块，两个方向都看不见它。本条按**集合相等**钉住「这个包
    是干嘛的」：`trainer/` 的每个模块都必须在快照里。
    """
    # `__init__.py` 不入账（包门面/文档，不是依赖图的节点 —— 同 `remote_dag.remote_modules()` 的口径）。
    extra = sorted(set(_trainer_modules()) - set(TRAINER_ORCHESTRATION) - {"__init__"})
    assert extra == [], (
        f"trainer/ 里出现非编排模块（游戏业务该住 biz/、训练栈该住 worker/，见 plan/nn-training-module-reorg.plan.md 刀 4/刀 6）：{extra}"
    )


def test_the_pure_logic_tree_is_gone_from_trainer() -> None:
    """机械事实：`biz/`（游戏业务）与 `worker/`（训练栈）**互不重名**，且 64 个模块还在。

    刀 4 把 64 个纯逻辑模块搬出 `rl/`；刀 6 又把其中训练侧的 52 个挪进 `worker/`
    （口径：`biz/` 只留游戏业务）。「搬回去」是最容易发生的静默回退（旧路径被某个写死
    路径的守卫读着、或有人顺手 cp 一份），所以按**基名集合**正面钉死：三棵树两两不重名
    —— 重名就说明有人复制了一份回旧家；而 64 个模块的**总数**不许缩水（少一个 = 被删或被
    塞回 `trainer/`）。
    """
    def mods(d: Path) -> set[str]:
        return {p.name for p in d.glob("*.py")} - {"__init__.py"}

    biz_dir = NN_ROOT / "biz"
    assert biz_dir.is_dir(), "biz/ 不存在（2026-09-30 刀 4 未落地？）"
    biz, worker, trainer = mods(biz_dir), mods(NN_ROOT / "worker"), mods(NN_ROOT / "trainer")
    # 66 = 12 个游戏业务（`biz/`）+ 54 个 `worker/` 顶层（52 个从 `biz/` 搬来 + 刀 3 的
    # `iter_rollout` / `serve_pool`）；少一个 = 被删，或被塞回 `trainer/`（本用例的靶子）。
    assert len(biz) + len(worker) >= 66, (
        f"biz ∪ worker 只有 {len(biz) + len(worker)} 个模块——刀 4/刀 6 的分家缩水了？"
    )
    assert len(biz) < 20, f"biz/ 长到 {len(biz)} 个模块了——它只该留游戏业务（刀 6 口径）"
    for a, b, na, nb in (
        (biz, worker, "biz", "worker"),
        (biz, trainer, "biz", "trainer"),
        (worker, trainer, "worker", "trainer"),
    ):
        assert not (a & b), f"同名文件同时住 {na}/ 与 {nb}/：{sorted(a & b)}"


def test_trainer_orchestration_set_is_exactly_the_modules_reaching_remote() -> None:
    """声明式快照双向对账：**多一个红，少一个也红**（这份清单不许腐烂）。

    「远端」= `remote` **或** `worker`（判据口径见 `_trainer_reaching_remote` 的 docstring）。

    刀 4 后又刀 5：`trainer/`（前 `rl/`）只剩编排 ⇒ 本条与
    `test_trainer_holds_only_orchestration_modules` 一起构成双向对账：
    **「trainer 里的都达远端」+「达远端的都在 trainer」**。
    """
    computed = _trainer_reaching_remote()
    declared = set(TRAINER_ORCHESTRATION)
    grew = sorted(computed - declared)
    shrank = sorted(declared - computed)
    assert not grew and not shrank, (
        "TRAINER_ORCHESTRATION 与实测不符：\n"
        f"  新增（偷偷碰了传输/执行面 / 经包内传递可达 remote|worker，请确认后加进集合）：{grew}\n"
        f"  失效（已回纯逻辑，请从集合里删掉）：{shrank}"
    )


def test_remote_never_reaches_orchestration_trainer() -> None:
    """**包循环已断**的机械形式：传输/执行面包不得（传递地）触及任何**编排**模块。

    S3 之前的形态是双向的（`remote/*` → `rl/eval_local` → `worker/serve_pool`，一条边就够
    把整片 eval 模块拽进环里）。现在它们只能触达**纯逻辑** —— 那是**合法**的向下方向
    （刀 4 前写作 `worker → rl.reports`，如今同样的边是 `worker → biz.reports`），
    碰到编排态 `trainer/` 才是「把上层拽回执行层」，环又回来了。

    2026-09-30（刀 1/刀 3）：扫描根从「只有 `remote/`」扩到 `remote/` + `hub/` + `worker/`。
    2026-09-30（刀 5）：被扫的名字从 `rl` 换成 `trainer`（同一栋房子换门牌）。
    """
    hit = sorted(_remote_reaching_trainer() & set(TRAINER_ORCHESTRATION))
    assert hit == [], (
        "传输/执行面包（remote/hub/worker）触达了编排层 trainer，trainer ↔ remote 环回来了：\n  "
        + "\n  ".join(hit)
    )


def test_common_is_a_leaf_package() -> None:
    """`common/` 不得反向依赖任何上层（要能随 code.zip 解到没有 torch 的云机上）。"""
    forbidden = set(L1_PACKAGES) | set(UPPER_PACKAGES)
    offenders: list[str] = []
    for p in _py_files(NN_ROOT / "common"):
        offenders += [
            f"common/{p.name} -> {m}" for m in sorted(_top_modules(p) & forbidden)
        ]
    assert offenders == [], f"common/ 必须自底向上无依赖：{offenders}"


def test_worker_never_reaches_the_transport_the_hub_or_the_orchestration() -> None:
    """★ `worker/` 是 L2：不得 import `remote/` · `hub/`，也不得 import 编排态 `trainer/`。

    2026-09-30（刀 3）：`serve_pool` / `iter_rollout` 从 `remote/` 出包成 `worker/`，
    坐在 `remote/` **下面**。它们**零 `remote.*` 依赖**是这次出包成立的前提：

      · 云机侧（`remote/offline_eval.py` · `remote/worker.py`）引用它们是**向下**边；
      · trainer 侧（`trainer/dispatch.py` · `trainer/queue_local.py`）引用它们也是向下边（L4 → L2）；
      · 一旦有 `worker → remote` / `worker → hub` 的边，四条方向全反、审计器立刻非零。

    原 `rl/`（今 `trainer/`）分两半：**纯逻辑**（2026-09-30 刀 4 起住 `biz/`，L1）是允许的
    向下依赖（实测只剩一条：`iter_rollout` 延迟 import `biz.reports` 的聚合助手）；
    **编排态**（`trainer/`，L4）不许碰 —— 那不是「向下」，而是把 worker 拽回应用层。
    """
    offenders: list[str] = []
    for p in _py_files(NN_ROOT / "worker"):
        for d in sorted(_imports(p)):
            head = d.split(".")[0]
            if head in {"remote", "hub"}:
                offenders.append(f"worker/{p.name} -> {d}")
            elif head == "trainer" and d[8:] in TRAINER_ORCHESTRATION:
                offenders.append(f"worker/{p.name} -> {d}（编排不达 worker）")
    assert offenders == [], (
        "worker/ 必须坐在 remote/ 与 hub/ 下面：\n  " + "\n  ".join(offenders)
    )


def test_the_moved_modules_are_gone_from_remote() -> None:
    """机械事实：已下沉的模块不在旧家（别悄悄搬回去）——S3 的 `remote/`→`common/` 一批，
    加载刀 2 的「根下单文件 + `remote/` 两个 + `rl/jsonc`」一批，以及刀 3 的 `worker/` 一批。"""
    for name in ("protocol.py", "game_watch.py", "net_http.py", "_instance_lock.py", "_port_guard.py"):
        assert not (NN_ROOT / "remote" / name).exists(), (
            f"remote/{name} 已下沉到 common/（S3 / 2026-09-30 刀 2）；"
            "若确需搬回，请同时更新本文件与 DECISIONS"
        )
    for name in ("protocol.py", "game_watch.py", "net_http.py", "instance_lock.py", "port_guard.py"):
        assert (NN_ROOT / "common" / name).exists(), f"common/{name} 不存在"
    # 2026-09-30（刀 3）：节点侧执行体出包到 worker/（L2，坐在 remote/ 下面）。
    for name in ("iter_rollout.py", "serve_pool.py"):
        assert not (NN_ROOT / "remote" / name).exists(), (
            f"remote/{name} 已出包到 worker/（2026-09-30 刀 3）；若确需搬回，"
            "请同时更新本文件、tests/helpers/remote_dag.py 与 DECISIONS"
        )
        assert (NN_ROOT / "worker" / name).exists(), f"worker/{name} 不存在（刀 3 未落地？）"


def test_the_old_rl_package_is_gone_after_the_rename() -> None:
    """机械事实（2026-09-30 刀 5）：`rl/` 这个包**不存在**了，编排全在 `trainer/`。

    为什么值得单独钉：`NN_ROOT` 在 `sys.path` 上，留一个 `trainer/__init__.py`（哪怕只是空壳）
    就能让 `import rl.x` 继续解析得到 —— 于是「整包改名」退化成「两个名字并存」，
    任何漏改的 `rl.*` 都不再报错（扫描面缩水是哑的，本文件头部的同型教训）。
    所以旧家的存在性本身就是判据，不靠「没搜到 `rl.` 字面量」这种间接证据。
    """
    assert not (NN_ROOT / "rl").exists(), (
        "rl/ 还在——2026-09-30 刀 5 已把整包改名 trainer/（成员一个没变）；"
        "若是别的实验，请换一个不与旧包名冲突的目录名"
    )
    assert (NN_ROOT / "trainer" / "__init__.py").exists(), "trainer/ 不是包（刀 5 未落地？）"


def test_l0_top_level_single_files_are_gone_from_the_root() -> None:
    """2026-09-30（刀 2）：L0 不再散在根下 —— 十一个模块全部住进 `common/`。

    `NN_ROOT` 会进 `sys.path`（conftest / py-modules），所以根下的 `schema.py` / `dist_common.py`
    曾经可以 `import schema` 直接用；留一个旧名文件就会让「两处都能 import」长期共存，
    而「删掉旧家」正是本仓第十刀起那条「名字是契约、位置不是」被执行的方式。
    """
    assert L0_TOP_MODULES == (), "顶层 L0 名单不再为空（详见它上面的注释）"
    for name in (
        "dist_common.py",
        "dist_shard.py",
        "dist_weights_ledger.py",
        "schema.py",
        "platform_utils.py",
        "pid_probe.py",
        "log_bundle.py",
    ):
        assert not (NN_ROOT / name).exists(), f"{name} 已收进 common/（刀 2）；根下不留旧名"
    for name in (
        "distribution.py",
        "shard.py",
        "weights_ledger.py",
        "schema.py",
        "platform_utils.py",
        "pid_probe.py",
        "log_bundle.py",
        "jsonc.py",
        "net_http.py",
        "instance_lock.py",
        "port_guard.py",
    ):
        assert (NN_ROOT / "common" / name).exists(), f"common/{name} 不存在（刀 2 未落地？）"
