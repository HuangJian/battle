"""tests/remote/test_offline_deliverable_split.py — S5 第十四刀守卫：offline_boot 交付面下沉。

`remote/offline_deliverable.py` 从 `remote/offline_boot.py` 搬出的交付面（13 节点 / 179 行）：
真交付三函 + 课程路径链 + 4 常量。本刀的特殊面是 **standalone 运输**——notebook 从 GitHub raw
把引导文件集拉进同一目录，原家**不能**顶层 import 兄弟文件（DAG 顶层边守卫 + standalone
exec 守卫），只能懒装载 + PEP 562 `__getattr__` 门面。这个文件把七件事钉成事实：

  ① **定义唯一**：13 名只在 `offline_deliverable.py` 里实现（原家不留定义、不留常量）；
  ② **依赖面闭集**：新模块只许 import stdlib 白名单（standalone 纪律；多一个即红）；
  ③ **禁反向 import**：不得 import `offline_boot` / `remote.*` / `common.*` / `rl.*`（含延迟）；
  ④ **门面恒等**：`offline_boot.<名>`（经 `__getattr__` 懒转发）与 `offline_deliverable.<名>` 同一对象；
  ⑤ **门面闭集**：只转发 13 名，未知名照常 `AttributeError`（别把打错的属性喂给交付面）；
  ⑥ **功能性**：装载顺序（引导兄弟优先、包内兜底）/ 命名对账 / 课程三写法——搬走不改语义；
  ⑦ **notebook 拉取名单 ⇄ 装载名单一致**：主引导格名单、`sys.modules.pop` 名单、取回格兜底成对抓。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.offline_boot as boot_mod
import remote.offline_deliverable as deliverable_mod

BOOT_FILE = ROOT / "remote" / "offline_boot.py"
DELIVERABLE_FILE = ROOT / "remote" / "offline_deliverable.py"
NOTEBOOK = ROOT / "ipynb" / "battle.offline.ipynb"

#: 交付面名集合的单一事实（S5 第十四刀的 13 名 + 2026-10-14 两个根/备份面/恢复原语那一批）。
MOVED_NAMES = {
    "requested_courses",
    "courses_of",
    "_split_course_names",
    "course_work_dir",
    # ★ 2026-10-14（plan/offline-local-disk-first §3.2）：两个根 + 备份抽屉的名字/原语也在交付面 ——
    # 它们的判据与包名/三件名同源（同一份产物目录契约），拆到别处就又变成两份各写一遍。
    "net_root",
    "net_artifacts_dir",
    "local_root_path",
    "resolve_local_root",
    # ★ 2026-10-14 B2：持久面容量判据的常量与算式（产物根的判据面，与两个根同源）
    "artifact_need_bytes",
    "ARTIFACT_BYTES_PER_ROUND",
    "SEGMENT_ROUNDS_DEFAULT",
    "ARTIFACT_MIN_FREE_RATIO",
    "backup_ready",
    "install_backup",
    "WEIGHTS_NAME",
    "OPT_TAR_NAME",
    "LOCAL_ROOT_ENV",
    "DEFAULT_LOCAL_PARENT",
    "LOCAL_ROOT_NAME",
    "BACKUP_DIR_NAME",
    "download_dir",
    "package_deliverable",
    "_partial_last_it",
    "package_partial",
    # ★ 2026-10-17（plan/offline-backfeed-resilience §5）：一键打包（两态）+ 包内元信息。
    "package_pending",
    "deliver_meta",
    "deliver_pack_name",
    "write_deliver_zip",
    "ALL_ZIP",
    "LATEST_ZIP",
    "PARTIAL_CANDIDATES",
    "LATEST_ROW_NAME",
    "IT_PREFIX",
    "METRICS_NAME",
    "EVAL_LOG_NAME",
    "WEIGHTS_ALT_NAME",
    "IMPORT_META_NAME",
    "IMPORT_META_MAGIC",
    "_COURSE_NAME_RE",
}

#: 新模块的允许 import 面（**闭集**）：standalone 兄弟文件 —— 只许 stdlib。
#: `shutil` 是 2026-10-14 加的（恢复腿要 `copytree`/`rmtree`）：库面不变，仍全是 stdlib。
ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "datetime",  # 2026-10-17：`IMPORT_META.generated_at`（打包那一刻的事实）
    "json",
    "os",
    "pathlib",
    "re",
    "shutil",
    "zipfile",
}


def _quiet(_msg: str) -> None:
    pass


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


def _imports(path: Path) -> set[str]:
    """全部 import（含函数内延迟 import）的完整点分模块名。"""
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
    return out


# ───────────────────────── ① 定义唯一 ─────────────────────────


def test_moved_names_are_defined_only_in_the_new_home() -> None:
    """交付面的这些名只在 `offline_deliverable.py` 里实现；原家不得再出现定义/常量赋值。"""
    missing = sorted(MOVED_NAMES - _defined(DELIVERABLE_FILE))
    assert missing == [], f"新家缺这些名字：{missing}"
    moved_back = sorted(MOVED_NAMES & _defined(BOOT_FILE))
    assert moved_back == [], f"交付面名字又在 offline_boot 里实现：{moved_back}"


def test_twin_constants_match_the_artifacts_contract(tmp_path: Path) -> None:
    """三件名/包名是**拄的一份**（两个模块各定义一份，靠这条用例对账）。

    为什么不合并（已经“共享”）就完事：`offline_deliverable` 是 standalone（顶层不许 import 仓内），
    `offline_boot` 同理 ⇒ “单一来源”只能是**契约 + 用例**（先例 = `ALL_ZIP`/`LATEST_ZIP`）。
    """
    from remote.artifacts import ArtifactStore

    for name in (
        "STATE_NAME",
        "PLAN_NAME",
        "MANIFEST_NAME",
        "ALL_ZIP",
        "LATEST_ZIP",
        "IT_PREFIX",
        "METRICS_NAME",
        "EVAL_LOG_NAME",
    ):
        assert getattr(deliverable_mod, name) == getattr(ArtifactStore, name), (
            f"{name} 与 `ArtifactStore.{name}` 逐字相同（产物目录契约）"
        )
    # `weights.json` 在 `ArtifactStore` 里是写在 `weights_path()` 里的字面量（没有同名常量）
    store = ArtifactStore(tmp_path, run_id="r", log=lambda _m: None)
    assert store.weights_path(0).name == deliverable_mod.WEIGHTS_NAME, (
        "`WEIGHTS_NAME` 与 `ArtifactStore.weights_path()` 的末段逐字相同"
    )
    assert store.opt_path(0).name == deliverable_mod.OPT_TAR_NAME, (
        "`OPT_TAR_NAME` 与 `ArtifactStore.opt_path()` 的末段逐字相同（备份对账靠它）"
    )
    for name in ("STATE_NAME", "PLAN_NAME", "MANIFEST_NAME"):
        assert getattr(deliverable_mod, name) == getattr(boot_mod, name), (
            f"{name} 与 `offline_boot.{name}` 逐字相同（同一份产物目录契约）"
        )


# ─────────────────────── ② 依赖面闭集（standalone） ───────────────────────


def test_import_surface_is_closed_to_stdlib_only() -> None:
    got = _imports(DELIVERABLE_FILE)
    extra = sorted(got - ALLOWED_IMPORTS)
    assert extra == [], f"交付面长了仓内/未知依赖：{extra}（standalone 兄弟文件只许 stdlib）"
    unused = sorted(ALLOWED_IMPORTS - got)
    assert unused == [], f"白名单腐化（这些已不在 import 面里）：{unused}"


# ─────────────────────── ③ 禁反向 import ───────────────────────


def test_deliverable_never_imports_boot_or_repo_packages() -> None:
    banned = [
        m
        for m in _imports(DELIVERABLE_FILE)
        if m.startswith(("remote", "common", "trainer", "biz", "offline_boot", "offline_deliverable"))
    ]
    assert banned == [], f"交付面反向 import 了 {banned}（standalone 拿不到 code.zip）"


# ───────────────────────── ④ 门面恒等 ─────────────────────────


def test_boot_facade_forwards_the_same_objects() -> None:
    """`offline_boot.<名>` 走 PEP 562 `__getattr__` 懒转发 ⇒ 与真实现是**同一对象**。"""
    for name in sorted(MOVED_NAMES):
        got = getattr(boot_mod, name)
        want = getattr(deliverable_mod, name)
        assert got is want, f"remote.offline_boot.{name} 不是交付面里的同一个对象（转发成了副本）"


# ───────────────────────── ⑤ 门面闭集 ─────────────────────────


def test_facade_closure_is_exactly_the_moved_names() -> None:
    assert set(boot_mod._DELIVERABLE_NAMES) == MOVED_NAMES, (
        "门面名单与搬运名集合不一致（改名/加名时两处要一起改）"
    )


def test_facade_does_not_swallow_typos() -> None:
    """打错的名字必须照常 `AttributeError`——不能把随便什么属性都喂给交付面。"""
    assert getattr(boot_mod, "package_partials", None) is None, "笔误名被门面转发给了交付面"
    assert not hasattr(boot_mod, "definitely_not_a_delivery_name")


def test_deliverable_has_no_facade_of_its_own() -> None:
    """反向：新家是**真实现**，不得再定义 `__getattr__` 之类转发（避免套娃）。"""
    assert "__getattr__" not in _defined(DELIVERABLE_FILE)


# ───────────────────────── ⑥ 功能性 ─────────────────────────


def test_loader_prefers_the_boot_sibling_then_the_package() -> None:
    """装载顺序 = 引导兄弟优先（fresh-wins）、包内兜底——按源码事实钉住（顺序是契约）。"""
    src = BOOT_FILE.read_text(encoding="utf-8")
    assert 'for _name in ("offline_deliverable", "remote.offline_deliverable"):' in src, (
        "装载顺序变了：引导兄弟必须优先（与会话刚刷新的 offline_boot 同源）"
    )


def test_package_deliverable_names_the_zip_after_the_course(tmp_path: Path) -> None:
    """命名对账是**语义**（控制台靠文件名对课程）——搬走不改。"""
    (tmp_path / "artifacts.zip").write_bytes(b"payload")
    out = tmp_path / "out"
    dest = deliverable_mod.package_deliverable(tmp_path, "c5-gae", out, _quiet)
    assert dest is not None and dest.name == "deliver-c5-gae.zip"
    assert dest.read_bytes() == b"payload"
    assert deliverable_mod.package_deliverable(tmp_path / "nope", "c5-gae", out, _quiet) is None


def test_course_forms_and_work_dir_unchanged(tmp_path: Path) -> None:
    """课程三写法 + 多课工作目录再套一层——原语义（直接调新家；门面另有 test_offline_boot 覆盖）。"""
    assert deliverable_mod.courses_of({"course": "c5-gae"}) == ["c5-gae"]
    assert deliverable_mod.courses_of({"course": ["b", "a", "b"]}) == ["b", "a"]
    assert deliverable_mod.courses_of({"course": "c5-gae, c6-gae"}) == ["c5-gae", "c6-gae"]
    with pytest.raises(SystemExit):
        deliverable_mod.courses_of({})
    cfg = {"work_dir": str(tmp_path / "wd"), "course": "a"}
    assert deliverable_mod.course_work_dir(cfg, "a", multi=False) == tmp_path / "wd"
    assert deliverable_mod.course_work_dir(cfg, "b", multi=True) == tmp_path / "wd" / "b"


# ───────────── ⑦ notebook 拉取名单 ⇄ 装载名单一致 ─────────────


def _code_cells() -> list[str]:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return [
        "".join(c.get("source", []))
        for c in nb["cells"]
        if c.get("cell_type") == "code"
    ]


def test_notebook_fetches_and_pops_the_sibling() -> None:
    cells = _code_cells()
    boot = next(t for t in cells if "_run(CFG" in t)
    assert 'for _name in ("offline_boot.py", "tailscale_boot.py", "offline_deliverable.py"):' in boot, (
        "主引导格拉取名单缺 offline_deliverable.py —— 交付面会响亮 ImportError（老 notebook 形态）"
    )
    assert '"offline_deliverable", "remote.offline_deliverable"' in boot, (
        "sys.modules.pop 名单缺兄弟文件 ⇒ 同 kernel 第二次 Run 会命中旧模块（2026-09-25 事故同型）"
    )


def test_pack_cell_fallback_fetches_the_pair() -> None:
    cells = _code_cells()
    pack = next(t for t in cells if "package_pending" in t)
    assert 'for _name in ("offline_boot.py", "offline_deliverable.py"):' in pack, (
        "取回格的 raw 兜底要成对抓（只抓 offline_boot.py 会让交付面缺兄弟文件）"
    )
    assert "_ob.package_pending(CFG, _log)" in pack, "取回格仍走 runtime 的 package_pending（单一实现）"
    # ⚠ 旧入口 `package_partial` 仍在（老 notebook / 老调用方），但**不再**是底部那格的入口：
    # 它只挑一个包（现场 27 轮只拿到一轮），而这一格要的是整段（plan §5.2）。
    assert "package_partial" not in pack, "取回格不该再挑单包（那正是本片要修的形状）"
