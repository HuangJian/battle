"""common 层契约与回归 —— 共享原语的单一实现，以及「谁不能用它」。

这一组用例守的不是某个功能，而是**共享原语层本身**（`common/`，2026-09-23 引入）。
它存在的理由（同名实现各写 2~4 份且语义漂移）只有靠断言才守得住：

1. **单一实现**：`sha256_*` / `bun_version` / `exc_tail` 等只允许有一处 `def`，
   上层只能 re-export（`is` 同一对象）——否则下一份「顺手复制」的实现在门禁里悄悄长回来。
2. **层契约**：`common/` 只依赖 stdlib（要随 `code.zip` 解到没有 torch 的云机上）。
3. **豁免是结构性的**：三个从 GitHub raw 单独拉取的引导模块**不得** import `common`
   ——它们的重复是有意为之，测试防止下一个人「顺手合并」。
4. **§19 编码坑**：`subprocess` 捕获必须显式 UTF-8（`text=True` 单用会按 locale 解码，
   非 ASCII 输出会让读线程死掉、`stdout` 变 `None`）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import common.fs
import common.hashing
import common.logutil
import common.proc
import common.text
import dist_common
import remote.artifacts
import remote.bundle
import remote.hub_client
import remote.iter_rollout
import remote.run_loop
import rl.agent_meta
import rl.bc_ledger
import rl.queue
import rl.stream
from tests.helpers import source_scan

NN_ROOT = Path(__file__).resolve().parent.parent

#: 必须保持零依赖（从 GitHub raw 单独拉取，拿不到 `code.zip` 里的 `common`）。
#: 见 `common/__init__.py`「谁不能用本包」与各自模块 docstring。
STANDALONE_BOOT_MODULES = (
    NN_ROOT / "remote" / "tailscale_boot.py",
    NN_ROOT / "remote" / "notebook_boot.py",
    NN_ROOT / "remote" / "offline_boot.py",
)


def _read(p: Path) -> str:
    # 缓存版：同一文件被多个用例/多条判据读到也只读盘一次（见 tests/helpers/source_scan.py）。
    return source_scan.read_text(str(p))


def _module_level_imports(src: str) -> list[str]:
    """模块级 import 的模块名（不含函数内的延迟 import）。"""
    out: list[str] = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _defs_of(name: str, *, roots: tuple[Path, ...] = (NN_ROOT,)) -> list[str]:
    """全仓（生产代码面）里 `def <name>` 出现的文件列表。"""
    skip_parts = {".venv", "__pycache__", "tests", "e2e", "tmp", "weights", "ipynb"}
    found: list[str] = []
    for root in roots:
        for p in root.rglob("*.py"):
            if skip_parts & set(p.parts):
                continue
            if re.search(rf"^def {re.escape(name)}\b", _read(p), re.MULTILINE):
                found.append(str(p.relative_to(NN_ROOT)).replace("\\", "/"))
    return sorted(found)


# --------------------------------------------------------------------- 层契约


def test_common_package_depends_on_stdlib_only() -> None:
    """`common/*` 只能 import stdlib —— 它要随 code.zip 解到没有 torch 的云机上。"""
    allowed_first_party = {"platform_utils", "common"}
    banned = {"torch", "numpy", "rl", "remote", "ppo", "models", "data", "train"}
    for p in sorted((NN_ROOT / "common").glob("*.py")):
        mods = _module_level_imports(_read(p))
        for m in mods:
            top = m.split(".")[0]
            assert top not in banned, f"{p.name} 不得依赖 {m}（层契约见 common/__init__.py）"
            if top in sys.stdlib_module_names or top in allowed_first_party:
                continue
            pytest.fail(f"{p.name} 的第三方/未知依赖: {m}")


def test_standalone_boot_modules_stay_dependency_free() -> None:
    """三个引导模块必须保持零依赖（重复是**有意**的，别顺手合并）。"""
    for p in STANDALONE_BOOT_MODULES:
        mods = _module_level_imports(_read(p))
        for m in mods:
            top = m.split(".")[0]
            assert top not in {"common", "rl", "ppo", "models", "data", "train"}, (
                f"{p.name} 从 GitHub raw 单独拉取，不得 import {m}"
                "（拿不到 code.zip ⇒ 云端 ImportError）"
            )
            assert not m.startswith("remote.") or p.name == "tailscale_boot.py", (
                f"{p.name} 顶部不得 import {m}（remote 包在拿到 code.zip 之前不存在）"
            )


# ------------------------------------------------------------------ 单一实现


def test_sha256_has_single_definition_and_is_reexported() -> None:
    assert _defs_of("sha256_file") == ["common/hashing.py"]
    assert _defs_of("sha256_bytes") == ["common/hashing.py"]
    # 上层只允许 re-export（同一对象），不允许再包一层拷贝
    assert remote.artifacts.sha256_file is common.hashing.sha256_file
    assert remote.artifacts.sha256_bytes is common.hashing.sha256_bytes
    assert remote.bundle.sha256_bytes is common.hashing.sha256_bytes
    assert remote.bundle.sha256_file is common.hashing.sha256_file


def test_sha256_matches_independent_reimplementation(tmp_path: Path) -> None:
    """独立重实现对账（不调被测函数）：文件与字节两条路径都要逐字节一致。"""
    raw = bytes(range(256)) * 3 + "终点".encode()
    f = tmp_path / "blob.bin"
    f.write_bytes(raw)
    assert common.hashing.sha256_bytes(raw) == hashlib.sha256(raw).hexdigest()
    assert common.hashing.sha256_file(f) == hashlib.sha256(raw).hexdigest()
    # 账本口径必须与文件口径同源（wver / init_weights_fp / blob 键都靠这条）
    assert dist_common.weights_fingerprint(str(f)) == hashlib.sha256(raw).hexdigest()


def test_bun_version_and_exc_tail_and_atomic_write_have_single_definition() -> None:
    # `bun_version` 唯一实现 = common/proc.py；另两处是**声明式薄包装**，各自钉住历史
    # 默认值（训练机侧 timeout=10/fallback="?"、节点侧 timeout=30/fallback=""/require_zero），
    # 同时保住 1 参签名与 monkeypatch 接缝。再多一份实现就红。
    assert _defs_of("bun_version") == [
        "common/proc.py",
        "remote/iter_rollout.py",
        "rl/queue.py",
    ]
    assert _defs_of("exc_tail") == ["common/text.py"]
    assert _defs_of("atomic_write_bytes") == ["common/fs.py"]
    assert _defs_of("append_jsonl") == ["common/fs.py"]
    assert _defs_of("extract_tar_bytes") == ["common/fs.py"]


def test_delegating_call_sites_keep_their_public_names() -> None:
    """收敛的是**定义**，不是名字：历史调用点与测试的 monkeypatch 接缝必须还在。"""
    # rl.queue 是公共 re-export 面（batch_eval / eval_dispatch / e2e 从这里取）
    assert rl.queue.bun_version("definitely-not-a-real-bun-binary") == "?"
    assert rl.queue.mm("1.2.3") == "1.2"
    assert rl.queue._record_agent_meta is rl.agent_meta.record_agent_meta
    # 私有别名仍在各自模块命名空间里（monkeypatch.setattr(mod, "bun_version", ...) 依赖它）
    assert callable(remote.iter_rollout.bun_version)
    assert callable(remote.hub_client._sha256_file)
    # 转发的薄包装不改变语义
    assert remote.hub_client._sha256_bytes(b"x") == common.hashing.sha256_bytes(b"x")
    assert remote.iter_rollout.bun_version("definitely-not-a-real-bun-binary") == ""


def test_bun_version_require_zero_keeps_the_two_historical_flavors() -> None:
    """训练机侧不看退出码、节点侧要求 rc==0 —— 这条差异是**刻意保留**的（不是旋钮）。"""
    # 用一个必然失败的命令：rc 非 0
    assert common.proc.bun_version("definitely-not-a-real-bun-binary", fallback="?") == "?"
    assert (
        common.proc.bun_version(
            "definitely-not-a-real-bun-binary", fallback="", require_zero=True
        )
        == ""
    )


# ------------------------------------------------------------------ §19 编码坑


def test_run_capture_survives_undecodable_bytes_and_keeps_chinese() -> None:
    """§19 回归：不可解码字节不得让 stdout 变 None，中文必须原样读回。"""
    child = (
        "import sys\n"
        "sys.stdout.buffer.write('中文行 ok\\n'.encode('utf-8'))\n"
        "sys.stdout.buffer.write(b'\\xff\\xfe bad bytes\\n')\n"
        "sys.stdout.buffer.flush()\n"
    )
    proc = common.proc.run_capture([sys.executable, "-c", child], timeout=30)
    assert proc.stdout is not None, "解码死在读线程 ⇒ stdout 丢成 None（§19 的哑巴错误）"
    assert "中文行 ok" in proc.stdout
    assert "bad bytes" in proc.stdout
    assert "\ufffd" in proc.stdout, "非法字节必须变成替换符而不是丢掉整段输出"


def test_every_production_capture_site_pins_encoding() -> None:
    """源码守卫（AST，不看注释/文档串）：不得再有裸的 `text=True` 捕获调用。

    裸 `text=True` = 回到控制台代码页解码 ⇒ 非 ASCII 子进程输出让读线程死掉、
    `stdout` 变 `None`（`docs/nn/engineering.md` §19 的「响亮错误变哑巴」）。

    合法形态只有两种：
      ① 走 `common.proc.run_capture`（唯一入口，内部恒带 encoding/errors）；
      ② 就地写 `encoding=`（三个独立拉取的引导模块 + `bootstrap.py`——装依赖前跑，
         刻意零依赖，不能引 `common`）。

    判据用 AST 而不是行文本：文档串里引用 `text=True` 是**说明**，不是缺陷。
    """
    skip_parts = {".venv", "__pycache__", "tests", "e2e", "tmp", "weights", "ipynb"}
    offenders: list[str] = []
    for p in NN_ROOT.rglob("*.py"):
        if skip_parts & set(p.parts):
            continue
        for node in ast.walk(source_scan.parse(str(p))):
            if not isinstance(node, ast.Call):
                continue
            text_on = any(
                kw.arg == "text" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
                if kw.arg
            )
            if not text_on:
                continue
            if "run_capture" in ast.unparse(node.func):
                continue  # 唯一入口：encoding/errors 由它保证
            if any(kw.arg == "encoding" for kw in node.keywords):
                continue
            offenders.append(f"{p.relative_to(NN_ROOT)}:{node.lineno}")
    assert offenders == [], f"这些捕获点缺显式 encoding（§19）: {offenders}"


# --------------------------------------------------------------------- 其他原语


def test_atomic_write_json_replaces_and_append_jsonl_appends(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "state.json"
    common.fs.atomic_write_json(target, {"a": 1, "中文": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "中文": 2}
    assert not list(target.parent.glob("*.tmp")), "原子写不得留下临时文件"

    ledger = tmp_path / "l" / "ledger.jsonl"
    for i in range(3):
        common.fs.append_jsonl(ledger, {"i": i, "note": "中文"})
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"i": 0, "note": "中文"}, {"i": 1, "note": "中文"}, {"i": 2, "note": "中文"}]


def test_agent_meta_is_best_effort_and_uses_the_shared_writer(tmp_path: Path) -> None:
    """账本写不进去绝不影响结算（写点在最要救命的那条路径上）。"""
    rl.agent_meta.record_agent_meta(tmp_path / "sub" / "dist-agent-meta.jsonl", {"node": "n0"})
    # 目录不可建（父“目录”是个文件）⇒ 必须静默吞掉，不能抛
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    rl.agent_meta.record_agent_meta(blocker / "x.jsonl", {"node": "n1"})
    rl.bc_ledger.append_ledger(tmp_path / "sub" / "dist-agent-meta.jsonl", {"node": "n2"})


def test_exc_tail_keeps_the_tail_not_the_head() -> None:
    """截**尾**段：栈顶几帧是 transport 样板，真正的原因在最后一帧。"""
    marker = "真正的原因在最末尾"
    try:
        raise ValueError("head-part " + "x" * 800 + marker)
    except ValueError as e:
        tail = common.text.exc_tail(e, limit=120)
    assert marker in tail, "尾段必须保留（否则单行 ENOENT 无从定位抛点）"
    assert len(tail) <= 120

    # 缺省上限（4000）足够大 ⇒ 异常类型与消息都在，回报体可读
    try:
        raise ValueError("boom")
    except ValueError as e:
        full = common.text.exc_tail(e)
    assert "ValueError" in full and "boom" in full


def test_log_line_format_and_injected_clock(capsys: pytest.CaptureFixture[str]) -> None:
    class _Clock:
        def strftime(self, fmt: str) -> str:
            return "12:34:56"

    common.logutil.log_line("tag", "消息", clock=_Clock())
    out = capsys.readouterr().out
    assert out == "[12:34:56] [tag] 消息\n"
    assert common.logutil.stamp(_Clock()) == "[12:34:56]"


def test_run_loop_default_logger_uses_the_shared_format(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tag 化日志出口（4 处 `_log_default`）已收敛到 common.logutil 的同一格式。"""
    remote.run_loop._log_default("hello")
    out = capsys.readouterr().out
    assert re.fullmatch(r"\[\d{2}:\d{2}:\d{2}\] \[run\] hello\n", out), out


