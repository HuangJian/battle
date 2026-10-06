"""test_production_isolation —— 测试进程**永不**碰在跑的生产状态（2026-10-06 两起事故）。

事故一（控制台）：`bun run dashboard`（:8900，本机常驻）的命令行刷出 21 行
`[action] autoOfflineHandoff <课> → fail (HTTP 200): … 未开课 …` —— 触发者不是人，是**门禁里的
测试**：控制台地址的解析是 `hub/task_pack.py` 的「`BCITY_CONSOLE_URL` 为空 ⇒ `DEFAULT_CONSOLE_URL`
= `http://127.0.0.1:8900`」（生产便利），而 `e2e/test_auto_handoff_e2e.py` 起的**真 hub 子进程**
有一半没传 `console_url` ⇒ 它们把自动交接请求打到了开发机上正在跑的那个 dashboard。

事故二（状态文件）：全量门禁跑完，`dashboard/data/evalboard/runner_state.json`（EvalBoard 心跳，
操作员面板读它）被改成 `batch_id="bp"` 的**测试状态** —— 门禁里 `e2e/test_cloud_iter_e2e.py`
跑真 `TrainingLoop`，心跳的落点是 `worker/eval_heartbeat.py` 的缺省根
`dashboard/data/evalboard/`（`EVALBOARD_DATA` 未钉 ⇒ 缺省）。同一张网还罩着三个**真控制文件**：
`tmp/gate-halt.json`（平台门禁意图：能停全平台训练）、`tmp/loop-control.json`（暂停/恢复真循环）、
`nn-training/weights/`（真权重归档，控制台的权重选择器扫它；工装用例往里撒文件 = 面板上多出假轮次）。

为什么必须堵死（不只是日志噪声）：`autoOfflineHandoff` 的动作本体是
`applyTrainModeToConfig(course, 'offline', {remember: true})` —— 控制台的**唯一配置写面**。
课名撞上一门**正在开课**的真课时，一次测试运行就能把那门课翻成离线停采（并可能顺手导包）。

修法（本文件钉住它）：两层 conftest 各挂一个 autouse fixture，调**同一份**
`tests/conftest.py::pin_production_env` —— 控制台地址钉到**死端口**（连接当场被拒 ⇒ 万一漏网
也只是「控制台不可达」的响亮降级，绝不写真配置），四个状态面钉进本用例自己的 `tmp_path`。
`e2e/conftest.py` **不会**继承 `tests/conftest.py` 的夹具（兄弟目录），所以两层都要挂——
本文件的源码守卫就是防「下一个 e2e 又漏」。

## 复现/复查口径（真状态是否被工装碰过）

    cd /home/hj/battle && snap(){ find tmp dashboard/data nn-training/weights -type f \
        -printf '%T@ %s %p\n' | sort -k3; }; snap > tmp/before.txt
    bash tools/githook/nn-python-gate.sh
    snap > tmp/after.txt && diff tmp/before.txt tmp/after.txt

2026-10-06 实测（修前）：`runner_state.json` 的行出现（`<` 旧 / `>` 新，size 都是 169）——
即 e2e 层把真心跳改写了；修后该 diff 只剩 `tmp/pytest-tmp/**` 与缓存。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub import task_pack as tp
from hub.task_pack import DEFAULT_CONSOLE_URL
from tests.conftest import PRODUCTION_STATE_PINS, TEST_CONSOLE_URL


def test_process_never_points_at_the_live_console() -> None:
    """本体：测试进程的控制台地址 = 死桩，且死桩不等于生产默认（:8900）。"""
    now = os.environ.get("BCITY_CONSOLE_URL", "")
    assert now == TEST_CONSOLE_URL, (
        f"测试进程的控制台地址是 {now!r}——不是 conftest 钉的死桩。"
        "裸 hub 代码（`hub/offline.py::_ask_console_freshness` / `trigger_auto_handoff`）"
        "会打到开发机上正在跑的控制台，而那个动作会**写真配置**"
    )
    assert TEST_CONSOLE_URL != DEFAULT_CONSOLE_URL, (
        f"死桩不许等于生产默认（{DEFAULT_CONSOLE_URL} 就是本机在跑的 dashboard）"
    )


def test_default_console_url_is_still_this_machine_and_watch_for_drift() -> None:
    """生产默认仍是「本机 :89xx」——变了就要顺着改本隔离的注释与假设（别让守卫悄悄失效）。"""
    assert DEFAULT_CONSOLE_URL.startswith("http://127.0.0.1:89"), (
        f"生产默认控制台地址变了（{DEFAULT_CONSOLE_URL}）：本隔离的「同机 dashboard」假设要重核"
    )


def test_hub_dials_the_stub_not_the_live_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """行为面：裸 `trigger_auto_handoff` 真的拨的是死桩（地址**调用时**从 env 现读）。

    只断言「环境变量是死桩」不够——真正要钉的是 hub 代码的解析路径（`hub/task_pack.py::
    trigger_auto_handoff` 的 `os.environ.get(BCITY_CONSOLE_URL) or DEFAULT_CONSOLE_URL`）。
    把 `_net_urlopen` 换成捕获器：既不碰网络，又能看见它到底拨了哪个 URL；同时钉住
    不可达的语义（降级为手动，**不得**读成「已触发」）。
    """
    seen: list[str] = []

    def _capture(req: Any, timeout: float | None = None) -> Any:
        """替身：只记 URL —— 不碰网络（本用例要看的正是「拨哪个地址」）。"""
        seen.append(str(req.full_url))
        raise OSError("stub：测试期不许有真控制台")

    monkeypatch.setattr(tp, "_net_urlopen", _capture)
    ok, why = tp.trigger_auto_handoff("c5-gae", log=lambda _m: None)
    assert seen and seen[0].startswith(TEST_CONSOLE_URL), (
        f"hub 拨的不是死桩：{seen}（生产默认是 {DEFAULT_CONSOLE_URL}）"
    )
    assert seen[0].endswith(tp.AUTO_HANDOFF_CONSOLE_PATH), seen
    assert ok is False and why == "OSError", f"不可达必须降级为手动，不得读成已触发：{(ok, why)}"


# ───────────────────────── 状态面：四个「真控制文件」的缺省根 ─────────────────────────


def _defaults() -> dict[str, Path]:
    """各状态面的**生产缺省**（从模块常量现读，不抄字面量）。"""
    from hub import store_offline as so
    from trainer import loop_control as lc
    from worker import eval_heartbeat as hb
    from worker import gate_halt as gh

    return {
        "NN_GATE_HALT": Path(gh.DEFAULT_INTENT_PATH),
        "NN_GATE_HALT_APPLIED": Path(gh.DEFAULT_APPLIED_PATH),
        "NN_LOOP_CONTROL": Path(lc.DEFAULT_CONTROL_PATH),
        "NN_LOOP_CONTROL_APPLIED": Path(lc.DEFAULT_APPLIED_PATH),
        "EVALBOARD_DATA": hb._DEFAULT_ROOT,
        "BCITY_WEIGHTS_ARCHIVE_ROOT": so.WEIGHTS_ARCHIVE_ROOT,
    }


def test_state_pins_live_inside_the_test_tmp_dir(tmp_path: Path) -> None:
    """四个状态面在本用例期间必须落在 `tmp_path` 里（且都真的设了）。"""
    assert set(PRODUCTION_STATE_PINS) == set(_defaults()), "钉名单与状态面清单漂了"
    for name in PRODUCTION_STATE_PINS:
        raw = os.environ.get(name)
        assert raw, f"{name} 没被夹具钉住——缺省路径就是生产状态"
        target = Path(raw)
        assert target.is_relative_to(tmp_path), (
            f"{name} = {raw} 不在本用例的 tmp_path（{tmp_path}）里："
            "真 hub 子进程 / 心跳 / 权重归档会写到操作员正在用的活状态上"
        )


def test_defaults_are_still_the_repo_paths(tmp_path: Path) -> None:
    """缺省必须仍是仓库里的真路径——否则「隔离有效」这件事就失去对照（钉漂了要重核）。"""
    repo = ROOT.parent
    for name, default in _defaults().items():
        assert default.is_relative_to(repo), f"{name} 的生产缺省跑出仓库了：{default}"
        assert not default.is_relative_to(tmp_path), f"{name} 的缺省与测试 tmp 撞了：{default}"


def test_writers_land_in_tmp_and_never_touch_production(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """行为面：真写点（心跳 / 门禁意图 / 回执）落 tmp，生产侧文件**一个字节不动**。

    只钉 env 不够——写点的解析链是「模块函数 → env → 缺省」，本用例把链走完，
    并给生产侧文件拍前后指纹（mtime_ns + size；不存在 = None）。`write_*` 都返回错误文案
    （空 = 成功），顺手钉住「真的写成了」而不是「静默没写」。
    """
    from trainer import loop_control as lc
    from worker import eval_heartbeat as hb
    from worker import gate_halt as gh

    defaults = _defaults()
    watch = [defaults["NN_GATE_HALT"], defaults["NN_GATE_HALT_APPLIED"], defaults["NN_LOOP_CONTROL_APPLIED"]]
    watch.append(defaults["EVALBOARD_DATA"] / "runner_state.json")

    def _stamp(p: Path) -> tuple[int, int] | None:
        try:
            st = p.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    before = {str(p): _stamp(p) for p in watch}

    hb.write_state(window_open=True, batch_id="iso-probe", unit_idx=0, unit_of=1)
    assert hb.state_path().is_relative_to(tmp_path), f"心跳落到了 {hb.state_path()}"
    assert (tmp_path / "evalboard" / "runner_state.json").is_file(), "心跳没写进 tmp（静默失败）"

    err = gh.write_intent("notify", by="test-production-isolation")
    assert err == "", f"门禁意图没写成：{err}"
    assert Path(gh.intent_path()).is_relative_to(tmp_path), gh.intent_path()

    err = gh.write_applied_if_changed("c5-gae", gh.Resolved("halt", "platform"))
    assert err == "", f"门禁回执没写成：{err}"
    assert Path(gh.applied_path()).is_relative_to(tmp_path), gh.applied_path()

    err = lc.write_applied(["c5-gae"])
    assert err == "", f"循环控制回执没写成：{err}"
    assert Path(lc.applied_path()).is_relative_to(tmp_path), lc.applied_path()

    after = {str(p): _stamp(p) for p in watch}
    for path, was in before.items():
        assert after[path] == was, f"生产侧状态被改了：{path}（{was} → {after[path]}）"


def test_weights_archive_root_points_into_tmp(tmp_path: Path) -> None:
    """权重归档是控制台权重选择器的扫描面：工装用例往里撒文件 = 面板上多出假训练轮次。"""
    from hub import store_offline as so

    root = so._weights_archive_root()
    assert root.is_relative_to(tmp_path), f"归档根是 {root}——不是测试 tmp（真归档会被撒文件）"
    assert root != so.WEIGHTS_ARCHIVE_ROOT, "归档根没被隔离：缺省就是 `nn-training/weights`"


# ───────────────────────── 源码守卫：两层都挂同一份隔离 ─────────────────────────


def test_both_test_layers_install_the_isolation() -> None:
    """源码守卫：`tests/` 与 `e2e/` 两层都必须挂这层隔离（e2e 不继承 tests 的夹具）。

    2026-10-06 之前的形状：只有 `tests/hub/test_auto_handoff.py` 给自己打桩（注释里写明
    「真发 HTTP 会打到开发机上正在跑的控制台」），其余走 `hub/offline` 那条路的用例全靠运气
    （dashboard 没跑就"看起来没事"）。夹具是**全局**兜底，本用例防它被删/漏挂。
    """
    src = (ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "def pin_production_env" in src, "tests/conftest.py 少了 pin_production_env（唯一实现）"
    for name in (*PRODUCTION_STATE_PINS, "BCITY_CONSOLE_URL"):
        assert f'"{name}"' in src, f"pin_production_env 没钉 {name}"

    e2e_src = (ROOT / "e2e" / "conftest.py").read_text(encoding="utf-8")
    assert "pin_production_env" in e2e_src, (
        "e2e/conftest.py 没挂状态隔离——e2e 是兄弟目录，不继承 tests/ 的夹具；"
        "2026-10-06 真心跳就是从这里被写进 `dashboard/data/evalboard/` 的"
    )
    for name in (*PRODUCTION_STATE_PINS, "BCITY_CONSOLE_URL"):
        assert f'"{name}"' not in e2e_src, (
            f"钉名单只许有一份定义（`tests/conftest.py::PRODUCTION_STATE_PINS`）——"
            f"e2e 侧别再抄 {name}"
        )
    assert "http://127.0.0.1:9" not in e2e_src, (
        "死桩只许有一份定义（`tests/conftest.py::TEST_CONSOLE_URL`）——e2e 侧引用它，别再抄字面量"
    )
