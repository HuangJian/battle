"""intent 干净评估的 policy 选择（P′-4a，2026-11-28）。

`run_clean_eval` 曾对 mode=intent 硬编码 `--policy intent-exec`。option 线的权重是
6 维联合槽（intentDim=6），用 8 意图执行器评估会把槽 id 当意图、语义全错。选择必须
由权重 `arch.intentDim` 决定（单一来源）。
"""

from __future__ import annotations

import json

from trainer.eval_m1 import intent_eval_policy


def _weights(path, arch: dict | None) -> str:
    doc: dict[str, object] = {"params": {}}
    if arch is not None:
        doc["arch"] = arch
    p = path / "w.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return str(p)


def test_option_weights_use_hier_option(tmp_path) -> None:
    assert intent_eval_policy(_weights(tmp_path, {"kind": "intent", "intentDim": 6})) == "hier-option"


def test_legacy_intent_weights_use_intent_exec(tmp_path) -> None:
    assert intent_eval_policy(_weights(tmp_path, {"kind": "intent", "intentDim": 8})) == "intent-exec"


def test_missing_arch_falls_back_to_intent_exec(tmp_path) -> None:
    assert intent_eval_policy(_weights(tmp_path, None)) == "intent-exec"
    assert intent_eval_policy(str(tmp_path / "absent.json")) == "intent-exec"
