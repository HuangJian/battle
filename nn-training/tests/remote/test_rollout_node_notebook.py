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
import os
import re
import shutil
import tempfile
import textwrap
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from remote import rollout_node as rn

NN = Path(__file__).resolve().parents[2]
NOTEBOOK = NN / "ipynb" / "rollout.ipynb"


def _cells(kind: str) -> str:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return "".join("".join(c.get("source", [])) for c in nb["cells"] if c["cell_type"] == kind)


def _cell_funcs(wanted: set[str], ns: dict[str, Any]) -> dict[str, Any]:
    """从 cell 里摘出指定函数执行（notebook 不在 pytest 里整段跑）。"""
    nb_tree = ast.parse(_cells("code"))
    selected = [n for n in nb_tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in selected} == wanted, (
        f"cell 缺少函数：{sorted(wanted - {n.name for n in selected})}"
    )
    payload = ast.Module(body=cast(list[ast.stmt], selected), type_ignores=[])
    exec(compile(payload, "<rollout.ipynb>", "exec"), ns)
    return ns


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


def test_credentials_are_read_before_tailscale_ensure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 顺序契约：`ensure()` 会注入代理 ⇒ 平台 Secrets（公网 HTTPS）必须先读完。"""
    src = inspect.getsource(rn.run)
    cred = src.index('secret("TS_AUTHKEY"')
    open_ts = src.index("_open_tailscale(")
    assert cred < open_ts, (
        "凭据读取被挪到 tailscale 引导之后 —— 复现 2026-09-17 事故（代理注入后取不到 secret）"
    )
    # ensure() 本身搬进 `_open_tailscale`（plan/rollout-tunnel-url-retry B2），
    # 顺序契约因此拆成两段：run 先读凭据，helper 里才 ensure。
    seen: list[dict] = []

    def _fake_ensure(cfg: dict, _log: Callable[[str], None]) -> dict:
        seen.append(cfg)
        return {"ip": "100.64.0.1", "mode": "kernel"}

    monkeypatch.setattr(rn.tailscale_boot, "ensure", _fake_ensure)
    assert rn._open_tailscale({}, "ak", 8443, lambda _m: None) == (
        "http://100.64.0.1:8443",
        True,
    )
    assert seen == [
        {
            "ts_authkey": "ak",
            "ts_ephemeral": True,
            "proxy_env": True,
            "engine": "",
            "no_proxy_extra": "",
            "ts_tarball": "",
            "ts_prefix": "",
        }
    ]


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


def test_cell_clone_auto_tries_builtin_mirrors(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub 直连不通时 clone 自动换路：**不填开关**，候选 = 直连 + 内置镜像，且先探活再 clone。

    只把 cell 里的 `_clone_candidates`（纯函数）摘出来执行；并 AST 检查 clone 分支确实用了
    候选表 + `git ls-remote` 探活（notebook 不在 pytest 里整段执行）。
    """
    monkeypatch.delenv("GITHUB_BASE", raising=False)
    nb_ast = ast.parse(_cells("code"))
    clone_fn = next(
        (n for n in nb_ast.body if isinstance(n, ast.FunctionDef) and n.name == "_clone_candidates"),
        None,
    )
    assert clone_fn is not None, "cell 里应有 _clone_candidates（自动换路）"
    ns: dict[str, Any] = {"CFG": {}, "os": os}
    exec(compile(ast.Module(body=[clone_fn], type_ignores=[]), "<rollout.ipynb>", "exec"), ns)
    direct = "https://github.com/HuangJian/battle.git"
    got = ns["_clone_candidates"](direct)
    assert got[0] == direct
    assert set(got[1:]) == {m + "/HuangJian/battle.git" for m in rn.GITHUB_MIRRORS}

    nb_calls = [n for n in ast.walk(nb_ast) if isinstance(n, ast.Call)]
    assert any(getattr(c.func, "id", "") == "_clone_candidates" for c in nb_calls), "clone 分支没用候选表"
    assert any(
        getattr(c.func, "id", "") == "_rank_clone_candidates" for c in nb_calls
    ), "clone 分支没用并行测速排序"
    ls_remote_calls = [
        c
        for c in nb_calls
        if getattr(c.func, "id", "") == "_sh"
        and c.args
        and isinstance(c.args[0], ast.List)
        and any(isinstance(e, ast.Constant) and e.value == "ls-remote" for e in c.args[0].elts)
    ]
    assert ls_remote_calls, "clone 前必须逐个探活（git ls-remote）"


