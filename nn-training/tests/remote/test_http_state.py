"""`remote/http.py` 的两份进程级状态：`ProxyOpeners` / `WarnThrottle`（P0-5，2026-11-21）。

它们原来是模块级 `_opener`（`global` 重绑懒建）与 `_POLL_WARN_AT`（dict + 调用点里的 60s 判断）。
收成对象后，各自的**语义**第一次有了名字，也第一次能被单独钉住：

* opener：懒建**只发生一次**，`reset()` 之后重新读 env（旧语义：进程内改 `http_proxy` 不影响
  已建好的 opener —— 这条是 Colab 现场 2026-09-16 的结论，不能悄悄变）；
* 节流：同一 key 在窗口内只放行一次，跨窗口放行；不同 key 互不影响。
"""

from __future__ import annotations

import urllib.request

import remote.http as http_mod


def test_opener_is_built_once_and_reused(monkeypatch) -> None:
    built: list[int] = []
    real = urllib.request.build_opener

    def _counting(*args, **kwargs):
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(urllib.request, "build_opener", _counting)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    openers = http_mod.ProxyOpeners()
    assert len(built) == 0, "构造持有者不该建 opener（懒建）"
    first = openers.get()
    assert openers.get() is first, "第二次 get 必须复用"
    assert len(built) == 1, f"opener 被建了 {len(built)} 次"


def test_opener_reset_rebuilds_and_repicks_env(monkeypatch) -> None:
    """`reset()` 是「下次重建」的唯一入口：改完代理 env 要 reset 才生效（旧语义逐字保留）。"""
    for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    openers = http_mod.ProxyOpeners()
    direct = openers.get()
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:1")
    assert openers.get() is direct, "env 变了也不重建（进程内改 env 不影响已建的 opener）"
    openers.reset()
    rebuilt = openers.get()
    assert rebuilt is not direct, "reset() 之后必须重建"
    # `handlers` 是 OpenerDirector 的**运行时**属性（typeshed 没声明）⇒ getattr 取，
    # 不用 `# type: ignore` 消音（编译器是评审）。
    handlers = getattr(rebuilt, "handlers", ())
    proxy = next(h for h in handlers if isinstance(h, urllib.request.ProxyHandler))
    assert dict(getattr(proxy, "proxies", {})).get("http") == "http://proxy.invalid:1"


def test_default_opener_state_is_one_object() -> None:
    """默认持有者只有一份，`reset()` 之后懒建仍只发生一次（历史入口走的就是它）。"""
    assert http_mod.openers() is http_mod.openers()
    http_mod.openers().reset()
    opener = http_mod.openers().get()
    assert hasattr(opener, "open")
    assert http_mod.openers().get() is opener


def test_throttle_allows_once_per_window() -> None:
    throttle = http_mod.WarnThrottle(window_sec=60.0)
    assert throttle.allow("hub:409", now=1000.0) is True
    assert throttle.allow("hub:409", now=1030.0) is False, "窗口内只放行一次"
    assert throttle.allow("hub:409", now=1061.0) is True, "过了窗口再放行"
    assert throttle.allow("hub:500", now=1062.0) is True, "不同 key 互不影响"
    assert len(throttle) == 2


def test_throttle_zero_window_always_allows_and_forgets() -> None:
    throttle = http_mod.WarnThrottle(window_sec=0.0)
    assert throttle.allow("k", now=10.0) is True
    assert throttle.allow("k", now=10.5) is True, "0 秒窗口 = 下一条（不同时刻）就放行"
    throttle.forget_all()
    assert len(throttle) == 0


def test_default_throttle_is_one_object_and_defaults_to_a_minute() -> None:
    assert http_mod.WARN_THROTTLE_SEC == 60.0
    assert http_mod.warn_throttle() is http_mod.warn_throttle()
    # 缺省窗口 = 60s：行为判据（不开私有面）：59s 内吞掉、61s 放行。
    default_window = http_mod.WarnThrottle()
    assert default_window.allow("k", now=0.0) is True
    assert default_window.allow("k", now=59.0) is False
    assert default_window.allow("k", now=61.0) is True
