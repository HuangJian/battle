"""tests/test_offline_notebook.py —— `ipynb/battle.offline.ipynb` 的接线与口径守卫。

notebook 不 import 仓库代码：它先从 GitHub raw 拉 `remote/offline_boot.py`，拉不到就用
**任务包里的 `code.zip`** 引导（同 commit 的源码）。于是「cell 里那份 CFG / 凭据时序 /
引导来源」都没有类型系统兜着，会静默漂移——这个文件就是那份兜底。

守的都是**会静默出事**的那几处：

  1. CFG 的键名与 `offline_boot.run` 读的键名（少一个 ⇒ 用户填了没用、或悄悄用缺省值）；
  2. `code.zip` 这个名字在 cell 里硬编码了一份（离线引导靠它）——必须与 `CODE_NAME` 同步；
  3. 凭据必须在**任何网络/代理改动之前**读完（2026-09-17 Kaggle 事故的时序约束）；
  4. 长任务日志落文件（AGENTS §16.2）与 `live_backfeed` 缺省 = 实时回传（用户口径）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remote import offline_boot

NN = Path(__file__).resolve().parent.parent
NB = NN / "ipynb" / "battle.offline.ipynb"

#: `run()` 会读的 CFG 键（缺一个就是「用户填了也不生效」）。
CFG_KEYS = (
    "course",
    "hub_token",
    "hub_ip",
    "hub_port",
    "hub_url",
    "ts_authkey",
    "task_zip",
    "wait_pack_sec",
    "prompt_upload",
    "live_backfeed",
    "device",
    "threads",
    "max_iters",
    "budget_sec",
    "work_dir",
    "download_dir",
    "repo_url",
    "branch",
)


def _quiet(_msg: str) -> None:
    pass


def notebook_cells(path: Path, kind: str | None = None) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for cell in nb["cells"]:
        if kind is not None and cell.get("cell_type") != kind:
            continue
        src = cell.get("source", "")
        out.append("".join(src) if isinstance(src, list) else str(src))
    return out


@pytest.fixture(scope="module")
def cell() -> str:
    hits = [t for t in notebook_cells(NB, "code") if "_run(CFG" in t]
    assert len(hits) == 1, f"期望恰好 1 个含引导调用的 code cell，实际 {len(hits)}"
    return hits[0]


def test_notebook_is_valid_and_has_one_boot_cell(cell: str) -> None:
    nb = json.loads(NB.read_text(encoding="utf-8"))
    assert nb["nbformat"] == 4
    assert nb["metadata"]["colab"]["name"] == NB.name
    assert "_run(CFG, _log, _secret, _keepalive_stop)" in cell
    assert "raise SystemExit(_rc)" in cell, "cell 末尾要把 rc 交给 SystemExit（收尾语义）"


@pytest.mark.parametrize("key", CFG_KEYS)
def test_cfg_exposes_every_key_the_runtime_reads(key: str, cell: str) -> None:
    assert f'"{key}"' in cell, f"CFG 少了 {key}（runtime 读它，用户却看不到/改不了）"


def test_live_backfeed_defaults_to_on(cell: str) -> None:
    assert '"live_backfeed": True' in cell, "缺省必须是实时回传（用户口径）"
    assert 'cfg.get("live_backfeed", True)' in Path(offline_boot.__file__).read_text(encoding="utf-8"), (
        "runtime 侧的缺省与 notebook 不一致 —— 两条路默认行为会分叉"
    )


def test_cell_knows_the_code_zip_name(cell: str) -> None:
    """离线引导要从包里取代码：这个名字在 cell 里硬编码了一份，改名必须两边一起改。"""
    assert f'"{offline_boot.CODE_NAME}"' in cell, (
        f"cell 里找不到 {offline_boot.CODE_NAME} —— 重命名后离线引导会静默失效"
    )
    assert "_load_boot_from_pack" in cell and "sys.path.insert" in cell


def test_credentials_are_read_before_any_network_change() -> None:
    """凭据 → 取包 → （必要时才）tailscale：顺序反了就会「够不着平台 Secrets」。

    静态一半：`run()` 里 `creds = {` 必须早于 `obtain_pack(`；而 tailscale 只在
    `obtain_pack` 内部按**传进去的** creds 起（它拿不到别处的凭据 ⇒ 不可能抢在前面）。
    """
    src = Path(offline_boot.__file__).read_text(encoding="utf-8")
    body = src[src.index("def run(") :]
    body = body[: body.index("\ndef ")]  # run() 的函数体（下一个顶层 def 之前）
    assert body.index("creds = {") < body.index("pack = obtain_pack(")
    sig = src[src.index("def obtain_pack(") : src.index("def obtain_pack(") + 200]
    assert "creds: dict" in sig, "obtain_pack 必须收下已读好的凭据，而不是自己去读"


def test_run_reads_secrets_first_at_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行为版守卫（比数行号硬）：三个 secret 先读完，然后才轮到取包。"""
    order: list[str] = []

    def spy_secret(key: str, cfg_val: str = "") -> str:
        order.append(f"secret:{key}")
        return "tok" if key == "HUB_TOKEN" else ""

    def spy_obtain(cfg: dict, creds: dict, log, work, stop=None):
        order.append("obtain_pack")
        assert creds["HUB_TOKEN"] == "tok", "取包时凭据还没就绪 —— 时序反了"
        raise SystemExit("stop-here")

    monkeypatch.setattr(offline_boot, "obtain_pack", spy_obtain)
    with pytest.raises(SystemExit, match="stop-here"):
        offline_boot.run({"course": "c5-gae", "work_dir": str(tmp_path)}, _quiet, spy_secret)  # type: ignore[arg-type]
    assert order == ["secret:HUB_TOKEN", "secret:HUB_IP", "secret:TS_AUTHKEY", "obtain_pack"]


def test_run_requires_a_course(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="course"):
        offline_boot.run({}, _quiet, lambda k, v="": "")


def test_long_task_logging_is_wired(cell: str) -> None:
    """长任务输出必须落文件（Kaggle 会话被回收时 cell 输出会连着丢）。"""
    assert "battle-offline.log" in cell
    assert '"/kaggle/working/battle-offline.log"' in cell
    assert "with open(_LOG_FILE, \"a\", encoding=\"utf-8\")" in cell


def test_boot_modules_are_fetched_by_name_and_exist(cell: str) -> None:
    for name in ("offline_boot.py", "tailscale_boot.py"):
        assert f'"{name}"' in cell, f"cell 没拉 {name}"
        assert (NN / "remote" / name).is_file(), f"cell 要拉 {name}，仓库里却没有"


def test_markdown_names_the_secrets_and_both_bootstrap_paths() -> None:
    md = "\n".join(notebook_cells(NB, "markdown"))
    for s in ("HUB_TOKEN", "HUB_IP", "TS_AUTHKEY"):
        assert s in md, f"说明里没提 {s} —— 用户找不到这个键"
    assert "task-<课>.zip" in md and "Add Data" in md and "导入产物" in md
    assert "live_backfeed" in md, "回传开关要在说明里可见（它是「怎么把结果拿回来」的分叉点）"


def test_cell_has_no_key_material_or_hardcoded_course() -> None:
    text = "\n".join(notebook_cells(NB))
    assert '"course": ""' in text, "CFG.course 必须是空串让用户填（不许预置某门课）"
    for key in ("hub_token", "ts_authkey", "hub_ip"):
        assert f'"{key}": ""' in text, f"{key} 不该预置值（凭据走 Secret / 现场填）"
