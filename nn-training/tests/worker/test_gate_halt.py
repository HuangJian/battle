"""`worker/gate_halt.py` 的契约用例（2026-10-01 门禁停机平台级化）。

钉住五件事（判据见 plan/gate-halt-platform-level.plan.md §3.6）：

1. **保守方向**：缺省 `halt`；文件**存在但坏**（非法 JSON / 非法 mode / 非法 `until`）⇒ `halt`，
   **绝不给 notify**——即使 CLI 那一刻写着 notify（坏文件不许被旧启动参数接管）。
2. **优先级**：平台文件 > CLI > 缺省；`source` 三值诚实地反映值从哪来。
3. **`until` 是读时求值**：未到 ⇒ notify、已过 ⇒ halt；缺失与 `null` 同义 = 不过期。
4. **离线腿短路**：`NN_GATE_HALT_LEG=offline` ⇒ 根本不读文件（连坏文件都不碰），一律缺省 halt。
5. **原子写 + 回执**：`tmp` + `replace`（不留半截、不留残file）；回执**只在变化时**写、
   按课程合并（多课并存互不覆盖）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.gate_halt as gh


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """把意图/回执都重定向进临时目录（惰性取值 ⇒ 用例内改 env 立即生效）。"""
    intent = tmp_path / "gate-halt.json"
    applied = tmp_path / "gate-halt.applied.json"
    monkeypatch.setenv("NN_GATE_HALT", str(intent))
    monkeypatch.setenv("NN_GATE_HALT_APPLIED", str(applied))
    monkeypatch.setenv("NN_GATE_HALT_LEG", "local")
    return intent, applied


def _write_intent_file(path: Path, body: object) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


# ───────────────────────── ① 保守方向：缺省 / 坏文件 ─────────────────────────


def test_missing_file_uses_cli_then_default(paths: tuple[Path, Path]) -> None:
    """文件不存在 ⇒ CLI 兜底；CLI 也没给（或非法）⇒ 缺省 halt（source 诚实）。"""
    assert gh.resolve("notify") == gh.Resolved("notify", "cli")
    assert gh.resolve("halt") == gh.Resolved("halt", "cli")
    assert gh.resolve(None) == gh.Resolved("halt", "default")
    assert gh.resolve("") == gh.Resolved("halt", "default")
    assert gh.resolve("garbage") == gh.Resolved("halt", "default")


def test_platform_file_beats_cli(paths: tuple[Path, Path]) -> None:
    """★ 平台文件赢（控制台的意图不许被历史命令行静默盖掉），两个方向都判。"""
    intent, _ = paths
    _write_intent_file(intent, {"version": 1, "mode": "notify", "until": None, "by": "console"})
    assert gh.resolve("halt") == gh.Resolved("notify", "platform", None)
    _write_intent_file(intent, {"version": 1, "mode": "halt"})
    assert gh.resolve("notify") == gh.Resolved("halt", "platform", None)


@pytest.mark.parametrize(
    "body",
    [
        "[1, 2]",  # 根不是对象
        {"mode": "skip"},  # 非法模式（历史课程级 rl-config 里的值就是这个）
        {"mode": "notify", "until": "tomorrow"},  # until 不是数字
        {"mode": "notify", "until": True},  # bool 不是数字（python 的 1 陷阱）
        {"version": 1},  # 没有 mode
    ],
)
def test_bad_file_never_notifies(paths: tuple[Path, Path], body: object) -> None:
    """★ 坏文件 ⇒ halt + error，**即使 CLI 写着 notify**（红线 2）。"""
    intent, _ = paths
    _write_intent_file(intent, body)
    r = gh.resolve("notify")
    assert r.mode == "halt" and r.source == "default" and r.error, r


def test_broken_json_reads_as_halt(paths: tuple[Path, Path]) -> None:
    """写坏到一半的 JSON（原子写失效/手改坏）⇒ halt，不是异常。"""
    intent, _ = paths
    intent.write_text('{"mode": "not', encoding="utf-8")
    r = gh.resolve("notify")
    assert r.mode == "halt" and "读失败" in r.error


# ───────────────────────── ② until：读时求值 ─────────────────────────


def test_until_expiry_is_evaluated_at_read_time(paths: tuple[Path, Path]) -> None:
    """同一份文件、只推进 now：未到 ⇒ notify；已到/过期 ⇒ halt（到点自动回落）。"""
    intent, _ = paths
    _write_intent_file(intent, {"mode": "notify", "until": 1000.0, "by": "console"})
    assert gh.resolve(now=999.0).mode == "notify"
    assert gh.resolve(now=1000.0).mode == "halt"  # 边界：到点即停
    assert gh.resolve(now=1001.0).mode == "halt"
    assert gh.resolve(now=1001.0).source == "platform"  # 值仍从平台文件来，只是生效值是 halt


@pytest.mark.parametrize("body", [{"mode": "notify"}, {"mode": "notify", "until": None}])
def test_until_missing_or_null_means_no_expiry(paths: tuple[Path, Path], body: object) -> None:
    """缺失与 null 同义 = 不过期（「不限时」不需要额外写法）。"""
    intent, _ = paths
    _write_intent_file(intent, body)
    r = gh.resolve(now=10**12)
    assert r == gh.Resolved("notify", "platform", None)


# ───────────────────────── ③ 离线腿短路 ─────────────────────────


def test_offline_leg_never_reads_the_platform_file(
    paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ `NN_GATE_HALT_LEG=offline` ⇒ 缺省 halt，且**根本不读**文件（连坏文件都不碰）。"""
    intent, _ = paths
    _write_intent_file(intent, {"mode": "notify", "until": None})

    def _boom(*_a: object, **_k: object) -> object:
        raise AssertionError("离线腿不许读平台文件")

    monkeypatch.setattr(gh, "read_intent", _boom)
    monkeypatch.setenv("NN_GATE_HALT_LEG", "offline")
    assert gh.resolve("notify") == gh.Resolved("halt", "default")


