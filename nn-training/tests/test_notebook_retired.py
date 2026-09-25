"""tests/test_notebook_retired.py —— 退役 notebook 的守卫（2026-09-25，P4）。

`battle.cloudflared.ipynb` 曾是**第三块盘**：也能起 worker，但 ① 不装 bun ⇒ `kind=iter`
（节点侧 rollout）被 worker 的能力自检**零下载拒单**；② cell 骨架是不拉远端引导模块的
**第二份内联实现**（审计 §I6：仓库里的修复到不了它）。裁决 = **退役成零逻辑指路牌**
（`plan/online-offline-role-routing.plan.md` §9，`DECISIONS §2026-09-25-goalnn-cloudflared-notebook-retired`）。

为什么值得一条用例：这个文件的**失效方式是静默的** —— 把引导代码贴回去，它又能起 worker、
又能被控制台派活，而「它跑不了 iter / 修复到不了它」要等到真机报障才看得见（同一形状已经
咬过两次：`f274ac1b` 之前的白传 + §46 的「从来没装 bun」）。所以钉两条：

  ① 它**不许再含**任何 worker 引导标识（谁想复活它，先回来读 §9）；
  ② 它必须**当场**停下并指路（`SystemExit` + 能力清单里的三个 notebook），不是静默什么都不做。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import cast

NN = Path(__file__).resolve().parent.parent
RETIRED_NB = NN / "ipynb" / "battle.cloudflared.ipynb"

#: 退役牌里不许再出现的**引导标识**（命中一条 = 它又被改成能起 worker 了）。
#: (标识, 它意味着什么)
BANNED: list[tuple[str, str]] = [
    ("notebook_runtime", "旧骨架的运行时入口（pull 腿起 worker）"),
    ("push_bootstrap", "旧骨架的 push 引导（第二份内联实现）"),
    ("worker_loop", "起 worker 循环"),
    ("run_pull_worker", "pull 腿 worker 入口"),
    ("run_push_worker", "push 腿 worker 入口"),
    ("cloudflared-linux-amd64", "装 cloudflared 二进制（起公网隧道 ⇒ 又能被控制台派活）"),
    ("YOUR_TOKEN_HERE", "push_token 占位符（有它 = 这台盘还想跑）"),
]

#: 指路词：缺一条就说明牌子没把人导到该去的地方（能力清单 = plan §9.2 的那张表）。
POINTERS = ("battle.tailscale.ipynb", "rollout.cloudflared.ipynb", "battle.offline.ipynb")


def _cells() -> list[dict]:
    """退役牌的两个 cell（ipynb 是 JSON ⇒ `json.loads` 回 Any，显式声明类型）。"""
    return cast("list[dict]", json.loads(RETIRED_NB.read_text(encoding="utf-8"))["cells"])


def _joined() -> str:
    return "".join("".join(c.get("source", [])) for c in _cells())


def test_retired_notebook_carries_no_worker_bootstrap() -> None:
    """零逻辑：任何能起 worker 的标识都不许回来。"""
    src = _joined()
    hits = [f"{tok}（{why}）" for tok, why in BANNED if tok in src]
    assert hits == [], (
        f"{RETIRED_NB.name} 又含 worker 引导标识：{hits}\n"
        "它 2026-09-25 已退役（不装 bun ⇒ kind=iter 零下载拒单 + 骨架是 notebook_boot 的第二份"
        "内联实现、不拉远端引导模块）——要恢复它请先读 plan/online-offline-role-routing §9 并改那一段裁决"
    )


def test_retired_notebook_stops_loudly_and_points_somewhere() -> None:
    """当场停下 + 指路：`SystemExit` 与三份替代 notebook。"""
    cells = _cells()
    codes = [c for c in cells if c.get("cell_type") == "code"]
    assert len(codes) == 1, f"退役牌只该留一个 code cell（实际 {len(codes)}）——多出来的都是逻辑"
    src = _joined()
    assert "退役" in src
    assert "SystemExit" in src, (
        "Run 必须当场停下：静默什么都不做最糟（用户会以为它在等就绪，而它根本没起过 worker）"
    )
    for tok in POINTERS:
        assert tok in src, f"指路缺 {tok}（能力清单 = plan §9.2 那张表）"
    ast.parse("".join(codes[0].get("source", [])))  # 仍是合法 python
