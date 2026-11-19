"""tests/remote/test_tailscale_boot_bun.py —— 「谁装 bun、装在哪」的边界。

## 沿革（别照旧的改回去）

* **2026-09-25**：两块**在线** worker 盘（`battle.tailscale.ipynb` 走 tailnet、
  `battle.cloudflared.ipynb` 走 cloudflared 公网隧道）**不装 bun** —— 它们当时不跑 rollout，
  不该付「公网下载 + 必须在改代理之前装」的代价。
* **2026-10-21**：**自采模式**（`kind=iter`，该盘自己跑 rollout）下允许，但约束①=复用
  `remote/rollout_node._ensure_bun`，不许另写一份；约束③=守卫随口径一起改。
* **2026-11-18**（用户口径：「两块在线盘环境启动后，需要先安装 bun，以执行 replay 到 npy 的
  转换；需要像 rollout 一样定义安装文件的持久化位置，避免每次新会话都下载」）：`corpus_fmt=replay`
  的 `kind=ppo` job 把 `replay.json` 下给 worker，由 worker 侧的转换器重建 obs/scalars/extra
  （`remote/replay_corpus.resolve_js_runtime`：**Bun 优先**、Node 只作兜底）。两块在线盘正是收
  这类 job 的盘 ⇒ **开机就供给 bun**，落在**持久缓存**里（`<持久根>/.battle-bin/bun`）。

## 钉的边界（错一条都是一个沉默的坑）

1. **一份实现**：在线盘的供给走 `remote/rollout_node.ensure_bun`（⇒ `_ensure_bun` +
   `_bin_cache_dir` 的持久缓存：DSW `/mnt/workspace` · Kaggle `/kaggle/working` ·
   Colab `/content` · 显式 `CFG.bin_cache_dir` / `BATTLE_BIN_CACHE`）。四个在线模块里不许再出现
   安装机制字面量 `bun.sh/install` —— 两份装机必然漂版本。
2. **早于任何 worker 子进程**：`run_notebook` 第一件事就供给；tailscale 的 push 在
   `run_notebook` **之前**就 `spawn_full_worker_server` ⇒ `notebook_boot._push` 自己先供给一次。
   replay 转换在 worker 子进程里跑，而它按 `shutil.which("bun")` 找运行时（PATH 只随 spawn 继承）。
3. **持久位置是数据**：两块盘的 cell 都留 `CFG.bin_cache_dir` 口子（空 = 按平台自动探测）。
4. **`tailscale_boot.ensure()` 仍然不碰 bun**（它只管 tailnet；退役符号不许回来）。
5. **该有 bun 的链仍然有**：`battle.offline.ipynb`（cell 内联）与采样节点（`node_toolchain`）。
"""

from __future__ import annotations

import ast
import inspect
import json
import textwrap
from pathlib import Path

import pytest

from common import proc as common_proc
from remote import (
    node_toolchain,
    notebook_boot,
    notebook_runtime,
    push_bootstrap,
    rollout_node,
    tailscale_boot,
)

NN = Path(__file__).resolve().parents[2]
IPYNB = NN / "ipynb"

#: 「装 bun」这件事的实现标识（出现在谁的地盘上 = 谁就在自己装）。
#: 刻意只认**安装机制**而不是裸词 `bun`：提到 bun / 调用共享装机函数都是 allowed。
INSTALL_MARKERS = ("bun.sh/install",)

#: 在线 worker 的两块盘（各自一条隧道）+ 服务它们的四份源码。
ONLINE_DISKS = ("battle.tailscale.ipynb", "battle.cloudflared.ipynb")
ONLINE_MODULES = {
    "remote/tailscale_boot.py": tailscale_boot,
    "remote/notebook_boot.py": notebook_boot,
    "remote/push_bootstrap.py": push_bootstrap,
    "remote/notebook_runtime.py": notebook_runtime,
}

