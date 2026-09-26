"""BN-free 硬约束守护（plan/python-refactor.md P2-4）。

**背景**：`models/student.py:2-7` 声明 BN-free 是**硬约束**——纯 TS 运行时
`src/nn/infer.ts` 要逐字节复现前向，BatchNorm 的 running stats 依赖训练时数据分布，
无法在导出权重中固化。此前全仓测试对 BatchNorm 零命中：有人在 student 族网络里加
一个 `nn.BatchNorm2d`，不会有任何测试失败，只会在导出后于浏览器里静默跑歪。

**守护范围**：TS 运行时消费的所有模型族（StudentNet / PPOStudent / IntentNet /
GoalNet / NNPolicy）必须零 BatchNorm / SyncBatchNorm / Dropout。教师网 `rl_model.py`
（ResNet）只在 Python 侧蒸馏用、不进 TS，允许 BN（RL-Net-Selection 评审时教师有意用 BN
提容量）。

**Dropout 同样禁止**：eval 模式下 Dropout 是恒等，测试期「看似正常」，但 TS 复现时
训练/推理分布不一致——与 BN-free 同一理由，一并禁止。

**为什么是源码扫描（2026-09-26 改）**：这条约束是**结构**性的（「这族网络里不许出现这些
层」），不是数值行为。原实现靠实例化每个模型再 `model.modules()` 查层，代价是整个文件
`import torch`，还隐含「每个模型都得能用最小参数构造出来」。改成 AST 扫模型源码后：
零 torch（文件移出 torch 组）、并额外覆盖「定义了一层但没接进 forward」的形态
（有人偷偷加一层时正是这个样子）。守卫自带自证用例（写临时文件验真能抓）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers import source_scan

#: TS 消费的模型模块（新增导出给 TS 的网络必须登记到这里）。教师网 `models/rl_model.py`
#: 有意用 BN（只在 Python 侧蒸馏用），**刻意不在**名单里。
TS_CONSUMED_MODULES = (
    "models/core.py",  # NNPolicy（BC 基座，也被 TS 消费）
    "models/student.py",  # StudentNet / PPOStudent
    "models/intent_net.py",  # IntentNet
    "models/goal_net.py",  # GoalNet
)

#: 禁止的层名：`nn.X(...)` 的属性名，或 `from torch.nn import X` 的名字（精确匹配，
#: 故 `nn.GroupNorm` / `nn.DropoutCounter` 这类只是前缀相同的名字不误报）。
_FORBIDDEN = frozenset(
    {
        "BatchNorm1d",
        "BatchNorm2d",
        "BatchNorm3d",
        "SyncBatchNorm",
        "Dropout",
        "Dropout1d",
        "Dropout2d",
        "Dropout3d",
    }
)


def _forbidden_layers(src: str) -> list[tuple[int, str]]:
    """源码里出现的禁用层 → [(行号, 名字)]；`nn.X(…)` 与直接 import 的名字都算。"""
    tree = ast.parse(src)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN:
            out.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN:
            out.append((node.lineno, node.id))
    return out


@pytest.mark.parametrize("rel", TS_CONSUMED_MODULES)
def test_ts_models_have_no_batchnorm_or_dropout(rel: str) -> None:
    """TS 消费的模型源码必须零 BatchNorm / SyncBatchNorm / Dropout（P2-4 守护）。"""
    src = source_scan.read_text(str(ROOT / rel))
    found = _forbidden_layers(src)
    assert not found, (
        f"{rel} 含 TS 无法逐字节复现的层 {found}——BN 的 running stats 依赖训练数据分布、"
        f"Dropout 的训练/推理不一致，二者都会让 src/nn/infer.ts 的前向静默跑歪。"
    )


def test_the_guard_actually_catches_forbidden_layers(tmp_path: Path) -> None:
    """自证：守卫不是恒真 —— 属性写法与 import 写法都抓；前缀相同的名字放行。"""
    bad = tmp_path / "bad_model.py"
    bad.write_text(
        "import torch.nn as nn\n"
        "from torch.nn import Dropout2d\n"
        "\n"
        "class M(nn.Module):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        self.bn = nn.BatchNorm2d(3)\n"
        "        self.dp = Dropout2d(0.1)\n",
        encoding="utf-8",
    )
    got = _forbidden_layers(bad.read_text(encoding="utf-8"))
    assert sorted(name for _, name in got) == ["BatchNorm2d", "Dropout2d"], got

    good = tmp_path / "good_model.py"
    good.write_text(
        "import torch.nn as nn\n"
        "\n"
        "class M(nn.Module):\n"
        "    def __init__(self):\n"
        "        super().__init__()\n"
        "        self.c = nn.Conv2d(3, 4, 3)\n"
        "        self.e = nn.GroupNorm(2, 4)\n"
        "        self.f = nn.DropoutCounter()\n",  # 名字只是前缀相同
        encoding="utf-8",
    )
    assert _forbidden_layers(good.read_text(encoding="utf-8")) == []
