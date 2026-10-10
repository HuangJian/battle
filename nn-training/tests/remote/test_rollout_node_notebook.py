"""tests/remote/test_rollout_node_notebook.py —— 薄 cell 与 `run()` 的顺序契约（plan §3-S4）。

2026-10-10（plan/rollout-node-auto-register v2）：采样节点 notebook（`ipynb/rollout.ipynb`）
的 cell 从「内联全套逻辑」改成「CFG / 凭据 / 保活 / clone / 调用」，逻辑收进
`remote/rollout_node.py`。钉四件事：

1. **cell 能编译**（`ast.parse`）—— 改 cell 时最容易留下半个括号，而它只在真机上才炸；
2. **cell 只做分工内的五件事**：CFG 覆盖模块读的每个键 · `_secret` · 保活 · clone ·
   `rollout_node.run(CFG, _log, _secret)`；**不准**再内联 bun/cloudflared 安装（那条由
   `test_tailscale_boot_bun.py` 的边界 3a 从「markers 不在 cell」方向再钉一遍）；
3. **凭据先于 `ensure()`**（2026-09-17 事故同族）：`run` 源码里 `secret("TS_AUTHKEY"` 的位置必须
   早于 `tailscale_boot.ensure(` —— userspace 会改写 HTTP_PROXY，之后平台 Secrets 取不到；
4. **公网下载包在 `platform_net_env()` 里**（bun / cloudflared），别在代理注入后裸下载。
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote import rollout_node as rn

NN = Path(__file__).resolve().parents[2]
NOTEBOOK = NN / "ipynb" / "rollout.ipynb"


def _cells(kind: str) -> str:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return "".join("".join(c.get("source", [])) for c in nb["cells"] if c["cell_type"] == kind)


def test_code_cell_compiles() -> None:
    code = _cells("code")
    assert code.strip(), "采样节点 notebook 的 code cell 不能是空的"
    ast.parse(code)  # 语法错在这里就炸，不留到真机


def test_cell_does_only_its_share_of_the_work() -> None:
    """cell = CFG / 凭据 / 保活 / clone / 调用；逻辑不回来。"""
    code = _cells("code")
    for needed, why in (
        ("CFG = {", "CFG 段要留在 cell 顶部给人填"),
        ("def _secret(", "凭据取值住 cell（Colab/Kaggle Secrets 是 cell 侧能力）"),
        ("keepalive", "保活必须住 cell（云机静默会被回收）"),
        ('"clone"', "clone 住 cell：GITHUB_TOKEN 只在这里用"),
        ("rollout_node.run(CFG, _log, _secret)", "主流程必须委托给仓库里的 run()"),
    ):
        assert needed in code, f"cell 少了「{needed}」（{why}）"
    assert code.index("sys.path.insert") < code.index("from remote import rollout_node"), (
        "必须先 sys.path.insert(code_dir/nn-training) 再 import（clone 之前 import 必失败）"
    )


def test_cell_inlines_no_installs() -> None:
    """bun / cloudflared 的安装住模块（`_ensure_bun` / `_start_cloudflared`），cell 里不许有。"""
    text = _cells("code") + _cells("markdown")
    for marker in ("bun.sh/install", "cloudflared-linux-amd64", "apt-get install"):
        assert marker not in text, f"采样节点 cell 又内联安装（{marker}）—— 应住 remote/rollout_node.py"


def test_cell_cfg_covers_every_key_the_module_reads() -> None:
    """cell 的 CFG 是模块的开关面板：模块读的键漏一个 = 云机上静默用缺省值。"""
    tree = ast.parse(_cells("code"))
    cfg_keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "CFG" for t in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        cfg_keys |= {
            k.value for k in node.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    assert cfg_keys, "没在 cell 里找到 CFG 字面量"
    read = set(re.findall(r'cfg\.get\(\s*"([a-z_]+)"', inspect.getsource(rn)))
    assert read - cfg_keys == set(), (
        f"模块读得到但 cell 的 CFG 没有这些键：{sorted(read - cfg_keys)} —— "
        "要么加进 cell（给人填），要么从模块里删掉"
    )


def _is_open_call(node: ast.Call) -> bool:
    """`open(...)` 或 `<路径>.open(...)`（cell 用的是后者）。"""
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "open") or (
        isinstance(f, ast.Attribute) and f.attr == "open"
    )


def _is_append_mode(node: ast.Call) -> bool:
    """mode 给成 `"a"`（位置参数或关键字都算）。"""
    cands = [node.args[0]] if node.args else []
    cands += [k.value for k in node.keywords if k.arg == "mode"]
    return any(isinstance(c, ast.Constant) and c.value == "a" for c in cands)


def test_cell_logs_to_a_file_too() -> None:
    """会话被无声回收时 cell 输出会连着丢 ⇒ 日志同时落文件（plan §2.5）。

    AST 判定（不挂措辞）：`_LOG_PATH` 被赋值过，且存在 `open(<...>, "a")` 追加调用。
    """
    tree = ast.parse(_cells("code"))
    binds = [
        t.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    ]
    appends = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_open_call(node) and _is_append_mode(node)
    ]
    assert binds.count("_LOG_PATH") == 1, "cell 的 _log 应把路径绑成 _LOG_PATH（/content → /kaggle/working → /tmp）"
    assert appends, "cell 的 _log 应同时写文件（追加模式）——否则会话被回收时输出连着丢"


def test_credentials_are_read_before_tailscale_ensure() -> None:
    """★ 顺序契约：`ensure()` 会注入代理 ⇒ 平台 Secrets（公网 HTTPS）必须先读完。"""
    src = inspect.getsource(rn.run)
    cred = src.index('secret("TS_AUTHKEY"')
    ensure = src.index("tailscale_boot.ensure(")
    assert cred < ensure, "凭据读取被挪到 ensure() 之后 —— 复现 2026-09-17 事故（代理注入后取不到 secret）"


def _download_statements_inside_public_net(
    func: Callable[..., Any], url_prefix: str
) -> tuple[list[ast.AST], list[ast.AST]]:
    """把 `func` 里含 `url_prefix` 的 URL 字面量（= 公网下载）与「落在 `with _public_net():` 里」
    的节点各收一份（AST 判定：不挂源码措辞，也不靠 `assert "…" in src`）。"""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    wrapped: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        if not any(
            isinstance(it.context_expr, ast.Call)
            and isinstance(it.context_expr.func, ast.Name)
            and it.context_expr.func.id == "_public_net"
            for it in node.items
        ):
            continue
        for stmt in node.body:
            wrapped.update(id(x) for x in ast.walk(stmt))
    urls: list[ast.AST] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.startswith(url_prefix)
    ]
    return urls, [u for u in urls if id(u) not in wrapped]


def test_public_downloads_are_wrapped_in_platform_net_env() -> None:
    """F5：`ensure()` 之后任何公网下载都得还原平台代理（先例 `battle.offline.ipynb:408-409`）。"""
    assert callable(rn._public_net)
    for func, prefix in (
        (rn._ensure_bun, "curl -fsSL https://bun.sh/install"),
        (
            rn._start_cloudflared,
            "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64",
        ),
    ):
        urls, unwrapped = _download_statements_inside_public_net(func, prefix)
        assert urls, f"{func.__name__} 里找不到公网下载字面量（守卫锚点该更新了）"
        assert unwrapped == [], f"{func.__name__} 的下载没包 `_public_net()`（代理注入后公网不通）"


def test_run_owns_unregister_on_exit() -> None:
    """收线语义（F1）：`finally` 里 best-effort unregister —— hub 据此写 `unregistered_at`。

    AST 判定：`run` 里存在 `try/finally`，其 `finally` 体调了 `unregister` 与 `_terminate`，
    且 `_terminate` 的两个实参恰是 `agent` / `cfp`（子进程都要收）。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(rn.run)))
    finals = [n for n in ast.walk(tree) if isinstance(n, ast.Try) and n.finalbody]
    assert finals, "run 没有 try/finally —— 会话收线（unregister + 清子进程）会漏"
    calls = [c for t in finals for stmt in t.finalbody for c in ast.walk(stmt) if isinstance(c, ast.Call)]
    names = {getattr(c.func, "id", "") for c in calls}
    assert names >= {"unregister", "_terminate"}, f"finally 里少了收线步骤：{sorted(names)}"
    terminated = {getattr(c.args[0], "id", "") for c in calls if getattr(c.func, "id", "") == "_terminate" and c.args}
    assert terminated == {"agent", "cfp"}, f"收线要清 agent + cloudflared，实际清了 {sorted(terminated)}"