def test_code_zip_packing_includes_the_common_package(tmp_path: Path) -> None:
    """`common/` 必须随 code.zip 下发，否则云机 import 即炸（结构性前提）。"""
    nn_root = tmp_path / "nn"
    (nn_root / "common").mkdir(parents=True)
    (nn_root / "common" / "__init__.py").write_text("", encoding="utf-8")
    (nn_root / "common" / "hashing.py").write_text("x = 1\n", encoding="utf-8")
    (nn_root / "remote").mkdir()
    (nn_root / "remote" / "w.py").write_text("y = 1\n", encoding="utf-8")
    z = tmp_path / "code.zip"
    remote.hub_client.pack_code_zip(nn_root, z)
    import zipfile

    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
    assert "common/hashing.py" in names
    assert "common/__init__.py" in names


def test_no_production_module_reintroduces_a_progress_logger_duplicate() -> None:
    """`_progress_logger` 的孪生是**有意**的：tailscale_boot 独立拉取 ⇒ 不能共享。

    这条断言把这个事实钉住：worker 侧可以从 common 取，但 tailscale_boot 必须自留一份
    ⇒ 全仓**恰好两份**，谁再抄第三份就红。
    """
    assert _defs_of("_progress_logger") == [
        "remote/download.py",
        "remote/tailscale_boot.py",
    ]


def test_subprocess_import_is_not_left_dangling_by_the_capture_migration() -> None:
    """迁移到 run_capture 后留下的 `import subprocess` 必须清掉（ruff 会抓，这里留个语义锚）。"""
    for rel in ("rl/archive.py", "run_rl.py", "run_bc.py", "rl/loop_serve.py"):
        src = _read(NN_ROOT / rel)
        if "import subprocess" in src:
            assert re.search(r"subprocess\.", src), f"{rel} 的 import subprocess 已悬空"
