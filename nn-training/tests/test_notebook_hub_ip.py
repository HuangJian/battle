"""tests/test_notebook_hub_ip.py —— 两个 notebook 的 `HUB_IP` 接线与口径守卫。

notebook 不 import 仓库代码（它们从 GitHub raw 拉 `remote/tailscale_boot.py`），所以
「本机 hub 地址也走凭据」这条链在 notebook 里各有一份自己的拷贝：

  1. 训练 cell 的正常路径 → `remote/notebook_boot.py::run()` 读 `HUB_IP`，再交给
     `tailscale_boot.resolve_hub_url`（有单测，见 `test_notebook_runtime.py` 一线）；
  2. 训练 cell 的**内联回退**（远端模块拉不到时唯一的活路）→ cell 里就地复刻同一段
     解析逻辑，没人守，会静默漂移；
  3. 体检 notebook → 自己读 `HUB_IP` 再塞进 `diagnose()`，必须与运行时同口径，
     否则出现「体检说连不上、真跑却连得上」这种最难信的一种诊断结论。

按 `test_tpu_probe_notebook.py` 的做法：把 cell 文本抠出来**独立执行**（不 import 被测
notebook），结果与 `resolve_hub_url` 逐例对账 —— 两边口径一旦分叉就在这里红。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from remote.tailscale_boot import HUB_DEFAULT_PORT, resolve_hub_url

NN = Path(__file__).resolve().parent.parent
MAIN_NB = NN / "ipynb" / "battle.tailscale.ipynb"
DEBUG_NB = NN / "ipynb" / "tailscale.debug.ipynb"

# cell 里那份 CFG 手填的占位模板：含 `<` ⇒ 一律视为未填
TEMPLATE_HUB_URL = "http://<本地TS_IP>:8787"

INLINE_START = "# 本机 hub 地址：HUB_IP 凭据优先"
# 用**调用**（不是函数定义）作右边界：`def _inline_ensure(` 出现在 cell 更早处
INLINE_CALL = "_inline_ensure(_ak"


def notebook_cells(path: Path, kind: str | None = None) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for cell in nb["cells"]:
        if kind is not None and cell.get("cell_type") != kind:
            continue
        src = cell.get("source", "")
        out.append("".join(src) if isinstance(src, list) else str(src))
    return out


def cell_with(path: Path, needle: str) -> str:
    hits = [t for t in notebook_cells(path, "code") if needle in t]
    assert len(hits) == 1, f"{path.name}: 期望恰好 1 个含 {needle!r} 的 code cell，实际 {len(hits)}"
    return hits[0]


def main_cell() -> str:
    return cell_with(MAIN_NB, '_secret("HUB_IP"')


def debug_cell() -> str:
    return cell_with(DEBUG_NB, '_secret("HUB_IP"')


def inline_hub_block() -> str:
    """内联回退里「算 `_hub`」的那一段（从 HUB_IP 读取到 `_inline_ensure` 之前）。"""
    text = main_cell()
    i = text.find(INLINE_START)
    j = text.find(INLINE_CALL)
    assert i != -1 and j != -1 and i < j, "找不到内联回退的 hub 解析段（cell 结构变了？）"
    return text[i:j]


def run_inline(hub_ip: str, hub_port: Any, hub_url: str) -> str:
    """在 stub 环境里真跑那段 cell 代码，返回它算出的 `_hub`。"""
    ns: dict[str, Any] = {
        "CFG": {"hub_url": hub_url, "hub_ip": "", "hub_port": hub_port},
        "logs": [],
        "_log": lambda msg: ns["logs"].append(msg),
        "_secret": lambda key, cfg_val="": hub_ip if key == "HUB_IP" else str(cfg_val or ""),
    }
    exec(compile(inline_hub_block(), "<cell>", "exec"), ns)
    assert ns["logs"], "cell 没把 hub 来源打进日志（口径不可观测）"
    return str(ns["_hub"])


# (HUB_IP, hub_port, CFG hub_url, 期望)
RESOLVED_CASES = [
    ("100.64.0.5", 8787, TEMPLATE_HUB_URL, "http://100.64.0.5:8787"),
    ("100.64.0.5", 9999, TEMPLATE_HUB_URL, "http://100.64.0.5:9999"),
    ("100.64.0.5", 0, TEMPLATE_HUB_URL, f"http://100.64.0.5:{HUB_DEFAULT_PORT}"),
    ("100.64.0.5:9999", 8787, TEMPLATE_HUB_URL, "http://100.64.0.5:9999"),
    ("hub.tailnet.ts.net", 8787, TEMPLATE_HUB_URL, "http://hub.tailnet.ts.net:8787"),
    ("http://127.0.0.1:8787/", 8787, TEMPLATE_HUB_URL, "http://127.0.0.1:8787"),
    ("", 8787, "http://100.64.0.5:8787", "http://100.64.0.5:8787"),
    ("", 8787, "http://100.64.0.5:8787/", "http://100.64.0.5:8787"),
]

UNSET_CASES = [
    ("", 8787, TEMPLATE_HUB_URL),  # 忘了改模板 ⇒ 没填
    ("", 8787, ""),
]


@pytest.mark.parametrize(("hub_ip", "hub_port", "hub_url", "expected"), RESOLVED_CASES)
def test_inline_fallback_resolves_hub_like_the_runtime_module(
    hub_ip: str, hub_port: int, hub_url: str, expected: str
) -> None:
    got = run_inline(hub_ip, hub_port, hub_url)
    assert got == expected
    assert got == resolve_hub_url(hub_url, hub_ip, hub_port), (
        "内联回退与 tailscale_boot.resolve_hub_url 口径分叉 —— 两条路会连到不同的 hub"
    )


@pytest.mark.parametrize(("hub_ip", "hub_port", "hub_url"), UNSET_CASES)
def test_inline_fallback_fails_loudly_when_hub_address_is_unset(
    hub_ip: str, hub_port: int, hub_url: str
) -> None:
    assert resolve_hub_url(hub_url, hub_ip, hub_port) == ""
    with pytest.raises(SystemExit) as ei:
        run_inline(hub_ip, hub_port, hub_url)
    msg = str(ei.value.code)
    assert "HUB_IP" in msg and "hub_url" in msg, f"失败信息该同时指名两个键，实际：{msg}"


def test_inline_fallback_default_port_tracks_the_module_constant() -> None:
    assert f"or {HUB_DEFAULT_PORT}" in inline_hub_block(), (
        "内联回退的缺省端口与 HUB_DEFAULT_PORT 漂移 —— HUB_IP 只写 IP 时两条路会连到不同端口"
    )


def test_main_notebook_reads_hub_ip_before_the_proxy_bootstrap() -> None:
    text = main_cell()
    assert '"hub_ip": ""' in text and '"hub_port": 8787' in text, "CFG 缺 hub_ip / hub_port 条目"
    i = text.find('_secret("HUB_IP"')
    j = text.find(INLINE_CALL)
    assert -1 < i < j, (
        "HUB_IP 必须在装 userspace 代理**之前**读 —— 引导后平台 Secrets（公网 HTTPS）就够不着了"
    )


def test_main_notebook_markdown_names_the_hub_ip_secret() -> None:
    md = "\n".join(notebook_cells(MAIN_NB, "markdown"))
    assert "HUB_IP" in md and "Secret" in md, "cell 说明没提 HUB_IP 凭据 —— 用户找不到这个键"


def test_debug_notebook_uses_the_same_secret_and_feeds_diagnose() -> None:
    text = debug_cell()
    assert '"hub_ip": ""' in text and '"hub_port": 8787' in text, "体检 CFG 缺 hub_ip / hub_port"
    i = text.find('_secret("HUB_IP"')
    j = text.find("_boot.ensure(")
    assert -1 < i < j, "体检 cell 同样要在引导前读 HUB_IP（同一条公网 Secrets 时序约束）"
    assert '_secret("HUB_IP", CFG.get("hub_ip"))' in text
    assert '"hub_ip": _hub_ip' in text, "体检没把 HUB_IP 传给 diagnose —— 与运行时不同口径"
    assert '"hub_port": CFG.get("hub_port")' in text
