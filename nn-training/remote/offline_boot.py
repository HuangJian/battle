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

**Kaggle 上不使用 tailscale**（用户口径 2026-09-23）：平台容器既不给 NET_ADMIN 也换不得
网络命名空间，而 userspace 引导会把进程代理改写成只转发 Tailscale IP 的代理——2026-09-17
的 Kaggle 事故就是这么发生的（代理改写后平台 Secrets 够不着）。所以 `is_kaggle()` 为真时
**跳过全部 tailscale 步骤**：不起隧道、也不把 tailnet IP 当候选（那种地址没有隧道必然不通）。

**中途取回**（`package_partial`）：会话到点/被回收时，产物目录里已经有 `LATEST.zip`
（每次 checkpoint 刷新）——本函数把它复制成控制台习惯的 `deliver-<课>.zip`，人下载后
「导入产物」即可（与跑完时同一个名字、同一个导入路径）。

**为什么本模块顶部不 import `remote.*`**：它在**拿到任务包之前**就要干活——那时
`code.zip` 还没进 `sys.path`，`remote` 包根本不存在（中途取回那条路也一样：包可能还没下来，
而产物已经在盘上）。索引名/代码名/产物包名因此在这里各留一份常量（有测试盯着与
`remote/bundle.py` / `remote/artifacts.py` 逐字相同），读索引只用 `zipfile` + `json`。
`tailscale_boot` 按 `notebook_boot` 的做法双路加载（包内 `remote.tailscale_boot` 或
顶层的 `tailscale_boot`）。
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: 任务包索引名 / 代码件名 / 包身份 magic —— 与 `remote/bundle.py` 逐字相同（测试守）。
BUNDLE_INDEX = "task.json"
CODE_NAME = "code.zip"
BUNDLE_MAGIC = "battle2-task-bundle"

#: 解包目录（与 notebook_boot 的 `CODE_DIR` 同值：同一个进程里两份引导不打架）。
CODE_DIR = "/tmp/worker-code"

#: 产物目录里的两个包名（与 `remote/artifacts.py::ArtifactStore` 逐字相同；测试守）。
ALL_ZIP = "artifacts.zip"
LATEST_ZIP = "LATEST.zip"
#: 中途取回的优先顺序：全量包（已收尾）优先，其次最新一轮小包（跑到一半）。
PARTIAL_CANDIDATES = (ALL_ZIP, LATEST_ZIP)
#: `LATEST.zip` 里那行元信息（名字写死在 `remote/artifacts.py::_refresh_latest`）。
LATEST_ROW_NAME = "metrics_row.json"

#: 等包缺省时长（秒）：hub 没导出 / 用户还没上传，都在这条线上等。
DEFAULT_WAIT_SEC = 1800.0
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
    由 `tests/test_offline_boot.py` 盯着两边同值：官方标记 `KAGGLE_KERNEL_RUN_TYPE`、
    本文件 `download_dir()` 已在用的 `KAGGLE_URL_BASE`，或 `/kaggle/working` 存在。
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
            hub.rstrip("/") + "/ping", headers={"Authorization": "Bearer " + token}
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


def fetch_task_pack(
    hub: str,
    token: str,
    course: str,
    dest_dir: Path,
    log: Callable[[str], None],
    timeout: float = PACK_TIMEOUT,
) -> Path | None:
    """`GET /offline/task-pack?course=<课>` → 落到 `dest_dir/task-<课>.zip`。

    返回 None = **这一次**没取到（404 还没导出 / 网络抖动）——调用方据此重试或转手动。
    401/403 也不抛：包可能已经在用户手上，手动路仍然能把任务做完，所以只响亮记一笔。
    """
    url = f"{hub.rstrip('/')}/offline/task-pack?course={urllib.parse.quote(course)}"
    try:
        raw = _fetch_guarded(
            url, {"Authorization": "Bearer " + token}, log, "task-pack", timeout
        )
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            log(f"取包被拒 HTTP {e.code} —— HUB_TOKEN 不一致（手动上传仍然可行）")
        elif e.code == 404:
            log(f"hub 上还没有 task-{course}.zip —— 先在控制台「导出任务包」")
        else:
            log(f"取包失败 HTTP {e.code} —— 稍后重试")
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
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
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
            req = urllib.request.Request(q, headers={"Authorization": "Bearer " + token})
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
        return fetch_task_pack(hub, tok, course, work_dir, log)
    return None