#: 自己跑 rollout、因而**必须**装 bun 的链。
#:
#: ★ 2026-09-25（并入 `origin/goal-nn` 时实测）：这里原来还列了采样节点（当时叫
#: `rollout.cloudflared.ipynb`）——**那个文件在本仓不存在**，所以那条断言在任何干净检出的机器上
#: 都是 `FileNotFoundError`；故当时只留**真实存在**的盘，并留下约定：「若要回来，得先把
#: notebook 本身入库，再把名字加回这里」。2026-10-01 约定履行（用户裁决，见 `DECISIONS.md`
#: §2026-09-25-goalnn-online-worker-no-bun）。
#: ★ 2026-10-10：cell 拆薄（plan/rollout-node-auto-register v2）——离线盘仍住 cell，
#: 采样节点的 bun 安装搬进 `remote/node_toolchain.py`（`ROLLOUT_MODULES`），cell 里**不许**再有。
ROLLOUT_DISKS = ("battle.offline.ipynb",)
#: 采样节点的 bun 安装 2026-11-18 从 `rollout_node` 拆到 `node_toolchain`（同一份实现，
#: `rollout_node.ensure_bun` 只是薄入口）⇒ 安装机制字面量住那里。
ROLLOUT_MODULES = {"remote/node_toolchain.py": node_toolchain}


def _cell_source(name: str) -> str:
    cells = json.loads((IPYNB / name).read_text(encoding="utf-8"))["cells"]
    return "".join("".join(c.get("source", [])) for c in cells)


def _code_cells(name: str) -> str:
    """只拼 `cell_type == "code"` 的源码（markdown 不是 Python，不能交给 `ast.parse`）。"""
    cells = json.loads((IPYNB / name).read_text(encoding="utf-8"))["cells"]
    return "".join(
        "".join(c.get("source", [])) for c in cells if c.get("cell_type") == "code"
    )


def _cell_cfg_keys(name: str) -> set[str]:
    """cell 里 `CFG = {…}` 字面量的键集合（结构化读；不比措辞）。"""
    for node in ast.walk(ast.parse(_code_cells(name))):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Dict)
            and any(getattr(t, "id", "") == "CFG" for t in node.targets)
        ):
            return {str(k.value) for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


def _dedent(fn) -> str:
    return textwrap.dedent(inspect.getsource(fn))


def _first_calls(fn, names: set[str]) -> dict[str, int]:
    """函数体里各调用**首次出现的行号**（AST 按源码位置排序；不比措辞、不读字符串）。"""
    out: dict[str, int] = {}
    nodes = sorted(
        ast.walk(ast.parse(_dedent(fn))),
        key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)),
    )
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name in names:
            out.setdefault(name, int(getattr(node, "lineno", 0)))
    return out


def _imported_names(fn) -> set[str]:
    """函数体里 `from X import Y` 的 `X.Y` 全名（结构化读 import 节点，不比措辞）。"""
    out: set[str] = set()
    for node in ast.walk(ast.parse(_dedent(fn))):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


# ---------------------------------------------------------------- 边界 1：一份实现

def test_online_modules_reuse_shared_installer_not_a_second_copy() -> None:
    """边界 1：在线盘不许自己实现装机（`bun.sh/install` 只许出现在 rollout_node 那份里）。"""
    hits = [
        f"{path}（{marker}）"
        for path, mod in ONLINE_MODULES.items()
        for marker in INSTALL_MARKERS
        if marker in inspect.getsource(mod)
    ]
    assert hits == [], (
        f"在线 worker 引导链里又出现自建装机：{hits}\n"
        "bun 的安装 + 持久缓存只有一份实现（`remote/rollout_node.ensure_bun` ⇒ `_ensure_bun`），"
        "两块在线盘开机时调它 —— 见本文件抬头边界 1"
    )


def test_online_runtime_provisions_through_rollout_node() -> None:
    """边界 1b：`ensure_replay_bun` 必须 import 的是采样节点那份装机（不是本地重写）。"""
    got = _imported_names(notebook_runtime.ensure_replay_bun)
    assert got >= {"remote.rollout_node.ensure_bun"}, (
        f"在线盘的 bun 供给没有走共享实现（import 到的是 {sorted(got)}）——"
        "DECISIONS §2026-10-21 约束①：复用 `remote/rollout_node._ensure_bun`，不许另写一份"
    )


