"""
platform_utils.py — 跨平台进程/文件工具（工程化抽取，行为零变化）。

历史：train_bc.py / train_loop.py / run_rl.py / run_rl_intent.py / rl/eval_dispatch.py /
rl/queue.py 各自维护了一份逐字节相同的 `_POPEN_NO_WINDOW`（Windows 下隐藏子进程
控制台窗口，避免黑窗弹窗抢焦点）。本模块统一这一份，其余文件改 import。

导出：
  POPEN_NO_WINDOW —— subprocess.run/Popen 的额外 kwargs（Windows 下含
    creationflags=CREATE_NO_WINDOW；非 Windows 为空 dict）。
  popen_kwargs(**extra) —— 便捷包装：返回 {**POPEN_NO_WINDOW, **extra}。
  rmtree_best_effort(path, ignore_errors=False) —— 递归删除目录，**沙箱删除保护
    （SystemExit）绝不外泄**；替代裸 shutil.rmtree / ignore_errors=True。
  sandbox_delete_blocked(anchor) —— 探针：当前是否正被沙箱删除保护拦截真实删除
    （门禁抖动归因用，见 docstring）。
  force_utf8_stdio() —— CLI 入口调用：把本进程 stdout/stderr 运行时钉成 UTF-8
    （压过 PYTHONIOENCODING / PYTHONUTF8 / 控制台代码页；详见 docstring）。
  effective_cores() —— 本进程**真正能用**的核数（容器配额/亲和掩码 > os.cpu_count()）。
  cpu_worker_slots(cores=None) —— 本机 CPU 并行槽的**唯一口径**（见 docstring）：
    rollout 与 eval 都用它，谁都不为对方预留核数。
  popen_own_group(**extra) —— **自带进程组**的子进程 kwargs（POSIX 的 start_new_session）。
  kill_process_tree(proc) —— SIGKILL 掉整个进程组（连孤儿一起）；**不等回收**。
  reap_bounded(proc, timeout) —— 有界回收（waitpid）；超时返回 False，**绝不无限等**。
  KILL_REAP_SEC —— 回收预算的唯一数字（kill 之后最多等这么久）。
  keep_unreaped(proc) / sweep_unreaped() / unreaped_count() —— 收不了尸的进程记账。
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from typing import Any

# Windows：spawn 子进程时用 CREATE_NO_WINDOW，避免黑控制台窗口弹出抢焦点。
POPEN_NO_WINDOW: dict[str, Any] = {}
if sys.platform == "win32":
    POPEN_NO_WINDOW = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def force_utf8_stdio() -> None:
    """CLI 入口调用：把本进程 stdout/stderr 运行时钉成 UTF-8。

    为什么（2026-09-13 python-cli 编码问题复核）：被捕获的子进程字节流此前取决于
    启动环境的 locale——coding agent 沙箱间 PYTHONUTF8 / PYTHONIOENCODING 各异、
    zh-CN Windows 默认 cp936、Python 3.15 起（PEP 686）又默认 UTF-8。父进程的
    subprocess 解码默认值是启动期决定的、运行时改不了 ⇒ 唯一通用的做法是把「子进程
    输出什么编码」在子进程自己的入口处钉死：reconfigure 运行时覆盖 stdio 包装器，
    压过一切环境变量。消费方（测试 ``tests/subproc_util.run_utf8`` / agent）按
    utf-8 显式解码即可，无需任何环境变量协调。3.7+；3.15 下与运行时默认一致（no-op）。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 非 TextIOWrapper（被捕获替换等）——保持现状，打印不该因此崩溃
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass  # 流已关闭/底层不可重配——同上，尽力而为


