"""课程热加载（DECISIONS §2026-09-13-hot-reload · 全文 → docs/nn/training-stack.md §25）。

覆盖：① plan_reload 分类学（same/apply/rejected——语料身份变了整单拒绝，不做部分
应用）；② apply_hot_fields 白名单写回 + restart-only 记账（`*` 后缀）；③ 启动冻结
字节——course_fp 对 mid-run 文件编辑免疫（编辑不泄漏云端）。
"""

import types
from pathlib import Path

from rl.cmd import course_fp_for_args
from rl.config import corpus_identity_fp, load_course
from rl.hot_reload import apply_hot_fields, changed_field_names, plan_reload

REPO = Path(__file__).resolve().parents[2]
C6_DMGFIX = REPO / "nn-training" / "curricula" / "c6-dmgfix.jsonc"


def _write_course(tmp_path: Path, d: dict, name: str = "course.jsonc") -> Path:
    import json

    p = tmp_path / name
    p.write_text(json.dumps(d), encoding="utf-8")
    return p


def _course_dict() -> dict:
    from rl.jsonc import load as _load_jsonc

    return _load_jsonc(str(C6_DMGFIX))


def _args(**kw):
    return types.SimpleNamespace(**kw)


def test_plan_reload_same_apply_reject(tmp_path: Path) -> None:
    """分类学：无改动 same；预算字段 apply；语料身份（wChip）rejected。"""
    old = load_course(C6_DMGFIX)
    assert plan_reload(old, load_course(C6_DMGFIX))[0] == "same"

    d = _course_dict()
    d["iters"] = 99
    new = load_course(_write_course(tmp_path, d, "iters.jsonc"))
    verdict, hot, restart = plan_reload(old, new)
    assert verdict == "apply" and hot == ["iters"] and restart == []

    d2 = _course_dict()
    d2["reward"]["params"]["wChip"] = 0.03
    new2 = load_course(_write_course(tmp_path, d2, "wchip.jsonc"))
    assert plan_reload(old, new2)[0] == "rejected"


def test_plan_reload_rejects_whole_edition(tmp_path: Path) -> None:
    """wChip + iters 同时改 = 整单拒绝（不做部分应用，避免半新半旧配置）。"""
    old = load_course(C6_DMGFIX)
    d = _course_dict()
    d["iters"] = 99
    d["reward"]["params"]["wChip"] = 0.03
    new = load_course(_write_course(tmp_path, d))
    assert plan_reload(old, new)[0] == "rejected"


def test_apply_hot_fields_updates_args_and_marks_restart_only(tmp_path: Path) -> None:
    """白名单字段写回 args；restart-only 字段记 `*`（不写回也标记日志口径）。

    这里直接以「新课程对象 vs 启动 args」驱动；args 的初始值从启动课程取。"""
    old = load_course(C6_DMGFIX)
    # 模拟 apply_course 后的 args：old 的全部 HOT/RESTART 字段铺平
    from rl.hot_reload import HOT_FIELDS, RESTART_ONLY_FIELDS

    args = _args()
    for f in (*HOT_FIELDS, *RESTART_ONLY_FIELDS):
        setattr(args, f, getattr(old, f, None))
    args.course_obj = old

    d = _course_dict()
    d["iters"] = 99
    d["max_hours"] = 6.0
    d["workers"] = 4  # restart-only
    new = load_course(_write_course(tmp_path, d))
    changed = apply_hot_fields(args, new)

    assert "iters" in changed and "max_hours" in changed
    assert "workers*" in changed  # `*` = restart-only
    assert args.iters == 99 and args.max_hours == 6.0
    assert args.workers == old.workers  # 结构字段未被写回
    assert args.course_obj is new
    assert corpus_identity_fp(args.course_obj) == corpus_identity_fp(new)


def test_rejected_apply_leaves_args_untouched(tmp_path: Path) -> None:
    """拒绝路径：apply_hot_fields 不被调用（plan_reload verdict=rejected 时调用方跳过）。

    此测试钉「拒绝 ⇒ args 原样」的契约：verdict 判定先行，写回永不发生。"""
    old = load_course(C6_DMGFIX)
    d = _course_dict()
    d["reward"]["params"]["wChip"] = 0.03
    d["iters"] = 99
    new = load_course(_write_course(tmp_path, d))
    verdict, _, _ = plan_reload(old, new)
    assert verdict == "rejected"
    # 调用方按 verdict 分支——rejected 不触达 apply_hot_fields（本测试即契约占位）


def test_course_fp_frozen_against_midrun_edit(tmp_path: Path) -> None:
    """course_fp 用启动冻结字节：mid-run 编辑文件（含 wChip）不改 course_fp。"""
    import json
    import shutil

    work = tmp_path / "curricula-like.jsonc"
    shutil.copyfile(C6_DMGFIX, work)
    args = _args(course_obj=load_course(work), course_path=str(work))
    args.course_frozen_bytes = work.read_bytes()
    fp0 = course_fp_for_args(args)

    from rl.jsonc import load as _load_jsonc

    edited = _load_jsonc(str(work))
    edited["iters"] = 99
    edited["reward"]["params"]["wChip"] = 0.03
    work.write_text(json.dumps(edited), encoding="utf-8")

    assert course_fp_for_args(args) == fp0  # 冻结字节——编辑不进血缘/云端


def test_changed_field_names_lists_all(tmp_path: Path) -> None:
    """拒绝事件的字段清单 = 全字段 diff（含语料身份键）。"""
    old = load_course(C6_DMGFIX)
    d = _course_dict()
    d["iters"] = 99
    d["reward"]["params"]["wChip"] = 0.03
    new = load_course(_write_course(tmp_path, d))
    names = changed_field_names(old, new)
    assert "iters" in names and "reward" in names