# ───────────────────────── ④ 写面：原子 + 回执 ─────────────────────────


def test_write_intent_roundtrip_and_no_leftovers(paths: tuple[Path, Path]) -> None:
    """写读一致；目录里不留 `.<name>.<pid>.tmp`（原子替换而不是直写）。"""
    intent, _ = paths
    assert gh.write_intent("notify", 4242.0, by="console") == ""
    got = gh.read_intent()
    assert (got.mode, got.until, got.by) == ("notify", 4242.0, "console")
    assert gh.resolve(now=1.0).mode == "notify"
    leftovers = [p.name for p in intent.parent.iterdir() if p.name.startswith(".")]
    assert leftovers == [], leftovers


def test_write_intent_rejects_unknown_mode(paths: tuple[Path, Path]) -> None:
    """非法模式不落盘（写面也要保守：宁可什么都没写，不可写进一个没人认的值）。"""
    intent, _ = paths
    err = gh.write_intent("skip")
    assert err and not intent.exists()


def test_receipt_is_written_only_on_change(paths: tuple[Path, Path]) -> None:
    """★ 回执只在结果变化时写（不心跳）：同值第二次调用不重写文件。"""
    _, applied = paths
    r = gh.Resolved("notify", "platform", 4242.0)
    assert gh.write_applied_if_changed("c5-tick", r, now=1.0) == ""
    first = applied.read_text(encoding="utf-8")
    mtime = applied.stat().st_mtime_ns
    assert gh.write_applied_if_changed("c5-tick", r, now=2.0) == ""
    assert applied.read_text(encoding="utf-8") == first
    assert applied.stat().st_mtime_ns == mtime
    # 变了 ⇒ 重写，且 entry 是最新的事实
    assert gh.write_applied_if_changed("c5-tick", gh.Resolved("halt", "default"), now=3.0) == ""
    ent = gh.read_applied()["c5-tick"]
    assert (ent["effective_mode"], ent["source"], ent["until"]) == ("halt", "default", None)
    assert applied.stat().st_mtime_ns != mtime


def test_receipt_merges_courses_and_survives_broken_file(paths: tuple[Path, Path]) -> None:
    """按课合并（两门课互不覆盖）；回执坏掉 ⇒ 空读 + 下次重写（观测面坏不得变训练故障）。"""
    _, applied = paths
    assert gh.write_applied_if_changed("c5-tick", gh.Resolved("halt", "default")) == ""
    assert gh.write_applied_if_changed("x20-dodge-l1", gh.Resolved("notify", "platform", None)) == ""
    assert set(gh.read_applied()) == {"c5-tick", "x20-dodge-l1"}
    applied.write_text("not json", encoding="utf-8")
    assert gh.read_applied() == {}
    assert gh.write_applied_if_changed("c5-tick", gh.Resolved("halt", "default")) == ""
    assert set(gh.read_applied()) == {"c5-tick"}


def test_receipt_course_name_falls_back_to_nocourse(paths: tuple[Path, Path]) -> None:
    """没课程名的进程（本机裸跑）写 `nocourse` 桶——与控制台 `course || 'nocourse'` 同规。"""
    _, applied = paths
    assert gh.write_applied_if_changed("", gh.Resolved("halt", "default")) == ""
    assert set(gh.read_applied()) == {"nocourse"}
    assert applied.exists()


# ───────────────────────── ⑤ 旧课程级文件的告警 ─────────────────────────


def test_legacy_txt_warns_once(paths: tuple[Path, Path], tmp_path: Path) -> None:
    """旧 `<traj>/gate-halt-mode.txt` 存在 ⇒ 告警一次（内容一个字不读、不生效）。"""
    traj = tmp_path / "c5-tick"
    traj.mkdir()
    (traj / "gate-halt-mode.txt").write_text("notify\n", encoding="utf-8")
    seen: set[str] = set()
    line = gh.warn_once_on_legacy_txt(traj, seen)
    assert "gate-halt-mode.txt" in line and "可删" in line
    assert gh.warn_once_on_legacy_txt(traj, seen) == ""  # 进程内去重
    assert gh.warn_once_on_legacy_txt(tmp_path / "no-such-traj", seen) == ""
    assert gh.warn_once_on_legacy_txt(None, seen) == ""


def test_effective_mode_is_pure() -> None:
    """`effective_mode` 是纯函数（无 IO、无 env）：三档一眼可判。"""
    assert gh.effective_mode("halt", 0.0, 10.0) == "halt"
    assert gh.effective_mode("notify", None, 10.0) == "notify"
    assert gh.effective_mode("notify", 5.0, 10.0) == "halt"
    assert gh.effective_mode("notify", 50.0, 10.0) == "notify"
    assert gh.effective_mode(None, None, 10.0) == "halt"
