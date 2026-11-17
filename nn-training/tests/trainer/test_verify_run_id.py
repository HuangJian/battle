"""GPU 真机验证课的 runId 覆盖：五档必须共用同一个 runId，否则 job_seed 不可比。

`RUN_ID` 在两个模块各自生成（dispatch 调度用一份、dispatch_queue 的 iterId 用一份）——
两处都要认同一个 env，否则「一半任务用 A、一半用 B」比不覆盖更糟。
默认未设 env 时必须仍是随机（生产零行为变化）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(env_value: str | None) -> tuple[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "nn-training")
    if env_value is None:
        env.pop("NN_VERIFY_RUN_ID", None)
    else:
        env["NN_VERIFY_RUN_ID"] = env_value
    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from trainer.dispatch_queue import RUN_ID as a;"
            "from trainer.dispatch import RUN_ID as b;"
            "print(a, b)",
        ],
        cwd=str(ROOT),
        env=env,
        text=True,
    ).strip()
    a, b = out.split()
    return a, b


def test_verify_run_id_is_shared_by_both_modules() -> None:
    a, b = _run("replay-verify-s1")
    assert a == b == "replay-verify-s1"


def test_absent_env_keeps_random_run_id() -> None:
    # 不设 env 时两处各自随机（dispatch 的 RUN_ID 只用于队列洗牌，与 iterId 的
    # dispatch_queue.RUN_ID 向来是两份）——这里只钉「仍然是 16 hex、不被固定」。
    a, b = _run(None)
    assert len(a) == 16 and len(b) == 16  # secrets.token_hex(8)
    assert a != b
