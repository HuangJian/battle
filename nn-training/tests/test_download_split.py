"""拆分的**契约守卫**：下载簇永住 `remote/download.py`（S4 第七刀，2026-09-23）。

搬走的组（252 行）：`_progress_logger` · `download_payload` · `download_code` ·
`download_ts_code` · `download_blob` · `_cache_blob` · `_resolve_blob` · `_ensure_ts_code`。
`remote/worker.py` 2579 → 2348 行。

本文件钉六件事：

1. **定义唯一**——不许在 `worker.py` 里再实现一遍；
2. **依赖方向**——`download` 只许依赖 `common.protocol` / `remote.http` / `remote.wire` /
   `remote.bulk_sched`（全向下），不得 import `remote.worker`；
3. **转发同一对象**；
4. **顶层无新增可变容器**；
5. ✭ **注入点分档（本刀的主坑）**：`_resolve_blob` 调 `download_blob` 是**组内互调**，
   在本模块命名空间解析 ⇒ 必须 patch `remote.download`；而**宿主**（`run_job` /
   `_prefetch_fill`）调的 `download_*` 仍解析在 `worker` ⇒ patch `remote.worker` 照旧。
   两个方向各一条断言，防「patch 打偏而测试全绿」。
6. **`_progress_logger` 仍恰好两份**（本模块 + `remote/tailscale_boot.py`）——它是**有意的孪生**
   （tailscale_boot 要独立拉取），谁再抄第三份就红。
"""

from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.download as download_mod
import remote.worker as worker_mod
from common.protocol import BLOB_OPT
from tests.helpers import remote_dag as dag

DL_FILE = ROOT / "remote" / "download.py"
WORKER_FILE = ROOT / "remote" / "worker.py"

MOVED_NAMES = {
    "_cache_blob",
    "_ensure_ts_code",
    "_progress_logger",
    "_resolve_blob",
    "download_blob",
    "download_code",
    "download_payload",
    "download_ts_code",
}
ALLOWED_IMPORTS = {
    "common.protocol",
    "remote.bulk_sched",
    "remote.http",
    "remote.wire",
}



def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def test_moved_names_are_defined_in_download_and_not_redefined_in_worker() -> None:
    """定义唯一：搬走的名字只在 `download.py` 里实现。"""
    assert _defined(DL_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(DL_FILE))
    leftovers = MOVED_NAMES & _defined(WORKER_FILE)
    assert leftovers == set(), f"worker.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_download_only_depends_downwards_and_stays_within_its_whitelist() -> None:
    """无环 + 白名单：对账走**全局账本**（`tests/helpers/remote_dag.py`），不再各自写一份。

    原先这里是「不得 import `remote.worker`」+ 本地白名单；现在交付给共用实现：
    顶层 intra-remote 边严格向下 · 任何 import 不得碰 `rl` · 顶层仓内依赖 ⊆ `ALLOWED_IMPORTS`。
    """
    dag.assert_remote_module("remote.download", allowed_project_imports=ALLOWED_IMPORTS)


def test_worker_forwards_every_moved_name_as_the_same_object() -> None:
    """宿主与测试都从 `remote.worker` 取这些名字 ⇒ 必须是同一对象。"""
    for name in MOVED_NAMES:
        assert hasattr(worker_mod, name), f"remote.worker 丢了 {name}"
        assert getattr(worker_mod, name) is getattr(download_mod, name), (
            f"remote.worker.{name} 不是 remote.download.{name}（转发成了副本）"
        )


def test_download_has_no_top_level_mutable_container() -> None:
    """搬走的是函数簇（零模块级状态）——顶层不该多出可变容器。"""
    mutable: set[str] = set()
    for node in _tree(DL_FILE).body:
        pairs: list[tuple[str, ast.expr | None]] = []
        if isinstance(node, ast.Assign):
            pairs = [(t.id, node.value) for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            pairs = [(node.target.id, node.value)]
        for name, value in pairs:
            if not name.startswith("__") and isinstance(value, (ast.Dict, ast.List, ast.Set)):
                mutable.add(name)
    assert mutable == set(), f"download.py 顶层多了可变容器：{sorted(mutable)}"


def test_intra_module_seam_is_the_download_module(monkeypatch, tmp_path: Path) -> None:
    """✭ 组内互调：`_resolve_blob` → `download_blob` 解析在 **`remote.download`**。

    「把 `remote.worker.download_blob` 换成炸弹、`remote.download.download_blob` 返回真字节」
    必须仍然成功——反过来（只 patch worker）就是本步最容易犯的静默错误。
    """
    raw = b"opt-bytes"
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(
        worker_mod, "download_blob", lambda *a, **k: (_ for _ in ()).throw(AssertionError("打偏"))
    )
    monkeypatch.setattr(download_mod, "download_blob", lambda *a, **k: raw)
    got, hit, src = download_mod._resolve_blob(
        blob_root=tmp_path / "blob_cache",
        name=BLOB_OPT,
        sha=sha,
        inline_b64="",
        jid="j",
        base_url="http://hub",
        token="t",
        preloaded=None,
        log=lambda _m: None,
    )
    assert got == raw and hit is False and src == "download"


def test_host_callers_still_resolve_the_worker_namespace() -> None:
    """宿主侧相反档位：`run_job` / `_prefetch_fill` 把 `download_*` 当**裸名字**用 ⇒
    解析在 `worker` 命名空间 ⇒ `monkeypatch.setattr(worker_mod, "download_payload", …)` 仍有效
    （`tests/test_soft_hold_prefetch.py` 与 `test_remote_ppo.py` 就靠这个）。

    若哪天宿主改成 `download.download_payload(...)`（属性访问），这些 patch 会静默失效——
    本断言就是那个警报。
    """
    host_names = {"run_job", "_prefetch_fill"}
    loads: dict[str, set[str]] = {}
    for node in _tree(WORKER_FILE).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in host_names:
            loads[node.name] = {
                n.id
                for n in ast.walk(node)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            }
    assert set(loads) == host_names
    for name in host_names:
        assert "download_payload" in loads[name], name
    assert "download_code" in loads["run_job"]
    # 且**没有**属性式访问（否则就是换了命名空间）
    src = WORKER_FILE.read_text(encoding="utf-8")
    assert "download.download_" not in src and "download_mod.download_" not in src


def test_progress_logger_still_has_exactly_two_production_copies() -> None:
    """`_progress_logger` 的孪生是**有意**的（tailscale_boot 独立拉取）⇒ 全仓恰好两份。"""
    found = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if ".venv" not in path.parts
        and "__pycache__" not in path.parts
        and "_progress_logger" in _defined(path)
    )
    assert found == ["remote/download.py", "remote/tailscale_boot.py"], found
