"""tests/remote/test_tailscale_boot_bun.py —— 在线 worker 盘**不装 bun**、不跑 rollout（2026-09-25 重裁）。

**历史（别照它改回）**：曾有一版让 `tailscale_boot.ensure()` 在装 tailnet 之前 `ensure_bun()` ——
那是照着「在线腿收到 kind=iter ⇒ 缺 bun 就地装」修的。**那个结论是错的**：
两块在线盘（`battle.tailscale.ipynb` 走 **tailnet**、`battle.cloudflared.ipynb` 走 **cloudflared
公网隧道**）服务不同云机网络环境，**都不跑 rollout**，本来就不该收到 `kind=iter`，也就不该为 bun
付「公网 curl + 必须在改代理之前装」的代价（那条顺序硬约束是为了让一件本不需要的事能成）。
旧修法已退休：`BUN_INSTALL_URL` / `bun_path()` / `ensure_bun()` 三处一并删掉（无消费者）。

钉三条边界（错一条都是一个沉默的坑）：
  1. **在线 worker 引导链里没有任何 bun 安装**：`tailscale_boot` / `notebook_boot` /
     `push_bootstrap` / `notebook_runtime` 四份源码，加两个在线 ipynb 的 cell 全文；
  2. 退役的三个符号**不许回来** —— 谁要再给在线盘加 bun，先回来读这段；
  3. **该有 bun 的链仍然有**：`battle.offline.ipynb`（Kaggle/TPU，Kaggle 不给 tailnet ⇒ 只能走
     cloudflared 隧道，它自己跑 rollout）的 cell 里必须还装着 bun；**采样节点**
     （`rollout.ipynb`，2026-10-10 由 `rollout.cloudflared.ipynb` 改名、cell 拆薄）的 bun 安装
     随逻辑住 `remote/rollout_node.py`，且**不许内联回 cell** —— 只删不加，会把「谁需要 bun」
     这件事做没。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from remote import notebook_boot, notebook_runtime, push_bootstrap, rollout_node, tailscale_boot

NN = Path(__file__).resolve().parents[2]
IPYNB = NN / "ipynb"

#: 「装 bun」这件事的实现标识（出现在谁的地盘上 = 谁就在装 bun）。
#: 刻意只认**安装机制**而不是裸词 `bun`：退役注记/文档里提到 bun 是 allowed，装它不行。
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
#: `rollout.cloudflared.ipynb`）——**那个文件在本仓不存在**（本仓、`origin/goal-nn`、合并基
#: 三者都没有），所以那条断言在任何干净检出的机器上都是 `FileNotFoundError`；故当时只留
#: **真实存在**的盘，并留下约定：「若要回来，得先把 notebook 本身入库，再把名字加回这里」。
#: ★ 2026-10-01：约定履行，notebook 入库（用户裁决），名字加回（见 `DECISIONS.md`
#: §2026-09-25-goalnn-online-worker-no-bun）。
#: ★ 2026-10-10：cell 拆薄（plan/rollout-node-auto-register v2）——离线盘仍住 cell，
#: 采样节点的 bun 安装搬进 `remote/rollout_node.py`（`ROLLOUT_MODULES`），cell 里**不许**再有。
ROLLOUT_DISKS = ("battle.offline.ipynb",)
ROLLOUT_MODULES = {"remote/rollout_node.py": rollout_node}


def _cell_source(name: str) -> str:
    cells = json.loads((IPYNB / name).read_text(encoding="utf-8"))["cells"]
    return "".join("".join(c.get("source", [])) for c in cells)


def test_online_worker_modules_install_no_bun() -> None:
    """边界 1a：服务在线两块盘的模块源码里，不许出现任何 bun 安装。"""
    hits = [
        f"{path}（{marker}）"
        for path, mod in ONLINE_MODULES.items()
        for marker in INSTALL_MARKERS
        if marker in inspect.getsource(mod)
    ]
    assert hits == [], (
        f"在线 worker 引导链里又出现 bun 安装：{hits}\n"
        "这两块盘不跑 rollout（rollout 在 battle.offline.ipynb / rollout.ipynb 上跑），"
        "不该为 bun 付公网安装 + 代理顺序的代价 —— 见 tests/remote/test_tailscale_boot_bun.py 抬头"
    )


def test_online_notebook_cells_install_no_bun() -> None:
    """边界 1b：两个在线 ipynb 的 cell 全文同样不许有——notebook 是用户直接打开的那一份。"""
    hits = [f"{n}（{m}）" for n in ONLINE_DISKS for m in INSTALL_MARKERS if m in _cell_source(n)]
    assert hits == [], f"在线 notebook 的 cell 里又出现 bun 安装：{hits}"


def test_ensure_never_touches_bun() -> None:
    """边界 1c + 顺序契约作废：`ensure()` 里连「装 bun」的名字都不该有（注释可以谈 bun）。"""
    body = inspect.getsource(tailscale_boot.ensure)
    assert "ensure_bun(" not in body, "ensure() 不许再装 bun（2026-09-25 重裁）"
    assert "bun.sh" not in body, "ensure() 里不许有任何 bun 安装机制"


def test_retired_bun_helpers_do_not_come_back() -> None:
    """边界 2：退役就是退役 —— 三个符号不许回来（无消费者，且回来就会有人再调它）。"""
    back = [
        n
        for n in ("ensure_bun", "bun_path", "BUN_INSTALL_URL")
        if hasattr(tailscale_boot, n)
    ]
    assert back == [], (
        f"`remote/tailscale_boot.py` 又暴露了退役的 bun 符号：{back}\n"
        "（它们 2026-09-25 随「在线盘不该装 bun」一并退役；要恢复请先改那条裁决）"
    )


def test_sampling_node_installs_bun_in_module_not_cell() -> None:
    """边界 3a（新）：采样节点 cell 拆薄后，bun 安装只许住 `remote/rollout_node.py`。

    安装必须在 **tailscale 引导之前**（或包 `platform_net_env()`）——`ensure` 落到 userspace
    时会改写 HTTP_PROXY，之后公网 curl 走不通（2026-09-17 事故同族）。
    """
    src = _cell_source("rollout.ipynb")
    assert all(m not in src for m in INSTALL_MARKERS), (
        "采样节点 cell 又内联装 bun —— 应住 remote/rollout_node.py（cell 只留 CFG/凭据/保活/clone/调用）"
    )


def test_rollout_chains_still_provide_bun() -> None:
    """边界 3b（反向）：该有 bun 的链必须还有 —— 只删不加等于把「谁需要 bun」做没。

    `battle.offline.ipynb` = Kaggle/TPU 上的离线盘（Kaggle 不给 tailnet ⇒ 只能走 cloudflared
    隧道进 hub），它自己跑 rollout，安装必须留在 cell 里；
    采样节点的安装住 `remote/rollout_node.py`（见 `ROLLOUT_MODULES`）。
    """
    missing = [n for n in ROLLOUT_DISKS if not any(m in _cell_source(n) for m in INSTALL_MARKERS)]
    missing += [
        path
        for path, mod in ROLLOUT_MODULES.items()
        if not any(m in inspect.getsource(mod) for m in INSTALL_MARKERS)
    ]
    assert missing == [], (
        f"跑 rollout 的链不再装 bun：{missing}\n"
        "它们的 rollout 是节点侧 TS（bun 跑），安装不能删 —— 见本文件抬头边界 3"
    )
