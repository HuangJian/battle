"""`worker/ppo/loop.PPOSpec` 的形状护栏（`python.refactor.md` §P0-3）。

B8 ④（2026-11-19）曾特意把 `run_ppo` 的 15 个形参摊在签名上，理由是「形参表就是两个入口
的契约，包成对象只会把它隐形」。2026-11-22 改判的依据：契约在**入口**（`ppo_update_goal` /
`ppo_update_intent`，由 `test_ppo_loop_core.py::test_entry_signatures_are_frozen` 钉死），
而摊开的签名让两个入口各自手抄同一张旋钮表（`ckpt_path` / `target_kl` / `seed` /
`value_warmup_epochs` / `ref_model` / `kl_coef` / `on_epoch_done` / `rng`）—— 漏抄一个不报错：
warmup 静默退化成「直接 PPO」（value 头随机 ⇒ 策略塌缩）、kickstarting 静默关掉、
早停与断点续跑静默失效。

这里钉住：spec 是 frozen + kw_only · 15 个字段与缺省逐项对齐旧形参 · `run_ppo` 只收一份
spec · `HeadUpdateSpec.to_ppo` 确实把 15 个旋钮**逐个**交齐（用 AST 看 `PPOSpec(...)` 的关键字，
不是文本），且两个入口都只经这一条路（第四十九刀：13 个训练输入收进 `HeadUpdateSpec` 后，
逐个搬运只剩**一份**实现）。
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest
import torch

from worker.ppo.loop import HeadSpec, LossTerms, PPOSpec, run_ppo

PPO_DIR = Path(__file__).resolve().parents[2] / "worker" / "ppo"


def _tree(file_name: str) -> ast.AST:
    """`worker/ppo/<file_name>` → AST（两处 AST 判据共用；与 `test_ppo_loop_core.py` 同写法）。"""
    return ast.parse((PPO_DIR / file_name).read_text(encoding="utf-8"))

#: `(字段名, 缺省标记)` —— 与收进 spec 之前的 15 个形参逐一对齐（前 7 个无缺省）。
MISSING = dataclasses.MISSING
EXPECTED_FIELDS = [
    ("model", MISSING),
    ("opt", MISSING),
    ("chunks", MISSING),
    ("epochs", MISSING),
    ("device", MISSING),
    ("head", MISSING),
    ("terms", MISSING),
    ("ckpt_path", None),
    ("target_kl", 0.02),
    ("seed", 7),
    ("value_warmup_epochs", 0),
    ("ref_model", None),
    ("kl_coef", 0.0),
    ("on_epoch_done", None),
    ("rng", None),
]


def test_spec_is_frozen_and_fully_keyword_only() -> None:
    assert dataclasses.is_dataclass(PPOSpec)
    field_rows = dataclasses.fields(PPOSpec)
    field = field_rows[0].name  # 变量而非字面量（ruff B010）
    spec = _bare_spec()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in field_rows), [
        f.name for f in field_rows if not f.kw_only
    ]


def _zero_forward(
    _m: object, obs: torch.Tensor, _s: torch.Tensor, _i: torch.Tensor, _mask: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor | None, torch.Tensor]:
    zero = torch.zeros(obs.shape[0])
    return zero, None, zero


class _Stub:
    """`_PPOModel` / `_GradOpt` 的最小实现（本测试不真训，只构造 spec）。"""

    def train(self) -> None: ...

    def parameters(self) -> list[torch.Tensor]:
        return []

    def named_parameters(self) -> list[tuple[str, torch.Tensor]]:
        return []

    def zero_grad(self) -> None: ...


def _bare_spec() -> PPOSpec:
    return PPOSpec(
        model=_Stub(),
        opt=_Stub(),
        chunks=[],
        epochs=1,
        device="cpu",
        head=HeadSpec(tag="t", a_key="a", lp_key="lp", mask_key="mask"),
        terms=LossTerms(forward=_zero_forward, ent_coef=0.0),
    )


def test_field_table_matches_the_old_signature() -> None:
    """字段名 / 顺序 / 缺省逐项等价，且没有多余字段。"""
    rows = []
    for f in dataclasses.fields(PPOSpec):
        if f.default is not MISSING:
            rows.append((f.name, f.default))
        elif f.default_factory is not MISSING:
            rows.append((f.name, f.default_factory()))
        else:
            rows.append((f.name, MISSING))
    assert rows == EXPECTED_FIELDS


def test_run_ppo_takes_exactly_one_spec() -> None:
    params = inspect.signature(run_ppo).parameters
    assert [p.name for p in params.values()] == ["spec"]
    assert all(p.default is inspect.Parameter.empty for p in params.values())


#: 两个入口必须逐个交齐的 15 个关键字（`head` / `terms` 也在内：漏掉就是两个头并成一个）。
REQUIRED_KWARGS = sorted(name for name, _ in EXPECTED_FIELDS)


@pytest.mark.parametrize("file_name", ["loop.py"])
def test_to_ppo_passes_every_knob(file_name: str) -> None:
    """★ 本类失误形态：逐个搬运时漏一个关键字 —— 不报错，静默落回缺省。

    AST 而不是文本：只看 `PPOSpec(...)` 这次调用的关键字名集合；`head` / `terms` 也在内
    （漏掉就是两个头并成一个）。搬运只允许有一份实现（`HeadUpdateSpec.to_ppo`），否则
    又是两处手抄同一张表 —— 那正是本刀要杀的形状。
    """
    tree = _tree(file_name)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "to_ppo"
    )
    calls = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "PPOSpec"
    ]
    assert len(calls) == 1, f"to_ppo 里 `PPOSpec(...)` 出现 {len(calls)} 次（应恰好 1 次）"
    got = sorted(k.arg for k in calls[0].keywords if k.arg is not None)
    assert got == REQUIRED_KWARGS, f"to_ppo 漏交 {sorted(set(REQUIRED_KWARGS) - set(got))}"
    assert not calls[0].args, "to_ppo 仍用位置实参构造 spec（kw_only 就是为了不许这样）"


@pytest.mark.parametrize(("file_name", "entry"), [("goal.py", "ppo_update_goal"), ("intent.py", "ppo_update_intent")])
def test_entries_go_through_to_ppo(file_name: str, entry: str) -> None:
    """入口只许 `run_ppo(spec.to_ppo(_SPEC, LossTerms(...)))` —— 不迁回就地手拼 spec。"""
    tree = _tree(file_name)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == entry)
    callees = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    adapted = {
        n.func.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert callees <= {"run_ppo", "LossTerms"}, f"{entry} 直接调了 {sorted(callees - {'run_ppo', 'LossTerms'})}"
    assert {"run_ppo"} <= callees, f"{entry} 不再转发给 run_ppo"
    assert adapted == {"to_ppo"}, f"{entry} 的转发面变了：{sorted(adapted)}"
    direct = {getattr(n.func, "id", "") for n in ast.walk(fn) if isinstance(n, ast.Call)}
    assert direct.isdisjoint({"PPOSpec"}), f"{entry} 又就地手拼 PPOSpec（应交给 to_ppo）"