def rmtree_best_effort(path: Any, *, ignore_errors: bool = False) -> bool:
    """递归删除 ``path``；返回值 True=已删或本就不存在，False=未删掉。

    **为什么不能直接用 shutil.rmtree(..., ignore_errors=True)**（2026-09-10 门禁事故）：
    本沙箱的 WorkBuddy 删除保护 shim 把 ``shutil.rmtree`` 改道回收站，批量删除守卫
    触发时 ``raise SystemExit(1)`` —— 那是 ``BaseException``，**不是** ``Exception``，
    所以 ``ignore_errors=True`` 完全挡不住它，调用线程会当场被打死。
    实证：门禁两条用例同源失败——``rl/dispatch.py`` 的派发线程在 shard 清理处被
    SystemExit 打死，此后该线程不再派发任何任务，于是「慢任务仍 in-flight 时被空闲槽
    重赛」的竞态日志永远不出现，``test_it_early_race_v314`` 的 raced 断言随之失败。

    语义：
      * ``ignore_errors=False``（默认）：真的删不掉（OSError 等）照旧抛出 —— 与
        ``shutil.rmtree`` 一致，只额外吞掉删除保护的 SystemExit。
      * ``ignore_errors=True``：任何异常都不外泄（等价 shutil 的 ignore_errors，
        外加 SystemExit 防护）。
      * ``KeyboardInterrupt`` 一律照常传播。

    所有清理路径都应当走本函数：删不掉只是泄漏一个临时目录，绝不该杀掉线程或训练。
    """
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        return True
    except SystemExit:
        # 沙箱删除保护拦截：目录保留，调用方按 best-effort 继续。
        return False
    except Exception:
        if ignore_errors:
            return False
        raise
    return True


def sandbox_delete_blocked(anchor: Any) -> bool:
    """探针：当前环境是否正在拦截真实删除（沙箱 safe-delete 配额耗尽）。

    2026-09-12 门禁抖动根因：WorkBuddy safe-delete shim 按 turn 计批量删除配额
    （阈值 50，见 tools/githook/nn-python-gate.sh 头注）；同一会话反复跑全量门禁
    必然踩满，此后所有真实删除被拒并抛 SystemExit。rmtree_best_effort 把它转成
    False（best-effort），于是「断言删除落地」的测试（test_workdir_sweep /
    prune_job_dirs）转红——单跑（配额新鲜）又变绿，表现为偶发（实证：
    tmp/pre-commit-nn-*.log，assert 1 == 2 + [safe-delete] 行）。

    约定：**仅在删除断言已失败的路径调用**（绿路径零开销、零配额消耗）。在 anchor
    下建一个探针目录并用裸 shutil.rmtree 删除——SystemExit = 正在被拦 → True；
    删得掉 / OSError → False（失败是真回归，调用方测试应继续红）。必须用裸
    rmtree：rmtree_best_effort 会吞掉 SystemExit，探针就失灵了。
    """
    from pathlib import Path

    probe = Path(anchor) / "_shim_probe"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(probe)
    except SystemExit:
        return True
    except Exception:
        return False
    return False


#: CPU 并行槽的预留核数（给补传/日志/守护线程这类零碎常驻任务）。
#: 大机器上真正生效的是下面 20% 那一支——留 4 核就够这些线程跑。
CPU_RESERVE = 4

#: 容器 CPU 配额的 cgroup 文件（v2 优先；v1 兜底）。换算成核数见 `cgroup_cpu_quota`。
_CGROUP_V2_CPU_MAX = "/sys/fs/cgroup/cpu.max"
_CGROUP_V1_QUOTA = "/sys/fs/cgroup/cpu/cpu.cfs_quota_us"
_CGROUP_V1_PERIOD = "/sys/fs/cgroup/cpu/cpu.cfs_period_us"
#: cgroup v1 没写 period 时的约定缺省（内核文档：默认 100ms ⇒ 100000µs）。
_CGROUP_V1_PERIOD_DEFAULT = 100_000


