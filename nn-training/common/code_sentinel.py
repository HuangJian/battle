"""common/code_sentinel.py —— 代码树的**导入哨兵**（打包侧的唯一实现）。

为什么有这个东西（plan/offline-code-authority-and-width B3/B4，2026-10-14 现场）：
`code.zip` 是**工作树快照**（`remote/hub_client.pack_code_zip` 用 `os.walk` 扫 `.py`/`.jsonc`，
不是 `git archive HEAD`），工作树停在「`worker/ppo/np_core.py` 已改 / `common/shard.py` 未改」
这种半提交中间态时，产出的 zip **内部不自洽**；而坏代码直到云端 `remote/train_core` 里
`import worker.ppo.engine` 才爆 —— rollout 首批 + 全部补差批已经白跑，账本无行。
把「这份树能不能 import」变成打包与引导两处都能问的**一次确定判断**，事故就变成一行日志。

判据（为什么是这三个模块；实测见 plan §4-B3）：

  * `common.shard`        —— 出事那条边的**下游**（np_core 从它 import `METRICS_NAME`）；
  * `worker.ppo.np_core`  —— 出事的**上游**（顶层 `from common.shard import METRICS_NAME`）；
  * `remote.run_loop`     —— 离线引导真正要跑的那条链的入口。

★ **刻意不含 `worker.ppo.common`**：它顶层 `import torch`（实测 ~2s / `torch` 进 `sys.modules`），
无 torch 的机器上会把「环境缺件」误判成「代码候选坏」⇒ 试遍候选 ⇒ 拒跑。上面三个模块全是
torch-free 的（实测 <0.4s）。

失败**分类**（评审 P0-3 的硬要求：缺第三方 ≠ 代码坏）：

  * `kind="env"`  —— 最终异常是 `ModuleNotFoundError` 且缺的顶层名不在本仓（缺 numpy/torch…）：
    调用方应**放行 + WARN**（换候选也换不来一个 numpy）；
  * `kind="code"` —— 其余（本仓模块 ImportError/AttributeError/SyntaxError、哨兵超时…）：
    调用方应**换候选 / 拒跑**。

`remote/offline_boot.py` 是**自包含引导模块**（raw GitHub 单文件拉取，跑在「包到手之前」），
它按本仓既有的「抄一份 + 用例钉同字」模式内联了一份同形实现（`_run_import_sentinel`）；
`tests/remote/test_code_sentinel.py` 把两份实现放在同一批树上对账，防漂移。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from common.proc import run_capture

__all__ = [
    "CODE_META_NAME",
    "GIT_TIMEOUT_SEC",
    "REPO_TOPS",
    "SENTINEL_MODULES",
    "SENTINEL_TIMEOUT_SEC",
    "code_tree_provenance",
    "run_import_sentinel",
]

#: 代码快照的 provenance 件名（B2，2026-10-14）：随 `code.zip` 进包，回答「这份代码从哪儿来、
#: 全不全」。★ **只放由树内容唯一决定的字段** —— 打包端固定时间戳那条不变量的另一半：
#: 挥发字段（时间戳/绝对路径）一进 zip，`code_cache/<sha>` 就每次失配。
CODE_META_NAME = "code_meta.jsonc"

#: git 探测超时（仓库异常大时 `status` 会慢）；超时/没有 git ⇒ 当作「没有仓」，不挡打包。
GIT_TIMEOUT_SEC = 30.0

#: 代表模块清单（见模块 docstring；改这里必须同时改 `remote/offline_boot.py` 的那份 —— 用例钉）。
SENTINEL_MODULES: tuple[str, ...] = ("common.shard", "worker.ppo.np_core", "remote.run_loop")

#: 子进程墙钟上限：冷机器上 import numpy 也在秒级，60s 只是「挂住」的兜底（有界 > 无界）。
SENTINEL_TIMEOUT_SEC = 60.0

#: 本仓**自己**的顶层包名（`ModuleNotFoundError` 缺到这些 = 代码树不自洽，不是环境缺件）。
#: 与 `tests/helpers/remote_dag.project_roots` 同口径；写死是为了不 import 测试面。
REPO_TOPS: tuple[str, ...] = ("biz", "common", "remote", "worker")

#: 子进程正文：逐个 import，失败时把**分类**打成一行 JSON（父进程只解析它，不看 stderr 猜）。
#: 为什么分类在子进程做：只有它手里有活的异常对象（`ModuleNotFoundError.name` 比 stderr 文本准）。
_CHILD_SCRIPT = (
    "import importlib, json, os, sys, traceback\n"
    "mods = [m for m in os.environ.get('BCITY_SENTINEL_MODULES', '').split(',') if m]\n"
    "tops = {t for t in os.environ.get('BCITY_SENTINEL_REPO_TOPS', '').split(',') if t}\n"
    "for m in mods:\n"
    "    try:\n"
    "        importlib.import_module(m)\n"
    "    except BaseException as e:\n"
    "        tb = traceback.format_exc().strip().splitlines()[-4:]\n"
    "        is_mnf = isinstance(e, ModuleNotFoundError)\n"
    "        miss = str(getattr(e, 'name', '') or '').split('.')[0] if is_mnf else ''\n"
    "        kind = 'env' if (miss and miss not in tops) else 'code'\n"
    "        sys.stdout.write(json.dumps({'ok': False, 'kind': kind, 'missing': miss,\n"
    "                                     'exc': type(e).__name__, 'at': m, 'tb': tb}) + '\\n')\n"
    "        raise SystemExit(3)\n"
    "sys.stdout.write(json.dumps({'ok': True}) + '\\n')\n"
)


def _git_out(nn_root: str | Path, *args: str) -> str | None:
    """跑一条 git **只读**命令的 stdout（rc≠0 / 没有 git / 超时 ⇒ None，永不抛）。"""
    try:
        proc = run_capture(["git", "-C", str(nn_root), *args], timeout=GIT_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def code_tree_provenance(nn_root: str | Path, log=None) -> dict:  # log 与 pack_code_zip 同形（不引类型）
    """`code_meta.jsonc` 的实体：`{commit, dirty, dirty_files, import_ok}`（B2/B4）。

    * `commit` / `dirty` / `dirty_files` —— `git rev-parse HEAD` + `git status --porcelain -- .`。
      这是「快照 = 工作树」这件事的**体检**：混血树（`np_core.py` 已改 / `shard.py` 未改）的指纹
      就是 `dirty=true` + 两份文件同在一个清单里。**没有仓 / 没有 git / 超时 ⇒ 三项留空，不 WARN**
      （打包端刻意支持「没有仓也能打」——`tmp/.code-snapshot` 的所有者可能是任意进程）。
      `dirty_files` **排序**后才写，且走 git 默认口径（未跟踪目录折叠成一条，不 `-uall`）：
      `-uall` 会把 `.venv310bak/` 那种上万个文件展开进 meta 撑爆它；真正的干活判据是
      「被改的**已跟踪**文件」（现场那对 np_core/shard 正是这一类，逐个列出）。
    * `import_ok` —— 导入哨兵（`run_import_sentinel`，实测 <0.4s、torch-free）的结论。
      **只记录事实、不挡打包**：用户工作流是边改边跑（plan §6「打包端硬拒脏树」已否决）——
      脏包在**导出那一刻**就被 WARN 出来，比等云机跑到 `train_core` 里 `import worker.ppo.engine`
      才爆（rollout 全白跑、账本无行、kernel 继续计时）便宜得多。
    """
    commit = (_git_out(nn_root, "rev-parse", "HEAD") or "").strip()
    files: list[str] = []
    for line in (_git_out(nn_root, "status", "--porcelain", "--", ".") or "").splitlines():
        rel = line[3:].strip() if len(line) > 3 else ""
        if " -> " in rel:  # 重命名/复制：取新名
            rel = rel.split(" -> ", 1)[1]
        if rel:
            files.append(rel.strip('"'))
    files = sorted(files)
    dirty = bool(files)
    if log and dirty:
        head_txt = ", ".join(files[:5]) + ("…" if len(files) > 5 else "")
        log(f"WARN code.zip 出自脏工作树（{len(files)} 个未提交改动: {head_txt}）——快照可能内部不自洽")
    ok, kind, detail = run_import_sentinel(nn_root)
    if log and not ok:
        why = "环境缺件（非代码问题）" if kind == "env" else "这份代码树内部不自洽（云端会在训练前拒跑）"
        log(f"WARN code.zip 的导入哨兵未通过（{kind}）: {detail} —— {why}")
    return {"commit": commit, "dirty": dirty, "dirty_files": files, "import_ok": ok}


def run_import_sentinel(
    code_dir: str | Path,
    *,
    modules: tuple[str, ...] = SENTINEL_MODULES,
    timeout: float = SENTINEL_TIMEOUT_SEC,
    python: str | None = None,
) -> tuple[bool, str, str]:
    """在**有界子进程**里逐个 import `modules`（`PYTHONPATH=code_dir`）⇒ `(ok, kind, detail)`。

    为什么必须是子进程：① 隔离 —— 引导端（和 hub 端）自己的解释器 `sys.modules` 里可能已经
    装了别的版本的仓库模块，进程内 import 量的是**那份**、不是候选树；② 有界 ——
    `subprocess.run(timeout=…)` 能连树杀，进程内 import 没有取消手段。
    `detail` 是给人读的一手证据（异常类型 + 落点 + stderr 尾），进日志/`code_meta.jsonc` 的
    只有 `import_ok` 这个布尔（细节含绝对路径，进包会破坏「同源码同 sha」）。
    """
    tree = Path(code_dir)
    env = dict(os.environ)
    prev = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = str(tree) + ((os.pathsep + prev) if prev else "")
    env["BCITY_SENTINEL_MODULES"] = ",".join(modules)
    env["BCITY_SENTINEL_REPO_TOPS"] = ",".join(REPO_TOPS)
    try:
        # ★ 走 `common.proc.run_capture`（不是裸 `subprocess.run`）——2026-10-17 用户第三次报障
        # 「启动 hub 时黑窗一闪而过」的真身就在这里：哨兵跑的是 `python -c`（console 应用），
        # 而它的父进程（控制台起的 hub / 打包快照的那条链）**没有控制台**
        # （`spawnBg` 的 detached + windowsHide）⇒ Windows 会给这个子进程**新建一块可见控制台**。
        # 本文件同族的 `_git_head` 早就走 `run_capture` 了（details §20），漏的就是这一处。
        # `run_capture` 的形参与原来的裸调用**逐项等价**（capture_output + text + utf-8/replace +
        # timeout），所以这不是「顺手重构」，是收敛到唯一原语：旗标在那一份实现里，漏不掉。
        proc = run_capture(
            [python or sys.executable, "-c", _CHILD_SCRIPT],
            cwd=str(tree) if tree.is_dir() else None,
            env=env,
            timeout=float(timeout),
        )
    except subprocess.TimeoutExpired:
        # 挂住 ⇒ 这份树不能信任（换候选），**不是**环境缺件
        return False, "code", f"哨兵超时（>{timeout}s）"
    except OSError as e:
        # 起不了子进程 = 环境问题：换候选也换不来一个可用的解释器
        return False, "env", f"起不了哨兵子进程（{type(e).__name__}: {e}）"
    line = ""
    for s in (proc.stdout or "").splitlines():
        if s.startswith("{"):
            line = s
    res: dict = {}
    if line:
        try:
            got = json.loads(line)
        except ValueError:
            got = None
        if isinstance(got, dict):
            res = got
    if proc.returncode == 0 and res.get("ok"):
        return True, "", ""
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
    if res:
        kind = "env" if res.get("kind") == "env" else "code"
        miss = str(res.get("missing") or "")
        detail = f"{res.get('exc', '?')} @ {res.get('at', '?')}" + (f"（缺 {miss}）" if miss else "")
        tb = res.get("tb")
        if isinstance(tb, list) and tb:
            detail += "｜" + " / ".join(str(x) for x in tb)
        elif tail:
            detail += "｜" + " / ".join(tail)
        return False, kind, detail
    return False, "code", "｜".join(tail) or f"exit={proc.returncode}"