def test_cell_clone_probes_parallel_and_ranks_fastest() -> None:
    """clone 候选并行探活、按耗时排序（墙掉的链路不排队白等）。"""
    nb_tree = ast.parse(_cells("code"))
    clone_funcs = {n.name: n for n in nb_tree.body if isinstance(n, ast.FunctionDef)}
    assert {"_probe_clone", "_rank_clone_candidates"} <= set(clone_funcs)
    net_args = next(
        n
        for n in nb_tree.body
        if isinstance(n, ast.Assign)
        and any(t.id == "_GIT_NET_ARGS" for t in n.targets if isinstance(t, ast.Name))
    )
    clone_ns: dict[str, Any] = {
        "_GIT_NET_ARGS": ast.literal_eval(net_args.value),
        "time": time,
        "_sh": lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    }
    exec(
        compile(
            ast.Module(
                body=[clone_funcs[name] for name in ("_probe_clone", "_rank_clone_candidates")],
                type_ignores=[],
            ),
            "<rollout.ipynb>",
            "exec",
        ),
        clone_ns,
    )
    gate = threading.Barrier(2, timeout=5)

    def _probe(item):
        gate.wait()
        return (item[0], item[1], 1.0 if item[0] == "fast" else 2.0)

    ranked = clone_ns["_rank_clone_candidates"]([("fast", "f"), ("slow", "s")], _probe)
    assert ranked == [("fast", "f", 1.0), ("slow", "s", 2.0)]


def test_cell_git_uses_http1_and_aborts_stalled_transfers() -> None:
    """HTTP/2 走镜像会 `RPC 失败 / INTERNAL_ERROR`；低速 60s 就换源（不白等 15 分钟）。"""
    nb_tree = ast.parse(_cells("code"))
    net_args = next(
        n
        for n in nb_tree.body
        if isinstance(n, ast.Assign)
        and any(t.id == "_GIT_NET_ARGS" for t in n.targets if isinstance(t, ast.Name))
    )
    args = ast.literal_eval(net_args.value)
    assert set(args) >= {"http.version=HTTP/1.1", "http.lowSpeedLimit=1024", "http.lowSpeedTime=60"}
    clone_calls = [
        c
        for c in ast.walk(nb_tree)
        if isinstance(c, ast.Call)
        and getattr(c.func, "id", "") == "_sh"
        and c.args
        and isinstance(c.args[0], ast.List)
        and any(isinstance(e, ast.Constant) and e.value == "clone" for e in c.args[0].elts)
    ]
    assert clone_calls, "clone 分支没找到 git clone 调用"
    for call in clone_calls:
        argv = call.args[0]
        assert isinstance(argv, ast.List), "git 调用的第一个参数应是 argv 列表"
        assert any(
            isinstance(e, ast.Starred) and getattr(e.value, "id", "") == "_GIT_NET_ARGS"
            for e in argv.elts
        ), "git clone 没带 _GIT_NET_ARGS"


def test_cell_archive_fallback_maps_every_mirror() -> None:
    """git 克隆全挂 ⇒ 同一批源改下源码归档；顶层 `refs/heads` 与扁平两种形态都试。"""
    ns = _cell_funcs(
        {"_clone_candidates", "_archive_candidates"},
        {
            "CFG": {},
            "os": os,
            "repo": "https://github.com/o/r.git",
            "ref": "goal-nn",
        },
    )
    ranked = [
        ("direct", "https://github.com/o/r.git", 1.0),
        ("mirror", "https://gh-proxy.com/https://github.com/o/r.git", 2.0),
    ]
    got = ns["_archive_candidates"](ranked)
    head = "https://github.com/o/r/archive/refs/heads/goal-nn.zip"
    assert got[0] == head
    assert set(got) >= {
        "https://github.com/o/r/archive/goal-nn.zip",
        "https://gh-proxy.com/" + head,
        "https://gh-proxy.com/https://github.com/o/r/archive/goal-nn.zip",
    }