def _read_text(path: str) -> str | None:
    """读一个内核伪文件；读不到/无权限 ⇒ None（诊断用途，绝不抛）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def cgroup_cpu_quota() -> int | None:
    """容器 cgroup 允许的 CPU 核数（quota÷period，向上取整）；不限/读不到 ⇒ None。

    为什么需要它（2026-09-25 云机卡死）：容器里的 `os.cpu_count()` 报的是**宿主机**的逻辑
    核数，不是配额。Kaggle 的 TPU 会话实测报 224 而 cgroup 只给 96 核 ⇒ 按 224 算并发就是
    2.3× 超订（"把 96 核误读为 224 核"），单局墙钟直接被推过 5s 硬顶。
    v2：`/sys/fs/cgroup/cpu.max`（`"9600000 100000"`；`"max 100000"` = 不限）；
    v1：`cpu.cfs_quota_us`（`-1` = 不限）÷ `cpu.cfs_period_us`。
    """
    raw = _read_text(_CGROUP_V2_CPU_MAX)
    if raw:
        parts = raw.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
            except ValueError:
                pass
            else:
                if quota > 0 and period > 0:
                    return max(1, -(-quota // period))  # 向上取整
    try:
        quota = int(_read_text(_CGROUP_V1_QUOTA) or "0")
        period = int(_read_text(_CGROUP_V1_PERIOD) or str(_CGROUP_V1_PERIOD_DEFAULT))
    except ValueError:
        return None
    if quota > 0 and period > 0:
        return max(1, -(-quota // period))
    return None


def affinity_cores() -> int | None:
    """进程亲和掩码允许的核数（Linux `sched_getaffinity`）；无此接口/取不到 ⇒ None。

    与 cgroup 配额是**两个不同的事实**（cpuset 绑核 vs 配额限流），任一比 `os.cpu_count()` 小
    都说明这台机器给不了那么多核 ⇒ `effective_cores` 取两者的小值。
    """
    fn = getattr(os, "sched_getaffinity", None)
    if fn is None:
        return None
    try:
        return len(fn(0)) or None
    except (OSError, AttributeError, NotImplementedError, TypeError):
        return None


def effective_cores() -> int:
    """本进程**真正能用**的核数（单一口径）：容器配额与亲和掩码取小，都没有才回 `os.cpu_count()`。

    这三个来源的优先级不是风格问题（见 `cgroup_cpu_quota` 的 224/96 事故）：
    `os.cpu_count()` 在容器里报宿主机的核数 —— 它是**最不可信**的一个，只能当最后的兜底
    （Windows、裸机、无 cgroup 的容器）。
    """
    candidates = [c for c in (cgroup_cpu_quota(), affinity_cores()) if c]
    if candidates:
        return max(1, min(candidates))
    return max(1, int(os.cpu_count() or 1))


def cpu_worker_slots(cores: int | None = None) -> int:
    """本机该开几个 CPU 并行槽：``max(cores − 4, floor(cores × 0.8))``（至少 1）。

    `cores` 缺省走 `effective_cores()`（容器配额/亲和掩码 > `os.cpu_count()`）——**按物理数目**
    算，不按宿主机报出来的大数字算（详见 `cgroup_cpu_quota` 的 224/96 事故）。

    **唯一口径**（用户 2026-09-22）：「rollout 和 eval 是交替进行的，所以不应该为 eval 保留
    CPU 核数——两者都使用 max(cores − 4, cores × 0.8)，只要留两三个核给数据回传任务就够」。

    为什么不再「按对方留位」：云机离线段里 rollout 与 eval（以及 PPO）**本该是交替的**，
    给 eval 扣掉 rollout 的并行度等于两次扣同一份钱——两边都按本函数满配，谁在跑谁就用满。
    真正需要一直活着的只有补传线程/日志/守护，两三个核（大机器上 20% 的那一支还会多留一些）
    绰绰有余。

    ⚠ **前提得靠排程真正成立**（2026-09-25 云机卡死）：此前 eval 的提交点与下一轮 rollout
    的开跑点是同一个瞬间 ⇒ 两条腿同时各开满一份（96 核配额上 220+220，而那个 220 本身就是
    把 224 核的宿主机读数当成了配额），2× 超订把单局墙钟推过 5s 硬顶 ⇒ 成批超时 + 池回退
    放大 ⇒ 整轮停摆。所以「交替」现在是代码保证的（`remote/run_loop._maybe_cloud_eval`
    提交后**有界等**本轮评估收线），核数也走容器口径（`effective_cores`），而不是靠注释假设；
    同一份公式只在那个前提下才对。

    参照：96 vCPU 的 Kaggle TPU 会话 ⇒ 92（旧口径：先扣 rollout 再卡 64 = 白扔三成）；
    16 核 ⇒ 12（留 4）；8 核 ⇒ 6（留 2）。显式传 ``--eval-slots`` / ``--rollout-workers``
    仍然完全照用户给的数走（本函数只管缺省）。
    """
    n = max(1, int(cores if cores is not None else effective_cores()))
    return max(1, min(n, max(n - CPU_RESERVE, int(n * 0.8))))


def popen_kwargs(**extra: Any) -> dict[str, Any]:
    """subprocess 调用 kwargs：始终带上无窗口 flags，并合并调用方参数。

    ``subprocess.run(cmd, ..., **popen_kwargs(capture_output=True))`` 等价于旧的
    ``subprocess.run(cmd, ..., **_POPEN_NO_WINDOW, capture_output=True)``。
    """
    return {**POPEN_NO_WINDOW, **extra}


# --------------------------------------------------------------- 子进程回收（kill / reap）

#: kill 之后回收（waitpid）的**统一预算**（秒）：所有「杀了就得等它死」的路径都用这一个数。
#:
#: 为什么必须是个**有上限**的数（它取代的是裸 `p.wait()`）：见 `reap_bounded` —— 云机上那条
#: 「rollout 卡死机器半天」的现场就是一条线程永远停在 `waitpid` 上（一条线程卡住 = 整轮收不齐）。
KILL_REAP_SEC = 5.0

#: 杀不掉也收不了尸的子进程（SIGKILL 之后仍卡在不可中断的 IO 里）——留着，之后非阻塞地
#: 再碰一次（它们可能已经退出了）。见 `reap_bounded` 的 docstring 与 `keep_unreaped`。
_UNREAPED: list[Any] = []


def popen_own_group(**extra: Any) -> dict[str, Any]:
    """起一个**自带进程组**的子进程的 kwargs（= `popen_kwargs` + POSIX 的 start_new_session）。

    为什么要有它（2026-09-25 云机二次取证）：
      ① 只有自带进程组才敢 `killpg` —— 否则 `killpg(pid)` 打的是**我们自己**的进程组；
      ② 被池化/逐局起的 `bun` 自己还会带子进程（编译缓存 worker、子工具）——只杀进程本身
         会留下**孤儿**继续吃 CPU/内存，机器越跑越卡，而日志里只看得见「我们 kill 过它」。
    Windows 没有这套语义（连 `start_new_session` 都不存在）⇒ 退回 `popen_kwargs`（单进程 kill）。
    """
    if os.name == "posix":
        return popen_kwargs(start_new_session=True, **extra)
    return popen_kwargs(**extra)


def kill_process_tree(proc: Any) -> None:
    """SIGKILL 掉 `proc` 与其**整个进程组**（POSIX）/ 单进程（Windows）。**不等回收**。

    前置：`proc` 是用 `popen_own_group()` 起的。不是的话退回单进程 `kill()` —— `killpg(pid)`
    在「pid 不是组长」时是 ESRCH，绝不能拿它去赌（赌错就是打死自己的进程组）。
    刻意不等回收：kill 本身是异步的，在这里等就把它变成了阻塞点 —— 要等就 `reap_bounded`，
    而那里必须有上限（见它）。
    """
    # `os.killpg`/`signal.SIGKILL` 在 win32 的 typeshed stubs 里不存在 ⇒ 取属性（运行时判定），
    # 不用 `# type: ignore` 消音：那样在真 POSIX 上写错名字也不会有人发现。
    killpg = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if os.name == "posix" and killpg is not None and sigkill is not None:
        try:
            killpg(proc.pid, sigkill)
            return
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def reap_bounded(proc: Any, timeout: float) -> bool:
    """**有界**回收一个已经被 kill 的子进程：True = 确认收尸，False = 超时（它可能还活着）。

    为什么必须有上限（2026-09-25 云机二次取证「rollout 卡死机器半天」）：`kill()` 只是把信号
    递进去；子进程若正卡在**不可中断**的 IO（D 状态 —— 云机上被挂住的挂载点/慢盘就是这样）
    里，要等那个系统调用返回才真的死。旧代码是 `p.kill(); p.wait()`，**没有上限** ⇒ 那一局的
    线程停在 `waitpid` 上，而日志里什么都看不出来（超时行里的 elapsed 是 kill **之前**算的，
    照样写着「5.0s」）。现场读数：两条线程各卡 ~890s、轮内进度从 5s 的 270/336 一动不动，
    整轮（以及整台机器）就这么静默挂住。
    """
    try:
        proc.wait(timeout=float(timeout))
        return True
    except subprocess.TimeoutExpired:
        return False


def keep_unreaped(proc: Any) -> None:
    """记下一个「杀不掉也收不了尸」的子进程（之后由 `sweep_unreaped` 再试一次）。"""
    _UNREAPED.append(proc)


def sweep_unreaped() -> int:
    """非阻塞地再碰一次之前收不了尸的子进程：返回这一趟**真的收掉**的个数。

    只问 `poll()`（零等待）：它们可能早就退出了，只是当时没等到；这里顺手收尸，避免僵尸进程
    长期占着 pid/句柄（机器本来就已经被一堆进程压着）。
    """
    done = 0
    for p in list(_UNREAPED):
        try:
            if p.poll() is not None:
                _UNREAPED.remove(p)
                done += 1
        except Exception:  # 收尸是尽力而为：绝不能因此让调用方失败
            _UNREAPED.remove(p)
    return done


def unreaped_count() -> int:
    """还没收掉的「僵尸候选」个数（诊断与用例用）。"""
    return len(_UNREAPED)
