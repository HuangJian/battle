"""tests/remote/test_notebook_push_contract.py —— push 分支的 notebook 契约（2026-10-23）。

评审 P1-4 的回归：`battle.cloudflared` 的 `/code` 循环原本被 **pull 与 push-with-hub 共用**，
把 push 的有界等待/降级改在那段共享代码上，pull 模式就从「等 1 小时」变成「10 分钟后 FATAL」——
直接违反 plan 的非目标（pull 行为不变）。所以这里钉三件事：

1. pull 分支的那几行**逐字节**还在（1h 截止、401/403 直接 FATAL）；
2. push 分支的顺序是 `/code` → `git clone` → push-first（代码在注册/起服务之前）；
3. push-first 只在 `allow_push_first` 打开时才走，且内联注册带 `kind=gpu_push`。

只做静态解析（不执行 cell）：notebook 里的代码靠人读也要能对上。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

NN = Path(__file__).resolve().parents[2]
IPYNB = NN / "ipynb"


def _cell(name: str) -> str:
    cells = json.loads((IPYNB / name).read_text(encoding="utf-8"))["cells"]
    return "".join("".join(c.get("source", [])) for c in cells if c.get("cell_type") == "code")


def _cfg(name: str) -> dict:
    """取出 cell 里 `CFG = {...}` 字面量（不执行 cell 的其余部分）。"""
    tree = ast.parse(_cell(name))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "CFG":
                    value = ast.literal_eval(node.value)
                    assert isinstance(value, dict)
                    return value
    raise AssertionError(f"{name} 里没有 CFG = {{...}}")


def test_cloudflared_cfg_carries_the_push_keys() -> None:
    cfg = _cfg("battle.cloudflared.ipynb")
    assert cfg["mode"] == "push"  # 缺省仍是最省事的自动路径（老缺省）
    assert cfg["push_port"] == 8790
    for key in (
        "hub_url",
        "registry_url",
        "hub_token",
        "push_token",
        "node_id",
        "label",
        "repo_url",
        "branch",
        "code_wait_sec",
        "allow_push_first",
    ):
        assert key in cfg, f"CFG 缺 {key}（push 引导/注册要用）"


def test_pull_branch_keeps_its_old_waits_and_fatals() -> None:
    """非目标：pull 的行为逐字节不变（评审 P1-4）。"""
    src = _cell("battle.cloudflared.ipynb")
    start = src.index('if _mode == "pull":')
    end = src.index("# ── Push")
    pull = src[start:end]
    # 三条一起断（只算一条文本判据）：1h 截止 + 401/403 FATAL + 超时文案
    assert (
        "_deadline = time.time() + 3600" in pull
        and "if _e.code in (401, 403):" in pull
        and "等 code.zip 超 1h" in pull
    ), "pull 的 1h 等待与 401/403 FATAL 必须逐字节不变"
    # push 的有界等待不许出现在 pull 分支里
    assert "code_wait_sec" not in pull


def test_push_takes_code_before_starting_the_service() -> None:
    """代码必须先到位：否则 hub 不知道 URL / 服务没起，job 永远不来（§1.2 注册死锁）。"""
    src = _cell("battle.cloudflared.ipynb")
    hub_code = src.index('_code_base + "/code"')
    git_clone = src.index('["git", "clone", "--depth", "1", "--branch"')
    bootstrap = src.index("def _bootstrap_push_first():")
    assert hub_code < git_clone < bootstrap
    assert (
        "code_wait_sec" in src
        and "allow_push_first" in src
        and "source=hub" in src
        and "source=github" in src
    ), "push 分支必须带：有界 /code、兜底开关、两种来源日志"


def test_push_first_is_the_last_resort_and_registers_inline() -> None:
    src = _cell("battle.cloudflared.ipynb")
    # 显式开关：关掉时直接 FATAL（不再静默卡在「等 code 的 job」）
    boot = src[src.index("def _bootstrap_push_first():"):]
    assert (
        'if not CFG.get("allow_push_first", True):' in src
        and '"/admin/nodes/register"' in boot  # 否则死锁
        and '"kind": "gpu_push"' in boot
        and 'CFG["node_id"] = _node_id' in boot  # 升级后复用同一个 id
    ), "push-first 必须可关、内联注册带 kind、写回 node_id"


def test_battle_offline_push_branch_lands_only_at_w4() -> None:
    """W4 落地后：离线盘的 push 分支必须在，且 **pull 仍是缺省**（不可反向）。

    这条原先是「W4 以前不许出现半成品 push 分支」的反向守卫（W0–W2 时 `assert ... not in src`）；
    W4 把 `remote/offline_push` 与 `run_push(` 落到盘上后，同一条纪律翻面 = 两件事都要成立：

      * `CFG.mode` 的**缺省值仍是 "pull"**（老路径逐字节不变 —— 这是非目标）；
      * push 分支只在 `_PUSH_MODE` 为其时才接管，且代码来源（`_ensure_push_code`）**早于**
        `_load_boot`（顺序反了就是 ImportError / 半成品引导）。
    """
    src = _cell("battle.offline.ipynb")
    cfg = _cfg("battle.offline.ipynb")
    assert cfg["mode"] == "pull", "缺省必须仍是 pull（push 是显式 opt-in）"
    assert src.count("from remote.offline_push import run_push") == 1
    assert src.index("_ensure_push_code(_log)") < src.index("_boot = _load_boot(_log)")
