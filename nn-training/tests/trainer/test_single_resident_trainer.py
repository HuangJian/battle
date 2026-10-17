"""常驻训练准入闸（plan/single-resident-trainer.plan.md §1 + §3）。

契约三条：

  ① **没有一次性动作标志** ⇒ 常驻训练 ⇒ 拒启（与 `--course` 无关：无课程的老全局路径
     同样是常驻写者），且拒启消息**必须给出替代**（否则操作员只会去找 `--force`，
     而 `--force` 是锁抢先、过不了闸）；
  ② 一次性腿（`--smoke` / `--export-bundle` / `--collect-only` / `--echo-config`）一字不拦；
  ③ **S3（2026-10-17 用户裁决）**：`run_rl.py` 里**没有**常驻训练分支——逃生舱
     `--allow-detached-trainer` 连同它的自报文件/控制台横幅一起退役。判据从「拦走旁路」
     变成「没有旁路可走」：常驻训练唯一入口 = `run_rl_cluster.py --serve`。

③ 的两条正面判据（不是「某个词没出现」，而是**结构**）：

  · `trainer/run_rl.py` 的 AST 里没有对 `run_training` 的调用/import；
  · 唯一保留的 `run_training` 调用点在 `trainer/once_shot.py`（冒烟腿真跑一轮）。
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker.trainer_gate import (
    ONESHOT_FLAGS,
    REFUSE_MESSAGE,
    REFUSE_MESSAGE_BC,
    oneshot_action_present,
    refuse_detached_trainer,
    resident_trainer_wanted,
)


def _args(**over: object) -> types.SimpleNamespace:
    """裸 `run_rl.py --course x` 的命名空间（再按需覆写某几个动作键）。"""
    ns = types.SimpleNamespace(
        course="x20-clutch",
        smoke=False,
        collect_only=0,
        export_bundle="",
        echo_config=False,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# ────────────────────────── ① 判据（纯函数） ──────────────────────────


def test_bare_course_run_is_refused() -> None:
    """裸 `--course` = 常驻训练循环 ⇒ 拒启，且消息给出替代命令。"""
    msg = refuse_detached_trainer(_args())
    assert msg == REFUSE_MESSAGE
    # 拒启必须**给出替代**（否则操作员只会去找 `--force`）：三条替代路径。
    # 判据是消息的**结构**（三条 `  · ` 条目），不是某几个词的字面（改措辞不该红）。
    assert msg.count("\n  · ") == 3


def test_legacy_global_run_without_course_is_refused_too() -> None:
    """无 `--course` 的老全局路径同样是常驻写者（`.run_rl.lock` 时代形态）。"""
    assert refuse_detached_trainer(_args(course="")) == REFUSE_MESSAGE


@pytest.mark.parametrize(
    "flag",
    [
        {"smoke": True},
        {"collect_only": 1},
        {"export_bundle": "tmp/x.bundle.zip"},
        {"echo_config": True},
    ],
)
def test_oneshot_legs_are_exempt(flag: dict) -> None:
    """四类一次性腿一字不拦（豁免判的是**标志**，不是课程）。"""
    args = _args(**flag)
    assert refuse_detached_trainer(args) is None
    assert oneshot_action_present(args) is True
    assert resident_trainer_wanted(args) is False


def test_oneshot_flag_set_is_exactly_these_four() -> None:
    """豁免集合是**闭集**：S3 之后没有任何「我知道我在干什么」的旗标（逃生舱已退役）。"""
    assert set(ONESHOT_FLAGS) == {"smoke", "collect_only", "export_bundle", "echo_config"}


def test_collect_only_zero_is_not_an_action() -> None:
    """`--collect-only 0` = 没带（argparse 默认值），仍是常驻；>0 才是动作。"""
    assert refuse_detached_trainer(_args(collect_only=0)) == REFUSE_MESSAGE
    assert refuse_detached_trainer(_args(collect_only=1)) is None


def test_bc_message_points_at_bc_alternative() -> None:
    msg = refuse_detached_trainer(_args(), message=REFUSE_MESSAGE_BC)
    assert msg == REFUSE_MESSAGE_BC
    assert msg.count("\n  · ") == 2  # 控制台 / 一次性腿：BC 侧也有替代


# ────────────────────── ③ S3：入口里没有常驻训练分支 ──────────────────────


def _called_names(path: Path) -> set[str]:
    """文件里**被调用**的函数名集合（AST；`f()` / `mod.f()` 都算 `f`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _imported_names(path: Path) -> set[str]:
    """文件里 import 的顶层名字（`from x import y` ⇒ `y`；`import x.y` ⇒ `x.y`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ImportFrom, ast.Import)):
            names |= {a.name for a in node.names}
    return names


def test_run_rl_has_no_resident_training_call() -> None:
    """验收 §3.4：`run_rl.py` 里**搜不到** `run_training(` 调用（常驻训练只能从 serve 进）。"""
    entry = ROOT / "trainer" / "run_rl.py"
    # 集合判据（不是子串扫描）：`isdisjoint` = 「交集为空」
    assert _called_names(entry).isdisjoint({"run_training"}), (
        "run_rl.py 又长出常驻训练分支了 —— 常驻写者只允许 run_rl_cluster.py --serve"
    )
    assert _imported_names(entry).isdisjoint({"trainer.loop"}), (
        "run_rl.py 不该 import 训练循环（那是 once_shot / serve 的事）"
    )


def test_once_shot_holds_the_only_training_call() -> None:
    """一次性腿的实现搬到了 `once_shot.py`（冒烟是真跑一轮 ⇒ 那里必须有 `run_training`）。"""
    assert not _called_names(ROOT / "trainer" / "once_shot.py").isdisjoint({"run_training"})


# ────────────────── ③ 分流：壳把四条腿交给 once_shot，不自己跑 ──────────────────


@pytest.mark.parametrize(
    ("flag", "locked"),
    [
        (["--smoke"], True),
        (["--export-bundle", "tmp/x.bundle.zip"], False),
    ],
)
def test_lock_policy_per_leg(flag: list[str], locked: bool) -> None:
    """§3.1 选项 A：**只有导出腿不取课锁**（只读快照，与 serve 并行是设计目标）。

    冒烟保留取锁——它真跑一轮（collect → publish → echo → 作废），作废点全在收官/权重层，
    **没有**「零写 traj」的判据 ⇒ 与 serve 抢同一批 traj 的面还在。
    """
    from trainer.once_shot import takes_course_lock

    ns = _args()
    if flag[0] == "--smoke":
        ns.smoke = True
    else:
        ns.export_bundle = flag[1]
    assert takes_course_lock(ns) is locked


def _drive_entry(monkeypatch, tmp_path: Path, extra: list[str]) -> list[str]:
    """跑**真入口** `run_rl.main()`（参数 = 一门真课程 + `extra`），返回分流的 course_key 列表。

    `run_once_shot` 被替换成记录器：这里验证的是**壳的分流接线**（腿一进入就交给 once_shot），
    而腿内部的真副作用（快照/推送/hub/训练）属于各自的专项用例。
    """
    from trainer import run_rl

    cfg = tmp_path / "rl-config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(cfg))
    seen: list[str] = []
    monkeypatch.setattr(
        "trainer.once_shot.run_once_shot",
        lambda args, *, course_key: seen.append(course_key),
    )
    monkeypatch.setattr(sys, "argv", ["trainer/run_rl.py", "--course", "s-dodge", *extra])
    monkeypatch.chdir(ROOT)
    run_rl.main()
    return seen


@pytest.mark.parametrize(
    "extra",
    [["--smoke"], ["--export-bundle", "tmp/x.bundle.zip"], ["--collect-only", "1"]],
)
def test_shell_dispatches_oneshot_legs(monkeypatch, tmp_path: Path, extra: list[str]) -> None:
    """三条腿都从壳分流进 `once_shot`（带的是**同一个** course_key：课程文件 stem）。"""
    assert _drive_entry(monkeypatch, tmp_path, extra) == ["s-dodge"]


def test_echo_config_short_circuits_before_dispatch(monkeypatch, tmp_path: Path) -> None:
    """`--echo-config` 是**解析面**的 dump：在分流**之前**短路（它是 oracle 的读面）。

    判据不靠 stdout 文本：打桩 `echo_config` 看它被调过，且 `run_once_shot` **没**被调。
    """
    dumped: list[object] = []
    monkeypatch.setattr("worker.config.echo_config", lambda args, course: dumped.append(course))
    assert _drive_entry(monkeypatch, tmp_path, ["--echo-config"]) == []
    assert len(dumped) == 1  # 走了 dump 分支，而不是腿


# ────────────────────────── 真入口跑一遍 ──────────────────────────


def test_run_rl_entry_refuses_bare_start(tmp_path: Path, monkeypatch) -> None:
    """`trainer/run_rl.py`（无参数、无课程）在**真入口**上被闸拦下（不只是谓词对）。"""
    from trainer import run_rl

    cfg = tmp_path / "rl-config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(cfg))
    monkeypatch.setattr(sys, "argv", ["trainer/run_rl.py"])
    # 闸在代码快照之前 ⇒ 快照不该被触发；但为稳妥仍打桩（真跑会碰 hub）
    monkeypatch.setattr(
        "remote.code_snapshot.ensure_cluster_snapshot", lambda **_kw: None, raising=False
    )
    monkeypatch.chdir(ROOT)  # main() 自己会 chdir 到仓根；monkeypatch 兜底还原
    with pytest.raises(SystemExit) as e:
        run_rl.main()
    assert str(e.value) == REFUSE_MESSAGE  # 真入口吐的就是那条替代消息（不多不少）


def test_run_bc_entry_refuses_bare_start(monkeypatch) -> None:
    """`trainer/run_bc.py --course X`（无 `--smoke`）= 常驻 BC 编排 ⇒ 拒启（真入口）。"""
    from trainer import run_bc

    monkeypatch.chdir(ROOT)
    with pytest.raises(SystemExit) as e:
        run_bc.main(["--course", "x20-clutch"])
    assert str(e.value) == REFUSE_MESSAGE_BC


def test_cli_has_no_escape_hatch_flag() -> None:
    """逃生舱旗标**已不存在**（退役它就不能留半条：脚本传了它 = 未知参数响亮崩）。

    S3 的语义 =「没有旁路可走」；留一个能解析但什么都不做的旗标只会让人以为绕过了闸。
    """
    from worker.cli import build_argparser

    ap = build_argparser("per-tick", {})
    assert not any("--allow-detached-trainer" in a.option_strings for a in ap._actions)
