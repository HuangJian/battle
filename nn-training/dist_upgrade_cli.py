"""dist_upgrade_cli.py — 节点升级指令的一次性入口（供 TS 侧工具复用训练循环的守卫）。

背景：训练循环（`rl/dispatch.py` ping 门）发现节点 codeHash stale 时会
`dist_common.request_upgrade_guarded(...)` —— POST `/v1/restart {pullBranch}`，
带三重护栏（脏工作区拒发 / 跨代去重 / self 节点纯重启）。TS 侧的一次性评估工具
（`tools/sim/eval-course-ckpt.ts`、`tools/sim/m1-eval.ts`）此前只打印一行 skipped、
不升级也不汇总告警，等于把「远端不可用」静默降级成本地跑。

本文件让 TS 侧**调用**这份守卫而不是移植它：守卫与 dirty 判据只有一处实现
（`dist_common`），TS 侧只做「拼 spec → spawn → 读 JSON → 打日志」。输入（stdin，UTF-8 JSON）——两种模式：

  A) 扫描模式（**推荐**，调用方不 ping）：
    {
        "cfg_path": "nn-training/rl-config.json",   # 本 CLI 自己 ping 每个 enabled 节点
        "expected_hash": "<64hex>",       # 可省 = dist_common.compute_code_hash()
        "branch": "goal-nn",              # 远端 pull 分支；self/回环节点恒为空（禁 pull）
        "seen": [ {"id":"mac","pingHash":"<64hex>","expectedHash":"<64hex>"} ],
                                            # 可选：跨调用去重 memo 预置（见下）
        "dirty": null, "dry_run": false, "timeout": 20.0
    }
    判 stale 的那一步直接走训练循环自己的 `dist_common.upgrade_stale_nodes(...)`
    （ping → codeHash ≠ expected → request_upgrade_guarded），调用方**不重复实现探测**。

  B) 显式节点模式（调用方已 ping 过，把 hash 传来）：
    {
        "expected_hash": "<64hex>", "branch": "goal-nn",
        "nodes": [ {"id":"mac","url":"http://…","authKey":"…","pingHash":"<64hex>"} ],
        "dirty": null, "dry_run": false, "timeout": 20.0
    }

输出（stdout，单行 JSON）：
    {"dirty": ["src/…"],
     "results": [{"id":"mac","ok":true,"reason":"restart-requested","pingHash":"<64hex>"}]}

reason 取值同 `request_upgrade_guarded`：`restart-requested` / `dedup` /
`dirty-tree:<n>` / `restart-failed`；本文件另加 `current`（pingHash == expected，未发）
与 `unreachable`（扫描模式 ping 不通）。

退出码：0 = 已处理（即使全部失败，逐条 reason 说明）；2 = spec 非法（结构性错误）。

注意：本进程是一次性的 ⇒ `dist_common._RESTART_SEEN` 的跨代去重只在**本次调用内**
有效；跨调用去重由调用方持久化 memo，并经 spec 的 `seen` 预置回来（`seed_restart_state`），
判据本身仍只有 dist_common 一处实现。`pingHash` 就是给调用方写 memo 用的。
本文件不做任何删除/清理动作（沙箱安全），但仍按仓库纪律经
`bash tools/githook/nn-py-safe.sh` 启动。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dist_common


def _fail(msg: str) -> int:
    print(json.dumps({"error": msg}, ensure_ascii=False))
    return 2


def run_scan(spec: dict) -> dict:
    """扫描模式：本 CLI 自己 ping 每个 enabled 节点，判 stale 后下发升级。

    判据那一层**直接调用** `dist_common.upgrade_stale_nodes`——调用方（TS 工具）不再
    自己 ping、不再自己比 codeHash（此前那套是训练循环的重复实现，2026-09-19 用户
    指出）。`seen` 预置跨调用 memo，使 `dedup` 分支与常驻训练循环逐字一致。
    """
    cfg_path = str(spec.get("cfg_path") or "").strip()
    if not cfg_path:
        raise ValueError("cfg_path 缺失")
    expected = str(spec.get("expected_hash") or "").strip() or dist_common.compute_code_hash()
    branch = str(spec.get("branch") or "").strip()
    try:
        timeout = float(spec.get("timeout") or 20.0)
    except (TypeError, ValueError):
        raise ValueError("timeout 必须是数字") from None
    try:
        status_timeout = float(spec.get("status_timeout") or 3.0)
    except (TypeError, ValueError):
        raise ValueError("status_timeout 必须是数字") from None
    seen = spec.get("seen")
    if seen is not None and not isinstance(seen, list):
        raise ValueError("seen 必须是数组")
    dist_common.seed_restart_state(seen or [])
    cfg = dist_common.load_dist_config(cfg_path)
    if not isinstance(cfg, dict):
        raise ValueError(f"读不到节点配置（{cfg_path}）：文件缺失/损坏，或 nodes 不是数组")
    if not cfg.get("nodes"):
        # 配置里没有 enabled 节点：无可升级对象（不是结构性错误）。
        return {"dirty": _report_dirty(), "results": []}
    if bool(spec.get("dry_run")):
        # dry：只报告谁 stale、谁 current，不发任何 POST。
        results = []
        for n in cfg.get("nodes", []):
            if not n.get("enabled", True):
                continue
            nid = str(n.get("id") or n.get("url") or "?")
            ping = dist_common.node_ping(n["url"], n.get("authKey", ""), timeout=status_timeout)
            if ping is None:
                results.append({"id": nid, "ok": False, "reason": "unreachable", "pingHash": ""})
                continue
            ping_hash = str(ping.get("codeHash") or "")
            results.append(
                {
                    "id": nid,
                    "ok": False,
                    "reason": "current" if ping_hash == expected else "stale",
                    "pingHash": ping_hash,
                }
            )
        return {"dirty": _report_dirty(), "results": results}
    raw = dist_common.upgrade_stale_nodes(
        cfg,
        expected_hash=expected,
        branch=branch,
        status_timeout=status_timeout,
        restart_timeout=timeout,
    )
    results = [
        {
            "id": str(r.get("id")),
            "ok": bool(r.get("upgraded")),
            "reason": str(r.get("reason")),
            "pingHash": str(r.get("pingHash") or ""),
        }
        for r in raw
    ]
    return {"dirty": _report_dirty(), "results": results}


def _report_dirty() -> list[str]:
    """调用方要在日志里说明「为什么远端被拒」——取不到就不报（守卫侧仍会自行判定）。"""
    try:
        return list(dist_common.dirty_hash_files())
    except Exception:
        return []


def run_spec(spec: dict) -> dict:
    """显式节点模式：spec → {dirty, results}。可单测（monkeypatch 守卫即可）。

    调用方已 ping 过并把 hash 传来 ⇒ 不再 ping（避免同回合对同一节点打两次网络）。
    能自动 ping 的场景请用 run_scan（`cfg_path`），不要在这一侧重建探测。
    """
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是 JSON 对象")
    expected = str(spec.get("expected_hash") or "").strip()
    if not expected:
        raise ValueError("expected_hash 缺失（节点门的期望 codeHash）")
    branch = str(spec.get("branch") or "").strip()
    dry = bool(spec.get("dry_run"))
    try:
        timeout = float(spec.get("timeout") or 20.0)
    except (TypeError, ValueError):
        raise ValueError("timeout 必须是数字") from None
    raw_nodes = spec.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("nodes 必须是非空数组")

    dirty = spec.get("dirty")
    if dirty is not None and not isinstance(dirty, list):
        raise ValueError("dirty 必须是数组或 null")

    results: list[dict] = []
    for n in raw_nodes:
        if not isinstance(n, dict) or not str(n.get("url") or ""):
            raise ValueError("nodes[] 每项须含 url")
        nid = str(n.get("id") or n.get("url"))
        ping_hash = str(n.get("pingHash") or "")
        if ping_hash and ping_hash == expected:
            results.append({"id": nid, "ok": False, "reason": "current"})
            continue
        if dry:
            # dry 只看 dirty 与身份，不发 POST：给调用方一次「先说清楚再动手」的机会。
            eff_dirty = dirty
            if eff_dirty is None:
                eff_dirty = [] if dist_common.is_self_node(str(n["url"]), nid) else dist_common.dirty_hash_files()
            why = (
                f"dirty-tree:{len(eff_dirty)}"
                if eff_dirty and not dist_common.is_self_node(str(n["url"]), nid)
                else "planned"
            )
            results.append({"id": nid, "ok": False, "reason": why})
            continue
        # dirty=None ⇒ 守卫内部自己探测（远端才探；self 直接跳过探测，同 dist_common 语义）。
        if dirty is None:
            eff_dirty = [] if dist_common.is_self_node(str(n["url"]), nid) else None
        else:
            eff_dirty = dirty
        ok, reason = dist_common.request_upgrade_guarded(
            nid,
            str(n["url"]),
            str(n.get("authKey") or ""),
            branch,
            ping_hash,
            timeout=timeout,
            dirty=eff_dirty,
            expected_hash=expected,
        )
        results.append({"id": nid, "ok": bool(ok), "reason": str(reason)})

    # dirty 报告：self-only 的调用方不探测远端 dirty（与守卫同口径），但调用方要能
    # 在日志里说明「为什么远端被拒」——dry 分支已算过，这里再取一次对非 dry 也一致。
    reported_dirty = dirty if dirty is not None else _report_dirty()
    return {"dirty": list(reported_dirty or []), "results": results}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
    except Exception as e:
        return _fail(f"读 stdin 失败: {e}")
    if not raw.strip():
        return _fail("stdin 为空（需要 JSON spec）")
    try:
        spec = json.loads(raw)
    except json.JSONDecodeError as e:
        return _fail(f"spec 不是合法 JSON: {e}")
    if not isinstance(spec, dict):
        return _fail("spec 必须是 JSON 对象")
    try:
        out = run_scan(spec) if str(spec.get("cfg_path") or "").strip() else run_spec(spec)
    except ValueError as e:
        return _fail(str(e))
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
