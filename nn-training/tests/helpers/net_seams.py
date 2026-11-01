"""tests/helpers/net_seams.py — 「死桩 URL」的唯一取用口（回环 + **当场拒绝**）。

## 为什么不是随手写 `http://127.0.0.1:1`

「没人监听」不等于「连接被拒」：某些容器/防火墙把未监听端口做成**黑洞**（SYN 被丢，等到
超时才报错）。本仓容器实测 `127.0.0.1:9` **8.008s** 超时（2026-10-29）、`127.0.0.1:1`
**2.0s**（2026-11-01）—— 而用例通常只想说「这台不可达」，于是每次探活都白等满自己的超时
预算（`--durations` 里看着像「这个用例很慢」；见 `docs/nn/engineering.md` §14 / §94）。

所以死桩一律走**按机器探测**的端口（`tests/conftest.py` 的 `_dead_console_url`：只有
「连接当场被拒（`ECONNREFUSED`，<1ms）」的端口才算，探测不到就 bind 一个随机端口再 close）。
本模块只是给它一个**用例可读的名字**，并保留「为什么不能抄字面量」的理由 —— 定义仍然只有
一份（`tests/conftest.py::TEST_CONSOLE_URL`，`test_production_isolation` 有闸）。

用法：

    from tests.helpers import net_seams

    dead = net_seams.dead_loopback_url()   # 例如 http://127.0.0.1:45871
    cfg = {... "url": dead ...}
"""

from __future__ import annotations


def dead_loopback_url() -> str:
    """本机回环上一个**连接当场被拒**的 URL（可用作「不可达节点 / 死控制台」的桩）。"""
    # 延迟 import：`tests.conftest` 是 pytest 的入口模块，用例在收集期就要能 import 本模块。
    from tests.conftest import TEST_CONSOLE_URL

    return TEST_CONSOLE_URL
