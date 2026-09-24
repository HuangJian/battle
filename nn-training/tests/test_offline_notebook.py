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
    "force_pack",
    "wait_pack_sec",
    "prompt_upload",
    "hub_tries",
    "live_backfeed",
    "device",
    "threads",
    "max_iters",
    "budget_sec",
    "eval_on_cloud",
    "eval_slots",
    "eval_game_timeout_sec",
    "rollout_workers",
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

    静态一半：`run()` 里 `creds = {` 必须早于真正会碰网络的那一步；而 tailscale 只在
    `obtain_pack` 内部按**传进去的** creds 起（它拿不到别处的凭据 ⇒ 不可能抢在前面）。

    2026-09-22（多课程）：取包与网络动作都搬进了逐课的 `run_one_course`，所以判据是
    「`run()` 读完凭据之后才调 `run_one_course`，而 `obtain_pack` 只在 `run_one_course` 里」。
    """
    src = Path(offline_boot.__file__).read_text(encoding="utf-8")
    body = src[src.index("def run(") :]
    body = body[: body.index("\ndef ")]  # run() 的函数体（下一个顶层 def 之前）
    assert body.index("creds = {") < body.index("run_one_course("), (
        "取包/网络动作必须晚于凭据读取（2026-09-17 Kaggle 事故的时序约束）"
    )
    per_course = src[src.index("def run_one_course(") :]
    assert "creds: dict" in per_course[:400], "run_one_course 必须收下已读好的凭据"
    assert per_course.index("pack = obtain_pack(") > 0
    sig = src[src.index("def obtain_pack(") : src.index("def obtain_pack(") + 200]
    assert "creds: dict" in sig, "obtain_pack 必须收下已读好的凭据，而不是自己去读"


def test_run_reads_secrets_first_at_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """行为版守卫（比数行号硬）：三个 secret 先读完，然后才轮到取包。"""
    order: list[str] = []

    def spy_secret(key: str, cfg_val: str = "") -> str:
        order.append(f"secret:{key}")
        return "tok" if key == "HUB_TOKEN" else ""

    def spy_obtain(cfg: dict, creds: dict, log, work, stop=None, **kw):
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


def test_markdown_documents_cloud_eval_and_resume() -> None:
    """说明面必须看得见这两个开关（用户找不到的旋钮 = 不存在的旋钮）。"""
    md = "\n".join(notebook_cells(NB, "markdown"))
    assert "eval_on_cloud" in md, "云机评估开关要在说明书里"
    assert "与下一轮 PPO 并行" in md, "并行语义是这条腿最容易被误解的地方，要写明"
    assert "续跑" in md and "同轮齐全" in md
    # 并发口径：rollout 与 eval **同一个公式**（交替跑，互不预留）——说明书与代码里必须一致
    assert "rollout_workers" in md, "rollout 并发旋钮要在说明书里（它覆盖计划里的导出机规模）"
    assert "max(CPU−4, CPU×0.8)" in md, "并发口径要写出公式（老口径「扣掉 rollout 再卡 64」已废）"


def test_markdown_names_the_secrets_and_both_bootstrap_paths() -> None:
    md = "\n".join(notebook_cells(NB, "markdown"))
    for s in ("HUB_TOKEN", "HUB_IP", "TS_AUTHKEY"):
        assert s in md, f"说明里没提 {s} —— 用户找不到这个键"
    assert "task-<课>.zip" in md and "Add Data" in md and "导入产物" in md
    assert "live_backfeed" in md, "回传开关要在说明里可见（它是「怎么把结果拿回来」的分叉点）"


def test_boot_loader_refreshes_every_session_and_reports_the_revision() -> None:
    """引导模块**每次会话都刷新**，并把实际加载的那份的 sha12 打进日志。

    2026-09-22 事故：旧实现是「有缓存先用缓存」（`/tmp/battle-boot`）+ 日志只打 branch
    ⇒ **同一个 kernel 里跑过一次旧代码之后，后续每次 Run 都还在跑那份旧的**，而日志看不出
    来；实测症状是多课程 `CFG.course` 列表被旧 `offline_boot` 当成一门课名（拼出
    `['x20-demo-mix', ...]` 这种目录名、去找一个不存在的任务包）。
    """
    cell = "\n".join(notebook_cells(NB, "code"))
    assert "hashlib" in cell, "要算 sha12 就必须 import hashlib"
    assert "已刷新" in cell, "拉到最新时必须明说刷新了（否则看不出缓存新旧）"
    assert "@ sha12=" in cell, "加载日志必须带**实际加载那份**的 sha12（branch 不足以区分新旧）"
    assert "用上一份缓存继续" in cell, "回落到缓存必须响亮说明（不能静默用旧版）"
    assert ".replace(_dst)" in cell, "写回要用原子替换（半截写入不得留下坏模块）"
    assert "_branch.txt" not in cell, "旧的「按分支名失效」缓存策略已退役（它不挡同分支的新旧）"


def test_notebook_has_a_mid_run_package_cell() -> None:
    """底部那格「中途取回」（用户口径 2026-09-23）：跑到一半也能把产物交回控制台。

    守三件事：① 它**恰有一格**且不含引导调用（引导 cell 的唯一性靠 `_run(CFG` 判定，
    多一个含它的 cell 会让那份 fixture 直接报错）；② 它走 runtime 的 `package_partial`
    （工作目录的推导、包的选择、命名都在那里一份，cell 不许自己拼一份）；③ 名字与下一步
    写明 —— 控制台靠 `deliver-<课>.zip` 对账课程，而人需要知道去哪里下载。
    """
    hits = [t for t in notebook_cells(NB, "code") if "package_partial" in t]
    assert len(hits) == 1, f"期望恰好 1 个中途取回 cell，实际 {len(hits)}"
    pack = hits[0]
    assert "_run(CFG" not in pack, "中途取回 cell 不得含引导调用（会破坏引导 cell 的唯一性）"
    assert "_ob.package_partial(CFG, _log)" in pack, "必须调 runtime 的 package_partial（单一实现）"
    assert "导入产物" in pack, "要说清下一步是把包上传到控制台「导入产物」"
    assert "deliver-<课>.zip" in pack, "要写明产出的名字（与跑完时同一个）"
    assert "LATEST.zip" in pack or "artifacts.zip" in pack, "要说清包里装的是什么"
    assert "_IS_COLAB" in pack, "Colab 上要直接触发下载（否则人得自己去文件树里找）"


def test_markdown_documents_the_hub_retry_cap_and_kaggle_tailscale_skip() -> None:
    """两个新口径必须在说明书里可见（用户找不到的旋钮 = 不存在的旋钮）。

    2026-09-23 用户点名的两条：① hub 取包重试满 N 轮就转「等上传」；② 检测到 Kaggle
    就跳过全部 tailscale 步骤。以及底部那格「中途取回」。
    """
    md = "\n".join(notebook_cells(NB, "markdown"))
    assert "hub_tries" in md, "重试上限的旋钮要在说明书里（不然用户只能去读代码）"
    assert "等上传" in md and "不再轮询 hub" in md, "切换后的行为要写明（否则看起来像挂了）"
    assert "Kaggle" in md and "Tailscale" in md and "会被忽略" in md, (
        "Kaggle 上跳过 tailscale 这条要写明（否则人会先去填 HUB_IP/TS_AUTHKEY 白忙）"
    )
    assert "中途取回" in md and "LATEST.zip" in md, "中途取回那格要在说明书里指出"


def test_cell_has_no_key_material_or_hardcoded_course() -> None:
    text = "\n".join(notebook_cells(NB))
    # 空串 = 单课程老写法；空列表 = 多课程（用户 2026-09-22：course 要支持多门课）
    assert '"course": ""' in text or '"course": []' in text, (
        "CFG.course 必须是空值让用户填（不许预置某门课）"
    )
    for key in ("hub_token", "ts_authkey", "hub_ip"):
        assert f'"{key}": ""' in text, f"{key} 不该预置值（凭据走 Secret / 现场填）"