# ---------------------------------------------------------------- 边界 2：早于 worker spawn

def test_run_notebook_provisions_before_any_worker() -> None:
    """边界 2a：`run_notebook` 里供给必须排在 pull/push runner 之前（spawn 都在它们里面）。"""
    pos = _first_calls(
        notebook_runtime.run_notebook,
        {"ensure_replay_bun", "run_pull_worker", "run_push_worker"},
    )
    assert set(pos) == {"ensure_replay_bun", "run_pull_worker", "run_push_worker"}, (
        f"run_notebook 少了某个关键调用（{sorted(pos)}）—— 供给或 runner 被删/改名了？"
    )
    assert pos["ensure_replay_bun"] < min(pos["run_pull_worker"], pos["run_push_worker"]), (
        "run_notebook 的 bun 供给排在 runner 之后 —— PATH 改晚了，worker 子进程继承不到"
    )


def test_tailscale_push_provisions_before_spawning_serve() -> None:
    """边界 2b：tailscale 的 push 在 `run_notebook` **之前** spawn ⇒ 自己先供给一次。"""
    pos = _first_calls(notebook_boot._push, {"ensure_replay_bun", "spawn_full_worker_server"})
    assert set(pos) == {"ensure_replay_bun", "spawn_full_worker_server"}, (
        f"`_push` 少了供给或 spawn 调用（{sorted(pos)}）"
    )
    assert pos["ensure_replay_bun"] < pos["spawn_full_worker_server"], (
        "notebook_boot._push 先 spawn worker_server 再装 bun —— 那个子进程拿不到 PATH，"
        "replay 转换会回落 Node（甚至两者皆无时响亮失败）"
    )


# ---------------------------------------------------------------- 边界 3：持久位置是数据

def test_online_notebooks_expose_the_persistent_cache_knob() -> None:
    """边界 3：两块盘的 CFG 都要有 `bin_cache_dir`（空 = 按平台持久根自动探测）。"""
    missing = [n for n in ONLINE_DISKS if not (_cell_cfg_keys(n) >= {"bin_cache_dir"})]
    assert missing == [], f"在线 notebook 的 CFG 缺 `bin_cache_dir`（持久缓存口子）：{missing}"


def test_shared_installer_keeps_the_persistent_cache_contract() -> None:
    """边界 3b（行为面）：显式 CFG / env 优先，否则落到平台持久根的 `.battle-bin/`。"""
    assert node_toolchain.bin_cache_dir({"bin_cache_dir": "/cfg/cache"}) == Path("/cfg/cache")
    assert node_toolchain.bin_cache_dir({}, isdir=lambda p: p == "/kaggle/working") == (
        Path("/kaggle/working") / node_toolchain.BIN_CACHE_SUBDIR
    )
    assert node_toolchain.bin_cache_dir({}, isdir=lambda _p: False) is None


def test_notebook_bun_goes_through_the_cache_aware_installer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """边界 3c（行为面）：`ensure_replay_bun` 把 cfg（含持久目录）原样交给共享装机。"""
    calls: list[tuple[dict, Path]] = []

    def fake_ensure_bun(code_dir: Path, cfg: dict, log) -> str:
        calls.append((dict(cfg), Path(code_dir)))
        return str(Path(cfg["bin_cache_dir"]) / "bun")

    monkeypatch.setattr(rollout_node, "_ensure_bun", fake_ensure_bun)
    monkeypatch.setattr(common_proc, "bun_version", lambda *a, **k: "1.4.4")
    cache = tmp_path / "cache"
    code = tmp_path / "code"
    logs: list[str] = []
    got = notebook_runtime.ensure_replay_bun(
        {"code_dir": str(code), "bin_cache_dir": str(cache)}, logs.append
    )
    assert got == str(cache / "bun")
    assert len(calls) == 1
    seen_cfg, seen_code = calls[0]
    assert seen_cfg.get("bin_cache_dir") == str(cache), (
        "共享装机收不到持久缓存目录 —— `CFG.bin_cache_dir` 在转交路上丢了"
    )
    assert seen_code == code, "共享装机收到的代码根不对（`bun install` 会落到错的地方）"


