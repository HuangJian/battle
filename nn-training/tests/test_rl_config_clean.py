"""`tools/rl_config_clean.py` 契约（plan/rl-config-cleanup.plan.md §4 S1/S2、§6.1）。

要钉住的性质：
  · **dry-run 零写盘**（sha256 + mtime 双验，且不产生备份）；
  · **备份先于删除**：`--apply` 先落 `.bak.<ts>` 且 sha 与源一致，再动原文件；
  · **只删白名单键**：未知键（未来的手写键）一个字不碰；
  · **脱敏**：`remote_token` / `nodes[].authKey` 原文不出现在任何输出里；
  · **结构残缺拒删**：缺 `policy`/`rl`/`nodes` ⇒ 不写坏唯一的开训入口；
  · **courses 只删点名条目**；
  · 矩阵纯函数：来源优先级（课程 > level > rl-config > 缺省）与「全绿」判定。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

NN_ROOT = Path(__file__).resolve().parent.parent


def _load() -> Any:
    """按**文件路径**加载 `tools/rl_config_clean.py`（`tools/` 无 `__init__.py`，同 course_compare 先例）。"""
    spec = importlib.util.spec_from_file_location(
        "_rl_config_clean_under_test", NN_ROOT / "tools" / "rl_config_clean.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


clean = _load()

TOKEN = "SECRET-TOKEN-do-not-print"
AUTH = "SECRET-AUTHKEY-do-not-print"


def _cfg() -> dict[str, Any]:
    return {
        "version": 1,
        "policy": {
            "taskTimeoutSec": 900,
            "minDiskFreeMB": 2048,
            "streamKlCapIntent": 0.5,
            "streamWaveGamesIntent": 200,
            "upgradeBranch": "",
        },
        "intent_rl": {"iters": 0, "bc": "tmp/intent-weights-Bp.json"},
        "nodes": [{"id": "self", "url": "http://127.0.0.1:8443", "authKey": AUTH}],
        "rl": {
            "hub_port": 8787,
            "mb": 512,
            "remote_token": TOKEN,
            "stream": 0,
            "double_buffer": 0,
            "precollect_early": 0,
        },
        "courses": {"a": {"rollout_src": "run"}, "b": {}},
        "future_handwritten_key": {"keep": True},
    }


def _write(tmp_path: Path, cfg: dict[str, Any]) -> Path:
    p = tmp_path / "rl-config.json"
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ 删除（纯）

def test_deletions_hit_only_the_whitelist() -> None:
    out = clean.apply_deletions(_cfg())
    assert "intent_rl" not in out
    assert "upgradeBranch" not in out["policy"]
    assert "minDiskFreeMB" not in out["policy"]
    assert "streamKlCapIntent" not in out["policy"]
    assert "streamWaveGamesIntent" not in out["policy"]
    for gone in ("stream", "double_buffer", "precollect_early"):
        assert gone not in out["rl"]
    # 非白名单：原样保留
    assert out["policy"]["taskTimeoutSec"] == 900
    assert out["rl"]["mb"] == 512
    assert out["nodes"] == _cfg()["nodes"]
    assert out["future_handwritten_key"] == {"keep": True}
    assert out["courses"] == _cfg()["courses"]


def test_drop_course_only_removes_named_entries() -> None:
    out = clean.apply_deletions(_cfg(), ["a"])
    assert "a" not in out["courses"] and "b" in out["courses"]


def test_incomplete_structure_is_refused() -> None:
    broken = {k: v for k, v in _cfg().items() if k != "nodes"}
    try:
        clean.apply_deletions(broken)
    except AssertionError as e:
        assert "nodes" in str(e)
    else:  # pragma: no cover
        raise AssertionError("结构残缺竟然通过了清洗")


# ------------------------------------------------------------------ 写盘 / 备份

def test_dry_run_writes_nothing(tmp_path: Path, capsys: Any) -> None:
    p = _write(tmp_path, _cfg())
    before, mtime = _sha(p), p.stat().st_mtime_ns
    rc = clean.main(["--config", str(p)])
    assert rc == 0
    assert _sha(p) == before and p.stat().st_mtime_ns == mtime
    assert not list(tmp_path.glob("*.bak.*")), "dry-run 不应产生备份"


def test_apply_backs_up_then_deletes(tmp_path: Path) -> None:
    p = _write(tmp_path, _cfg())
    original = _sha(p)
    rc = clean.main(["--config", str(p), "--apply"])
    assert rc == 0
    backups = list(tmp_path.glob("rl-config.json.bak.*"))
    assert len(backups) == 1
    assert _sha(backups[0]) == original, "备份必须先于删除、且 sha 与源一致"
    new = json.loads(p.read_text(encoding="utf-8"))
    assert "intent_rl" not in new and "stream" not in new["rl"]
    assert new["future_handwritten_key"] == {"keep": True}


def test_incomplete_structure_refuses_to_write(tmp_path: Path) -> None:
    broken = {k: v for k, v in _cfg().items() if k != "policy"}
    p = _write(tmp_path, broken)
    before = _sha(p)
    rc = clean.main(["--config", str(p), "--apply"])
    assert rc == 2
    assert _sha(p) == before
    assert not list(tmp_path.glob("*.bak.*"))


# ------------------------------------------------------------------ 脱敏

def test_secrets_never_appear_in_output(tmp_path: Path, capsys: Any) -> None:
    p = _write(tmp_path, _cfg())
    assert clean.main(["--config", str(p)]) == 0
    out = capsys.readouterr().out
    assert TOKEN not in out and AUTH not in out
    assert "len=" in out  # 只留长度占位


def test_redact_reports_length_only() -> None:
    assert clean.redact("abc") == "…（len=3）"
    assert clean.redact(12345) == "…"


def test_secret_keys_are_the_single_source_of_redaction() -> None:
    """`SECRET_KEYS` 是脱敏的唯一清单：`rl` 段与 `nodes[]` 两处都按它脱（评审更正）。"""
    cfg = {
        "rl": {k: f"v-{k}" for k in clean.SECRET_KEYS},
        "nodes": [{k: f"n-{k}" for k in clean.SECRET_KEYS}],
    }
    out = clean.desensitize(cfg)
    for key in clean.SECRET_KEYS:
        assert "len=" in str(out["rl"][key])
        assert "len=" in str(out["nodes"][0][key])
    # hub URL / 端口不是凭据（plan §1.5 红线只点名 token/authKey）——别把它列进脱敏清单：
    # 列了但 `desensitize` 不脱两处就漂开（旧版的形状）。
    assert "remote_hub_url" not in clean.SECRET_KEYS


def test_dry_run_flag_is_accepted_and_conflicts_with_apply(tmp_path: Path) -> None:
    """`--dry-run` 是显式写法（plan §5 E2 就写的它），与 `--apply` 互斥。"""
    p = _write(tmp_path, _cfg())
    before = _sha(p)
    assert clean.main(["--config", str(p), "--dry-run"]) == 0
    assert _sha(p) == before and not list(tmp_path.glob("*.bak.*"))
    assert clean.main(["--config", str(p), "--apply", "--dry-run"]) == 2
    assert _sha(p) == before and not list(tmp_path.glob("*.bak.*")), "互斥时不得写盘"


def test_no_preview_suppresses_the_rl_preview(tmp_path: Path, capsys: Any) -> None:
    """`--no-preview` = **少打印**（不是「打印原文」）：本工具没有任何泄露凭据的路径。"""
    p = _write(tmp_path, _cfg())
    assert clean.main(["--config", str(p)]) == 0
    assert "rl 段预览" in capsys.readouterr().out
    assert clean.main(["--config", str(p), "--no-preview"]) == 0
    out = capsys.readouterr().out
    assert "rl 段预览" not in out
    assert TOKEN not in out and AUTH not in out


# ------------------------------------------------------------------ 矩阵纯函数

def test_resolve_key_source_precedence() -> None:
    rl = {"rl": {"mb": 512}}
    # 课程 > level > rl-config > 缺省
    assert clean.resolve_key_source("mb", course_keys={"mb": 1024}, level_keys={"mb": 2048}, rl_cfg=rl) == (1024, "course")
    assert clean.resolve_key_source("mb", course_keys={}, level_keys={"mb": 2048}, rl_cfg=rl) == (2048, "level")
    assert clean.resolve_key_source("mb", course_keys={}, level_keys={}, rl_cfg=rl) == (512, "rl-config")
    assert clean.resolve_key_source("mb", course_keys={}, level_keys={}, rl_cfg={}) == (None, "default")


def test_is_green_requires_every_course_to_declare() -> None:
    assert clean.is_green("mb", ["course", "level"]) is True
    assert clean.is_green("mb", ["course", "rl-config"]) is False
    assert clean.is_green("mb", []) is False


def test_whitelist_paths_are_the_documented_set() -> None:
    """防止有人顺手往白名单里加键（那是「未知键一律删」的滑坡）。"""
    assert set(clean.DELETE_PATHS) == {
        "intent_rl",
        "policy.upgradeBranch",
        "policy.minDiskFreeMB",
        "policy.streamKlCapIntent",
        "policy.streamWaveGamesIntent",
        "rl.stream",
        "rl.double_buffer",
        "rl.precollect_early",
    }


# ------------------------------------------------------------------ JSONC 加载

def test_load_jsonc_uses_the_product_loader(tmp_path: Path) -> None:
    """课程/level 文件带**尾逗号 + 行注释**时也必须读得进来。

    回归（2026-09-26 实测）：此前是 `strip_comments` + `json.loads`，漏了去尾逗号
    ⇒ `curricula/*.jsonc` 108 个里 88 个、`levels/*.jsonc` 25 个里 25 个都读不了，
    `--matrix` 只要遇到一门在训课程就 `JSONDecodeError` 崩掉（而「文件没读进来」
    会被误读成「课程没声明该键」= 静默删兜底）。产品侧 `load_course` 走
    `rl.jsonc.loads`（`strip_comments` → `_drop_trailing_commas` → `json.loads`）
    ——本工具必须同源。
    """
    p = tmp_path / "c.jsonc"
    p.write_text(
        '{\n  // 行注释\n  "level": "l1",\n  "mb": 1024,\n}\n',
        encoding="utf-8",
    )
    assert clean.load_jsonc(p) == {"level": "l1", "mb": 1024}


# ------------------------------------------------------------------ 矩阵范围 / 机器键

def test_machine_keys_are_never_green_for_deletion() -> None:
    """`local_slots` / `workers` 是**机器级**读数（不是课程兜底）⇒ 全绿也不删。

    `workers`：`dashboard/src/core/slots.ts::bareCapacity` = `max(rl.workers, rl.local_slots)`
    是本机并发容量；删了 ⇒ `Number(undefined ?? 0)` = 0 ⇒ 容量塌成 0，`checkCapacity`
    把每门课都报成超量（假红）。与 plan §3.2-4 对 `local_slots` 的豁免同一条理。
    """
    assert "workers" in clean.MACHINE_KEYS and "local_slots" in clean.MACHINE_KEYS
    assert "workers" not in clean.b_class_green(_cfg(), [])
    # 显式构造：唯一一门课把 workers 声明在课程文件里（全绿的形状）
    green = clean.green_keys_for_rows({"workers": ["course"], "mb": ["course"]})
    assert green == ["mb"], "workers 属机器级，不得进可删清单"


def test_b_class_green_only_when_every_course_declares() -> None:
    assert clean.green_keys_for_rows({"mb": ["course", "level"]}) == ["mb"]
    assert clean.green_keys_for_rows({"mb": ["course", "rl-config"]}) == []
    assert clean.green_keys_for_rows({"mb": []}) == []


# ------------------------------------------------------------------ 绿键删除

def test_apply_drops_green_b_class_keys_from_rl_block() -> None:
    out = clean.apply_deletions(_cfg(), b_class_keys=["mb"])
    assert "mb" not in out["rl"]
    # 别的 rl 键一个字不碰
    assert out["rl"]["hub_port"] == 8787
    assert out["future_handwritten_key"] == {"keep": True}


def test_plan_deletions_reports_green_b_class_keys() -> None:
    lines = "\n".join(clean.plan_deletions(_cfg(), b_class_keys=["mb", "not_present"]))
    assert "rl.mb: 512 → 删除" in lines
    assert "rl.not_present: 不存在，跳过" in lines


def test_dry_run_with_drop_b_class_still_writes_nothing(tmp_path: Path) -> None:
    p = _write(tmp_path, _cfg())
    before, mtime = _sha(p), p.stat().st_mtime_ns
    assert clean.main(["--config", str(p), "--drop-b-class", "--scope", "all"]) == 0
    assert _sha(p) == before and p.stat().st_mtime_ns == mtime
    assert not list(tmp_path.glob("*.bak.*"))
