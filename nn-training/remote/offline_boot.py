"""remote/offline_boot.py — 全离线训练 notebook 的运行时（`ipynb/battle.offline.ipynb`）。

**离线模式与在线模式的分工**（用户口径 2026-09-19）：

  在线：hub 每个 it 派活给 rollout 集群（语料/权重每轮传），本机训练循环原地等结果；
  离线：本机只导出**一个整段任务包**（`task-<课>.zip` = 计划 + 课程 + 起点权重 + 动量 +
        同 commit 的代码 + TS 运行时），云机拿过去**自主跑完整段**，产物按回传开关处置。

调用契约（cell 侧；与 `remote/notebook_boot.py::run` 同形）：

    import offline_boot
    raise SystemExit(offline_boot.run(CFG, _log, _secret, _keepalive_stop))

三步曲（顺序即需求）：

  1. **先连 hub**（best-effort）：能通就 `GET /offline/task-pack?course=<课>` 取整段任务包
     ——包本来就躺在 hub 的 `<traj-root>/<课>/task-<课>.zip`（控制台导出的就是它），
     这里只是把**同一个文件**按 HTTP 递出去，不造第二份真相；
  2. **连不上就等人上传**：轮询工作目录/挂载点找 `task-*.zip`（Colab 可弹上传框、
     Kaggle 挂数据集），到点还在等就**响亮退出**并打印「去控制台导出 → 上传到本机」；
  3. **拿到包就跑**：包里的 `code.zip` 解到 `CODE_DIR` 进 `sys.path`，然后走
     `remote.run_loop --bundle` —— 与云端 worker **字面同一条**训练链（不另写一份）。

**实时回传**（`CFG["live_backfeed"]`，缺省 True）：把 hub 地址与 token 交给 run_loop 的
产物补传腿（每轮 best-effort 推、`delivered.json` 记账、永不影响训练）。关掉则
`--no-deliver`：跑完统一打 `deliver-<课>.zip`，用户下载后到控制台导入。

**hub 取包有重试上限**（`CFG["hub_tries"]`，缺省 10；用户口径 2026-09-23）：连试这么多轮
还没拿到包（hub 没导出 / 抖动 / 鉴权错都算），**不再碰 hub**，转入「等上传」模式并响亮说明
——否则云机只会把整个 `wait_pack_sec`（缺省 30 分钟）耗在轮询上，而会话时间是花钱买的。
注意「取包」与「起隧道」是两回事：上限只管前者，后者按下面的规则单独判。
**上传框排在上限之后**（用户口径 2026-10-04）：Colab 的上传框（`files.upload()`）会**阻塞**
在交互上——第一次取包失败就弹，等于把「hub 正在导包、稍后自动取到」这条路一刀切断（现场：
hub 刚说「已替你触发一次重导，稍后重试」，下一行就是框）。所以只有 hub 试满上限（或根本没
配 hub）才弹一次；等它期间照样每轮扫落点，用户随时手动传上来的包都不会漏。

**Kaggle 上不使用 tailscale**（用户口径 2026-09-23）：平台容器既不给 NET_ADMIN 也换不得
网络命名空间，而 userspace 引导会把进程代理改写成只转发 Tailscale IP 的代理——2026-09-17
的 Kaggle 事故就是这么发生的（代理改写后平台 Secrets 够不着）。所以 `is_kaggle()` 为真时
**跳过全部 tailscale 步骤**：不起隧道、也不把 tailnet IP 当候选（那种地址没有隧道必然不通）。

**中途取回**（`package_partial`）：会话到点/被回收时，产物目录里已经有 `LATEST.zip`
（每次 checkpoint 刷新）——本函数把它复制成控制台习惯的 `deliver-<课>.zip`，人下载后
「导入产物」即可（与跑完时同一个名字、同一个导入路径）。

**为什么本模块顶部不 import `remote.*`**：它在**拿到任务包之前**就要干活——那时
`code.zip` 还没进 `sys.path`，`remote` 包根本不存在（中途取回那条路也一样：包可能还没下来，
而产物已经在盘上）。索引名/代码名因此在这里各留一份常量（有测试盯着与 `remote/bundle.py`
逐字相同），读索引只用 `zipfile` + `json`。**产物包名与交付物打包/课程路径（交付面）**住在
兄弟文件 `remote/offline_deliverable.py`：notebook 把它与本模块一起从 raw 拉取，本模块经
`_load_deliverable()` 延迟装载、`__getattr__` 转发（S5 第十四刀）。`tailscale_boot` 按
`notebook_boot` 的做法双路加载（包内 `remote.tailscale_boot` 或顶层的 `tailscale_boot`）。
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# ── 交付面（S5 第十四刀）住兄弟文件 `remote/offline_deliverable.py` ────────────────────
# notebook 把它与 `offline_boot.py` / `tailscale_boot.py` 一起从 GitHub raw 拉进同一目录
# （名单见 `ipynb/battle.offline.ipynb` 引导格）；仓库/包内形态回落到
# `remote.offline_deliverable`。本模块顶层不得 import `remote.*`（standalone 守卫），
# 也不能在顶层 import 兄弟文件（仓库形态下没有那个顶层模块名）⇒ **延迟装载 + 门面转发**。
_DELIVERABLE_NAMES = (
    "requested_courses",
    "courses_of",
    "_split_course_names",
    "course_work_dir",
    "download_dir",
    "package_deliverable",
    "_partial_last_it",
    "package_partial",
    "ALL_ZIP",
    "LATEST_ZIP",
    "PARTIAL_CANDIDATES",
    "LATEST_ROW_NAME",
    "_COURSE_NAME_RE",
)


def _load_deliverable() -> Any:
    """交付面模块：引导目录里的兄弟文件优先（与会话刚刷新的本模块同源），包内兜底。"""
    for _name in ("offline_deliverable", "remote.offline_deliverable"):
        try:
            return importlib.import_module(_name)
        except ImportError:
            continue
    raise ImportError(
        "找不到 offline_deliverable（notebook 拉取名单缺它？重新打开最新 notebook；"
        "或用任务包里的代码引导）"
    )


def __getattr__(name: str) -> Any:
    """门面（PEP 562）：交付面的名字转发到 `_load_deliverable()`——名字是契约，位置不是。

    只转发 `_DELIVERABLE_NAMES` 闭集（其余名字照常 `AttributeError`，别把打错的属性喂给
    交付面）；要 patch 交付面的行为请打 `offline_deliverable.<名>`——门面只改副本（本仓纪律）。
    """
    if name in _DELIVERABLE_NAMES:
        return getattr(_load_deliverable(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

#: 任务包索引名 / 代码件名 / 包身份 magic —— 与 `remote/bundle.py` 逐字相同（测试守）。
BUNDLE_INDEX = "tools.task.json"
CODE_NAME = "code.zip"
BUNDLE_MAGIC = "battle2-task-bundle"

#: 解包目录（与 notebook_boot 的 `CODE_DIR` 同值：同一个进程里两份引导不打架）。
CODE_DIR = "/tmp/worker-code"

#: 引导模块自述指纹（**只给 notebook 加载后打日志用**，不参与任何逻辑）：磁盘 sha 只说明
#: 「文件刷新成功」，说不了「内存里跑的哪一份」—— 2026-09-25 真机事故里两者恰好相反
#: （磁盘已是新版、`sys.modules` 里还是 08:09 那版，于是「看着新的、跑着旧的」）。
#: notebook 打 `getattr(offline_boot, "BOOT_SELF", "<missing>")`，旧模块会显示 `<missing>`。
#: **改本文件时把末位 +1**（纯人读约定，没有代码读它做判断）。
BOOT_SELF = "boot-2026-10-07a"

#: 产物目录的三件「续跑真值」（与 `remote/artifacts.py::ArtifactStore` 逐字相同；测试守）。
#: 三件齐全 = 本机有可续跑的产物（plan/offline-rerun-local-first §3 的判据）。
PLAN_NAME = "plan.json"
MANIFEST_NAME = "manifest.json"
STATE_NAME = "state.json"
#: TS 运行时树 / 运行时 zip（与 `bundle.TS_TREE_NAME` / `protocol.TS_CODE_NAME` 逐字相同）。
TS_TREE_NAME = "ts_code"
TS_CODE_NAME = "ts_code.zip"

#: 离线**任务清单 / 租约**端点（与 `common/protocol.py` 逐字相同；**本模块不得 import
#: `remote.*`**：包到手之前那个包还不存在）。清单协议版本与租约时长同理（测试守逐字相同）。
#: 为什么 cloud 侧也要有一份：`offline_boot.py` 是 notebook 每次会话从 GitHub raw 刷新的那份，
#: 而 hub 可能在**更旧**的机器上跑（老 hub 没有清单端点）⇒ 兼容必须靠运行时降级，不能靠一起发版。
OFFLINE_TASKS_PATH = "/offline/tasks"
OFFLINE_CLAIM_PATH = "/offline/claim"
OFFLINE_HEARTBEAT_PATH = "/offline/heartbeat"
OFFLINE_RELEASE_PATH = "/offline/release"
OFFLINE_PROGRESS_PATH = "/offline/progress"
OFFLINE_QUEUE_VERSION = 1
OFFLINE_LEASE_TTL_SEC = 900
#: **派发协议版本**（★M1b / plan/worker-type-dispatch-model §1.5.2-P0-4）：claim 必须带
#: `?proto=2`。新 hub 对缺它的请求一律 409（`busy:true` + `proto_required:2`）——所以这个
#: 常量不是「能力声明」，是**入场券**：漏了它本会话什么都领不到（会响亮地打一行日志）。
OFFLINE_CLAIM_PROTO = 2
#: 本机 worker 身份的落点（`<work>/.worker-id`）：**持久化** ⇒ cell 中断后重跑不会被**自己**
#: 留下的租约挡在门外（hub 判 `mine` 直接续上；评审 G1）。
WORKER_ID_NAME = ".worker-id"
#: 心跳周期（秒）：租约 900s ⇒ 60s 一跳留了 15 次补跳的余量（网络抖动 / 长轮之间）。
HEARTBEAT_SEC = 60.0
#: ★M3 / Q2：**轮内打点**的时间节流（秒）——「每 M 秒最多一句」，M ≤ 300s。hub 的 hold
#: 活性只看 `last_progress_at`（缺省 900s 无进度 = stale ⇒ 别的盘可自动接管），而心跳只刷
#: TTL（§68：心跳活、进度死）。取 240s：丢两三拍也还在 900s 以内，日志上每小时 ~15 行。
PROGRESS_MIN_INTERVAL_SEC = 240.0
#: 打点 POST 的超时（秒）：它是「顺手报一句」，绝不能把训练卡住（超时即放弃，下一拍再试）。
PROGRESS_TIMEOUT_SEC = 5.0
#: 连续几拍发不出去就停打点（hub 此刻显然也收不到进度；再刷只是每 240s 一行噪声）。
PROGRESS_FAIL_LIMIT = 3
#: 打点层在 `sys.modules` 里的注册名（与 `common/progress_hook.HOOK_NAME` 逐字相同；测试守——
#: 本模块不许 import 本仓代码，理由见文件头）。快照侧（`plan_run` / `iter_rollout`）就靠这个
#: 名字找到这一层：`common/progress_hook.report(kind, it=…, done=…, total=…)`。
PROGRESS_HOOK_NAME = "bcity_offline_progress"

#: 角色头（与 `common/protocol.py::ROLE_HEADER` / `ROLE_HEADER_VALUE` 逐字相同；测试守——
#: 本模块**不得 import `remote.*`**，理由见文件头）。
#:
#: ★ 2026-09-25（plan/online-offline-role-routing §7.0.1 #2）：跑本 notebook 的云机**天生
#: 就是离线盘** —— 它自报这一条，hub 才认得出「离线盘在线」（`/admin/queue` 的
#: `offline_disk` 读数），也才拦得住别人拿走离线课的任务包（与 job 腿共用同一份归属判据）。
#: 这是**这块盘的身份**，不是「每门课一个开关」：不因此要求 ipynb 填课程名
#: （`requested_courses` 空 ⇒ 照旧走 `/offline/tasks` 清单发现）。
ROLE_HEADER = "X-Battle-Offline"
ROLE_HEADER_VALUE = "1"


def _headers(token: str) -> dict[str, str]:
    """hub 请求头：口令 + **身份**（`X-Battle-Offline`，见 `ROLE_HEADER` 的理由）。

    所有 hub 调用（探活 / 取包 / 续跑锚点 / 代码 / 清单 / 租约 / 补传）都走它：少一处
    就等于那一次「这块盘没报名」，而漏掉的后果是**静默**的（读数少一个、包可能被别处拿走）。
    """
    return {"Authorization": "Bearer " + token, ROLE_HEADER: ROLE_HEADER_VALUE}
#: 清单轮询的缺省间隔（秒）——`CFG.queue_poll_sec`（与等包轮询同档）。
DEFAULT_QUEUE_POLL_SEC = 15.0

#: 等包缺省时长（秒）：hub 没导出 / 用户还没上传，都在这条线上等。
DEFAULT_WAIT_SEC = 1800.0
#: **清单全为终态**时的收工窗口（秒）——`CFG.idle_wait_terminal_sec`。
#: 为什么和 `DEFAULT_WAIT_SEC` 分开：1800s 是「活还没到」的窗口（等新开课 / 等导出），
#: 而终态（`completed` = 本段跑满待人重导包 / `not_offline` = 人固定在离线之外）只有**人介入**
#: 才复活 ⇒ 再等下去纯烧会话时间（2026-10-07 现场：17:04 已全终态，却空转到 17:33 = 29 分钟）。
#: 300s 只给人一个「重导包」的介入窗口；0 = 立刻收工，1800 = 恢复旧行为。
IDLE_WAIT_TERMINAL_SEC = 300.0
#: 等包循环的轮询间隔（秒）——两条源都在这条间隔上轮。
DEFAULT_POLL_SEC = 15.0
#: hub 取包的重试上限（轮数；`CFG["hub_tries"]`，0 = 不限）。到顶就转「等上传」模式。
DEFAULT_HUB_TRIES = 10
#: 探活与取包的超时（秒）。取包给得宽：任务包几 MB～几十 MB，云机的下行不一定快。
PING_TIMEOUT = 8.0
PACK_TIMEOUT = 300.0
#: 手动上传的搜索位置（浅扫，不递归大树）：工作目录/挂载点/平台默认落点。
UPLOAD_GLOBS = (
    ".",
    "/content",
    "/kaggle/working",
    "/kaggle/input/*",
    "/content/drive/MyDrive",
)


def is_kaggle(env: dict | None = None, *, exists: Callable[[str], bool] = os.path.isdir) -> bool:
    """是否在 Kaggle 容器里（与 `remote/artifacts.py::is_kaggle` 同一判据）。

    本模块**不能** import `remote.*`（拿包之前它还不存在），所以判据在这里重写一份，
    由 `tests/remote/test_offline_boot.py` 盯着两边同值：官方标记 `KAGGLE_KERNEL_RUN_TYPE`、
    交付面 `offline_deliverable.download_dir()` 已在用的 `KAGGLE_URL_BASE`，或 `/kaggle/working` 存在。
    """
    e = os.environ if env is None else env
    if e.get("KAGGLE_KERNEL_RUN_TYPE") or e.get("KAGGLE_URL_BASE"):
        return True
    return bool(exists("/kaggle/working"))


def _load_tailscale_boot() -> Any:
    """随 code.zip 下发时是 `remote.tailscale_boot`；从 GitHub raw 拉到临时目录时是顶层模块。"""
    for _name in ("remote.tailscale_boot", "tailscale_boot"):
        try:
            return importlib.import_module(_name)
        except ImportError:
            continue
    raise ImportError("找不到 tailscale_boot（远端引导模块拉取失败？）")


def _build_opener() -> urllib.request.OpenerDirector:
    """显式 ProxyHandler —— Colab 的 `urlopen()` 不读 HTTP_PROXY 环境变量（2026-09-16 实测）。

    与 `remote/notebook_boot.py::_build_opener` 同一份判断；离线盘多抄十行也值，
    因为少了它，userspace tailscale 一开，hub 就再也够不着了（而离线模式里
    「够不着」会静默退化成「等人上传」——最难查的一种）。
    """
    proxies: dict[str, str] = {}
    for k in ("http_proxy", "HTTP_PROXY"):
        v = os.environ.get(k)
        if v:
            proxies["http"] = v
            break
    for k in ("https_proxy", "HTTPS_PROXY"):
        v = os.environ.get(k)
        if v:
            proxies["https"] = v
            break
    if proxies:
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener()


# ── 任务包识别（只靠 zipfile，不 import remote）────────────────────────────


def read_pack_index(zip_path: str | Path) -> dict:
    """读任务包索引（`task.json`）；不是任务包就抛 `ValueError`（调用方决定响亮还是跳过）。

    刻意**不**复用 `remote.bundle.read_bundle_index`：本函数要在 `remote` 进 `sys.path`
    **之前**可用（先有包才有代码）。两边的判据（magic）有测试对账。
    """
    p = Path(zip_path)
    try:
        with zipfile.ZipFile(p) as zf:
            idx = json.loads(zf.read(BUNDLE_INDEX).decode("utf-8"))
    except KeyError as e:
        raise ValueError(f"不是任务包（缺 {BUNDLE_INDEX}）: {p}") from e
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        raise ValueError(f"任务包不可读（{type(e).__name__}: {e}）: {p}") from e
    if not isinstance(idx, dict) or idx.get("magic") != BUNDLE_MAGIC:
        raise ValueError(f"任务包身份不符（magic != {BUNDLE_MAGIC}）: {p}")
    return idx


def course_from_pack_name(path: str | Path) -> str:
    """从 `task-<课>.zip` 里取出 `<课>`（不符合习惯命名 → 空串，不猜）。

    控制台导出写的就是这个名字，`remote/bundle` 的索引里**没有** course 键（课程全文在
    `course.jsonc` 里，解析它得先进 `remote` 包）。所以「跑的是哪门课」这一步只靠文件名，
    而且**只用于对账** —— 取不到就不对账，绝不用猜值去拦人。与
    `remote/deliver_zip.py::course_from_filename` 同一条命名约定。
    """
    m = re.fullmatch(r"task-(.+)\.zip", Path(path).name, re.IGNORECASE)
    if not m:
        return ""
    name = m.group(1).strip()
    return name if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) else ""


def is_task_pack(path: str | Path) -> bool:
    """是不是任务包（`read_pack_index` 的无异常外壳）——扫目录时用，绝不因一个坏 zip 炸整轮。"""
    try:
        read_pack_index(path)
    except (ValueError, OSError):
        return False
    return True


def find_uploaded_pack(cfg: dict, log: Callable[[str], None]) -> Path | None:
    """在 `task_zip` 指定处或若干落点里找**新的**任务包（按 mtime 取最新）。

    为什么按 mtime 取最新而不是按名字：同一门课会导出多次（改超参后重导），而用户的
    动作永远是「把刚下载的那个传上来」——最新的那个就是他要跑的那个。
    """
    explicit = str(cfg.get("task_zip") or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        log(f"CFG.task_zip 指向的包不存在，改去落点里找：{p}")
    cands: list[Path] = []
    for pat in UPLOAD_GLOBS:
        base = Path(pat).expanduser()
        try:
            if base.is_file():
                cands.append(base)
            elif base.is_dir():
                cands.extend(q for q in base.glob("*.zip") if q.is_file())
        except OSError:
            continue
    cands = sorted({q.resolve() for q in cands}, key=lambda q: q.stat().st_mtime, reverse=True)
    for q in cands:
        if is_task_pack(q):
            return q
    return None


def prompt_upload(log: Callable[[str], None]) -> Path | None:
    """Colab：弹一次文件上传框（用户口径「等待用户手动上传」的最短路径）。

    只在 Colab 且 `prompt_upload` 开着时可用；任何失败都**不致命**——叫不动就退回轮询
    （Kaggle 没有上传框，只能靠 Add Data 挂数据集；那里轮询才是正路）。

    **调用点约束**（2026-10-04）：只许在 hub 重试到上界（`hub_parked`）或没配 hub 之后调
    ——`files.upload()` 会**阻塞**在交互上，抢在重试前面弹就等于关掉了自动取包这条路。
    """
    try:
        from google.colab import files  # type: ignore[import-not-found]

        log("弹出上传框：请选择控制台导出的 task-<课>.zip（在控制台「导出任务包」下载）")
        got = files.upload()
        for name in got or {}:
            if str(name).lower().endswith(".zip"):
                log(f"已收到上传文件: {name}")
    except Exception as e:  # 非 Colab / 无交互内核 / 用户取消
        log(f"上传框不可用（{type(e).__name__}: {e}）——改为轮询文件落点")
        return None
    return find_uploaded_pack({}, log)


# ── 本机产物优先（plan/offline-rerun-local-first §3/§4.1）─────────────────────


def _read_json_file(p: Path) -> dict | None:
    """读一个小 json（缺失/损坏/非对象 → None）：本机产物判据**绝不因半截文件炸**。"""
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def local_artifacts(dest: str | Path) -> dict | None:
    """本机产物目录的「续跑真值」（三件齐全才认，否则 None）。

    三件 = `state.json`（续跑点）+ `plan.json`（本段契约）+ `manifest.json`（代码/血统）。
    **为什么三件都要**：`run_loop` 的 `load_planned_manifest` 就是这么判的（缺一即拒）——
    这里不猜，只回答一个问题：要不要让包来改写这一段的计划与清单。
    """
    root = Path(dest)
    st = _read_json_file(root / STATE_NAME)
    plan = _read_json_file(root / PLAN_NAME)
    man = _read_json_file(root / MANIFEST_NAME)
    last_it = (st or {}).get("last_it")
    if st is None or plan is None or man is None:
        return None
    if not isinstance(last_it, int) or isinstance(last_it, bool):
        return None
    return {
        "root": root,
        "last_it": last_it,
        "start_it": int(plan.get("start_it", 0) or 0),
        "end_it": int(plan.get("end_it", 0) or 0),
        "plan_sha256": str(st.get("plan_sha256", "") or ""),
        "run_id": str(st.get("run_id", "") or ""),
        "code_sha256": str(man.get("code_sha256", "") or ""),
        "ts_code_sha256": str(man.get("ts_code_sha256", "") or ""),
    }


def _sha12(text: object) -> str:
    s = str(text or "")
    return s[:12] if s else "-"


def describe_local_vs_pack(local: dict, idx: dict) -> str:
    """G2 的对照行（本机 vs 包）：同段/异段一眼可辨 —— **绝不许静默换段**。"""
    same_plan = bool(local.get("plan_sha256")) and local["plan_sha256"] == str(
        idx.get("plan_sha256", "") or ""
    )
    head = "包与本机计划一致（同一段）" if same_plan else "★ 包与本机不是同一段"
    return (
        f"{head}：本机 it{local['last_it']}"
        f"（计划 it{local['start_it']}→it{local['end_it']}，plan_sha12={_sha12(local['plan_sha256'])}）"
        f" vs 包 it{idx.get('it')}→it{idx.get('end_it')}"
        f"（commit {_sha12(idx.get('commit'))}，plan_sha12={_sha12(idx.get('plan_sha256'))}）"
        "——本机优先：**不**用包改写计划/清单（要换段请清空产物目录，或置 CFG.force_pack=true）"
    )


def _local_sha(root: Path, fname: str, key: str) -> str:
    """从产物目录的一个 json 件里取一个 sha 字段（读不到/空 ⇒ `""` = 不设门）。"""
    return str((_read_json_file(root / fname) or {}).get(key, "") or "")


# ── hub 侧：解析地址 → 探活 → 取包 ─────────────────────────────────────────


def hub_candidates(cfg: dict, creds: dict) -> list[str]:
    """hub 候选地址（去重、保序）：`CFG.hub_url`（公网隧道 URL 或手填）→ `HUB_IP`（tailnet）。

    离线盘两条路都可能通：本机 hub 用 cloudflared 暴露成公网 URL 时**不需要 tailscale**；
    只有走 tailnet IP 时才需要（那条路才去起 tailscale）。

    **Kaggle 上不给 tailnet 候选**（用户口径 2026-09-23）：起了隧道也不通，把它放在候选里
    只会每次探活都白等一个超时；tailnet 地址与「起 tailscale」是同一条路的两半，一起跳过
    （只留 `hub_url` 那条公网路）。
    """
    out: list[str] = []
    u = str(cfg.get("hub_url") or "").strip().rstrip("/")
    if u and "<" not in u:
        out.append(u)
    ip = "" if is_kaggle() else str(creds.get("HUB_IP") or "").strip()
    if ip:
        try:
            resolved = _load_tailscale_boot().resolve_hub_url("", ip, int(cfg.get("hub_port") or 0))
        except Exception:
            resolved = ""
        if resolved and resolved not in out:
            out.append(resolved)
    return out


def probe_hub(hub: str, token: str, log: Callable[[str], None], timeout: float = PING_TIMEOUT) -> bool:
    """`GET /ping` 探活：True/False（**只判连通性**，鉴权错也算「通」——那是配置问题不是网络问题）。"""
    try:
        req = urllib.request.Request(
            hub.rstrip("/") + "/ping", headers=_headers(token)
        )
        with _build_opener().open(req, timeout=timeout) as resp:
            resp.read(1)
        return True
    except urllib.error.HTTPError as e:
        log(f"{hub} 应答 HTTP {e.code}（hub 在线，但鉴权/路由不对）")
        return e.code == 401 or e.code == 403
    except Exception as e:
        log(f"{hub} 连不上（{type(e).__name__}: {e}）")
        return False


def _fetch_guarded(
    url: str, headers: dict[str, str], log: Callable[[str], None], label: str, total_timeout: float
) -> bytes:
    """取一个**大 body**：走引导期传输护栏（进度行 / 停滞 / 超预算 / 低速重抽）。

    护栏住 `tailscale_boot`（三个 notebook 都拉它）——它必须在 `code.zip` **之前**可用，
    而任务包正是被下载的那个东西（鸡生蛋）。万一它没加载（纯离线兑底路径），退回
    朴素整读并**响亮记一笔**：任务包几 MB～几十 MB，没护栏时一次坏签就是「一行日志
    都没多，干等」——绝不静默降级。
    """
    try:
        ts = _load_tailscale_boot()
    except ImportError as e:
        log(f"引导传输护栏不可用（{e}）——本次取包只有整超时，无停滞/低速判据")
        req = urllib.request.Request(url, headers=headers)
        with _build_opener().open(req, timeout=total_timeout) as resp:
            return resp.read()  # type: ignore[no-any-return]
    got: bytes = ts.fetch_guarded(
        url, headers=headers, log=log, label=label, total_timeout=total_timeout, attempts=3
    )
    return got


class PackUnavailableError(SystemExit):
    """「这门课没拿到任务包」——**只表示取包失败**，不等于训练失败。

    单开一类（`SystemExit` 的子类，message 逐字不变）是为了让 `run()` 能**只**放行这一类：
    多课程串行时跳过它继续下一门（plan/offline-switch-auto-bundle §3.5），而课程名不一致、
    产物目录不完整这类**配置错误**照旧立即停（继续跑没有意义）。

    为什么必须是 `SystemExit` 子类：既有用例与 notebook 都按 `SystemExit` 处理取包失败
    （`pytest.raises(SystemExit)` / cell 末尾 `raise SystemExit(_rc)`）——子类让它们逐字不变。
    """


def _http_error_parts(
    e: urllib.error.HTTPError, limit: int = 400
) -> tuple[str, dict]:
    """HTTPError 的响应正文 → `(人读正文, 解析出的 dict)`。**只读一次**。

    为什么要合成一个函数（评审 F2）：HTTPError 的 fp **读完即空** ⇒ 「先 `_http_error_body(e)`
    再读一次拿 `path`/`known_courses`」第二次只会拿到 `b""`（现场表现是「hub 说：（空正文）」）。
    这里一次 `e.read()`，正文文本与结构体都从同一份 bytes 出。
    """
    try:
        raw = e.read()
    except Exception:  # 正文读不出来不该盖掉 HTTP 状态本身
        return "（无正文）", {}
    try:
        loaded = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        loaded = None
    doc = loaded if isinstance(loaded, dict) else {}
    if doc.get("error"):
        return str(doc["error"])[:limit], doc
    text = raw.decode("utf-8", "replace").strip()
    return (text[:limit] or "（空正文）"), doc


def _http_error_body(e: urllib.error.HTTPError, limit: int = 400) -> str:
    """HTTPError 的响应正文（优先取 json 的 `error` 字段；读不到就说清「无正文」）。

    ⚠ 只能读一次：既要不只正文、又要正文里的字段时用 `_http_error_parts`（F2）。
    """
    return _http_error_parts(e, limit)[0]


def fetch_task_pack(
    hub: str,
    token: str,
    course: str,
    dest_dir: Path,
    log: Callable[[str], None],
    timeout: float = PACK_TIMEOUT,
    *,
    lease: str = "",
) -> Path | None:
    """`GET /offline/task-pack?course=<课>[&lease=<token>]` → 落到 `dest_dir/task-<课>.zip`。

    返回 None = **这一次**没取到（404 还没导出 / 网络抖动）——调用方据此重试或转手动。
    401/403 也不抛：包可能已经在用户手上，手动路仍然能把任务做完，所以只响亮记一笔。

    ★M1b / P1-2：hub 的取包门现在还会问「这门课有没有 **live hold**」——有主时（包括
    **本机自己**刚 claim 完）不带 `?lease=` 会吃 409。所以 `_run_batch` 领到租约后必须把
    token 一路传到这里（空 = 老路径：没领租约的 `CFG.course` 腿，只有「无人持」时才取得到）。
    """
    url = f"{hub.rstrip('/')}/offline/task-pack?course={urllib.parse.quote(course)}"
    if lease:
        url += f"&lease={urllib.parse.quote(lease)}"
    try:
        raw = _fetch_guarded(
            url, _headers(token), log, "task-pack", timeout
        )
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            log(f"取包被拒 HTTP {e.code} —— HUB_TOKEN 不一致（手动上传仍然可行）")
        elif e.code == 404:
            # ★ 2026-09-25（plan/offline-switch-auto-bundle §3.2）：hub 的 404 正文里带
            #   诊断字段（`path` / `known_courses` / 触发回执），而此前只打一行固定文案 ⇒
            #   现场无法区分「包没导出」「hub 的 traj-root 不对」「hub 压根没发现这门课」
            #   （三条的表现逐字相同，用户 2026-09-25 因此白等 28 分钟）。这里把正文读出来。
            body, doc = _http_error_parts(e)
            log(f"hub 上还没有 task-{course}.zip —— 先在控制台「导出任务包」")
            if body and body not in ("（空正文）", "（无正文）"):
                log(f"  hub 说：{body}")
            path_s = str(doc.get("path") or "")
            known = doc.get("known_courses")
            if path_s:
                log(f"  hub 找的落点：{path_s}")
            if isinstance(known, list):
                names = ", ".join(str(x) for x in known)
                if course in [str(x) for x in known]:
                    log(f"  hub 已知课程：{names or '（无）'}（含本门 ⇒ hub 找得到课，只是没这个包）")
                else:
                    log(
                        f"  hub 已知课程：{names or '（一门都没有）'}（**没有本门** ⇒ hub 还没发现"
                        "这门课：课程名不一致 / 训练目录不新鲜 / hub 的 --traj-root 不是本机 tmp）"
                    )
            if doc:
                if doc.get("give_up"):
                    log("  hub 动作：重导已到上界 —— 请到控制台手动「导出任务包」")
                elif doc.get("triggered"):
                    log(f"  hub 动作：{doc.get('trigger_note') or '已替我们触发了一次重导'}，稍后会重试")
                elif doc.get("trigger_note"):
                    log(f"  hub 动作：{doc['trigger_note']}")
        elif e.code == 409:
            # 409 = hub 判「包已过期」（可能已经替我们触发重导）——**正文就是下一步**：
            # 只打一个数字的话，人在云机上看到的是「取包失败」，而真实原因已经写在正文里了
            # （plan/offline-rerun-local-first §8.3 第 3 条）。
            log(f"hub 说任务包已过期（HTTP 409）：{_http_error_body(e)}")
        else:
            log(f"取包失败 HTTP {e.code}：{_http_error_body(e)}")
        return None
    except Exception as e:
        log(f"取包异常（{type(e).__name__}: {e}）—— 稍后重试")
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"task-{course}.zip"
    dest.write_bytes(raw)
    sha12 = hashlib.sha256(raw).hexdigest()[:12]
    log(f"任务包就位: {dest}（{len(raw)} bytes, sha12={sha12}）")
    return dest


def fetch_resume(
    hub: str,
    token: str,
    course: str,
    dest_dir: Path,
    log: Callable[[str], None],
    timeout: float = PING_TIMEOUT,
) -> Path | None:
    """`GET /offline/resume?course=<课>` → 把锚点轮次铺进 `dest_dir`（秒级小请求，失败= None）。

    云机重领任务时「任务包比 hub 上的进度旧」是常态（包是导出那一刻的快照，而云机可能
    已被回收又重开）。锚点三件（weights/opt/指标行）拿到手后交给 `run_loop --resume-dir`，
    它把那一轮采纳进产物目录、从之后接着跑（用户口径：必须**同轮齐全**，否则退到更早轮
    ——所以这里只认 hub 给的那一轮，不在客户端自己扫描/凑件）。

    任何失败都**不影响训练**（安静回 None，日志里留一行原因）：锚点只是「少跑几轮」的
    优化，包自带的起点永远能跑。
    """
    url = f"{hub.rstrip('/')}/offline/resume?course={urllib.parse.quote(course)}"
    try:
        req = urllib.request.Request(url, headers=_headers(token))
        with _build_opener().open(req, timeout=timeout) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log(f"取续跑锚点失败（{type(e).__name__}: {e}）——从任务包自带起点跑")
        return None
    resume = meta.get("resume") if isinstance(meta, dict) else None
    if not isinstance(resume, dict):
        log("hub 上没有比任务包更新的完整轮次——从任务包自带起点跑")
        return None
    try:
        it = int(resume["it"])
    except (KeyError, TypeError, ValueError):
        log(f"续跑锚点元信息缺 it（{resume}）——忽略")
        return None
    ac_dir = dest_dir / f"it-{it:03d}"
    ac_dir.mkdir(parents=True, exist_ok=True)
    for name in ("weights.json", "opt.tar", "row.json"):
        q = f"{url}&it={it}&name={urllib.parse.quote(name)}"
        try:
            req = urllib.request.Request(q, headers=_headers(token))
            with _build_opener().open(req, timeout=timeout) as resp:
                (ac_dir / name).write_bytes(resp.read())
        except Exception as e:
            # 三件不齐 = 这个锚点不可用（不是「少一件也能跑」）：静默退回包自带起点。
            log(f"取续跑锚点件 {name} 失败（{type(e).__name__}: {e}）——放弃该锚点")
            return None
    (dest_dir / "resume.json").write_text(
        json.dumps({**resume, "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    log(
        f"续跑锚点就位：it{it}（来源 {resume.get('source')}，run={resume.get('run_id')}，"
        f"wfp={str(resume.get('weights_fp'))[:12]}…）——本段从它之后继续"
    )
    return dest_dir


# ── 拿包：hub 与手动两条源，一个等待循环 ───────────────────────────────────


def _try_hubs(
    hubs: list[str],
    broken: list[str],
    creds: dict,
    course: str,
    work_dir: Path,
    log: Callable[[str], None],
    *,
    lease: str = "",
) -> Path | None:
    """对着候选逐个试一轮取包（探活 → 取包），返回包或 None（纯函数壳，只求可测）。

    `broken` 就地记下**探活失败**的候选：够不着 ≠ 取不到 —— 够不着的候选在本次等待里不再探
    （探活超时 8s，无谓地重复探一个死地址就是白等），而「在线但没包」不算黑名单事件，
    它由调用方的重试上限兜住。**一轮只真取一次**（第一个够得着的候选说了算）——
    同一个包对 N 个候选各取一遍只是让日志变长。
    """
    tok = str(creds.get("HUB_TOKEN") or "")
    for hub in hubs:
        if hub in broken:
            continue
        if not probe_hub(hub, tok, log):
            broken.append(hub)
            continue
        log(f"hub 在线: {hub}")
        if not course:
            log("没填 CFG.course —— 没法按课程取包（去控制台看课程名，或在 CFG 里填）")
            return None
        return fetch_task_pack(hub, tok, course, work_dir, log, lease=lease)
    return None


def obtain_pack(
    cfg: dict,
    creds: dict,
    log: Callable[[str], None],
    work_dir: Path,
    stop: Any = None,
    *,
    optional: bool = False,
    lease: str = "",
) -> Path | None:
    """拿任务包：显式路径 → 已有落点 → （hub 取 / 等人传）等到 deadline 为止。

    `optional=True`（本机已有产物时的「本机优先」路径）：**只试一次就收手**——包这时只是
    「代码/TS 的备源」，为一个可选的东西等 30 分钟（甚至因为等不到而拒绝起跑）正是用户
    抱怨的那种白等。所以：`wait_s` 归零、不弹上传框、取不到**返回 `None`**（不是
    `SystemExit`——`0s` 的既有语义是「不等待，立刻报错」，与本模式无关）。

    **两条源在同一个循环里轮询**（每轮先看落点、再试 hub）：用户随时可能上传，hub 也随时
    可能被点上「导出」——把它们排成先后两步，会让「上传之后又等满 hub 的超时」这种事发生。
    **上传框只在 hub 试满上限之后弹一次**（2026-10-04：`files.upload()` 阻塞在交互上，
    抢在重试前面弹 = 重试机制失效）；没配 hub 时立刻弹（那已是唯一的路）。

    **hub 只试 `CFG["hub_tries"]` 轮**（缺省 `DEFAULT_HUB_TRIES` = 10，0 = 不限）：连试这么多
    轮还没拿到就不再碰 hub，**转入「等上传」模式**并响亮说明（用户口径 2026-09-23）。原来
    没有上限，hub 没导出时就把 30 分钟全花在每 15s 一次的探活/取包上：云机侧看起来像死了，
    而实际上它只是在一个永远不会成功的请求上刷日志。到点仍无包 ⇒ `SystemExit`，正文就是
    下一步该做什么（不猜、不静默重试）。
    """
    explicit = str(cfg.get("task_zip") or "").strip()
    if explicit and Path(explicit).expanduser().is_file():
        p = Path(explicit).expanduser()
        log(f"用 CFG.task_zip 指定的任务包: {p}")
        return p

    found = find_uploaded_pack(cfg, log)
    if found is not None:
        log(f"落点里已有任务包: {found}")
        return found

    course = str(cfg.get("course") or "").strip()
    # 注意 `0` 是**合法**值（「不等待，立刻报错」）——不能用 `or` 兜底（falsy-zero 陷阱）。
    wait_s = DEFAULT_WAIT_SEC if cfg.get("wait_pack_sec") is None else float(cfg["wait_pack_sec"])
    if optional:
        wait_s = 0.0  # 本机优先：只试一次（下面的 prompt 门也是 `wait_s > 0`）
    poll_s = max(0.05, float(cfg.get("poll_sec") or DEFAULT_POLL_SEC))
    # 同样注意 `0` 是**合法**值（不限轮数）——不能用 `or` 兜底（falsy-zero 陷阱，实测踩过）。
    tries_raw = cfg.get("hub_tries")
    max_hub_tries = DEFAULT_HUB_TRIES if tries_raw is None else max(0, int(tries_raw))
    deadline = time.time() + max(0.0, wait_s)
    hubs = hub_candidates(cfg, creds)
    hubs_tried_ts = False
    prompted = False
    hub_broken: list[str] = []
    hub_tries = 0
    hub_parked = False
    if optional:
        log(
            f"本机已有产物 ⇒ 只试一次 hub 取包（不等、不弹上传框）："
            f"hub 候选 {hubs or '(没配 hub 地址)'}"
        )
    else:
        log(
            f"等任务包（上限 {wait_s:.0f}s）：hub 候选 {hubs or '(没配 hub 地址)'}"
            + (f"，hub 最多试 {max_hub_tries} 轮" if max_hub_tries else "，hub 不限轮数")
        )
    while True:
        found = find_uploaded_pack(cfg, log)
        if found is not None:
            log(f"收到任务包: {found}")
            return found
        if stop is not None and stop.is_set():
            raise SystemExit("[offline] 收到停机信号 —— 等包中止（未开始任何训练）")
        if not hub_parked and hubs:
            hub_tries += 1
            got = _try_hubs(hubs, hub_broken, creds, course, work_dir, log, lease=lease)
            if got is not None:
                return got
            if not hubs_tried_ts and all(h in hub_broken for h in hubs):
                # 所有候选都够不着：**这时**才值得去起 tailscale（可能只是还没入 tailnet）。
                ip = ensure_tailscale(cfg, creds, log)
                hubs_tried_ts = True
                if ip:
                    # 之前的失败是「还没入 tailnet」，不是「地址不对」——清掉黑名单重试全部候选。
                    hub_broken.clear()
                    hubs = hub_candidates(cfg, creds)
                    continue
            if max_hub_tries and hub_tries >= max_hub_tries:
                hub_parked = True
                log(
                    f"hub 取包试了 {hub_tries} 轮都没成功（CFG.hub_tries={max_hub_tries}）"
                    "⇒ 转入「等上传」模式，不再轮询 hub：去控制台「导出任务包」→ 上传到本 "
                    "notebook（Colab 上传框 / Kaggle Add Data），或写进 CFG['task_zip']；"
                    "若你刚导出、想让它自己取，**重跑本 cell** 即可"
                )
        # ★ 2026-10-04 Colab 现场：弹框（`files.upload()`）会**阻塞**在交互上——抢在 hub
        #   重试前面弹，等于把「hub 正在导包、稍后自动取到」这条路一刀切断（现场日志：
        #   hub 刚说「已替你触发一次重导，稍后重试」，下一行就是上传框 ⇒ 重试机制失效）。
        #   用户口径：先让 hub 试满 `hub_tries` 轮（转「等上传」模式）**才**弹。没配 hub
        #   时无重试可破坏，照旧立刻弹（那是唯一的路）。
        if (
            not prompted
            and (hub_parked or not hubs)
            and cfg.get("prompt_upload", True)
            and wait_s > 0
        ):
            prompted = True
            got = prompt_upload(log)
            if got is not None:
                return got
        if time.time() >= deadline:
            break
        time.sleep(max(0.05, min(poll_s, deadline - time.time())))

    if optional:
        log("本机优先：这次没取到包 —— 代码/TS 从产物目录取，回传 best-effort（不因此停跑）")
        return None
    raise PackUnavailableError(
        f"[offline] {wait_s:.0f}s 内没拿到任务包 —— 两条路任选其一：\n"
        f"  ① hub 取包：控制台「导出任务包」（导出要求该课训练已停）→ 保持 hub 在线"
        f"（{'、'.join(hubs) if hubs else 'CFG.hub_url / HUB_IP 未配'}）→ 重跑本 cell；"
        f"\n  ② 手动送包：控制台下载 `task-<课>.zip` → 上传到本 notebook（Colab 上传框 / "
        f"Kaggle Add Data）或写进 `CFG['task_zip']` → 重跑本 cell。"
    )


def ensure_tailscale(cfg: dict, creds: dict, log: Callable[[str], None]) -> str:
    """best-effort 起 tailscale（离线模式**不因它失败而终止**：没隧道还有手动路）。

    ★ 凭据必须在进本函数之前读完：`ensure` 会把进程代理改写成只转发 Tailscale IP 的
    userspace 代理，之后平台 Secrets（公网 HTTPS）就够不着了（2026-09-17 Kaggle 事故）。

    ★ **Kaggle 上一律跳过**（用户口径 2026-09-23）：那里既没有 NET_ADMIN 也换不得网络
    命名空间，**正是**上面那个事故的现场；换不来隧道，只换来一个改坏了的代理环境。
    所以这一层（而不是调用点）做短路：任何将来新增的调用点都不会绕过它。
    """
    if is_kaggle():
        log("Kaggle 环境 ⇒ 跳过 tailscale（起不来隧道，且 userspace 引导会改坏平台代理）")
        return ""
    key = str(creds.get("TS_AUTHKEY") or "").strip()
    if not key:
        log("没有 TS_AUTHKEY —— 跳过 tailscale（只走公网 hub_url / 手动送包）")
        return ""
    try:
        ts = _load_tailscale_boot().ensure(
            {
                "ts_authkey": key,
                "ts_ephemeral": bool(cfg.get("ts_ephemeral", True)),
                "proxy_env": True,
                "engine": str(cfg.get("ts_engine") or ""),
            },
            log,
        )
        return str(ts.get("ip") or "")
    except Exception as e:
        log(f"tailscale 起不来（{type(e).__name__}: {e}）—— 继续走手动送包")
        return ""


# ── 跑：code.zip 引导 → run_loop（与云端 worker 同一条链）──────────────────


def _pack_member(pack: Path | None, name: str) -> bytes | None:
    """读包里的一个成员（没包/不是 zip/没这个件 → None，不抛）——本机优先时包只是备源。"""
    if pack is None:
        return None
    try:
        with zipfile.ZipFile(pack) as zf:
            return zf.read(name)
    except (KeyError, OSError, zipfile.BadZipFile):
        return None


def _code_candidates(
    pack: Path | None, dest: Path | None, hub: str, token: str, log: Callable[[str], None]
) -> list[tuple[str, bytes]]:
    """代码候选（按优先级）：产物目录 → 任务包 → hub 的共享 `GET /code`（与课程无关的兜底）。"""
    out: list[tuple[str, bytes]] = []
    if dest is not None and (dest / CODE_NAME).is_file():
        try:
            out.append((f"本机产物 {dest / CODE_NAME}", (dest / CODE_NAME).read_bytes()))
        except OSError as e:
            log(f"读本机 {CODE_NAME} 失败（{e}）——跳过它")
    raw = _pack_member(pack, CODE_NAME)
    if raw is not None:
        out.append((f"任务包 {pack}", raw))
    if hub and token:
        try:
            got = _fetch_guarded(
                hub.rstrip("/") + "/code",
                _headers(token),
                log,
                "code.zip",
                PACK_TIMEOUT,
            )
        except Exception as e:
            log(f"hub 取共享代码失败（{type(e).__name__}: {e}）——跳过它")
        else:
            out.append((f"hub {hub}/code", got))
    return out


def ensure_code(
    pack: Path | None,
    log: Callable[[str], None],
    code_dir: str = CODE_DIR,
    *,
    dest: str | Path | None = None,
    hub: str = "",
    token: str = "",
) -> Path:
    """把**与本机产物同 commit** 的 `code.zip` 解到 `code_dir` 并插进 `sys.path`。

    优先级（plan/offline-rerun-local-first §4.1 第 4 条）：产物目录 `<dest>/code.zip` →
    任务包里的 `code.zip` → hub 的共享 `GET /code`。期望 sha = `<dest>/manifest.json` 的
    `code_sha256`（读不到就不设门），**逐候选用 sha 选中**——绝不静默换代码：

      * 全部对不上 ⇒ `SystemExit`，**不写任何东西**（两条出路：`CFG.task_zip` 指对同 commit
        的包，或清空 `dest` 从头跑）；
      * 选中的那份 ≠ 现盘 `<dest>/code.zip` ⇒ **用同 sha 副本修复**它：`run_loop` 读的是产物
        目录那份（`_read_opt_file(root, "code.zip")`），不修就会在 worker 侧报「传输损坏」
        ——一条指向错误原因的报错（评审 F4）。
    """
    root = Path(dest) if dest is not None else None
    want = _local_sha(root, MANIFEST_NAME, "code_sha256") if root is not None else ""
    cands = _code_candidates(pack, root, hub, token, log)
    if not cands:
        raise SystemExit(
            "[offline] 没有可用的 code.zip：本机产物目录 / 任务包 / hub 共享代码三处都没有"
            f"（产物目录 {root if root is not None else '(未给)'}）——去控制台导一次包并填 CFG.task_zip"
        )
    picked_src, picked = "", b""
    why: list[str] = []
    for src, data in cands:
        got = hashlib.sha256(data).hexdigest()
        if not want or got == want:
            picked_src, picked = src, data
            break
        why.append(f"{src}: sha12={got[:12]}… ≠ 产物 manifest 的 {want[:12]}…")
    if not picked:
        raise SystemExit(
            "[offline] 代码与本机产物对不上（绝不静默换代码、也不覆盖）：\n  "
            + "\n  ".join(why)
            + f"\n  两条出路：① CFG.task_zip 指对**同 commit** 的任务包；② 清空重跑 {root}"
        )
    code_root = Path(code_dir)
    code_root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(picked)) as z:
        z.extractall(code_root)
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))
    log(
        f"代码就位: {code_root}（{len(picked)} bytes, "
        f"sha12={hashlib.sha256(picked).hexdigest()[:12]}，来源 {picked_src}）"
    )
    if root is not None and want:
        live = root / CODE_NAME
        live_sha = hashlib.sha256(live.read_bytes()).hexdigest() if live.is_file() else ""
        if live_sha != want:
            live.write_bytes(picked)  # manifest 背书的那份（不是"另一个版本"）
            log(f"产物目录的 {CODE_NAME} 已按 manifest 修复（{_sha12(live_sha)} → {_sha12(want)}）")
    return code_root


def ensure_ts_tree(pack: Path | None, log: Callable[[str], None], *, dest: str | Path) -> None:
    """把 TS 运行时树布置到产物目录（`<dest>/ts_code/` + `<dest>/ts_code.zip`）。

    已有 `ts_code/` ⇒ **直接用**（`run_standalone` 的 `ensure_ts_cache_layout(root, …)` 兜到
    它，幂等）；缺了才从包里补，而且补的字节必须与 `<dest>/manifest.json` 的
    `ts_code_sha256` 相符——否则就是「本机 manifest + 包里的 TS」的混血：rollout 行为与权重
    血统不符，读数看起来完全正常，只有 sha 能揭穿（评审 F4）。
    """
    root = Path(dest)
    if (root / TS_TREE_NAME).is_dir():
        log(f"TS 运行时用本机产物里的 {TS_TREE_NAME}/（不重解）")
        return
    raw = _pack_member(pack, TS_CODE_NAME)
    if raw is None and (root / TS_CODE_NAME).is_file():
        raw = (root / TS_CODE_NAME).read_bytes()
    if raw is None:
        log(
            f"WARN: 没有 TS 运行时（{root}/{TS_TREE_NAME} 与 {TS_CODE_NAME} 都缺）——"
            "rollout 起不来；把上一段的 ts_code/ 一起带过来，或给一个含它的包"
        )
        return
    want = _local_sha(root, MANIFEST_NAME, "ts_code_sha256")
    got = hashlib.sha256(raw).hexdigest()
    if want and got != want:
        raise SystemExit(
            "[offline] TS 运行时与本机产物对不上（包里的 ts_code.zip sha12="
            f"{got[:12]}… ≠ manifest 的 {want[:12]}…）——拒跑：混血会让 rollout 与权重血统不符。"
            f"\n  两条出路：① CFG.task_zip 指对**同 commit** 的任务包；② 清空重跑 {root}"
        )
    # ★ 2026-09-25 现场回归：**首次跑**时产物目录还不存在（它本来是 `run_loop`/`import_bundle`
    #   建的），而这里要往里写 `ts_code.zip` ⇒ 必须先把它建出来。不建的后果是**未捕获**的
    #   `FileNotFoundError: <dest>/ts_code.zip`，整个 cell 死在「代码就位」之后（用户实测报障）。
    #   为什么不用「等 bundle 导入来铺」：本机优先那条腿**不导入包**（argv 不带 `--bundle`），
    #   TS 树只能由这里铺；两条腿共用一个函数，就在函数里把目录准备好。
    root.mkdir(parents=True, exist_ok=True)
    (root / TS_CODE_NAME).write_bytes(raw)
    tree = root / TS_TREE_NAME
    tree.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(tree)
    log(f"TS 运行时已就位: {tree}（{len(raw)} bytes, sha12={got[:12]}）")


def write_token_file(dest_dir: Path, token: str) -> str:
    """把 hub token 落成 0600 文件（走 `--hub-token-file`，不进 argv/ps/日志）。"""
    if not token:
        return ""
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / "hub.token"
    p.write_text(token, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return str(p)


def write_lease_file(dest_dir: Path, lease: str) -> str:
    """把本课的租约 token 落成 0600 文件（`--hub-lease-file`，形状同 `write_token_file`）。

    为什么也要走文件：它是**权力**（P1-1：持它才能推进活动起点），与 hub token 同一量级的
    敏感物 ⇒ 不进 argv（`ps` 能看）/日志（`_redact` 名单也会盖到）。
    """
    if not lease:
        return ""
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / "hub.lease"
    p.write_text(lease, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return str(p)


def build_run_argv(
    cfg: dict,
    pack: Path | None,
    dest: Path,
    hub: str,
    token_file: str,
    resume_dir: str | Path | None = None,
    *,
    local_first: bool = False,
    lease_file: str = "",
) -> list[str]:
    """run_loop 的 argv（纯函数，便于单测钉住回传/评估/锚点三组开关的形状）。

    `cfg["course"]` 在这一层恒是**单个**课程名（`run_one_course` 已把多课程列表拆开），
    它同时是补传的归位键（hub 侧 `<traj>/<课>/` 的目录名）与任务包名的一部分。

    `local_first=True`（本机已有产物）或根本没拿到包 ⇒ **不传 `--bundle`**：包不参与，
    plan/manifest/代码/TS 全从产物目录读（`run_loop.main` 允许 `--artifacts` 单独用）。
    这是本 plan 的核心动作：**不存在"包覆盖本机计划"这条路径**（评审 F1/F3）。
    """
    course = str(cfg.get("course") or "").strip()
    argv: list[str] = []
    if pack is not None and not local_first:
        argv += ["--bundle", str(pack)]
    argv += ["--artifacts", str(dest)]
    device = str(cfg.get("device") or "").strip()
    if device:
        argv += ["--device", device]
    if int(cfg.get("threads") or 0):
        argv += ["--threads", str(int(cfg["threads"]))]
    if int(cfg.get("max_iters") or 0):
        argv += ["--max-iters", str(int(cfg["max_iters"]))]
    if float(cfg.get("budget_sec") or 0):
        argv += ["--budget-sec", str(float(cfg["budget_sec"]))]
    # rollout 并行局数：缺省（不传）= 按云机核数 `cpu_worker_slots`（≤4 核全给，否则
    # max(cores−2, cores×0.8)），与云机 eval 同一口径。
    # 传了就完全按它——两边都不为对方预留，因为两者**真交替**（`run_loop._maybe_cloud_eval`
    # 提交后有界等本轮评估收线，见那里的 EVAL_ALTERNATE_WAIT_SEC：两条腿同时开满会把单局
    # 墙钟推过 5s 硬顶，2026-09-25 云机卡死就是这么来的）。
    if int(cfg.get("rollout_workers") or 0):
        argv += ["--rollout-workers", str(int(cfg["rollout_workers"]))]
    # 云机 A 层评估（`eval_on_cloud`）：语料/口径全部由课程（随包的 course.jsonc）决定，
    # 这里只传两个执行面旋钮（并发/单局超时）——不在 notebook 里重复一遍语料定义。
    # ★ 2026-10-03（plan/auto-offline-handoff U5）：缺省改 **True**，与 notebook 的 CFG 同值
    # （此前两处不一致：notebook 默认 True、这里写死 False ⇒ 非 notebook 调用方静默少跑评估）。
    if bool(cfg.get("eval_on_cloud", True)):
        argv += ["--eval-on-cloud"]
        if int(cfg.get("eval_slots") or 0):
            argv += ["--eval-slots", str(int(cfg["eval_slots"]))]
        if float(cfg.get("eval_game_timeout_sec") or 0):
            argv += ["--eval-game-timeout-sec", str(float(cfg["eval_game_timeout_sec"]))]
    # 续跑锚点（hub 递回的最新「同轮齐全」轮次）：有它就从那儿接，没有就从包自带起点。
    if resume_dir and Path(resume_dir).is_dir():
        argv += ["--resume-dir", str(resume_dir)]
    if bool(cfg.get("live_backfeed", True)) and hub and token_file:
        argv += ["--hub-url", hub, "--hub-token-file", token_file]
        # 补传的**归位键**（多课程 hub 必需）：`CFG.course` 就是控制台里那门课的名字，
        # 也是 hub 侧 `<traj>/<课>/` 的目录名（hub 靠它把每一轮落进正确的课程目录）。
        # 缺了它，多课程 hub 会以「无法归属课程」把每条补传拒掉（400）——训练不受影响，
        # 但控制台上看不到段内进度，只剩「跑完自己下载导入」那条路。
        if course:
            argv += ["--hub-course", course]
        # ★M1b / P1-1：本课的租约 token → 补传体里的 `lease_token`。**没有它照样跑**
        # （补传照落、只是不推进活动起点：hub 侧 `offline_advance_ok` 的三态之一），
        # 所以缺租约（老 hub / 没 claim 成）不是错误。
        if lease_file:
            argv += ["--hub-lease-file", lease_file]
    else:
        argv += ["--no-deliver"]
    return argv


def run_one_course(
    cfg: dict,
    creds: dict,
    log: Callable[[str], None],
    keepalive_stop: Any,
    *,
    course: str,
    multi: bool = False,
    run_loop_main: Callable[[list[str]], int] | None = None,
    lease: str = "",
    progress: dict | None = None,
) -> int:
    """跑**一门课**的整段：取包 → 引导代码 → `remote.run_loop` → 打交付物；返回 rc。

    `multi=True`（同一会话里还有别的课）时工作目录再套一层课程名——见 `course_work_dir`。
    `run_loop_main` 是测试用的注入点（生产走 `remote.run_loop.main`）。
    `lease` = 本课的租约 token（`_run_batch` 领到后传进来）：取包要带它（P1-2 的 live hold 门），
    空值只在「没领租约/老 hub」时出现。
    `progress` 非空时，本段结束会把打点层给出的**租约结局**写进去（`{"outcome": …}`，
    ★M3：`revoked` ⇒ 本会话别再领这一课）；不传就是纯观测腿。
    """
    work = _load_deliverable().course_work_dir(cfg, course, multi=multi)
    work.mkdir(parents=True, exist_ok=True)
    log(f"工作目录: {work}")
    # 本课自己的 cfg：`course` 恒是**单个字符串**（下游放包路径/交付物名/`--hub-course`
    # 都按它拼；传原样的列表会拼出 "['a', 'b']" 这种目录名）。
    ccfg = {**cfg, "course": course}

    # ── 本机优先：产物目录三件齐全 ⇒ 不让包改写这一段的计划/清单（plan §3 判定表）──
    # 为什么决策住在这里（而不是 `run_loop`/`bundle`）：notebook 每次会话都从 GitHub raw
    # 刷新本文件，而 `remote.run_loop`/`remote.bundle` 来自**代码快照**（= 本机优先要保护的
    # 那份，可能很旧）——把新参数传给旧快照只会 argparse 崩或静默退化（评审 F1）。
    dest = work / "run"
    local = local_artifacts(dest)
    explicit_zip = str(ccfg.get("task_zip") or "").strip()
    force_pack = bool(ccfg.get("force_pack", False)) or bool(explicit_zip)
    if (dest / STATE_NAME).is_file() and local is None:
        # 半截产物目录：让包进来会**重置**本机 state（`ArtifactStore.start` 在 run_id/plan_sha
        # 不符时重开一段）⇒ 本机 `it-NNN/` 被重跑覆盖，比丢进度严重。这里响亮拒，不猜。
        raise SystemExit(
            f"[offline] 本机产物目录不完整（{dest}）：有 {STATE_NAME} 但缺 "
            f"{PLAN_NAME}/{MANIFEST_NAME}。让任务包补齐会重置本机进度并**重跑**已跑过的轮次。\n"
            "  ① 想接着本机进度跑：把缺的件找回来（上一轮的产物/备份）；\n"
            f"  ② 想用包从头跑：清空 {dest}（或换 CFG.work_dir）后重跑本 cell。"
        )
    local_first = local is not None and not force_pack
    if local is not None and force_pack:
        log(
            f"CFG.{'task_zip' if explicit_zip else 'force_pack'} 显式指定 ⇒ 用包覆盖本机计划/清单"
            f"（本机 it{local['last_it']}，计划 it{local['start_it']}→it{local['end_it']}）"
        )

    if local_first and local is not None:  # `local_first` 蕴含它非空；写出来给类型收窄
        log(
            f"本机已有产物 it{local['last_it']}（计划 it{local['start_it']} → it{local['end_it']}，"
            f"run={local['run_id'] or '-'}）⇒ 本机优先：不从包里导入 plan/manifest；"
            "包只当代码/TS 的备源"
        )
        pack = obtain_pack(
            ccfg, creds, log, work, stop=keepalive_stop, optional=True, lease=lease
        )
        if pack is None:
            log("本机优先：这次没取到任务包 —— 代码/TS 从产物目录取，回传 best-effort")
    else:
        if local is None:
            log(f"本机没有可续跑的产物（{dest}）——按老规矩取包起跑")
        pack = obtain_pack(ccfg, creds, log, work, stop=keepalive_stop, lease=lease)

    idx: dict = {}
    if pack is not None:
        idx = read_pack_index(pack)
        log(
            f"任务包: {idx.get('run_id')} it{idx.get('it')} → it{idx.get('end_it')}"
            f"（commit {_sha12(idx.get('commit'))}，{idx.get('created_at')}）"
        )
        in_name = course_from_pack_name(pack)
        if in_name and in_name != course:
            raise SystemExit(
                f"[offline] 文件名里的课程（{in_name}）与 CFG.course（{course}）不一致 —— "
                "跑错课的包会把权重接在别的课程账本上。确认是它就把 CFG.course 改对，"
                "否则去控制台导正确那门课的包"
            )
        if local is not None:
            log(describe_local_vs_pack(local, idx))
    if local_first and local is not None and local["last_it"] >= local["end_it"]:
        log(
            f"本机段落已完成（it{local['last_it']} ≥ end_it{local['end_it']}）——"
            "run_loop 会立刻收尾（不再跑轮次）；要用新段请清空产物目录、等新包，或置 "
            "CFG.force_pack=true"
        )

    token = str(creds.get("HUB_TOKEN") or "")
    hub = ""
    # hub 探活**只做一次**：回传、续跑锚点、代码兜底三条腿共用同一个连通性结论（它们都是
    # 「hub 此刻在不在」的问题，探两次只会让日志里出现两个可能不一致的结论）。
    for cand in hub_candidates(ccfg, creds):
        if probe_hub(cand, token, log):
            hub = cand
            break

    ensure_code(pack, log, dest=dest, hub=hub, token=token)
    ensure_ts_tree(pack, log, dest=dest)
    from remote.notebook_runtime import resolve_device  # code.zip 已在 sys.path 上

    if run_loop_main is None:
        from remote.run_loop import main as _run_loop_main

        run_loop_main = _run_loop_main
    if not str(ccfg.get("device") or "").strip() or str(ccfg["device"]).strip() == "auto":
        # `auto` 不能原样传下去（下游不认这个名字，2026-09-15 事故）——先解析成 cuda/tpu/cpu。
        ccfg = {
            **ccfg,
            "device": resolve_device(
                {
                    "device": ccfg.get("device", "auto"),
                    "use_multi_gpu": bool(ccfg.get("use_multi_gpu", True)),
                },
                log,
            ),
        }

    resume_dir: Path | None = None
    if hub:
        # 续跑锚点：hub 手里可能有更新的（自回传 / 人工导入的）完整轮次。先取它，
        # 再交给 run_loop——于是「重领任务」= 从最新进度接着跑，而不是从头重跑。
        resume_dir = fetch_resume(hub, token, course, work / "resume", log)
    # ★ `tok_file` 必须先置空：`live_backfeed=False`（或 hub 不可达）时下面不会赋值，
    #   而它又被传进 `build_run_argv`——原来那条路会 `NameError`（纯离线盘一直没跑到）。
    tok_file = ""
    lease_file = ""
    if bool(ccfg.get("live_backfeed", True)):
        if hub:
            tok_file = write_token_file(work, token)
            lease_file = write_lease_file(work, lease)
            log(f"实时回传开启 → {hub}（每轮 best-effort 推产物）")
        else:
            log("实时回传开着，但此刻够不着 hub —— 改为纯离线（产物照样逐轮落盘）")
    else:
        log("实时回传关闭（CFG.live_backfeed=False）—— 跑完统一打包，手动下载导入")

    argv = build_run_argv(
        ccfg, pack, dest, hub, tok_file, resume_dir, local_first=local_first, lease_file=lease_file
    )
    log("开始训练：python -m remote.run_loop " + " ".join(_redact(argv)))
    # ★M3 / Q2：把轮内打点层挂上（只在这时候才可能：hub 可达 ∧ 领到租约）——快照侧的完成
    # 事件（`plan_run` 的轮边界 / `iter_rollout` 的每 N 局完）会经
    # `common.progress_hook` 找到它。`finally` 里**必须撤销注册**：同一会话后面还要跑别的课，
    # 留着一个指向上一课的 URL 的打点层就会把下一段的进度报到别人账上。
    detach_progress: Callable[[], str] | None = None
    if hub and lease:
        detach_progress = install_progress_pinger(hub, token, course, lease, log)
    try:
        rc = int(run_loop_main(argv) or 0)
        log(f"run_loop 退出 rc={rc}；产物目录 {dest}")
    finally:
        if detach_progress is not None:
            outcome = detach_progress()
            if progress is not None:
                progress["outcome"] = outcome

    deliverable = _load_deliverable()
    got = deliverable.package_deliverable(dest, course, deliverable.download_dir(ccfg), log)
    if hub:
        log(
            "轮次已尽力回传给 hub（控制台按课程账户看进度）；"
            + ("上面的 zip 是**兜底**：hub 掉线时用它导入" if got else f"备份包没打出来，产物目录还在：{dest}")
        )
    elif got:
        log(
            f"纯离线完成。下一步：把上面的 `{got.name}` 下载到本机 → 控制台「导入产物」上传 "
            "→ 自动起 A 层评估"
        )
    else:
        log(f"纯离线完成，但没打出交付物 zip（见上面的报错）—— 产物目录还在：{dest}")
    return rc


def _json_dict(raw: bytes) -> dict:
    """小 JSON 应答 → dict（解不开 → `{}`：一个坏应答不该把取包/排队流程炸掉）。"""
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def _post_json(url: str, token: str, log: Callable[[str], None], *, timeout: float = PING_TIMEOUT) -> tuple[int, dict]:
    """小 JSON POST（带 Bearer）→ `(状态码, dict)`；`HTTPError` → `(码, 正文 dict)`；连不上 → `(0, {})`。

    为什么不用 `_fetch_guarded`：那是给**大 body**（几 MB 任务包）准备的停滞/低速护栏；
    这里是几百字节的控制消息，且要的是「失败也要拿到状态码与正文」（护栏会抛）。
    """
    req = urllib.request.Request(
        url, data=b"", method="POST", headers=_headers(token)
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), _json_dict(resp.read())
    except urllib.error.HTTPError as e:
        body, doc = _http_error_parts(e)
        if body and body not in ("（空正文）", "（无正文）"):
            log(f"  hub 说：{body}")
        return int(e.code), doc
    except Exception as e:  # 连不上/超时：best-effort（训练永不因网络停摆）
        log(f"  端点连不上（{type(e).__name__}: {e}）")
        return 0, {}


def _queue_work_dir(cfg: dict) -> Path:
    """worker 身份的落点：显式 `work_dir`，否则 `<download_dir>/battle-offline`（与 `course_work_dir` 同源）。"""
    explicit = str(cfg.get("work_dir") or "").strip()
    return Path(explicit).expanduser() if explicit else _load_deliverable().download_dir(cfg) / "battle-offline"


def worker_id_of(work: Path, log: Callable[[str], None]) -> str:
    """本机 worker 身份：**持久化**在 `<work>/.worker-id`（评审 G1）。

    为什么必须是文件、不是每次现生成：cell 中断/重跑时同一台机器会看到**自己**留下的租约；
    只按「陌生人持有」拒绝就等于被自己挡在门外，白等到 900s 过期（Kaggle 上十几分钟的会话
    预算，这一等就是整个会话）。同一个 `work_dir` 复用同一个 id ⇒ hub 判 `mine` 直接续上。
    """
    p = work / WORKER_ID_NAME
    try:
        old = p.read_text(encoding="utf-8").strip()
    except OSError:
        old = ""
    if old:
        return old
    wid = f"{os.environ.get('HOSTNAME') or 'node'}-{uuid.uuid4().hex[:8]}"
    try:
        work.mkdir(parents=True, exist_ok=True)
        p.write_text(wid + "\n", encoding="utf-8")
        log(f"本机 worker id: {wid}（写在 {p}；重跑本 cell 会复用它）")
    except OSError as e:
        # 写不进去不是致命（下次重生成一个新 id，只是会被自己的旧租约挡一次）。
        log(f"⚠ 写 {p} 失败（{e}）——本次会话的 worker id 不持久（重跑可能被自己的旧租约挡）")
    return wid


def fetch_task_list(
    hub: str,
    token: str,
    log: Callable[[str], None],
    *,
    worker: str = "",
    timeout: float = PING_TIMEOUT,
) -> list[dict] | None:
    """`GET /offline/tasks` → 任务清单；`None` = **hub 不支持 / 不可达**（调用方据此降级）。

    ★M1b / Q3：带 `?worker=` 时清单会把「一拖一」算进去（`busy` / `claimable` 按**这台盘**
    判）——这才是云机该看的那份（不带 = 上界语义，可能把「你这台盘正在跑别的课」漏掉，
    于是选了一门 claim 必吃 409 的课）。
    """
    url = f"{hub.rstrip('/')}{OFFLINE_TASKS_PATH}"
    if worker:
        url += f"?worker={urllib.parse.quote(worker)}"
    req = urllib.request.Request(url, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            doc = _json_dict(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            log(f"清单端点被拒 HTTP {e.code} —— HUB_TOKEN 不一致（本次降级到 CFG.course）")
        elif e.code in (404, 405):
            log("hub 没有 /offline/tasks（老 hub）⇒ 降级到 CFG.course（升级 hub 后即可不填 course）")
        else:
            log(f"清单端点取失败 HTTP {e.code}：{_http_error_body(e)}")
        return None
    except Exception as e:
        log(f"清单端点连不上（{type(e).__name__}: {e}）⇒ 降级到 CFG.course")
        return None
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        log("清单端点回了意外形状（没有 tasks 列表）⇒ 降级到 CFG.course")
        return None
    log(f"hub 清单：{len(tasks)} 条（hub_version={doc.get('hub_version')}）")
    return [t for t in tasks if isinstance(t, dict)]


def claim_course(
    hub: str,
    token: str,
    course: str,
    worker: str,
    log: Callable[[str], None],
    *,
    takeover: bool = False,
    timeout: float = PING_TIMEOUT,
) -> tuple[str, str]:
    """领一门课的离线租约 → `(lease_token, "")`；没领到 → `("", reason)`。

    ★ 2026-10-03（plan/auto-offline-handoff §3.1a / 二轮评审 P0）：返回值从裸 token 升级为
    `(token, reason)` —— 自动交接下这几类拒绝**必须区别对待**（旧口径「一律照旧跑」会把
    无包 claim 的 409 当成「被别人持有」，于是没有租约就进入 run、干等 30 分钟再整会话
    `SystemExit`，busy 闸/stalled 告警全被绕开）：

      · `pending_export`：hub 已记下导包意向（`pending_export`）、正在等控制台导包（分钟级）⇒ 本拍**不跑**，
        过一会儿再问；
      · `busy`：另一门课正在跑（U2 一拖一，闸在 hub 侧）⇒ 本拍**不跑**，等它 release；
      · `completed`：当前包已跑满（U6）⇒ 本拍**不跑**（重导包后 sha 变会自动解封）；
      · `not_offline`：这门课不在训练中（停课）⇒ 本拍**不跑**；
      · `give_up`：hub 触发导包已到上界 ⇒ **本会话放弃这门课**（三条出路留给人：TPU 重连 /
        手工导入结果包 / 到控制台重导任务包）；
      · `held`（★M1b / P0-4 后半，行为变更）：被别人 **live hold** 占据 ⇒ **本拍不跑，换下一门**
        （旧口径「照旧跑」= 同一份活两处跑；重复的那份由回传首写幂等丢弃）；
      · `proto`：对面 hub 嫌本端旧（Q4）⇒ 本拍不跑，刷新 notebook 后重开会话；
      · `no_pack`（404，人管课缺包）/ `net`（连不上）：照旧跑（训练永不因网络停摆；
        缺包由 `PackUnavailableError` 响亮收场）。
    """
    url = (
        f"{hub.rstrip('/')}{OFFLINE_CLAIM_PATH}?proto={OFFLINE_CLAIM_PROTO}"
        f"&course={urllib.parse.quote(course)}"
        f"&worker={urllib.parse.quote(worker)}" + ("&takeover=1" if takeover else "")
    )
    code, doc = _post_json(url, token, log, timeout=timeout)
    lease = doc.get("lease")
    if code == 200 and isinstance(lease, dict):
        log(f"领到租约（{lease.get('ttl_sec')}s，worker={worker}）")
        return str(lease.get("token") or ""), ""
    if code == 409:
        if doc.get("proto_required"):
            # ★M1b：hub 显式拒旧端（本文件没带 `?proto=`）。正常不会发生（上面已带上），
            # 真的发生了就是「克隆的 notebook 太旧 / 这份文件被改过」⇒ 响亮说清怎么修。
            log(
                f"hub 要求派发协议 v{doc.get('proto_required')}（本会话的客户端是 v"
                f"{OFFLINE_CLAIM_PROTO}）：{doc.get('error') or ''}"
            )
            return "", "proto"
        if doc.get("pending_export"):
            if doc.get("give_up"):
                log(
                    "hub 触发导包已到上界 —— 本会话放弃这门课（三条出路：TPU 重连 / "
                    "手工导入结果包 / 到控制台重导任务包）"
                )
                return "", "give_up"
            log("这门课正在导包（hub 已记下 pending_export，导包约需数分钟）——本拍不跑，稍后再问")
            return "", "pending_export"
        if doc.get("busy"):
            log(f"别的课正在跑（一拖一）：{doc.get('error') or 'busy'}——本拍不跑，稍后再问")
            return "", "busy"
        if doc.get("completed"):
            log("这门课的当前任务包已跑满（等人停课 / 重导包）——本拍不跑")
            return "", "completed"
        if doc.get("not_offline"):
            log("这门课不在训练中（停课 ⇒ 不是自动候选）——本拍不跑")
            return "", "not_offline"
        # ★M4b：旧的 `pinned_online`（人固定在在线）随模式退役 —— hub 不会再发这一档；
        # 万一对面是旧 hub，它会落到下面这条「有主」腿（保守不跑，不双跑）。
        holder = doc.get("holder") or {}
        left = float(holder.get("expires_in") or 0.0)
        # ★M1b / P0-4 后半（行为变更）：`held` 从「照旧跑」改成**本拍不跑，换下一门**。
        # 为什么：hold v2 把「谁在跑」变成**互斥**（不是观测）——别人持着 live hold 时本机
        # 再跑一遍 = 同一份活两处跑（数据损坏级）；hub 侧的 live hold 也不可被顶（不变量 3）。
        # 旧口径「产物照落、回传被判 duplicate 丢弃」只在**没有互斥**的时代成立。
        log(
            f"这门课正被 {holder.get('worker_id') or '?'} 接管（{left:.0f}s 内无进度即自动可接管）"
            "——本拍不跑，换下一门（或等它静默后被本机自动接管）"
        )
        return "", "held"
    if code == 404:
        log("hub 说这门课还没有任务包（人管课：先到控制台「导出任务包」）")
        return "", "no_pack"
    return "", "net"


def heartbeat_loop(
    hub: str,
    token: str,
    course: str,
    lease: str,
    log: Callable[[str], None],
    *,
    interval: float = HEARTBEAT_SEC,
) -> threading.Event:
    """起一个**守护线程**按 `interval` 续租；返回它的 stop event（调用方 `finally` 里 set）。

    为什么必须另起线程：`run_loop_main(argv)` 同步阻塞到整段跑完（本文件里那行调用），
    主线程发不了心跳。心跳失败**只记日志**：租约过期/被接管绝不能让已经在跑的训练停下来
    （plan §1.4-4）。
    """
    done = threading.Event()
    url = (
        f"{hub.rstrip('/')}{OFFLINE_HEARTBEAT_PATH}?course={urllib.parse.quote(course)}"
        f"&lease={urllib.parse.quote(lease)}"
    )

    def _beat() -> None:
        while not done.wait(interval):
            code, doc = _post_json(url, token, log)
            if code == 200:
                continue
            if code == 409:
                if doc.get("revoked"):
                    # ★M1b / M4b：人强制解除了接管（`/admin/courses?release_hold=1`）——**停止再领**，
                    # 当前段在下一个轮边界收尾并打包（旧 worker 照旧跑完，兼容降级）。
                    # 为什么在此处置事件：心跳线程是唯一知道「接管被强制解除」的地方，而
                    # `run_loop` 的轮边界读的就是这个停止信号（不能杀正在算的那一轮）。
                    log(
                        "⚠ 租约已被撤销（人强制解除了接管）——停止再领本课；"
                        "当前段在下一个轮边界收尾并打包（回传可能被判 duplicate 丢弃）"
                    )
                    done.set()
                    return
                log(
                    f"⚠ 租约失效（{'已过期' if doc.get('expired') else '已被接管'}）"
                    "——继续跑完并打包（回传可能被判 duplicate 丢弃）"
                )
                return
            log(f"心跳失败 HTTP {code}（不影响训练，下一跳再试）")

    threading.Thread(target=_beat, name=f"offline-heartbeat-{course}", daemon=True).start()
    return done


def progress_url(hub: str, course: str, lease: str) -> str:
    """`POST /offline/progress?course=<课>&lease=<token>`（纯函数：形状就是契约）。"""
    return (
        f"{hub.rstrip('/')}{OFFLINE_PROGRESS_PATH}?course={urllib.parse.quote(course)}"
        f"&lease={urllib.parse.quote(lease)}"
    )


def _progress_label(kind: str, *, it: int, done: int, total: int) -> str:
    """打点日志的标签（`kind` + 有就带上的轮号/局计数）——**只进日志**，不上线。"""
    bits = [str(kind or "ping")]
    if it:
        bits.append(f"it{int(it)}")
    if total:
        bits.append(f"局 {int(done)}/{int(total)}")
    elif done:
        bits.append(f"局 {int(done)}")
    return " ".join(bits)


def install_progress_pinger(
    hub: str,
    token: str,
    course: str,
    lease: str,
    log: Callable[[str], None],
    *,
    interval: float = PROGRESS_MIN_INTERVAL_SEC,
    timeout: float = PROGRESS_TIMEOUT_SEC,
    post: Callable[..., tuple[int, dict]] | None = None,
) -> Callable[[], str]:
    """把**打点层**挂进 `sys.modules[PROGRESS_HOOK_NAME]` → 返回 `detach()`（撤销注册）。

    ★M3 / Q2 / F3：打点是「**完成事件**驱动的观测」，不是定时任务。快照侧
    （`remote/plan_run.py` 的轮边界、`worker/iter_rollout.py` 的每 N 局完）通过
    `common/progress_hook.report(...)` 上报事实；这里做三件事——**时间节流**（每
    `interval` 秒最多一句）、POST、409 分流。

    **绝不另起线程**：定时线程正是「心跳活、进度死」的成因（§68）——`heartbeat_loop` 那条
    守护线程只续 TTL（那是租约的账），而 hold 的活性要的是「活干到哪了」，只能挂在真的干完
    一点活的那一刻上。

    返回的 `detach()` 撤销注册，并给出**本段的租约结局**（空串 = 一切正常）：

      · `"revoked"`（409）：租约被撤销（人强制解除了接管）⇒ 调用方**别再领这一课**
        （当前段照常跑完并打包）；
      · `"expired"` / `"taken"`（409）：租约过期 / 已被别人接管 ⇒ 跑完当前段并打包，停打点
        （继续 ping 只会每 `interval` 秒刷一行 409）；
      · `"unreachable"`：连续 `PROGRESS_FAIL_LIMIT` 拍发不出去 ⇒ 停打点（hub 此刻显然也
        收不到；租约到期由心跳/重建会话处理）。

    打点的任何失败都**不影响训练**（观测腿，与心跳同一条纪律）；`post` 是测试注入点。
    """
    post_fn = post or _post_json
    url = progress_url(hub, course, lease)
    state: dict[str, Any] = {"at": 0.0, "fails": 0, "dead": "", "outcome": ""}

    def _ping(
        kind: str = "", *, it: int = 0, done: int = 0, total: int = 0, force: bool = False
    ) -> bool:
        """一个完成事件 → 打点（时间节流；True = 这一拍真发出去了）。**永不抛**。"""
        if state["dead"]:
            return False
        now = time.monotonic()
        # `at` 从 0 起 ⇒ **首拍恒发**（段开工那一句不会被时间窗吃掉）。
        if not force and (now - float(state["at"])) < float(interval):
            return False
        state["at"] = now
        label = _progress_label(kind, it=int(it), done=int(done), total=int(total))
        code, doc = post_fn(url, token, log, timeout=timeout)
        if code == 200:
            state["fails"] = 0
            log(f"[打点] {label} → hub（hold 活性 = 进度；心跳只续 TTL）")
            return True
        if code == 409:
            why = "revoked" if doc.get("revoked") else ("expired" if doc.get("expired") else "taken")
            holder = doc.get("holder") if isinstance(doc.get("holder"), dict) else {}
            state["dead"] = why
            state["outcome"] = why
            log(
                {
                    "revoked": "⚠ 打点被拒：租约已被撤销（人强制解除了接管）——"
                    "本段跑完即打包，本会话不再领这一课",
                    "expired": "⚠ 打点被拒：租约已过期——本段继续跑完并打包"
                    "（回传可能被判 duplicate 丢弃）",
                }.get(
                    why,
                    f"⚠ 打点被拒：租约已被 {(holder or {}).get('worker_id') or '别人'} 接管——"
                    "本段继续跑完并打包（回传可能被判 duplicate 丢弃）",
                )
            )
            return False
        # 连不上（0）/ 5xx / 404：下一拍再试；连着 PROGRESS_FAIL_LIMIT 拍就停打点。
        state["fails"] = int(state["fails"]) + 1
        if int(state["fails"]) >= PROGRESS_FAIL_LIMIT:
            state["dead"] = "unreachable"
            state["outcome"] = "unreachable"
            log(
                f"⚠ 打点连续 {state['fails']} 拍没成功（HTTP {code or '连不上'}）——停打点；训练继续"
                "（hub 此刻也收不到进度，租约由心跳/重建会话兜）"
            )
        elif force:
            log(f"[打点] {label} 没成功（HTTP {code or '连不上'}）——下一拍再试")
        return False

    # 写模块命名空间而不是 `mod.note_progress = …`：后者在 mypy 眼里是「Module 没有这个
    # 属性」（契约是**运行时**的，见 `common/progress_hook.py`），而 ruff 不许为它加 setattr。
    mod = types.ModuleType(PROGRESS_HOOK_NAME)
    mod.__dict__["note_progress"] = _ping
    prev = sys.modules.get(PROGRESS_HOOK_NAME)
    sys.modules[PROGRESS_HOOK_NAME] = mod
    log(
        f"轮内打点就绪（{url.split('?')[0]}；每 {interval:.0f}s 最多一句，"
        "完成事件驱动 —— 不另起线程）"
    )

    def detach() -> str:
        """撤销注册（还原上一个注册者，若有）→ 本段的租约结局（空串 = 正常）。"""
        if sys.modules.get(PROGRESS_HOOK_NAME) is mod:
            if prev is not None:
                sys.modules[PROGRESS_HOOK_NAME] = prev
            else:
                sys.modules.pop(PROGRESS_HOOK_NAME, None)
        return str(state["outcome"])

    return detach


def release_course(
    hub: str, token: str, course: str, lease: str, log: Callable[[str], None], *, timeout: float = PING_TIMEOUT
) -> None:
    """交还租约（best-effort；失败只记一行——这一课已经跑完了）。"""
    if not lease:
        return
    url = (
        f"{hub.rstrip('/')}{OFFLINE_RELEASE_PATH}?course={urllib.parse.quote(course)}"
        f"&lease={urllib.parse.quote(lease)}"
    )
    code, doc = _post_json(url, token, log, timeout=timeout)
    if code == 200:
        log("租约已交还")
    elif code == 409:
        log(f"租约没交还成功（{doc.get('error') or '不是当前持有人'}）——不影响本课成果")


def resolve_courses(
    cfg: dict,
    creds: dict,
    log: Callable[[str], None],
    *,
    served: dict[str, str] | None = None,
    probe: dict | None = None,
    skip: set[str] | None = None,
    worker: str = "",
) -> tuple[list[dict], list[dict], list[dict]]:
    """本次要跑的课 → `([{"course", "pack_sha256"}], blocked, manifest)`。

    `picks` = 要跑的课（同旧形状）；**空 + `blocked` 也空 = 队列真的空**（正常的没事干）。
    `manifest` = hub 的**原始清单行**（每个 dict 带 `state`/`claimable`/`pack`/`holder`…）——
    `picks`/`blocked` 都丢掉了 `state`，而 `_run_auto` 的终态收工判据要它（`all_terminal`）。
    非 hub 路（`CFG.course` 点名）没有清单 ⇒ 给空表（`all_terminal([]) is False`，不误触发）。
    ★P1-9（R3-g，五轮 P1-E）：`blocked` = 「**有活、但被非自己的有效租约持有**」的行
    （`{course, holder, expires_in}`）——「空队列」与「还得等一会儿」从此在**预算**上分开：
    调用方对 `blocked` 退避再问且**不占 `idle_wait_sec`**（否则排 TTL 的十几分钟会被当成空转、
    会话提前收工）。与 `_run_batch` 的 `leases["blockers"]`（`tasks` 非空时的交接中间态）
    **同名不同物**，别接错线。`picks` 非空时它恒为空表。

    `CFG.course` 非空 ⇒ 老行为（顺序/校验一字不改），`pack_sha256` 空。
    空 ⇒ 向 hub 问清单，**一层选择**（★M4b：课程无模式 ⇒「离线课」与「在线课」不再可区分，
    旧口径的「离线优先、没有就抢第一个在训在线课」两条腿塌成一条：no-hold 的课一律可领）：

      ① `claimable` 的行整批取（含无包课：它的 claim 会触发导包、回来 409 `pending_export`）；
      ② `pending_export` 的排最后（软态不占闸，但 claim 必然 409 ⇒ 别让注定失败的 claim 排队首）；
      ③ 都没有、而某几行是**自己的**租约 ⇒ 照领（claim 续上，不必等 900s 过期）。

    共用同一套过滤：本会话已跑过的包（防自激：同一份包跑两次 = `run_id` 相同 ⇒ 回传全判
    duplicate ⇒ 看起来在跑、实际零产出）与 `skip`（本会话已放弃的课）。
    `probe` 是调用方持有的小字典（`{"unsupported": True}`）：老 hub 只探测**一次**，
    之后不再每轮刷一个必然失败的端点。
    """
    explicit = _load_deliverable().requested_courses(cfg)
    if explicit:
        return [{"course": c, "pack_sha256": ""} for c in explicit], [], []
    if probe is not None and probe.get("unsupported"):
        raise SystemExit(_no_courses_msg("hub 没有 /offline/tasks（本会话已探过）"))
    hubs = hub_candidates(cfg, creds)
    if not hubs:
        raise SystemExit(_no_courses_msg("没有可用的 hub 地址（CFG.hub_url / HUB_IP 都没配）"))
    tasks = fetch_task_list(
        hubs[0], str(creds.get("HUB_TOKEN") or ""), log, worker=worker
    )
    if tasks is None:
        if probe is not None:
            probe["unsupported"] = True
        raise SystemExit(_no_courses_msg("hub 不支持任务清单（或本机连不上它）"))

    def _eligible(t: dict) -> bool:
        """两路共用的过滤：`skip` / 本会话已跑过的包（同 sha）。"""
        course = str(t.get("course") or "")
        if not course:
            return False
        if skip and course in skip:
            # 两种原因共用一个名单（★M3 起）：hub 触发导包到上界【本会话放弃】与
            # 租约被撤销（打点 409）【本会话不再领】——两种都要人处理后才再领。
            log(f"跳过 {course}：本会话不再领它（导包到上界 / 租约被撤销；人处理后才再领）")
            return False
        pack_doc = t.get("pack")
        sha = str(pack_doc.get("sha256") or "") if isinstance(pack_doc, dict) else ""
        if sha and served is not None and served.get(course) == sha:
            log(f"跳过 {course}：本会话已跑过这份包（sha12={sha[:12]}）——包换了新段才会再领")
            return False
        return True

    def _as_pick(t: dict) -> dict:
        pack_doc = t.get("pack")
        sha = str(pack_doc.get("sha256") or "") if isinstance(pack_doc, dict) else ""
        return {"course": str(t.get("course") or ""), "pack_sha256": sha}

    claimable_rows = [t for t in tasks if t.get("claimable") and _eligible(t)]
    # ★M3：**导包软态（`pending_export`）排到最后**——`pending_export` 不占闸（Q1），所以行
    # 照样 `claimable`，但它的 claim 必然 409（包还没造出来）：排后面 = 先把能跑的挑完，
    # `_run_batch` 走到它时记一笔 blocker 就换下一门（不让一次注定失败的 claim 排在队首）。
    ready = [_as_pick(t) for t in claimable_rows if not t.get("pending_export")]
    exporting = [_as_pick(t) for t in claimable_rows if t.get("pending_export")]
    picked = ready + exporting
    if picked:
        log("清单里可领：" + "、".join(f"{p['course']}" for p in picked))
        if exporting:
            log(
                "其中 "
                + "、".join(f"{p['course']}" for p in exporting)
                + " 正在导包（pending_export，软态不占闸）——排在最后：本拍多半领不到，"
                "领到就触发交接"
            )
        return picked, [], tasks
    # ★ 2026-10-04 现场（Kaggle「清单 3 条 ⇒ 队列为空」，用户：「colab 已停机、也切过模式，
    #   为什么还持有租约？」）：租约只有在**显式 release / 900s TTL 到期 / hub 重启**时才消失，
    #   而**清单行的 `claimable` 把任何持有者（包括自己）一律排除**，这一档到不了现场 ⇒
    #   自己把自己锁到 TTL 过期（Kaggle 十几分钟的会话就废在这一等上）。
    #   这里补上：没有可领的课、而某几行是**我自己**的租约 ⇒ 照领（claim 会续上）。
    mine = [t for t in tasks if worker and _holder_id(t) == worker and _eligible(t)]
    if mine:
        log(
            "续领自己未交还的租约："
            + "、".join(str(t.get("course") or "") for t in mine)
            + "（hub 判 mine ⇒ claim 直接续上，不必等 900s 过期）"
        )
        return [_as_pick(t) for t in mine], [], tasks
    # busy 的行**照收**：claim 会回 409 `busy`，调用方按「中间态不占 idle 预算」等到
    # 别的课跑完（比「空队列」更准确）。★P1-9（R3-g）：被**别人**的有效租约持有的行——带回
    # 给调用方（退避再问、不占 idle 预算）。
    blocked = [
        {
            "course": str(t.get("course") or ""),
            "holder": _holder_id(t),
            "expires_in": _holder_left(t),
        }
        for t in tasks
        if _holder_id(t) and _holder_id(t) != worker and _eligible(t)
    ]
    # ★ 2026-10-04 现场（Kaggle：「hub 清单：3 条」接着「队列为空」，看不出为什么）：
    #   不可领的原因**就在 hub 行的 `state`/`reason`/`holder` 里**（not_offline=停课 /
    #   held=有主 / completed=本段跑满 / busy / 无任务包 / 包过期），不打印就等于把排查
    #   推给人工去 curl `/offline/tasks`。这里逐行报出——空队列从此自解释。
    wait_rows = [t for t in tasks if not t.get("claimable")]
    if wait_rows:
        log("不可领：" + "、".join(f"{_blocked_note(t)}" for t in wait_rows))
    return [], blocked, tasks


def _holder_id(t: dict) -> str:
    """这行被**谁**持有（`holder` 形状不对/无主 ⇒ 空串）：判「自己的租约」与打日志用。"""
    holder = t.get("holder")
    return str(holder.get("worker_id") or "") if isinstance(holder, dict) else ""


def _holder_left(t: dict) -> float:
    """这行持有者的剩余租约秒数（无 holder / 形状不对 ⇒ 0.0）——P1-9 的 `blocked` 带它。"""
    holder = t.get("holder")
    left = holder.get("expires_in") if isinstance(holder, dict) else None
    return float(left) if isinstance(left, (int, float)) else 0.0


def _blocked_note(t: dict) -> str:
    """一行说清「这行课为什么不可领/不可抢」（纯函数；日志用）：`课[state；reason；…]`。

    为什么单独成函数：空队列的排查线索全在这里——`state`（hub 的读面判据）与 `reason`
    （not_offline / held:<worker> / completed / busy）是 hub 侧的直接事实，缺包/过期包是
    盘上事实；把这些丢掉，云机日志就只剩一句「队列为空」（2026-10-04 Kaggle 现场）。
    ★ 被持有的行还报**剩余 TTL**（`holder.expires_in`）：用户 2026-10-04 的追问正是
    「为什么还持有租约、还要等多久」——这个数字就是答案（切模式/停课都不会清租约）。
    """
    bits = [str(t.get("state") or "?")]
    reason = str(t.get("reason") or "")
    if reason:
        bits.append(reason)
    holder = t.get("holder")
    if isinstance(holder, dict):
        left = holder.get("expires_in")
        if isinstance(left, (int, float)):
            bits.append(f"持有 {_holder_id(t) or '?'}（{float(left):.0f}s 后过期）")
        if holder.get("stale") is True:
            # ★P1-4：死盘（静默超阈）可直接接管——这句必须在日志里，否则人以为要等 TTL。
            bits.append(f"持有者已静默 {float(holder.get('silent_sec') or 0.0):.0f}s（可直接接管）")
    if not t.get("pack"):
        bits.append("无任务包（等控制台导出）")
    stale = str(t.get("stale_reason") or "")
    if stale:
        bits.append(f"包过期:{stale}")
    return f"{t.get('course')}[{'；'.join(bits)}]"


#: 「只有人介入才能复活」的清单态（`_run_auto` 的终态窗口用它）：
#: `completed` = 本段跑满（等人重导包 / 停课；`hub/queue_offline.py` 的 `TASK_STATE_COMPLETED`）·
#: `not_offline` = 人把课固定在离线之外（`pinned_online`）。
#: ⚠ **停课（删开课标记）不在清单里**（hub 六轮 F3 之后：标记一删即不列，只有 `?include=all` 才附带），
#: 而云机的 `fetch_task_list` 不带 `include=all` ⇒ 这一档实际对应的是 `pinned_online` 行。
TERMINAL_STATES = frozenset({"completed", "not_offline"})


def all_terminal(rows: list[dict], served: dict[str, str] | None = None) -> bool:
    """清单非空 ∧ 每一行都「不会再自己变好」⇒ 等待没有意义（除非人去重导包 / 停课 / 开新课）。

    两档算「不会自己变好」（判据是**整张清单**，不是 `resolve_courses` 的 picks）：

      ① `state` ∈ `TERMINAL_STATES`；
      ② **本会话已跑过这份包**（`served[course] == 行上 pack.sha256`）——`resolve_courses` 的
         `_eligible` 会把它永远过滤掉（防自激：同 sha 再跑一遍 = 回传全判 duplicate），
         包不换新段就再也领不到，效果与终态等同。
         ★ 2026-10-07 现场：`x21-psh-b` 正是这一档（它没进「不可领/不可抢」日志 ⇒ 它是
         `claimable` 行，只被 served 挡下）⇒ **只判 ① 会漏掉现场**。

    `rows` 传**原始 hub 清单**（`resolve_courses` 的第三个返回值）：`picks`/`blocked` 都不带 `state`。
    """
    if not rows:
        return False
    for t in rows:
        if str(t.get("state") or "") in TERMINAL_STATES:
            continue
        pack = t.get("pack")
        sha = str(pack.get("sha256") or "") if isinstance(pack, dict) else ""
        course = str(t.get("course") or "")
        if sha and served and course and served.get(course) == sha:
            continue
        return False
    return True


def _no_courses_msg(why: str) -> str:
    """「不知道跑哪几门课」的唯一文案（CFG 没填 + 清单用不了 ⇒ 响亮失败，不猜）。"""
    return (
        f"[offline] CFG.course 没填，且拿不到任务清单：{why}。\n"
        "  ① 填 CFG.course（可以多门，列表按顺序串行）；\n"
        "  ② 或让 hub 侧可用 /offline/tasks（同一版本的 hub_server.py）。"
    )


def _explicit_leases(cfg: dict, creds: dict, log: Callable[[str], None]) -> dict:
    """点名腿（`CFG.course`）的租约上下文（★M3 / P1-2 配套）：**先 claim 再取包**。

    为什么点名腿也要 claim（旧行为是不 claim、直接取包）：

      · **取包门**（P1-2）现在看 hold：本机上一段留下的 live hold（cell 中断重跑）会让
        **自己**取不到包；claim 判 `mine` 直接续上，包照取；
      · hub 侧「谁在跑这门课」只剩 hold 这一个真源（plan §3-M1a）：不 claim 的话，云机在跑
        而控制台看到「没人接手」——停滞告警 / 自动交接判据全部落空。

    没有可用的 hub 地址 ⇒ 返回**空 ctx**（老行为：纯离线 + 手动包，一切照旧）；claim 的
    409 分流与自动腿**同一条**（`_run_batch` 的 blockers）——“这课能不能在离线盘跑”由 hub
    说了算（AGENTS §5：训练操作唯一入口 = 控制台）。
    """
    hubs = hub_candidates(cfg, creds)
    if not hubs:
        log("没有可用的 hub 地址 ⇒ 点名腿不领租约（纯离线：取包/回传照旧）")
        return {}
    return {
        "hub": hubs[0],
        "token": str(creds.get("HUB_TOKEN") or ""),
        # 本机 worker 身份（持久化在 `<work>/.worker-id`）：hub 判 `mine` 直接续上，
        # 不会把自己上一段留下的租约当成别人的。
        "worker": worker_id_of(_queue_work_dir(cfg), log),
        "served": {},
    }


def _run_batch(
    cfg: dict,
    creds: dict,
    log: Callable[[str], None],
    keepalive_stop: Any,
    courses: list[str],
    *,
    multi: bool,
    leases: dict | None = None,
    shas: dict[str, str] | None = None,
) -> int:
    """串行跑一批课；返回 rc。失败分两类（plan/offline-switch-auto-bundle §3.5）：

      · **取不到任务包**（`PackUnavailableError`）⇒ 记一行、**跳过继续下一门**；末尾汇总，
        `M>0 ⇒ 非零 rc`，`N==0`（全跳过）⇒ 响亮 `SystemExit`；
      · 其它 `SystemExit`（课程名不一致 / 产物目录不完整）与训练 `rc≠0` ⇒ **照旧立即停**。

    `leases` 非空时逐课领租约（best-effort）+ 心跳线程 + 跑完交还，并把跑成功的包 sha 记进
    `leases["served"]`（防自激）。租约的任何失败都**不影响**训练。
    """
    rc = 0
    skipped: list[str] = []
    # 自动交接的两个「不跑」名单（§3.1a/§3.3a）：中间态（等下一拍）与会话放弃。
    blockers: dict[str, str] = {}
    gave_up: list[str] = []
    # ★M3：打点层报回「租约被撤销」（人强制解除了接管）的课——本会话不再领它
    # （段已跑完并打包；hub 也已经不收它了）。
    revoked: list[str] = []
    ran = 0
    for i, course in enumerate(courses):
        rest = courses[i + 1 :]
        log(f"===== [{i + 1}/{len(courses)}] 课程 {course} =====")
        # 租约上下文取成局部 dict（不用 `leases[...]`）：`finally` 里也要用，而 `leases` 是
        # 可选的（None = 老行为、不领租约）。空 dict 即「这条腿不领」。
        ctx = leases if leases is not None else {}
        lease = ""
        beat: threading.Event | None = None
        if ctx:
            lease, why = claim_course(ctx["hub"], ctx["token"], course, ctx["worker"], log)
            if why in ("busy", "pending_export", "completed", "not_offline", "held", "proto"):
                # 自动交接的中间态：**本拍不跑**（不是「取不到包」的错误，也不进 skipped ——
                # 它会在 caller 的下一拍重问；旧口径「一律照旧跑」会在这里白跑一场）。
                blockers[course] = why
                log(
                    f"课程 {course} 本拍不跑（{why}）"
                    + (f"——继续看下一门：{rest[0]}" if rest else "——等下一拍")
                )
                continue
            if why == "give_up":
                gave_up.append(course)
                log(f"课程 {course} 本会话放弃（hub 触发导包到上界）——等下一门/等处理")
                continue
            if lease:
                beat = heartbeat_loop(ctx["hub"], ctx["token"], course, lease, log)
        progress: dict = {}
        try:
            rc = run_one_course(
                cfg,
                creds,
                log,
                keepalive_stop,
                course=course,
                multi=multi,
                lease=lease,
                progress=progress,
            )
        except PackUnavailableError as e:
            # ★ 只放宽「取不到包」这一类（顺序必须在 `except SystemExit` **之前**，
            #   否则会被父类吃掉）：记一行 + 继续下一门；汇总与非零退出在循环之后。
            skipped.append(course)
            log(f"课程 {course} 跳过（取不到任务包）：{e.code}")
            if rest:
                log(f"  —— 继续下一门：{rest[0]}（末尾会汇总；训练失败/配置错误仍然立即停）")
            continue
        except SystemExit as e:
            if rest:
                log(
                    f"课程 {course} 未跑完（{e.code}）——串行到此为止，剩余 {len(rest)} 门课未执行："
                    f"{', '.join(rest)}"
                )
            raise
        finally:
            if beat is not None:
                beat.set()
            if lease:
                release_course(ctx["hub"], ctx["token"], course, lease, log)
        outcome = str(progress.get("outcome") or "")
        if outcome:
            log(f"课程 {course} 的租约结局：{outcome}（段已跑完并打包）")
            if outcome == "revoked":
                revoked.append(course)
        if rc != 0:
            log(
                f"课程 {course} 退出 rc={rc} ——串行到此为止；"
                + (f"剩余 {len(rest)} 门课未执行：{', '.join(rest)}" if rest else "它已是最后一门课")
            )
            return rc
        ran += 1
        if ctx and shas:
            sha = str(shas.get(course) or "")
            if sha:
                ctx["served"][course] = sha
        if rest:
            log(f"课程 {course} 完成 —— 下一门：{rest[0]}")
    if skipped:
        done = len(courses) - len(skipped)
        log(
            f"本会话完成 {done} 门 / 跳过 {len(skipped)} 门（取不到任务包）"
            f"：{', '.join(skipped)}"
        )
        if done == 0:
            # 响亮失败：一门都没跑（**不谎报成功**）。注意这与「向 hub 问清单、队列为空」
            # 是两回事 —— 后者是正常的没事干（plan/offline-task-discovery §3.3）。
            raise SystemExit(
                f"[offline] 没有任何一门课拿到任务包（{len(skipped)} 门全被跳过）："
                f"{', '.join(skipped)}\n"
                "  ① hub 取包：控制台「导出任务包」→ 保持 hub 在线 → 重跑本 cell；\n"
                "  ② 手动送包：控制台下载 task-<课>.zip → 上传到本 notebook（或写进 "
                "CFG['task_zip']）→ 重跑本 cell。"
            )
        return rc if rc else 1
    if leases is not None:
        # 回填给 `_run_auto`：它据此决定「这拍到底跑了没有」与「下拍跳过谁」。
        leases["blockers"] = blockers
        leases["gave_up"] = gave_up
        leases["revoked"] = revoked
        leases["ran"] = ran
    if blockers or gave_up:
        log(
            f"本拍结束：跑了 {ran} 门；{len(blockers)} 门等下一拍"
            f"（{', '.join(f'{c}:{w}' for c, w in blockers.items()) or '无'}）；"
            f"{len(gave_up)} 门本会话放弃（{', '.join(gave_up) or '无'}）"
        )
    else:
        log(f"全部课程完成（{len(courses)} 门）：{', '.join(courses)}")
    return rc


def _run_auto(
    cfg: dict, creds: dict, log: Callable[[str], None], keepalive_stop: Any
) -> int:
    """`CFG.course` 留空时的队列循环（plan/offline-task-discovery §3.3）。

    一轮：问清单 → 领租约 → 取包 → 跑完 → 交还 → 记 `served[course]=包 sha`。
    队列空 ⇒ `queue_mode="once"` 直接收工；缺省 `"drain"` 驻守轮询，直到
    `idle_wait_sec` / `session_budget_sec` 用尽或收到停机信号（**空队列不是错误**）。
    ★ 2026-10-07：清单**全为终态**（或本会话已跑过它的包）⇒ 用**独立**的短窗口
    `idle_wait_terminal_sec` 收工——再等也不会自己变好，只有人介入才复活（`all_terminal`）。
    """
    served: dict[str, str] = {}
    probe: dict = {}
    #: 本会话已放弃的课（hub 触发导包到上界）：别再每轮领一次（`resolve_courses(skip=…)`）。
    gave_up: set[str] = set()
    rc = 0
    mode = str(cfg.get("queue_mode") or "drain").strip().lower()

    def _num(key: str, default: float) -> float:
        """数值旋钮（**`0` 是合法值**：`or 缺省` 那套写法会把「立刻收工」悄悄变成等半小时）。"""
        v = cfg.get(key)
        if v is None or v == "":
            return default
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    budget = _num("session_budget_sec", 0.0)  # 0 = 不限（与逐段的 budget_sec 不是一把旋钮）
    idle_wait = _num("idle_wait_sec", _num("wait_pack_sec", DEFAULT_WAIT_SEC))
    # 终态窗口：**独立**锚点与预算（不占 idle_wait_sec——「没活了」与「活还没到」是两件事）。
    idle_wait_terminal = _num("idle_wait_terminal_sec", IDLE_WAIT_TERMINAL_SEC)
    poll = _num("queue_poll_sec", DEFAULT_QUEUE_POLL_SEC)
    start = time.monotonic()
    idle_since = start
    #: 终态窗口的锚点（`None` = 本拍不是「全终态」）。在**非终态**的每一拍复位。
    terminal_since: float | None = None
    while True:
        # 本机 worker id 在**取清单之前**备好：`resolve_courses` 要拿它认「自己的租约」
        # （hub 判 `mine` 直接续上——否则 cell 中断重跑会白等 900s TTL，G1 的原始动机）。
        # 它持久化在 `<work>/.worker-id`，重复调用只读文件（不打日志）。
        worker = worker_id_of(_queue_work_dir(cfg), log)
        tasks, blocked_rows, manifest = resolve_courses(
            cfg, creds, log, served=served, probe=probe, skip=gave_up, worker=worker
        )
        if tasks:
            idle_since = time.monotonic()
            terminal_since = None  # 有活 ⇒ 终态窗口作废（下一拍若全终态，重新起算）
            hubs = hub_candidates(cfg, creds)
            leases = {
                "hub": hubs[0] if hubs else "",
                "token": str(creds.get("HUB_TOKEN") or ""),
                "worker": worker,
                "served": served,
            }
            batch_rc = _run_batch(
                cfg,
                creds,
                log,
                keepalive_stop,
                [str(t["course"]) for t in tasks],
                multi=len(tasks) > 1,
                leases=leases,
                shas={str(t["course"]): str(t.get("pack_sha256") or "") for t in tasks},
            )
            rc = batch_rc or rc
            gave_up.update(leases.get("gave_up") or [])
            # ★M3：租约在段内被撤销的课（人强制解除接管）——本会话不再领它（段已跑完
            # 并打包）。与 `gave_up`（hub 触发导包到上界）同一处理，只是原因不同。
            revoked_now = leases.get("revoked")
            if revoked_now:
                log(
                    "本会话不再领这些课（租约已被撤销）："
                    + "、".join(str(c) for c in revoked_now)
                )
                gave_up.update(str(c) for c in revoked_now)
            raw_blockers = leases.get("blockers")
            blockers: dict[str, str] = raw_blockers if isinstance(raw_blockers, dict) else {}
            if not leases.get("ran") and (blockers or leases.get("gave_up")):
                # 全是自动交接的中间态（导包是分钟级 / 别的课在跑）：**不消耗 idle_wait_sec
                # 预算**（否则排导包的这几分钟会被当成空转、会话提前收工），但要给自己一个
                # backoff，别 tight loop 打爆 hub。
                log(
                    f"本拍没有可跑的课（{', '.join(sorted(blockers)) or '交接中'}）"
                    f"—— {poll:.0f}s 后再问（不占 idle 预算）"
                )
                time.sleep(poll)
            continue
        # ★P1-9（R3-g）：`tasks` 空但 `blocked` 非空 = 「还得等一会儿」（别人的有效租约，
        # 通常等 release / TTL / 接管），与「空队列」是两件事：退避再问、**不消耗**
        # `idle_wait_sec`（重置 idle 锚点），只受会话预算与停机信号约束。
        # ⚠ 与上面 `leases["blockers"]`（tasks 非空时的交接中间态）同名不同物。
        if blocked_rows:
            log(
                "清单里有活但都被别人持有："
                + "、".join(
                    f"{b['course']}@{b['holder'] or '?'}（{float(b['expires_in']):.0f}s 后过期）"
                    for b in blocked_rows
                )
                + f"—— {poll:.0f}s 后再问（不占 idle 预算）"
            )
            if keepalive_stop is not None and keepalive_stop.is_set():
                log("收到停机信号 ⇒ 收工")
                return rc
            if budget > 0 and time.monotonic() - start >= budget:
                log(f"会话预算 session_budget_sec={budget:.0f}s 用尽 ⇒ 收工")
                return rc
            idle_since = time.monotonic()
            terminal_since = None  # 有活（被别人持有）⇒ 同上，别让终态窗口提前收工
            time.sleep(poll)
            continue
        if mode != "drain":
            log("队列为空（queue_mode=once）⇒ 收工（rc=0：没活干不是失败）")
            return rc
        # ★ 2026-10-07：清单**全为终态**（跑满待人重导包 / 人固定在离线之外）或**本会话已跑过这份包**
        #   ⇒ 再等也不会自己变好（只有人介入才复活）。用**独立**的短窗口收工，而不是烧满
        #   `idle_wait_sec`（现场：17:04 已全终态，却空转到 17:33 = 29 分钟）。
        #   判据走 `all_terminal(manifest, served)`——注意是**原始清单**：`tasks`/`blocked_rows` 都不带 `state`。
        if all_terminal(manifest, served):
            now = time.monotonic()
            if terminal_since is None:
                terminal_since = now
                log(
                    "清单全为终态（completed / not_offline）或本会话已跑过它的包："
                    + "、".join(str(t.get("course") or "?") for t in manifest)
                    + f" —— 等 {idle_wait_terminal:.0f}s 确认无人介入后收工"
                    "（要立刻收工把它设成 0；要恢复旧行为设成 1800）"
                )
            if now - terminal_since >= idle_wait_terminal:
                log(
                    f"清单全终态且已等满 idle_wait_terminal_sec={idle_wait_terminal:.0f}s ⇒ 收工"
                )
                return rc
            if budget > 0 and now - start >= budget:
                log(f"会话预算 session_budget_sec={budget:.0f}s 用尽 ⇒ 收工")
                return rc
            if keepalive_stop is not None and keepalive_stop.is_set():
                log("收到停机信号 ⇒ 收工")
                return rc
            log(
                f"清单全终态 —— {poll:.0f}s 后再问"
                f"（已等 {now - terminal_since:.0f}s / 上限 {idle_wait_terminal:.0f}s）"
            )
            time.sleep(poll)
            continue
        terminal_since = None
        waited = time.monotonic() - idle_since
        if waited >= idle_wait:
            log(f"队列空且已等满 idle_wait_sec={idle_wait:.0f}s ⇒ 收工")
            return rc
        if budget > 0 and time.monotonic() - start >= budget:
            log(f"会话预算 session_budget_sec={budget:.0f}s 用尽 ⇒ 收工")
            return rc
        if keepalive_stop is not None and keepalive_stop.is_set():
            log("收到停机信号 ⇒ 收工")
            return rc
        log(f"队列为空 —— {poll:.0f}s 后再问一次（idle 已等 {waited:.0f}s / 上限 {idle_wait:.0f}s）")
        time.sleep(poll)


def run(
    cfg: dict,
    log: Callable[[str], None],
    secret: Callable[..., str],
    keepalive_stop: Any = None,
) -> int:
    """cell 的唯一入口。返回 rc（交给 `SystemExit`）。

    `cfg` 见 `ipynb/battle.offline.ipynb` 的 CFG。两条路（2026-09-25 新增第二条）：

      · **`CFG.course` 非空** ⇒ 点名跑哪几门，顺序即执行序（★M3 起**先 claim 再取包**，
        理由见 `_explicit_leases`：取包门要看 hold，而「谁在跑」只剩 hold 一个真源）。
        塞拿到包的课**跳过继续**下一门（末尾汇总、`M>0` 非零 rc、全跳过 ⇒ 响亮 `SystemExit`）；
        配置错误与训练 `rc≠0` 照旧**立即停**；
      · **`CFG.course` 留空** ⇒ **向 hub 问清单**（`GET /offline/tasks`）：可领的逐个
        `claim` → 取包 → 跑完 → `release`；`queue_mode` 缺省 `drain`（跑完一批继续驻守），
        受 `session_budget_sec` / `idle_wait_sec` / 停机信号限制；**队列空 = 正常收工（rc=0）**。
        老 hub（没有清单端点）⇒ 降级回「必须填 course」，此时空 ⇒ `SystemExit`。

    边界（评审 X1，与 plan/offline-switch-auto-bundle §3.5 是同一句话的两半）：
    「**点名**（CFG 或清单）要跑的课取不到包」是**异常**（响亮）；「队列本来就空」是**正常**。
    """
    # ★ 凭据一律在任何网络改动**之前**读完（2026-09-17 Kaggle 事故的时序约束）：
    #   userspace 引导会把平台 Secrets（公网 HTTPS）变成够不着的东西。
    creds = {
        "HUB_TOKEN": secret("HUB_TOKEN", cfg.get("hub_token")),
        "HUB_IP": secret("HUB_IP", cfg.get("hub_ip")),
        "TS_AUTHKEY": secret("TS_AUTHKEY", cfg.get("ts_authkey")),
    }
    log("凭据就绪（值不落日志）：" + (", ".join(k for k, v in creds.items() if v) or "（一个都没读到）"))
    explicit = _load_deliverable().requested_courses(cfg)
    if explicit:
        log(f"课程队列（{len(explicit)} 门，串行，CFG 点名）：{', '.join(explicit)}")
        return _run_batch(
            cfg,
            creds,
            log,
            keepalive_stop,
            explicit,
            multi=len(explicit) > 1,
            leases=_explicit_leases(cfg, creds, log),
        )
    if not bool(cfg.get("auto_discover", True)):
        # 显式关掉自动发现 = 「我就是要手填 course」的口径 ⇒ 与今天逐字相同地响亮拒。
        raise SystemExit(
            "[offline] CFG.course 没填，且 auto_discover=False ⇒ 不知道跑哪几门课。\n"
            "  ① 填 CFG.course（可以多门，列表按顺序串行）；\n"
            "  ② 或把 auto_discover 打开（缺省 True）⇒ 云机向 hub 问清单。"
        )
    log("CFG.course 留空 ⇒ 向 hub 问任务清单（plan/offline-task-discovery）")
    return _run_auto(cfg, creds, log, keepalive_stop)


def _redact(argv: list[str]) -> list[str]:
    """日志用的 argv（token 只走文件，这里只是防御性地不打印任何 token 形参）。"""
    out: list[str] = []
    skip = False
    for a in argv:
        if skip:
            out.append("***")
            skip = False
            continue
        out.append(a)
        skip = a in ("--hub-token", "--hub-lease")
    return out
