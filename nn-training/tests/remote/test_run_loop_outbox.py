"""tests/remote/test_run_loop_outbox.py —— `--outbox` 与 `--no-deliver` 的不变量（W3，P0-1）。

评审 P0-1 说的是一个**静默**的失败形态：push 段里 `hub_url` / `token` 都是空，而补传有两道
互相独立的闸（`deliver=not args.no_deliver` 与 `make_deliverer` 的 hub_url 早退）——两者一叠，
结果是**训练照跑、outbox 永远是空的**，最后被 hold stale 判掉线。所以这一层钉住：

  * `run_standalone(outbox_dir=...)` **真的**写 body（`rounds/itNNNN.json` + `final.json`）；
  * 同一趟里**从未构造过 HTTP 补传腿**（不是「没有 POST」——那是更弱的断言）；
  * `--outbox` 模式下 `run_loop.main` **不得**回填任务包里的 `hub_url`（回填它就变回 HTTP 腿，
    而包里不带 token ⇒ 又是「静默什么都不做」的那条路）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.bundle as bundle_mod
import remote.deliver_proc as deliver_proc_mod
import remote.run_loop as run_loop_mod
from remote import deliver_parts
from remote.deliver_outbox import LocalOutboxDeliverer, read_state, round_path
from remote.deliver_proc import make_deliverer
from remote.run_loop import run_standalone
from tests.remote.test_offline_deliver import RUN, _make_artifacts  # type: ignore
from tests.remote.test_run_loop import (  # type: ignore
    _FakeRunJob,
    _quiet,
    _run,
    _warm_code_cache,
)
from worker.plan import planned_iters


def test_run_standalone_with_outbox_writes_rounds_and_constructs_no_http_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正反两面：① body 真的落盘；② 全程**没构造过** `OfflineDeliverer`（只有「没 POST」不够）。"""
    plan, _m, _fake, _result, art = _run(tmp_path, max_iters=2)  # 只跑两轮，留出可续跑的尾巴
    out = tmp_path / "outbox"
    monkeypatch.setenv("NN_DELIVER_MODE", "thread")  # 进程内模式（真实装配链，不起子进程）

    def _never(**_kw: Any) -> Any:  # 构造即失败：这就是「outbox 分支必须先于 hub 早退」的判据
        raise AssertionError("outbox 模式不许构造 HTTP 补传腿（P0-1）")

    monkeypatch.setattr(deliver_proc_mod, "OfflineDeliverer", _never)

    res = run_standalone(
        artifacts_dir=art,
        run_job_fn=_FakeRunJob(tmp_path),
        device="cpu",
        code_cache_dir=_warm_code_cache(tmp_path),
        outbox_dir=out,
        # ★ 任务包就是这么调的：`--outbox` + `--no-deliver`（只关 HTTP，outbox 必须活）。
        deliver=False,
        log=_quiet,
    )
    planned = planned_iters(plan)
    assert res["it_end"] == planned[-1]
    # 缺省 `floor_it=0` ⇒ 产物目录账本里的每一轮都物化（**不丢**优先于不发重复体；
    # 已经上过 hub 的历史轮由 hub 按内容去重，W4 的派发器会把 hub cursor 当 `floor_it` 传下来）。
    for it in planned:
        assert round_path(out, it).exists(), f"outbox 缺 it{it} 的 body"
    st = read_state(out)
    assert sorted(int(k) for k in st["rounds"]) == planned
    assert all(st["rounds"][str(it)]["rev"] == 1 for it in planned)
    assert (out / "final.json").exists(), "段末摘要必须落盘（hub 来拉的就是它）"
    assert json.loads((out / "final.json").read_text(encoding="utf-8"))["it_end"] == planned[-1]