def obtain_pack(
    cfg: dict,
    creds: dict,
    log: Callable[[str], None],
    work_dir: Path,
    stop: Any = None,
) -> Path:
    """拿任务包：显式路径 → 已有落点 → （hub 取 / 等人传）等到 deadline 为止。

    **两条源在同一个循环里轮询**（每轮先看落点、再试 hub）：用户随时可能上传，hub 也随时
    可能被点上「导出」——把它们排成先后两步，会让「上传之后又等满 hub 的超时」这种事发生。

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
            got = _try_hubs(hubs, hub_broken, creds, course, work_dir, log)
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
        if not prompted and cfg.get("prompt_upload", True) and wait_s > 0:
            prompted = True
            got = prompt_upload(log)
            if got is not None:
                return got
        if time.time() >= deadline:
            break
        time.sleep(max(0.05, min(poll_s, deadline - time.time())))

    raise SystemExit(
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


def ensure_code(pack: Path, log: Callable[[str], None], code_dir: str = CODE_DIR) -> Path:
    """把包里的 `code.zip` 解到 `code_dir` 并插进 `sys.path`（同 commit 的运行时）。

    与云端 worker 的做法同源（D6：不下 git，只吃包里那份代码）——云机上没有仓，
    「重放 build_pairs」靠的就是这份代码与 commit 一致。
    """
    with zipfile.ZipFile(pack) as zf:
        try:
            raw = zf.read(CODE_NAME)
        except KeyError as e:
            raise SystemExit(f"[offline] 任务包缺 {CODE_NAME}（包损坏？重导一次）: {pack}") from e
    dest = Path(code_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        z.extractall(dest)
    if str(dest) not in sys.path:
        sys.path.insert(0, str(dest))
    log(f"代码就位: {dest}（{len(raw)} bytes, sha12={hashlib.sha256(raw).hexdigest()[:12]}）")
    return dest


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


def build_run_argv(
    cfg: dict,
    pack: Path,
    dest: Path,
    hub: str,
    token_file: str,
    resume_dir: str | Path | None = None,
) -> list[str]:
    """run_loop 的 argv（纯函数，便于单测钉住回传/评估/锚点三组开关的形状）。

    `cfg["course"]` 在这一层恒是**单个**课程名（`run_one_course` 已把多课程列表拆开），
    它同时是补传的归位键（hub 侧 `<traj>/<课>/` 的目录名）与任务包名的一部分。
    """
    course = str(cfg.get("course") or "").strip()
    argv = ["--bundle", str(pack), "--artifacts", str(dest)]
    device = str(cfg.get("device") or "").strip()
    if device:
        argv += ["--device", device]
    if int(cfg.get("threads") or 0):
        argv += ["--threads", str(int(cfg["threads"]))]
    if int(cfg.get("max_iters") or 0):
        argv += ["--max-iters", str(int(cfg["max_iters"]))]
    if float(cfg.get("budget_sec") or 0):
        argv += ["--budget-sec", str(float(cfg["budget_sec"]))]
    # rollout 并行局数：缺省（不传）= 按云机核数 `max(cores−4, cores×0.8)`，与云机 eval 同一口径。
    # 传了就完全按它——两边都不为对方预留（rollout 与 eval 在这条链上是交替跑的）。
    if int(cfg.get("rollout_workers") or 0):
        argv += ["--rollout-workers", str(int(cfg["rollout_workers"]))]
    # 云机 A 层评估（`eval_on_cloud`）：语料/口径全部由课程（随包的 course.jsonc）决定，
    # 这里只传两个执行面旋钮（并发/单局超时）——不在 notebook 里重复一遍语料定义。
    if bool(cfg.get("eval_on_cloud", False)):
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
    else:
        argv += ["--no-deliver"]
    return argv


#: 课程名的合法形状（与 `course_from_pack_name` 同一条口径：它是目录名/文件名，不是自由文本）。
_COURSE_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def courses_of(cfg: dict) -> list[str]:
    """`CFG.course` → **课程名列表**（去重、保序）：字符串 = 一门课，列表 = 串行多门。

    用户指令（2026-09-22）：「battle.offline.ipynb 的 course 配置项，需支持多个离线课程名。
    云机串行从 hub 取任务，逐个完成。」——列表即执行顺序（控制台里那几门课的名字，逐字相同）。

    三种写法都认：`"c5-gae"`、`["c5-gae", "c6-gae"]`、`"c5-gae, c6-gae"`（逗号/空白分隔）。
    空列表/空名/非法名（含路径分隔符、`..` 等）一律 SystemExit —— 课程名会被拼进目录名与
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
    if not out:
        raise SystemExit(
            "[offline] CFG.course 没填 —— 取包/交付物都按课程名走，必须给"
            "（支持多门课：列表按顺序串行跑完）"
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


def run_one_course(
    cfg: dict,
    creds: dict,
    log: Callable[[str], None],
    keepalive_stop: Any,
    *,
    course: str,
    multi: bool = False,
    run_loop_main: Callable[[list[str]], int] | None = None,
) -> int:
    """跑**一门课**的整段：取包 → 引导代码 → `remote.run_loop` → 打交付物；返回 rc。

    `multi=True`（同一会话里还有别的课）时工作目录再套一层课程名——见 `course_work_dir`。
    `run_loop_main` 是测试用的注入点（生产走 `remote.run_loop.main`）。
    """
    work = course_work_dir(cfg, course, multi=multi)
    work.mkdir(parents=True, exist_ok=True)
    log(f"工作目录: {work}")
    # 本课自己的 cfg：`course` 恒是**单个字符串**（下游放包路径/交付物名/`--hub-course`
    # 都按它拼；传原样的列表会拼出 "['a', 'b']" 这种目录名）。
    ccfg = {**cfg, "course": course}

    pack = obtain_pack(ccfg, creds, log, work, stop=keepalive_stop)
    idx = read_pack_index(pack)
    log(
        f"任务包: {idx.get('run_id')} it{idx.get('it')} → it{idx.get('end_it')}"
        f"（commit {str(idx.get('commit') or '')[:12]}，{idx.get('created_at')}）"
    )
    in_name = course_from_pack_name(pack)
    if in_name and in_name != course:
        raise SystemExit(
            f"[offline] 文件名里的课程（{in_name}）与 CFG.course（{course}）不一致 —— "
            "跑错课的包会把权重接在别的课程账本上。确认是它就把 CFG.course 改对，"
            "否则去控制台导正确那门课的包"
        )

    ensure_code(pack, log)
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

    token = str(creds.get("HUB_TOKEN") or "")
    hub = ""
    # hub 探活**只做一次**：回传与「续跑锚点」两条腿共用同一个连通性结论（它们都是
    # 「hub 此刻在不在」的问题，探两次只会让日志里出现两个可能不一致的结论）。
    for cand in hub_candidates(ccfg, creds):
        if probe_hub(cand, token, log):
            hub = cand
            break
    resume_dir: Path | None = None
    if hub:
        # 续跑锚点：hub 手里可能有更新的（自回传 / 人工导入的）完整轮次。先取它，
        # 再交给 run_loop——于是「重领任务」= 从最新进度接着跑，而不是从头重跑。
        resume_dir = fetch_resume(hub, token, course, work / "resume", log)
    if bool(ccfg.get("live_backfeed", True)):
        if hub:
            tok_file = write_token_file(work, token)
            log(f"实时回传开启 → {hub}（每轮 best-effort 推产物）")
        else:
            log("实时回传开着，但此刻够不着 hub —— 改为纯离线（产物照样逐轮落盘）")
    else:
        log("实时回传关闭（CFG.live_backfeed=False）—— 跑完统一打包，手动下载导入")

    dest = work / "run"
    argv = build_run_argv(ccfg, pack, dest, hub, tok_file, resume_dir)
    log("开始训练：python -m remote.run_loop " + " ".join(_redact(argv)))
    rc = int(run_loop_main(argv) or 0)
    log(f"run_loop 退出 rc={rc}；产物目录 {dest}")

    got = package_deliverable(dest, course, download_dir(ccfg), log)
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


def run(
    cfg: dict,
    log: Callable[[str], None],
    secret: Callable[..., str],
    keepalive_stop: Any = None,
) -> int:
    """cell 的唯一入口。返回 rc（交给 `SystemExit`）。

    `cfg` 见 `ipynb/battle.offline.ipynb` 的 CFG（course / hub_url / device / task_zip /
    wait_pack_sec / hub_tries / live_backfeed / budget_sec / threads / max_iters / eval_on_cloud /
    rollout_workers …）。

    **多课程：串行跑完**（2026-09-22 用户指令）——`CFG.course` 给列表时按顺序逐门跑，
    每门课都是完整一段（取包 → 训练 → 交付物）；哪一门没跑完就停在那里并把剩余课程
    列出来（一门课的错误不该被下一门课的错误盖住；而“剩余课程名”是人在云机上最需要的
    下一步）——不静默跳课。
    """
    # ★ 凭据一律在任何网络改动**之前**读完（2026-09-17 Kaggle 事故的时序约束）：
    #   userspace 引导会把平台 Secrets（公网 HTTPS）变成够不着的东西。
    creds = {
        "HUB_TOKEN": secret("HUB_TOKEN", cfg.get("hub_token")),
        "HUB_IP": secret("HUB_IP", cfg.get("hub_ip")),
        "TS_AUTHKEY": secret("TS_AUTHKEY", cfg.get("ts_authkey")),
    }
    log("凭据就绪（值不落日志）：" + (", ".join(k for k, v in creds.items() if v) or "（一个都没读到）"))
    courses = courses_of(cfg)
    multi = len(courses) > 1
    log(f"课程队列（{len(courses)} 门，串行）：{', '.join(courses)}")
    rc = 0
    for i, course in enumerate(courses):
        rest = courses[i + 1 :]
        log(f"===== [{i + 1}/{len(courses)}] 课程 {course} =====")
        try:
            rc = run_one_course(cfg, creds, log, keepalive_stop, course=course, multi=multi)
        except SystemExit as e:
            if rest:
                log(
                    f"课程 {course} 未跑完（{e.code}）——串行到此为止，剩余 {len(rest)} 门课未执行："
                    f"{', '.join(rest)}"
                )
            raise
        if rc != 0:
            log(
                f"课程 {course} 退出 rc={rc} ——串行到此为止；"
                + (f"剩余 {len(rest)} 门课未执行：{', '.join(rest)}" if rest else "它已是最后一门课")
            )
            return rc
        if rest:
            log(f"课程 {course} 完成 —— 下一门：{rest[0]}")
    log(f"全部课程完成（{len(courses)} 门）：{', '.join(courses)}")
    return rc


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
        skip = a in ("--hub-token",)
    return out