def test_cell_archive_fallback_extracts_stripped_tree(tmp_path: Path) -> None:
    """下载的归档把顶层 `<repo>-<ref>/` 剥掉再铺到 code_dir（无 .git 也能跑）。"""
    code_dir = tmp_path / "code"

    def _fake_sh(cmd, **_kw):
        dest = Path(cmd[cmd.index("-o") + 1])
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("battle-goal-nn/tools/agent/sampler-agent.ts", "x")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ns = _cell_funcs(
        {"_safe_unzip", "_fetch_archive"},
        {
            "zipfile": zipfile,
            "tempfile": tempfile,
            "os": os,
            "shutil": shutil,
            "Path": Path,
            "code_dir": code_dir,
            "_log": lambda _m: None,
            "_sh": _fake_sh,
            "_archive_candidates": lambda _ranked: ["https://mirror/archive.zip"],
            "_DL_ROUNDS": 3,
            "_DL_BACKOFF": (),
        },
    )
    assert ns["_fetch_archive"]([]) is True
    assert (code_dir / "tools" / "agent" / "sampler-agent.ts").read_text(encoding="utf-8") == "x"
    assert not (code_dir / "battle-goal-nn").exists()


def test_cell_archive_bad_zip_falls_back_to_next_source(tmp_path: Path) -> None:
    """坏归档（不是 zip）不致命：换下一源继续下。"""
    code_dir = tmp_path / "code"
    fetched: list[str] = []

    def _fake_sh(cmd, **_kw):
        url = cmd[cmd.index("-o") - 1]
        fetched.append(url)
        dest = Path(cmd[cmd.index("-o") + 1])
        if url.endswith("bad.zip"):
            dest.write_bytes(b"not a zip")
        else:
            with zipfile.ZipFile(dest, "w") as zf:
                zf.writestr("r/tools/agent/a.ts", "x")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ns = _cell_funcs(
        {"_safe_unzip", "_fetch_archive"},
        {
            "zipfile": zipfile,
            "tempfile": tempfile,
            "os": os,
            "shutil": shutil,
            "Path": Path,
            "code_dir": code_dir,
            "_log": lambda _m: None,
            "_sh": _fake_sh,
            "_archive_candidates": lambda _ranked: ["https://m/bad.zip", "https://m/good.zip"],
            "_DL_ROUNDS": 3,
            "_DL_BACKOFF": (),
        },
    )
    assert ns["_fetch_archive"]([]) is True
    assert fetched == ["https://m/bad.zip", "https://m/good.zip"]
    assert (code_dir / "tools" / "agent" / "a.ts").is_file()


def test_cell_archive_retries_after_whole_round_fails(tmp_path: Path) -> None:
    """候选一轮全挂不是终点：退避后整轮重试（DSW 出网抖动回归）。"""
    code_dir = tmp_path / "code"
    attempts: list[str] = []

    def _fake_sh(cmd, **_kw):
        url = cmd[cmd.index("-o") - 1]
        attempts.append(url)
        dest = Path(cmd[cmd.index("-o") + 1])
        if len(attempts) == 1:
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("r/tools/agent/a.ts", "x")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ns = _cell_funcs(
        {"_safe_unzip", "_fetch_archive"},
        {
            "zipfile": zipfile,
            "tempfile": tempfile,
            "os": os,
            "shutil": shutil,
            "Path": Path,
            "code_dir": code_dir,
            "_log": lambda _m: None,
            "_sh": _fake_sh,
            "_archive_candidates": lambda _ranked: ["https://m/archive.zip"],
            "_DL_ROUNDS": 2,
            "_DL_BACKOFF": (),
        },
    )
    assert ns["_fetch_archive"]([]) is True
    assert attempts == ["https://m/archive.zip", "https://m/archive.zip"]
    assert (code_dir / "tools" / "agent" / "a.ts").is_file()


def test_cell_archive_extraction_rejects_path_traversal(tmp_path: Path) -> None:
    """归档来自公网 ⇒ 成员路径越界必须拒收，不许写到 code_dir 外面。"""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../evil.txt", "x")
    ns = _cell_funcs({"_safe_unzip"}, {"zipfile": zipfile, "Path": Path})
    with pytest.raises(ValueError):
        ns["_safe_unzip"](archive, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
