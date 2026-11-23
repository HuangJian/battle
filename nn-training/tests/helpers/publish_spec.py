"""测试共用的 `PublishSpec` 构造器（56 参发布签名的单一测试入口）。

`_publish` 辅助函数曾各自抄一份必填字段；改字段名时要改十几处。这里只保留
**能过协议校验的最小集**，各用例用 `overrides` 表达差异。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remote.hub_publish import PublishSpec


def publish_fields(root: Path, **overrides: Any) -> dict[str, Any]:
    """最小必填字段集；`root` 同时给出job_root / jsonl / traj 的默认落点。"""
    base: dict[str, Any] = {
        "job_root": str(root / "jobs"),
        "jsonl_path": str(root / "training_log.jsonl"),
        "run_id": "spec-run",
        "it": 1,
        "traj_dir": str(root),
        "shard_dirs": [],
        "commit": "c" * 40,
        "code_sha256": "z" * 64,
        "course": "// course jsonc\n{}",
        "course_fp": "a" * 64,
        "mode": "per-tick",
        "epochs": 1,
        "mb": 2,
        "lr": 1e-4,
        "gamma": 0.995,
        "lam": 0.95,
        "init_weights_path": "",
    }
    return {**base, **overrides}


def make_spec(root: Path, **overrides: Any) -> PublishSpec:
    return PublishSpec(**publish_fields(root, **overrides))
