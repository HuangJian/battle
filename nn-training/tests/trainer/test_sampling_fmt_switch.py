"""控制台「采样格式 auto | full | replay」全局开关（2026-11-20 用户）。

需求原话：面板首页「采样节点」区域放一枚三档 toggle，默认 auto；选 `full`/`replay` 时
**全局**采用该模式（网络差 ⇒ replay 省传输体积；网络好且样本不大 ⇒ full 省 PPO worker
重建语料的时间）。

两侧契约（缺一条面板就是谎报或写不出效果）：

1. **读面** `worker.config_file.global_corpus_fmt()`：读 `rl.corpus_fmt_mode`，缺省/脏值 = auto；
2. **生效面** `RoundSteps._resolve_corpus_fmt()`：全局 full/replay **压过课程请求**，
   但同机腿（`rollout_src=node` / `--export-bundle`）仍强制 full；
3. **可切换**：每轮边界重读 ⇒ 训练途中改档下一轮生效，切回 auto 恢复课程自己的值
   （`_corpus_fmt_req` 只存**课程请求**，不被全局污染）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.config_file as file_mod
from trainer.loop_round_steps import RoundSteps


def _write_config(tmp_path, monkeypatch, body: str) -> None:
    p = tmp_path / "rl-config.json"
    p.write_text(body, encoding="utf-8")
    monkeypatch.setenv(file_mod.RL_CONFIG_ENV, str(p))


def test_global_corpus_fmt_reads_whitelist_and_defaults_to_auto(tmp_path, monkeypatch) -> None:
    """读面：缺省 auto；三个活值原样；大小写/脏值按白名单收口。"""
    # 文件都不存在 ⇒ auto（存量配置的今天行为）
    monkeypatch.setenv(file_mod.RL_CONFIG_ENV, str(tmp_path / "nope.json"))
    assert file_mod.global_corpus_fmt() == "auto"
    assert file_mod.CORPUS_FMT_MODES == ("auto", "full", "replay")

    for raw, want in [
        ("replay", "replay"),
        ("full", "full"),
        ("auto", "auto"),
        (" REPLAY ", "replay"),  # 面板/手改都可能带空白或大写
        ("bogus", "auto"),  # 脏值不猜：混用两种语料包 = take() 失配
        ("true", "auto"),
    ]:
        _write_config(
            tmp_path, monkeypatch, json.dumps({"rl": {"corpus_fmt_mode": raw}})
        )
        assert file_mod.global_corpus_fmt() == want, raw

    # rl 块不是 dict / 整份不是 dict ⇒ auto（与 read_rl_config_file 的降级同一结果）
    _write_config(tmp_path, monkeypatch, '{"rl": 3}')
    assert file_mod.global_corpus_fmt() == "auto"


class _Loop(RoundSteps):
    """`_resolve_corpus_fmt` 只需要 `self.args` + 两个去重键（不碰 World/引擎）。

    继承而非造一个鸭子类型替身：mypy 才能真的检查那次调用（替身会被 arg-type 拒收，
    而 `cast` 会把类型检查关掉）。父类 __init__ 不跑——本函数不依赖任何父类状态。
    """

    def __init__(self, **args) -> None:
        self.args = SimpleNamespace(**args)


def _resolve(stub: _Loop, src: str = "local") -> str:
    stub._resolve_corpus_fmt(src)
    return str(stub.args.corpus_fmt)


def test_global_override_beats_course_request(tmp_path, monkeypatch) -> None:
    """生效面：全局 replay 压过课程的 full；全局 full 压过课程的 replay。"""
    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "replay"}}')
    assert _resolve(_Loop(corpus_fmt="full")) == "replay"

    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "full"}}')
    assert _resolve(_Loop(corpus_fmt="replay")) == "full"


def test_auto_keeps_course_request(tmp_path, monkeypatch) -> None:
    """auto = 听课程自己声明（今天的行为），不凭空改成 full。"""
    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "auto"}}')
    assert _resolve(_Loop(corpus_fmt="replay")) == "replay"
    assert _resolve(_Loop(corpus_fmt="full")) == "full"
    # 完全没有该键 = 与 auto 逐字节同义
    _write_config(tmp_path, monkeypatch, "{}")
    assert _resolve(_Loop(corpus_fmt="replay")) == "replay"


def test_same_machine_leg_still_forces_full(tmp_path, monkeypatch) -> None:
    """同机腿安全规则不被全局档推翻（replay 零收益且节点盘上不再有 obs）。"""
    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "replay"}}')
    assert _resolve(_Loop(corpus_fmt="replay"), src="node") == "full"
    assert _resolve(_Loop(corpus_fmt="replay", export_bundle="/tmp/b"), src="local") == "full"


def test_mid_run_switch_takes_effect_and_auto_restores_course_value(
    tmp_path, monkeypatch
) -> None:
    """训练途中切换：轮边界重读 ⇒ 下一轮换档；回 auto 恢复**课程请求**（不是上一轮结果）。"""
    stub = _Loop(corpus_fmt="replay")
    _write_config(tmp_path, monkeypatch, "{}")
    assert _resolve(stub) == "replay"  # 第 1 轮：auto，听课程

    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "full"}}')
    assert _resolve(stub) == "full"  # 第 2 轮：全局改 full（不重启就生效）

    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "auto"}}')
    # 第 3 轮：回 auto ⇒ 课程请求仍是 replay（前两轮的全局值没有污染它）。这里只看
    # `corpus_fmt` 这个公开读面：它就是下游四处唯一读的东西。
    assert _resolve(stub) == "replay"

    # 再切 full 仍能压过课程（可重入，不是一次性开关）
    _write_config(tmp_path, monkeypatch, '{"rl": {"corpus_fmt_mode": "full"}}')
    assert _resolve(stub) == "full"
