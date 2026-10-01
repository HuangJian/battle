"""remote/offline_deliverable.py — 离线引导的交付面：课程名/工作目录 + 交付物打包（standalone 兄弟文件）。

从 `remote/offline_boot.py` 搬来（S5 第十四刀，**逐字节纯搬**）：**两个所有者、两个触发**——
「中途取回」格在训练 cell 停下来之后单独调 `package_partial`（与训练链零共享状态），而
课程名解析/工作目录推导是它唯一需要的环境（读者还有 `offline_boot.run` / `resolve_courses`，
经门面转发）。

**运输面**：notebook 每次会话从 GitHub raw 把
`{offline_boot,tailscale_boot,offline_deliverable}.py` 拉进同一目录（`/tmp/battle-boot`），
`offline_boot` 到用的时候再 `importlib` 装载本模块（`offline_boot._load_deliverable()`）；
仓库/包内形态回落到 `remote.offline_deliverable`。所以**本模块与 `offline_boot` 同纪律：
顶层不得 import `remote.*`**（拿包之前那个包不存在），`common/` / `trainer/` 同理
（`tests/common/test_common_layer.py` + `tests/helpers/remote_dag.py` 守着）。

**产物包名常量是抄的一份**（`ALL_ZIP` / `LATEST_ZIP` / `LATEST_ROW_NAME`）：与
`remote/artifacts.py::ArtifactStore` 逐字相同（测试守）——搬家不改变这条孪生对账关系。
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Callable
from pathlib import Path

#: 产物目录里的两个包名（与 `remote/artifacts.py::ArtifactStore` 逐字相同；测试守）。
ALL_ZIP = "artifacts.zip"
LATEST_ZIP = "LATEST.zip"
#: 中途取回的优先顺序：全量包（已收尾）优先，其次最新一轮小包（跑到一半）。
PARTIAL_CANDIDATES = (ALL_ZIP, LATEST_ZIP)
#: `LATEST.zip` 里那行元信息（名字写死在 `remote/artifacts.py::_refresh_latest`）。
LATEST_ROW_NAME = "metrics_row.json"


#: 课程名的合法形状（与 `course_from_pack_name` 同一条口径：它是目录名/文件名，不是自由文本）。
_COURSE_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def requested_courses(cfg: dict) -> list[str]:
    """`CFG.course` → 课程名列表（去重、保序）；**空 = 没点名**（交给 hub 清单）。

    用户指令（2026-09-22）：「battle.offline.ipynb 的 course 配置项，需支持多个离线课程名。
    云机串行从 hub 取任务，逐个完成。」——列表即执行顺序（与控制台课程名逐字相同）。
    用户指令（2026-09-25）：「云机不应该要在 notebook 里配置离线课程名，它应该直接向 hub
    问询」⇒ 空不再是错误，而是「按清单跑」（`resolve_courses`/`_run_auto`）。

    三种写法都认：`"c5-gae"`、`["c5-gae", "c6-gae"]`、`"c5-gae, c6-gae"`（逗号/空白分隔）。
    非法名（含路径分隔符、`..` 等）一律 `SystemExit`——课程名会被拼进目录名与
    `task-<课>.zip`，含糊的名字在这里就得拦下，不能等到写盘。
    """
    raw = cfg.get("course")
    items: list[str] = []
    if isinstance(raw, (list, tuple)):
        for x in raw:
            items.extend(_split_course_names(str(x)))
    else:
        items.extend(_split_course_names(str(raw or "")))
    out: list[str] = []
    bad: list[str] = []
    for name in items:
        if not _COURSE_NAME_RE.fullmatch(name):
            bad.append(name)
            continue
        if name not in out:
            out.append(name)
    if bad:
        raise SystemExit(
            f"[offline] CFG.course 里的课程名非法（只允许字母/数字/._-，≤64 字）：{bad}"
            "——课程名会进目录名与任务包名，请与控制台课程名逐字对齐"
        )
    return out


def courses_of(cfg: dict) -> list[str]:
    """老入口（既有调用方/用例）：空 ⇒ `SystemExit`（不知道跑哪几门课就别开跑）。"""
    out = requested_courses(cfg)
    if not out:
        raise SystemExit(
            "[offline] CFG.course 没填 —— 取包/交付物都按课程名走，必须给"
            "（支持多门课：列表按顺序串行跑完；新 hub 也可以留空 ⇒ 按 /offline/tasks 清单跑）"
        )
    return out

def _split_course_names(text: str) -> list[str]:
    """一个字符串 → 课程名（逗号/空白分隔；单名就是 [name]）。"""
    return [p for p in re.split(r"[,\s]+", str(text).strip()) if p]


def course_work_dir(cfg: dict, course: str, *, multi: bool) -> Path:
    """该课的临时工作目录（包/产物/hub.token 都在这儿）。

    单课与今日**逐字相同**（缺省 `<download_dir>/battle-offline/<课>`；显式 `work_dir`
    原样用）；多课时即使给了显式 `work_dir` 也**再套一层课程名**——否则两门课共用
    `<work_dir>/run/` 这个产物目录，第二门课的 run_loop 会把第一门的产物当成自己的
    续跑点（权重接错课，且看起来完全正常）。
    """
    explicit = str(cfg.get("work_dir") or "").strip()
    base = Path(explicit).expanduser() if explicit else download_dir(cfg) / "battle-offline"
    if explicit and not multi:
        return base
    return base / course


def download_dir(cfg: dict) -> Path:
    """交付物的落点：Kaggle 的 Output / Colab 的 `/content` / 否则 cwd（人能一眼找到）。"""
    explicit = str(cfg.get("download_dir") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if os.environ.get("KAGGLE_KERNEL_RUN_TYPE") or os.environ.get("KAGGLE_URL_BASE"):
        return Path("/kaggle/working")
    if os.environ.get("COLAB_RELEASE_TAG") or os.environ.get("COLAB_GPU"):
        return Path("/content")
    return Path.cwd()


def package_deliverable(
    artifacts_dir: Path, course: str, out_dir: Path, log: Callable[[str], None]
) -> Path | None:
    """把 `artifacts.zip` 复制成 `deliver-<课>.zip`（控制台的导入习惯名）。

    名字不是装饰：`remote/deliver_zip.py` 会用文件名里的课程与控制台当前课程**对账**
    （拿 A 课的权重去评 B 课，读数看起来完全正常，只有对账能拦）。`artifacts.zip`
    落到人手上再改名，就等于把这道对账让给运气。
    """
    src = artifacts_dir / "artifacts.zip"
    if not src.exists():
        log(f"没找到 {src} —— 产物目录还在：{artifacts_dir}")
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"deliver-{course}.zip" if course else "deliver.zip"
    dest = out_dir / name
    try:
        dest.write_bytes(src.read_bytes())
    except OSError as e:
        log(f"复制交付物失败（{e}）—— 手动取 {src}")
        return None
    log(f"交付物: {dest}（{dest.stat().st_size} bytes）")
    return dest


def _partial_last_it(art: Path, src: Path) -> str:
    """产物目录/包里最新一轮的编号（纯日志用；拿不到就 `"-"`，不猜、不抛）。

    两个来源：`state.json` 的 `last_it`（每轮都刷新，最权威），其次是包内的
    `metrics_row.json`（`LATEST.zip` 带的）。中途取回是**人已经慌了才用的路**，
    所以这一层绝不能因为一个缺字段就炸。
    """
    try:
        st = json.loads((art / "state.json").read_text(encoding="utf-8"))
        if isinstance(st, dict) and isinstance(st.get("last_it"), int):
            return str(st["last_it"])
    except (OSError, ValueError):
        pass
    try:
        with zipfile.ZipFile(src) as zf:
            row = json.loads(zf.read(LATEST_ROW_NAME).decode("utf-8"))
        if isinstance(row, dict) and isinstance(row.get("it"), int):
            return str(row["it"])
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        pass
    return "-"


def package_partial(
    cfg: dict,
    log: Callable[[str], None],
    *,
    courses: list[str] | None = None,
) -> list[Path]:
    """把「跑到一半」的产物打成 `deliver-<课>.zip`（会话中途下载 → 控制台导入）。

    用户口径 2026-09-23：「battle.offline.ipynb 底部增加一个 cell，用于将训练到中途的
    课程结果打包下载回来，供导入至 dashboard。」

    为什么需要它：云机会话会到点/被回收，而**跑到一半**的产物已经在盘上
    （`LATEST.zip` 每次 checkpoint 都刷新）——但没有一个“能交回控制台”的名字，而控制台的
    导入靠 `deliver-<课>.zip` **对账课程**（拿 A 课的权重去评 B 课，读数看起来完全正常，
    只有对账能拦）。于是这里只做两件事：

      1. 选出**最能代表当前进度**的那个包（全量包 `artifacts.zip` 优先，其次最新一轮小包
         `LATEST.zip`——两者形状都被 `remote/deliver_zip.py` 接受）；
      2. 复制成 `deliver-<课>.zip` 放进 `download_dir`（Kaggle=`/kaggle/working`、
         Colab=`/content`、否则 cwd）——人一眼能找到、下载、导入。

    **只读 + 复制**：不动产物、不训练、不碰网络。找不到产物就**响亮说明**是哪个目录为空
    （第一轮 checkpoint 之前本来就没东西）并返回空列表，绝不因拿不到包而抛。
    跑完全程时打的同名包是**全量**的（`package_deliverable`）——中途包被它覆盖是预期。
    """
    names = list(courses) if courses else courses_of(cfg)
    multi = len(names) > 1
    out_dir = download_dir(cfg)
    made: list[Path] = []
    for course in names:
        work = course_work_dir(cfg, course, multi=multi)
        art = work / "run"
        src = next((art / n for n in PARTIAL_CANDIDATES if (art / n).exists()), None)
        if src is None:
            log(
                f"[pack] {course}: 没有可打包的产物（{art} 下既没有 {ALL_ZIP} 也没有 "
                f"{LATEST_ZIP}）——第一轮 checkpoint 之前都是这样"
            )
            continue
        if src.name == LATEST_ZIP:
            log(
                f"[pack] {course}: 只找到 {LATEST_ZIP}（会话跑完/中途停机时打的全量包还不在）"
                "——它含最新一轮的 weights/opt + 计划 + 清单，控制台「导入产物」接受"
            )
        dest = out_dir / (f"deliver-{course}.zip" if course else "deliver.zip")
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
        except OSError as e:
            log(f"[pack] {course}: 复制失败（{e}）—— 手动取 {src}")
            continue
        log(
            f"[pack] {course}: {dest}（{dest.stat().st_size} bytes，含到 it{_partial_last_it(art, src)}，"
            f"来源 {src.name}）"
        )
        made.append(dest)
    if made:
        log(
            "[pack] 下一步：把上面的 zip 下载到本机 → 控制台「导入产物」上传（中途包与跑完时的 "
            "deliver-<课>.zip 同名同形，导入后自动起 A 层评估）"
        )
    return made