def test_main_passes_the_outbox_and_never_backfills_the_pack_hub_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--outbox` ⇒ 不回填包里的 `hub_url`；不给 `--outbox` ⇒ 照旧回填（对照组）。"""
    # `run_standalone` 会被打桩，但 `main` 自己仍要读产物目录（`load_planned_manifest`）⇒ 给一份真的。
    dest = _run(tmp_path)[4]
    out = tmp_path / "outbox"
    monkeypatch.setattr(
        bundle_mod,
        "read_bundle_index",
        lambda _p: {"run_id": "r", "it": 1, "end_it": 3, "hub_url": "https://hub.example"},
    )
    monkeypatch.setattr(
        bundle_mod,
        "import_bundle",
        lambda _p, d: {
            "artifacts_dir": str(d),
            "end_it": 3,
            "hub_url": "https://hub.example",
            "ts_code_tree": "",
        },
    )
    seen: dict[str, Any] = {}

    def spy(*_a: Any, **kw: Any) -> dict[str, Any]:
        seen.clear()
        seen.update(kw)
        return {"it_end": 3, "run_state": "noop", "artifacts": {"dir": str(dest), "zip": "x"}}

    monkeypatch.setattr(run_loop_mod, "run_standalone", spy)
    argv = ["--bundle", str(tmp_path / "task.zip"), "--artifacts", str(dest)]

    assert run_loop_mod.main([*argv, "--outbox", str(out)]) == 0
    assert seen["outbox_dir"] == str(out)
    assert seen["hub_url"] == "", "outbox 模式回填 hub_url ⇒ 这条腿就变回 HTTP（且没有 token）"

    assert run_loop_mod.main(argv) == 0
    assert seen["outbox_dir"] == ""
    assert seen["hub_url"] == "https://hub.example", "没有 outbox 时包里的地址仍要照旧带上"


def test_make_deliverer_builds_an_outbox_leg_without_any_hub_credentials(tmp_path: Path) -> None:
    """P0-1 的工厂面：有 `outbox_dir` 就要有腿（哪怕 hub_url/token 全空）；没它才返回 None。"""
    root = _make_artifacts(tmp_path / "art", iters=(1,))
    out = tmp_path / "outbox"
    leg = make_deliverer(
        hub_url="",
        hub_token="",
        run_id=RUN,
        artifacts_dir=root,
        outbox_dir=out,
        mode="thread",
        log=_quiet,
    )
    assert isinstance(leg, LocalOutboxDeliverer)
    leg.start()
    leg.submit_round(1)
    end = time.time() + 10.0
    while time.time() < end and not round_path(out, 1).exists():
        # sleep-ok: 轮询步长（等的是后台线程真的写了文件；超时只当挂起兜底）
        time.sleep(0.02)
    assert round_path(out, 1).exists(), "线程模式的 outbox 腿必须真的写盘"
    leg.close(5.0)

    assert (
        make_deliverer(
            hub_url="", hub_token="", run_id=RUN, artifacts_dir=root, log=_quiet
        )
        is None
    ), "没有 outbox 也没有 hub ⇒ 这条腿不存在（旧语义不变）"


def test_outbox_leg_reaches_the_body_through_the_parts_module(tmp_path: Path) -> None:
    """`parts` 是**注入**的：换一个空实现就什么都不该写（注入边界是显式的，不是碰巧）。"""

    class _Noop:
        def rounds_with_rows(self, _root: str | Path) -> set[int]:
            return set()

        def eval_rows_for(self, *_a: Any, **_kw: Any) -> tuple[list[dict], int, int]:
            return [], 0, 0

        def row_for(self, *_a: Any, **_kw: Any) -> dict | None:
            return None

        def build_round_body(self, *_a: Any, **_kw: Any) -> Any:
            raise AssertionError("不该被调用（`rounds_with_rows` 是空的）")

        def build_result_body(self, **_kw: Any) -> tuple[dict, bytes]:
            raise AssertionError("不该被调用")

        def build_metrics_body(self, *_a: Any, **_kw: Any) -> tuple[dict, int]:
            raise AssertionError("不该被调用")

    root = _make_artifacts(tmp_path / "art", iters=(1,))
    leg = LocalOutboxDeliverer(
        outbox_dir=tmp_path / "outbox",
        artifacts_dir=root,
        run_id=RUN,
        parts=_Noop(),  # type: ignore[arg-type]
        log=_quiet,
    )
    assert leg.sync() == 0
    assert not round_path(tmp_path / "outbox", 1).exists()
    assert deliver_parts is not None  # 真装配器在别处被测（test_deliver_outbox 的字节对账）