def test_ensure_replay_bun_is_never_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """边界 3d：没有代码根 / 装不上都只是「回落 node」，**不许**拖垮 worker 启动。"""
    calls: list[str] = []

    def empty_ensure(code_dir: Path, cfg: dict, log) -> str:
        calls.append("x")
        return ""

    monkeypatch.setattr(rollout_node, "_ensure_bun", empty_ensure)
    logs: list[str] = []
    assert notebook_runtime.ensure_replay_bun({}, logs.append) == ""
    assert calls == [], "没有代码根的宿主（单测/嵌入）不该去装 bun"
    assert notebook_runtime.ensure_replay_bun(
        {"code_dir": str(tmp_path / "c")}, logs.append
    ) == ""
    assert calls == ["x"]

    def boom(code_dir: Path, cfg: dict, log) -> str:
        raise OSError("network down")

    monkeypatch.setattr(rollout_node, "_ensure_bun", boom)
    assert notebook_runtime.ensure_replay_bun({"code_dir": str(tmp_path / "c")}, logs.append) == ""


# ---------------------------------------------------------------- 边界 4：tailscale_boot 不碰 bun

def test_ensure_never_touches_bun() -> None:
    """边界 4a：`ensure()` 里连「装 bun」的名字都不该有（注释可以谈 bun）。"""
    tree = ast.parse(_dedent(tailscale_boot.ensure))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert called.isdisjoint({"ensure_bun"}), "ensure() 不许装 bun（它只管 tailnet）"
    strings = [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    assert not any("bun.sh" in s for s in strings), "ensure() 里不许有任何 bun 安装机制"


def test_retired_bun_helpers_do_not_come_back() -> None:
    """边界 4b：退役就是退役 —— 三个符号不许回来（无消费者，且回来就会有人再调它）。"""
    back = [
        n
        for n in ("ensure_bun", "bun_path", "BUN_INSTALL_URL")
        if hasattr(tailscale_boot, n)
    ]
    assert back == [], (
        f"`remote/tailscale_boot.py` 又暴露了退役的 bun 符号：{back}\n"
        "（它们 2026-09-25 随「在线盘不自己装 bun」一并退役；共享装机在 `remote/rollout_node.py`）"
    )


# ---------------------------------------------------------------- 边界 5：该有的链仍然有

def test_sampling_node_installs_bun_in_module_not_cell() -> None:
    """边界 5a：采样节点 cell 拆薄后，bun 安装只许住 `remote/rollout_node.py`。

    安装必须在 **tailscale 引导之前**（或包 `platform_net_env()`）——`ensure` 落到 userspace
    时会改写 HTTP_PROXY，之后公网 curl 走不通（2026-09-17 事故同族）。
    """
    hits = [m for m in INSTALL_MARKERS if m in _cell_source("rollout.ipynb")]
    assert hits == [], (
        "采样节点 cell 又内联装 bun —— 应住 remote/rollout_node.py（cell 只留 CFG/凭据/保活/clone/调用）"
    )


def test_rollout_chains_still_provide_bun() -> None:
    """边界 5b（反向）：该有 bun 的链必须还有 —— 只删不加等于把「谁需要 bun」做没。

    `battle.offline.ipynb` = Kaggle/TPU 上的离线盘（Kaggle 不给 tailnet ⇒ 只能走 cloudflared
    隧道进 hub），它自己跑 rollout，安装必须留在 cell 里；
    采样节点的安装住 `remote/node_toolchain.py`（见 `ROLLOUT_MODULES`）。
    """
    missing = [
        n for n in ROLLOUT_DISKS if not any(m in _cell_source(n) for m in INSTALL_MARKERS)
    ]
    missing += [
        path
        for path, mod in ROLLOUT_MODULES.items()
        if not any(m in inspect.getsource(mod) for m in INSTALL_MARKERS)
    ]
    assert missing == [], (
        f"跑 rollout 的链不再装 bun：{missing}\n"
        "它们的 rollout 是节点侧 TS（bun 跑），安装不能删 —— 见本文件抬头边界 5"
    )
