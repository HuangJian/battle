"""tests/helpers/warmup.py — 门禁 forkdist 的**收集期预热清单**（2026-10-29）。

## 机制

门禁的 `tools/forkdist.py` 是「master 收集一次 + `fork()` 出 worker」（`--forkdist $NPROC`）。
fork 出来的进程与 master **共享**已经建好的对象（写时复制）：收集相算一次的整仓扫描，所有
worker 直接命中缓存。不预热的话，凡是跑到该用例的 worker 都要从头再算一遍 —— 那些扫描是纯
AST 解析，在 16 个 worker 上就是同一笔 CPU 被重复付 N 次。

`conftest.py::pytest_collection_finish` 在**收集完成后**调 `warm_all()`；**非 forkdist
（xdist / 裸 pytest）不预热**：那边每个 worker 是新进程、各自重新收集，master 里的对象不共享，
预热纯属白付。

## 判据：什么进清单、什么不进

预热把成本从**并行的执行相**搬到**串行的收集相**，所以它只有在「省下的并行 CPU ÷ 有效核数 >
预热自付的墙钟」时才划算。换算成可执行的判据：

    进：部署值 / 预热成本 ≥ 3（≈ 至少 3 个 worker 会各自再算一遍，且单份不便宜）

| 项 | 冷启动 | 谁在付 | 结论 |
|---|---|---|---|
| `remote_dag.graph()` | 0.9s 静默 / **3.5~4.8s 满载** | 9 个 worker（`test_remote_dag` + 三份 `*_split.py`） | **进**：省 ~35s CPU |
| `torch` **首次算子**（conv+backward+Adam 一整步） | **1.76s**（`OMP=1` 实测；第二步 0.005s） | **每个第一次跑 torch 算子的 worker**，各一次 | **进**：16 × 1.76 ≈ **28s CPU** / 预热自付 1.76s ≈ **16× ≥ 3** |
| `source_scan` 整树 sweep（`imports()` + `_module_facts()`，供 `test_layering` 与 30 份 `*_split.py`） | 1.8~2.5s | 摊到 ~16 个 worker，但每个只扫**自己那点**（0.3~1.0s） | **不进**：实测算**更慢**，见下 |

**torch 那项为什么是「进」而不是「加载即完」**：`import torch` 早在收集相就发生了（测试模块
顶层就 import，master 付一次、COW 共享），**贵的不是 import 而是首次算子的内部初始化**
（oneDNN/内存池/算子调度）——它只在**执行相第一次真的算**时发生，所以每个 worker 各付一次。
指纹：`--durations` 下 `test_ckpt_convert` / `test_opt_delta*` 满载 **2.1~2.6s**，同一条用例在
**热进程**里只要 **0.01~0.02s**（100~250×）——这不叫用例慢，叫「谁的第一次 torch 落在了它头上」。
继承性实测（同机同 `OMP=1`）：父进程先算一步 **1.761s** → `os.fork()` 出的孩子再算一步
**0.014s** ⇒ 状态经 COW 传递，预热在 master 付一次、全部 worker 白拿。

**被否决的那一项的实测**（2026-10-29，本机 16 worker，4 组交错 A/B，同一批 30 个
`test_layering` / `*_split.py` 文件，402 例）：预热开着 **6.8 / 8.9 / 9.8 / 6.8s**，关掉
**4.8 / 8.4 / 4.8 / 4.3s** —— 用户 CPU 少了 ~15s（33→18s），**墙钟却稳定慢 ~2s**。根因：
每个 worker 各自只扫自己那条用例需要的文件（0.3~1.0s），摊到 16 个 worker 上与其它用例完全
重叠；而 1.8s 的预热卡在 fork 之前，是**纯串行**的加法。整条门禁 3 组 A/B 同向（同负载窗口
内 +1.2s / ±0.3s）。⇒ 判据写死在模块头，别再凭「它看起来很贵」往清单里加。

**也不进的**：只有**一个** worker 会付的扫描 —— `reuse_audit.scan()`（8s，`test_reuse_ledger`
合并后只剩一条用例）· `text_asserts.totals()`（2.6s，`test_source_text_assert_budget` 一条用例）·
`cli_surface._scan()`（0.37s，`test_cli_house` 一个文件）。它们本来就只付一次，预热只会更慢；
要加速这几份得改用例结构（合并断言 / 缓存派生小结果 / 改扫描实现），不是往这张表里塞。

## 判据的第三列：**本次会话里真的有人用**（2026-11-02，用户指令「Check whether
## `test_forkdist.py::test_timeout_reports_its_real_cause` really needs to wait 5s」）

上表的「划算」算的是**门禁**那套语料（整套 800 个模块）。但对**临时目录里的两三个假用例**
（`test_forkdist.py` 的五个 end-to-end 用例各自落一个 scratch 目录跑真 `-m pytest`），清单里的
每一项都是纯损失 —— 它们一份 torch 算子也不跑、一张 remote_dag 图也不看。实测（`--forkdist 2`
+ 2 条假用例）：预热 **4.58s**（`remote_dag.graph` 1.32 + torch 首次算子 3.26），而
`test_timeout_reports_its_real_cause` 整条 5.18s 里真被测的只有 `--timeout=1` 那一秒。

所以每项自带一个**判据模块**：收集相已把本次会话的测试模块全部 import 过了 ⇒
「用例真会用到 torch / remote_dag」等价于「它已经在 `sys.modules` 里」。不在就跳过并打一行，
行为退回「每 worker 自算」。**注意**：本模块自己不许在顶层 import 被预热的包（否则判据恒真，
`remote_dag` 就是为此改成 thunk 内 import 的）。

## 2026-11-02：判据不再**只靠** `sys.modules`（加源码面）

`sys.modules` 有个隐含前提：「用例会用到 torch」⇒「收集相已经 import 了 torch」。§104 量过，
这个前提只在现在这种「测试模块顶层 import torch」的写法下成立：一旦有人把重导入改懒（或只是
把 `import torch` 挑进函数），预热会在**没任何报错**下静默停掉，而后果是每个 worker 各付一次
`import torch` + 首算子（实测 1.3–1.8s）—— 正是 §100 之前那个格局。

所以现在是**两个信号取或**：

* **源码面**（主）：收集到的测试模块源码里提到 `import torch` / `from torch`（或 `remote_dag`）；
* `sys.modules`（兜）：有人经助手/间接路径把它导进来了。

源码面用 `source_scan.read_text`（已缓存）逐文件子串判断、命中即停，通常头几个文件就 return；
读不到就跳过（判据绝不能把预热弄成红）。

## 安全

* 每一项都必须是**幂等、纯读**的缓存预热（不写盘、不建线程、不碰生产状态）。
* 某一项抛异常 ⇒ 打印一行 WARN 后继续，该扫描退回「每 worker 自算」（= 加预热前的行为），
  绝不让预热把整条门禁带红（用例会在使用点自己报，带完整栈）。
* 预热**不算**进单测耗时预算（预算只统计 call 阶段，见根 conftest 的 `pytest_runtest_makereport`）。
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterable

from tests.helpers import source_scan


def _remote_dag_graph() -> object:
    """`remote_dag.graph()` 的 thunk（**必须**在这里 import：顶层 import 会让下面的判据恒真）。"""
    from tests.helpers import remote_dag

    return remote_dag.graph()


def _torch_first_op() -> object:
    """torch **首次算子**的一整步（conv + backward + Adam），返回 True（纯粹为了让 warm_all 计时）。

    为什么是一整步而不是 `torch.ones(1).sum()`：贵的是算子族的初始化，只碰 `matmul` 未必能
    把 `conv2d`/`autograd`/`optim` 那几处都预热到 —— 预热必须覆盖执行相真正会碰到的第一口
    （用例：`test_ckpt_convert` 建 conv 模型 + 3 步训练）。

    纯内存计算：不写盘、不开线程池（`GATE_THREADS=1` 口径下）、不碰任何生产状态，幂等可重入。
    """
    import torch

    torch.manual_seed(0)
    model = torch.nn.Sequential(
        torch.nn.Conv2d(3, 4, 3, padding=1), torch.nn.Flatten(), torch.nn.Linear(4 * 2 * 2, 3)
    )
    opt = torch.optim.Adam(model.parameters(), lr=3e-4)
    for _ in range(3):
        x = torch.randn(2, 3, 2, 2)
        loss = model(x).pow(2).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    return True

#: 预热清单：`(名字, 无参 thunk, 判据模块, 源码标记)`，顺序即执行顺序。
#: 加一项之前先在模块头的表里写清「冷启动 / 谁在付 / 换算下来划不划算」，
#: 并给出「本次会话里谁用得到它」的两道判据（理由见模块头那两节）。
WARMUPS: tuple[tuple[str, Callable[[], object], str, tuple[str, ...]], ...] = (
    ("remote_dag.graph", _remote_dag_graph, "tests.helpers.remote_dag", ("remote_dag",)),
    ("torch 首次算子", _torch_first_op, "torch", ("import torch", "from torch")),
)


def _session_uses(module: str) -> bool:
    """本次会话的测试有没有真把它 import 进来（收集相已经跑完 ⇒ `sys.modules` 就是答案）。"""
    return any(name == module or name.startswith(module + ".") for name in sys.modules)


def _sources_mention(items: Iterable[object], markers: tuple[str, ...]) -> bool:
    """收集到的测试模块**源码**里提没提这些字面量（命中即停；读不到就当没提）。

    为什么不靠「已经 import 了」：那只在「测试模块顶层 import」的写法下成立（见模块头）。
    """
    for item in items:
        path = getattr(item, "path", None)
        if path is None:
            continue
        try:
            text = source_scan.read_text(str(path))
        except OSError:
            continue
        if any(marker in text for marker in markers):
            return True
    return False


def _needed(needed_by: str, markers: tuple[str, ...], items: Iterable[object]) -> bool:
    """两项取或的判据：源码面提到（主）或已经 import 进来（兜）。"""
    return _sources_mention(items, markers) or _session_uses(needed_by)


def warm_all(items: Iterable[object] = ()) -> None:
    """跑一遍清单，逐项打一行读数（§16.3：长任务每步可观测）。

    `items` = `session.items`：判据要看它们的源码（见 `_needed`）；不传就只看 `sys.modules`。
    """
    total = 0.0
    done = 0
    for name, fn, needed_by, markers in WARMUPS:
        if not _needed(needed_by, markers, items):
            print(f"[warmup] {name} 跳过（本次会话没人用到 {needed_by}）", flush=True)
            continue
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as e:  # 预热是锦上添花：真出错时由使用点的夹具报（带完整栈）
            print(
                f"[warmup] {name} 跳过（{type(e).__name__}: {e}）—— 退回每 worker 自算",
                flush=True,
            )
            continue
        secs = time.perf_counter() - t0
        total += secs
        done += 1
        print(f"[warmup] {name} {secs:.2f}s", flush=True)
    if done:
        print(f"[warmup] 合计 {total:.2f}s（master 付一次，worker 经 COW 共享）", flush=True)
