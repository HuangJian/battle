"""tests/helpers/hub_seams.py — 假 hub（不存在的 URL）下，`worker_loop` 一轮里**唯二**会真发
HTTP 的两条缝。

## 为什么需要它

一批 `worker_loop` 用例把 hub 地址写成 `http://hub` / `http://h0` 这类**不存在的主机名**，
只打桩 `acquire_job` / `run_job` / `post_result`。但 worker 跑一轮还有两条缝会真的发请求：

* **预取填充器** `_prefetch_fill` → `peek_jobs`（`GET /jobs/peek`，解析在 `remote.job_round`）；
* **阶段通知** → `job_ready`（`POST /jobs/{id}/ready`，同解析在 `remote.job_round`）。

两者在生产里都是**尽力而为**（`except Exception: pass`：不可达不致命），所以用例**看不见**
它们——看得见的是耗时：本容器实测每次 **~1.4s**（环境里 `HTTP_PROXY` 指向一个把不可达主机
转成 `502 Bad Gateway` 的代理；不走代理的机器上则是 ~0.2s 的 DNS 失败）。于是这些用例比它们
的判据所需时间慢上秒级，`--durations` 里看着像「这个测试很慢」。

这就是 `docs/nn/engineering.md` §14「测试空等生产超时」的同型（2026-09-29 由
`tmp/dur/net_audit.py`——包装 `remote.http._request` 计慢调用——揪出来的：全量里
`test_worker_queue` / `test_remote_hotswap` / `test_priority_schedule` 各白等 1.4~4.3s）。

## 用法

放在「打桩 `acquire_job`」的那几行旁边（表示「这台 worker 面对的是假 hub」）：

    from tests.helpers import hub_seams

    hub_seams.stub_round_http(monkeypatch)

用例若**要**验证这些请求本身（例如 `test_soft_hold_prefetch` 的预取命中面），就不要用本
助手，自己打桩成能断言的记录器。
"""

from __future__ import annotations


def stub_round_http(monkeypatch) -> None:
    """把一轮里两条会真发 HTTP 的缝打桩成「hub 不可达」的同形返回。

    `peek_jobs` 的返回形状 = 生产口径 `(jobs, halt)`（空候选 + 未停机）。
    """
    import remote.job_round as JR

    monkeypatch.setattr(JR, "peek_jobs", lambda *a, **k: ([], False), raising=True)
    monkeypatch.setattr(JR, "job_ready", lambda *a, **k: None, raising=True)
