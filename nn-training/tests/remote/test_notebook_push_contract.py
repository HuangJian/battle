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


def test_github_fallback_lays_the_nn_training_root() -> None:
    """2026-10-26 真机：push + `source=github` 之后 `ModuleNotFoundError: No module named 'remote'`。

    hub 的 code.zip 根是 **`nn-training/`**（`remote/hub_client.pack_code_zip` 走
    `os.walk(nn_root)` + `relative_to(nn_root)`），所以 `/tmp/worker-code` 里必须铺
    `nn-training/` 的**内容**。GitHub clone 那条路原来把**仓库根**整个拷进去 ⇒
    `remote` 落在 `worker-code/nn-training/remote` ⇒ `sys.path` 上根本找不到它。
    """
    src = _cell("battle.cloudflared.ipynb")
    assert '_src = _tmp / "nn-training"' in src
    assert '(_src / "remote").is_dir()' in src, "clone 完要验证 remote/ 就在解析出的根下"
    assert "shutil.copytree(_src, _code_dir" in src
    assert "shutil.copytree(_tmp, _code_dir" not in src, "不许再拷仓库根（remote/ 不在它下面）"


def test_both_code_sources_are_verified_as_a_code_root() -> None:
    """两条来源（hub /code、GitHub）过同一条判据：`<code_dir>/remote` 必须存在。

    顺手钉住「缺 registry_url/hub_token 时不许静默跳过 hub」——旧版一声不响降级 GitHub，
    既拿不到 hub 的代码快照，也把自动注册整条关掉（节点起得来、hub 派不到活）。
    """
    src = _cell("battle.cloudflared.ipynb")
    assert '(Path(_code_dir) / "remote").is_dir()' in src
    assert "if not (_code_base and _hub_tok):" in src


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


def test_cloud_notebooks_expose_epoch_mult_only() -> None:
    """2026-10-26 用户口径：cloudflared / tailscale 与离线盘同一条 `epoch_mult`
    （PPO 每轮更新遍数 × N，缺省 1.0 = 课程原样）。

    **不接** `sample_mult`：它改的是训练采样量（= 改训练语料），云机这一轮只要
    「同一份 rollout 多重训几遍」——与课程/教师口径都不打架。
    """
    for name in ("battle.cloudflared.ipynb", "battle.tailscale.ipynb"):
        cfg = _cfg(name)
        assert cfg.get("epoch_mult") == 1.0, f"{name} 缺 epoch_mult=1.0（缺省 = 课程原样）"
        assert cfg.get("sample_mult") is None, f"{name} 不该有 sample_mult（只放大更新遍数）"


def test_inline_fallbacks_forward_epoch_mult() -> None:
    """两条**内联回退**（不经过 notebook_boot/notebook_runtime 的 spawn 路径）也要带倍数。

    cloudflared 的 push-first 自己 `spawn_full_worker_server`，tailscale 的内联回退自己
    拼 `run_notebook({...})`——少了这个关键字，旋钮只在这条路上静默失效（无报错）。
    用 AST 判关键字而不是拼文本：措辞改了不该红。
    """
    cf = ast.parse(_cell("battle.cloudflared.ipynb"))
    spawns = [
        n for n in ast.walk(cf)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "spawn_full_worker_server"
    ]
    assert [kw.arg for kw in spawns[0].keywords] == ["epoch_mult"], "push-first 的 spawn 漏传 epoch_mult"

    ts = ast.parse(_cell("battle.tailscale.ipynb"))
    calls = [
        n for n in ast.walk(ts)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "run_notebook"
    ]
    assert len(calls) == 1, "内联回退应当只有一处 run_notebook({...})"
    fallback = calls[0].args[0]
    assert isinstance(fallback, ast.Dict), "run_notebook 的第一个实参应是 cfg 字典字面量"
    keys = {k.value for k in fallback.keys if isinstance(k, ast.Constant)}
    assert {"epoch_mult"} <= keys, "tailscale 内联回退的 run_notebook 漏传 epoch_mult"


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
