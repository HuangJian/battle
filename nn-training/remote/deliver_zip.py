"""remote/deliver_zip.py —— 训练产物 zip（`deliver-<课程>.zip`）的**导入**入口。

控制台上的那条腿：导出任务包 → 上传 Kaggle/Colab 跑完 → 把产出的 zip 交回来 →
导入并**自动按课程配置跑 A 层评估**。本模块只管「把 zip 变成一份可评估的产物目录」，
评估由控制台接着发起（`trainer/eval_a_once.py`，复用现有 evalA 动作）。

**产物 zip 是什么**：`remote/artifacts.py` 收尾时打的 `artifacts.zip`（人从 Kaggle
output / Colab Drive 下载下来，文件名习惯是 `deliver-<课程>.zip`）。形状：

    plan.json / manifest.json / state.json / metrics.jsonl / README.txt
    it-NNN/weights.json + it-NNN/opt.tar      （逐轮不可变快照）

`LATEST.zip`（会话中途下载的「最新一轮」小包）**同样接受**——它少 `state.json`，
但 `it-NNN/weights.json` 才是唯一的硬要求（没有权重的包没有可评估的东西）。

**这是不可信输入**（文件经人手搬运），所以三道门：

  1. **zip-slip 拒绝**（复用 `remote.bundle.safe_extract_zip`，与任务包同一道门）；
  2. **形状校验**：至少一个 `it-*/weights.json`；拿错包（把 `task-*.zip` 传上来）
     要**明确指出拿错了**——那句错误信息值一次排障；
  3. **课程名对账（可选）**：文件名 `deliver-<课程>.zip` 里的课程与控制台当前课程
     不一致就拒收——把 A 课跑出来的产物导进 B 课并评估，得到的读数看起来完全正常，
     这是最贵的一种错；
  4. **包内自述对账（2026-10-17，plan §5.2/§6.2-2）**：包里有 `IMPORT_META.json` 时
     （一键包/导出包会带），它的 `course` 与本次导入的课程不一致也拒收（改名戴上去的包）。
     缺这份自述 = 没有这条证据（老包/手打包照收），magic 不认识则**当它不存在**并与体检里
     记一项。

CLI（控制台调用；stdout 末行是机器可读的 `DELIVER_IMPORT_JSON={...}`）：

    python -m remote.deliver_zip --zip <deliver-X.zip> --dest <tmp/<course>/deliver> \\
        [--course X] [--json-only]
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

# 产物账本行 → 课程账本行的搬运**只在 remote.artifacts 实现一份**（人工导入与实时补传共用）：
# 两份翻译必然漂开，而「两腿同字段」正是那张表存在的意义。
import remote.delta_files as delta_files
from common.cli import house_parser, run_main
from common.protocol import ProtocolError
from remote.artifacts import ArtifactStore, ledger_row_from_metrics, sha256_file
from remote.bundle import BUNDLE_INDEX, safe_extract_zip

#: 包内元信息的名字/魔数从**唯一实现**处取（`remote/offline_deliverable` = standalone 交付面，
#: 它写的、这里读的必须是同一串；不各写一份常量 —— 2026-10-17，plan §5.2/§6.2-2）。
from remote.offline_deliverable import IMPORT_META_MAGIC, IMPORT_META_NAME

#: 机器可读结果的标记（控制台按它切 stdout 的末行；前面的人读日志随便打）。
IMPORT_JSON_MARK = "DELIVER_IMPORT_JSON="
#: `run_complete` 的 reason（控制台灰横幅第一行显示它；与本地停车那条同形）。
RUN_COMPLETE_REASON = "离线段跑满（云机自报 end_it_reached + 导入转交）"
#: 产物 zip 的习惯文件名（`deliver-<课程>.zip`）。
DELIVER_PREFIX = "deliver-"
#: 单个 zip 的大小上限（产物 zip 含逐轮权重：几轮到几十轮 = 几 MB 到几十 MB；
#: 2GB 以上不是「产物」而是别的东西，别把 hub 的磁盘和内存当垃圾场）。
ZIP_MAX_BYTES = 2 * 1024 * 1024 * 1024


def course_from_filename(zip_path: str | Path) -> str:
    """从 `deliver-<课程>.zip` 里取出 `<课程>`（不符合习惯命名 → 空串）。

    只用于**对账**（见模块 docstring 的门 ③），所以命名不标准时不猜、直接返回空。
    """
    base = Path(zip_path).name
    m = re.fullmatch(re.escape(DELIVER_PREFIX) + r"(.+)\.zip", base, re.IGNORECASE)
    if not m:
        return ""
    name = m.group(1).strip()
    # 课程段 = `@` **之前**那一段（与控制台 `validateDeliverUpload` 同一规则，两处同改）：
    # 一键包/导出名把留历史用的附注放在 `@` 之后（`deliver-<课>@<run_id>-it<N>.zip`，§5.2）
    # ——附注**不当课程**，否则「文件名里的课程与控制台不符」会用一段不是课程的文本去对账
    # （现场 `@<run>-it40` 这一串会把本来正确的包判成「别的课的产物」）。
    name = name.split("@", 1)[0].strip()
    # 课程名 = 目录名的安全子集（与 dashboard 的 validateCourseName 同精神）
    return name if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) else ""


def _package_checks(
    base: Path,
    pkg_iters: list[int],
    st: dict,
    *,
    names: int,
    size: int,
    partial: bool,
    meta: dict,
    meta_raw: dict,
) -> list[dict]:
    """包的**体检报告**（plan §6.2-1 / DoD-5c）：一行一项 `{id, ok, detail}`。

    为什么值得单独做：门散在四处（形状/课名/撞名/体上限），失败时人只看到最后那一道；
    而「包能导入」与「包是完整的」是两件事——半段快照、缺轮、行与权重指纹不符、状态文件
    与末轮不符，都能导入成功但读数不可信（控制台恰好只读那些读数）。

    ⚠ 这是**报告不是门**：任何一项不过都不阻断导入（先落权重是这一半个功能的主价值），
    所以失败项进 message/detail，而不是抛异常。
    """
    out: list[dict] = []
    iters_ok = bool(pkg_iters)
    span = f"it{pkg_iters[0]} → it{pkg_iters[-1]}（{len(pkg_iters)} 轮）" if iters_ok else "没有 it-NNN/"
    out.append({"id": "entries", "ok": names > 0, "detail": f"{names} 个成员"})
    out.append({"id": "shape", "ok": iters_ok, "detail": span})
    # 轮次连续性（缺轮 = 那几轮的权重永远回不来，而曲线会看起来「有」那一段）
    gaps = (
        [n for n in range(pkg_iters[0], pkg_iters[-1] + 1) if n not in set(pkg_iters)] if iters_ok else []
    )
    out.append({
        "id": "continuity",
        "ok": not gaps,
        "detail": "连续" if not gaps else f"缺 {_its_text(gaps)}",
        # 结构化读数（与 `detail` 并存）：控制台/用例可以按**数据**判，不用去读那句人话
        # （「判据挂在措辞上」是本仓点名过的反模式，见 `tests/helpers/text_asserts.py`）。
        "missing": gaps,
    })
    # 逐轮账本行 + **权重指纹对账**（行里的 `weights_fp` 就是 `sha256_file(weights)`，
    # `remote/artifacts.py:348`）：行/权重是两个东西时（续跑重跑同一 it）在这里就看得见。
    rows: dict[int, str] = {}
    try:
        for line in (base / ArtifactStore.METRICS_NAME).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and isinstance(ev.get("it"), int):
                rows[int(ev["it"])] = str(ev.get("weights_fp") or "")
    except OSError:
        rows = {}
    bad_fp: list[int] = []
    no_row: list[int] = []
    no_w: list[int] = []
    for it in pkg_iters:
        if it not in rows:
            no_row.append(it)
        w = delta_files.find_existing(base / f"{ArtifactStore.IT_PREFIX}{it:03d}", "weights.json")
        if w is None:
            no_w.append(it)
            continue
        if rows.get(it) and sha256_file(w) != rows[it]:
            bad_fp.append(it)
    out.append({
        "id": "rows-fp",
        "ok": not (no_row or bad_fp),
        "no_rows": no_row,
        "bad_fp": bad_fp,
        "detail": (
            f"{len(pkg_iters) - len(no_row)}/{len(pkg_iters)} 轮有行，指纹相符"
            if not (no_row or bad_fp)
            else "; ".join(
                ([f"缺行 {_its_text(no_row)}"] if no_row else [])
                + ([f"指纹不符 {_its_text(bad_fp)}"] if bad_fp else [])
            )
        ),
    })
    out.append({
        "id": "weights",
        "ok": not no_w,
        "missing": no_w,
        "detail": f"{len(pkg_iters) - len(no_w)}/{len(pkg_iters)} 件权重在场"
        if iters_ok
        else "无轮次可查",
    })
    state_last = int(st.get("last_it") or 0)
    out.append({
        "id": "state",
        "ok": bool(st.get("run_id")) and state_last == (pkg_iters[-1] if iters_ok else 0),
        "state_last_it": state_last,
        "package_last_it": pkg_iters[-1] if iters_ok else 0,
        "detail": (
            f"run_id={st.get('run_id') or '?'}·last_it={state_last}"
            if not partial
            else f"半段快照：state.last_it={state_last} > 包内末轮"
            f"{'it' + str(pkg_iters[-1]) if iters_ok else '?'}"
        ),
    })
    last_w = (
        delta_files.find_existing(base / f"{ArtifactStore.IT_PREFIX}{pkg_iters[-1]:03d}", "weights.json")
        if iters_ok
        else None
    )
    st_fp = str(st.get("last_weights_fp") or "")
    fp_ok = bool(st_fp) and last_w is not None and sha256_file(last_w) == st_fp
    out.append({
        "id": "state-fp",
        "ok": fp_ok,
        "detail": "状态文件的末轮指纹与末轮字节相符" if fp_ok else "状态文件缺指纹/与末轮字节不符",
    })
    anchors = [
        n
        for n in (ArtifactStore.MANIFEST_NAME, ArtifactStore.PLAN_NAME, ArtifactStore.STATE_NAME)
        if not (base / n).is_file()
    ]
    out.append({
        "id": "anchors",
        "ok": not anchors,
        "missing": anchors,
        "detail": "manifest/plan/state 齐" if not anchors else f"缺 {'/'.join(anchors)}",
    })
    out.append({
        "id": "body-limit",
        "ok": 0 < size <= ZIP_MAX_BYTES,
        "detail": f"{size / 1048576:.1f}MB",
    })
    # 包内自述（§5.2 的 `IMPORT_META.json`）：课程/run/来源 —— 一键包与导出包才有，
    # 老包/手打包没有它 ⇒ 那一项**不算不过**（只是没有这条证据，对账退回文件名那条路）。
    out.append({
        "id": "meta",
        "ok": bool(meta) or not meta_raw,
        "package_course": str(meta.get("course") or ""),
        "package_source": str(meta.get("source") or ""),
        "detail": (
            f"课程={meta.get('course') or '?'}·run={meta.get('run_id') or '?'}·"
            f"来源={meta.get('source') or '?'}·it{meta.get('it_from')}→it{meta.get('it_to')}"
            if meta
            else (
                "包内没有 IMPORT_META.json（老包/手打包）——按文件名与控制台课程对账"
                if not meta_raw
                else f"IMPORT_META.json 的 magic（{meta_raw.get('magic') or '?'}）不认识——当它不存在"
            )
        ),
    })
    return out


def _its_text(its: list[int], cap: int = 8) -> str:
    """一组轮次号的紧凑写法（体检详情用：`it5,it7`；超过 cap 个就省略尾部）。"""
    head = ",".join(f"it{n}" for n in its[:cap])
    return head if len(its) <= cap else f"{head}…（共 {len(its)} 个）"


def _scan_iters(root: Path) -> list[int]:
    """产物目录里的轮次（`it-NNN/weights.json` **或它的压缩版** `weights.delta` 存在者，升序）。

    两态都认（2026-10-15 后缀约定）：名字跟着内容走，而人工导入的包可能来自一条
    **离线段**（逐轮回传的就是 `BWD1` 容器 ⇒ 包里那件可能叫 `weights.delta`）。
    只认 `.json` 会把整段产物判成「不是训练产物 zip」（而包里明明有东西）。
    """
    out: list[int] = []
    for d in sorted(root.glob(f"{ArtifactStore.IT_PREFIX}*")):
        if not d.is_dir():
            continue
        name = d.name[len(ArtifactStore.IT_PREFIX) :]
        if not name.isdigit():
            continue
        if delta_files.find_existing(d, "weights.json") is not None:
            out.append(int(name))
    return out


def _read_json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _same_bytes(a: Path, b: Path) -> bool:
    """两件是否同字节（先比大小，再比内容——逐件对账的代价要压在「名字+大小对得上」的常态外）。"""
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _round_files(d: Path) -> list[Path]:
    """一个轮次目录里的件，**权重排最后**（半截轮的可见性由这个顺序决定，见 `_land_rounds`）。"""
    files = [f for f in sorted(d.iterdir()) if f.is_file()]
    # `weights.*` 交给 `delta_files.find_existing` 认（两态：`weights.json` / `weights.delta`）。
    return sorted(files, key=lambda f: f.name.startswith("weights."))


def _land_rounds(
    src: Path,
    final: Path,
    iters: list[int],
    *,
    replace: bool = False,
    log=lambda _m: None,
) -> dict:
    """把 `src` 里那几轮**逐件**并进 `final`；返回 `{kept_rounds, skipped_rounds, healed_files}`。

    为什么不是「解到 `<dest>/<run>.tmp-<pid>` 再整体 rename」（2026-10-16，现场 §3.8）：Windows 的
    `MoveFile` 要求源目录**整树无句柄**，而 `tmp/**` 下的目录会被本机常驻进程/监视器按序挂上句柄
    ⇒ 只要源在 `tmp/` 里，目录改名**永久** `WinError 5`（等 90s、退避 30s、`os.replace` 全无效）。
    逐件写盘对「源被监视」不敏感，且与 `MoveFile` 的语义坑彻底脱钩。

    判据（与 hub 的 `store_offline_artifact` 的 duplicate/heal 同语义，plan §8.2-1）：

      * 目标缺这一件 ⇒ **拷**（愈合：回灌腿原来是「存在即整轮跳过」⇒ 丢 `opt.tar` 的轮永不补）；
      * 已有且字节相同 ⇒ duplicate（跳过）；
      * 已有且字节不同 ⇒ **保留盘上先到的那一份**（`skipped_rounds` + 一行 WARN）——同名轮是不可变
        快照，换掉它只会把「已经收下的那一份」换成另一份（2026-10-11 事故的同一条口径：响比静默好）；
        `replace=True`（D5 的逃生门，人工裁决用）先把该轮清掉再照包里的字节写。

    **单轮内权重最后拷**：半截轮对 `_scan_iters`（只认 `weights.*`）不可见 ⇒ 下一次导入会把它补齐
    ——旧代码靠「原子改名」拿到的「不留半截」由这个顺序接手。
    """
    counts = {"kept_rounds": 0, "skipped_rounds": 0, "healed_files": 0}
    final.mkdir(parents=True, exist_ok=True)
    for it in iters:
        s_dir = src / f"{ArtifactStore.IT_PREFIX}{it:03d}"
        if not s_dir.is_dir():
            continue
        d_dir = final / f"{ArtifactStore.IT_PREFIX}{it:03d}"
        has_weights = delta_files.find_existing(d_dir, "weights.json") is not None
        fresh = not has_weights
        if replace and d_dir.exists():
            shutil.rmtree(d_dir, ignore_errors=True)
            fresh = True
        d_dir.mkdir(parents=True, exist_ok=True)
        diff = healed = 0
        for f in _round_files(s_dir):
            dst = d_dir / f.name
            if dst.exists():
                if not _same_bytes(f, dst):
                    diff += 1
                continue
            shutil.copy2(f, dst)
            healed += 1
        if fresh:
            counts["kept_rounds"] += 1
        counts["healed_files"] += healed
        if diff:
            counts["skipped_rounds"] += 1
            log(
                f"[deliver] it{it} 在盘上已有**不同字节**的 {diff} 件——保留先到的那一份"
                "（同名轮先到者为准；要人工裁决用 --replace）"
            )
    return counts


def _land_anchors(src: Path, final: Path, *, conflicts: int, log=lambda _m: None) -> None:
    """把包里的**顶层旁文件**（`state/plan/manifest/artifacts.zip/LATEST.zip/两账本/README`…）
    落进 `final`：**缺件才拷**（先到者为准），`state.json` 是唯一的例外。

    为什么不是「包里有什么就盖什么」：这些旁文件是这一份产物的**自述**（`state.json.last_it` /
    `last_weights_fp` 会被恢复腿与读方拿去用），盖上去就会让它指向**被先到者为准跳过的轮**。
    例外（`state.json`）：当「新包自述的 `last_it` 更靠后」∧「本次零冲突（自述的轮确实都在盘上）」
    时采纳它——否则自述与盘上事实不符，比陈旧更贵。

    逐 `iterdir` 而不是写一份名字清单：产物根的形状不只有四个锚点（`README.txt`/`LATEST.zip`
    等），清单会让「旧代码整体搬过的东西」悄悄少下去。两账本也走这里补位；但**课程账本的
    并账不靠它**——`_merge_carried_*` 从**包里**读（见那里的为什么）。
    """
    adopt_state = False
    if (final / ArtifactStore.STATE_NAME).exists():
        cur = _read_json(final / ArtifactStore.STATE_NAME)
        want = _read_json(src / ArtifactStore.STATE_NAME)
        try:
            adopt_state = (
                bool(want)
                and int(conflicts or 0) == 0
                and int(want.get("last_it") or 0) > int(cur.get("last_it") or 0)
            )
        except (TypeError, ValueError):
            adopt_state = False
        if adopt_state:
            log(
                f"[deliver] state.json 采纳更靠后的自述（it{int(want.get('last_it') or 0)} > "
                f"it{int(cur.get('last_it') or 0)}，本轮零冲突）"
            )
    for s in sorted(p for p in src.iterdir() if p.is_file()):
        d = final / s.name
        if d.exists() and not (s.name == ArtifactStore.STATE_NAME and adopt_state):
            continue
        shutil.copy2(s, d)


def import_deliver_zip(
    zip_path: str | Path,
    dest_root: str | Path,
    *,
    course: str = "",
    replace: bool = False,
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """把产物 zip **逐轮并进** `dest_root/<run_id>/`，返回导入句柄 dict。

    返回：`{run_id, dir, iters, last_it, ckpt, package_iters, package_last_it, kept_rounds,
    skipped_rounds, healed_files, course, course_in_name, state, source_zip}`。
    `iters`/`last_it`/`ckpt` 全部是**盘上真实**（合并后）——`ckpt` 必须指向盘上存在的那份权重
    （控制台拿它去跑 evalA）；`package_*` = 这个包自己带的那一段（便于对账）。
    任一门不过抛 `ProtocolError`，且**不写任何东西**；落位不再依赖目录改名（`_land_rounds`）。
    """
    src = Path(zip_path)
    if not src.exists():
        raise ProtocolError(f"产物 zip 不存在: {src}")
    size = src.stat().st_size
    if size <= 0:
        raise ProtocolError(f"产物 zip 是空文件: {src}")
    if size > ZIP_MAX_BYTES:
        raise ProtocolError(f"产物 zip 过大（{size / 1e9:.2f} GB > {ZIP_MAX_BYTES / 1e9:.0f} GB）: {src}")

    name_course = course_from_filename(src)
    if course and name_course and name_course != course:
        raise ProtocolError(
            f"文件名里的课程（{name_course}）与控制台当前课程（{course}）不一致——"
            "产物是拿 A 课的权重去评 B 课的语料，读数会看起来完全正常。"
            "确认要导就把它改名成 deliver-%s.zip（或切到对应课程）" % course
        )

    try:
        with zipfile.ZipFile(src) as zf:
            names = zf.namelist()
    except (OSError, zipfile.BadZipFile, ValueError) as e:
        raise ProtocolError(f"不是合法的 zip: {src}: {e}") from e
    if BUNDLE_INDEX in names:
        raise ProtocolError(
            f"这是**任务包**（含 {BUNDLE_INDEX}），不是训练产物——任务包给云机跑（"
            "`python -m remote.run_loop --bundle <zip>`），要交回来的是它跑完后落盘的"
            "产物 zip（`it-NNN/weights.json` 那一种）"
        )
    if not names:
        raise ProtocolError(f"zip 是空的: {src}")

    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    # run_id 先按文件名猜（它就是 hub 侧/云机侧的 run id），解完再用 state.json 校正。
    run_id = name_course or ""
    if not run_id:
        run_id = re.sub(r"[^A-Za-z0-9._-]", "_", src.stem)[:64] or "deliver"
    work = dest_root / f"{run_id}.tmp-{os.getpid()}"
    final = dest_root / run_id
    st: dict = {}
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    try:
        work.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src) as zf:
            safe_extract_zip(zf, work)  # 门①：zip-slip（不可信输入）
        # zip 成员可能整包套了一层目录（`unzip -d` 手滑）——下探一层直到看见 it-*/。
        base = work
        for _ in range(3):
            if _scan_iters(base):
                break
            subs = [d for d in base.iterdir() if d.is_dir()]
            if len(subs) != 1:
                break
            base = subs[0]
        iters = _scan_iters(base)  # 门②：形状
        if not iters:
            raise ProtocolError(
                f"这个包里没有 {ArtifactStore.IT_PREFIX}NNN/weights.json（或压缩版 "
                "weights.delta）——不是训练产物 zip"
                f"（包内顶层: {sorted(p.name for p in list(base.iterdir())[:8])}）"
            )
        st = _read_json(base / ArtifactStore.STATE_NAME)
        # 包内自述（§5.2 / §6.2-2）：一键包会带 `IMPORT_META.json`。它**不是**权威（`state.json`
        # 仍是 run_id 的权威，而课程由 `--course` 与文件名对账），但它能拦下两种静默错：
        #   ① 包自述的课 ≠ 本次导入的课（有人改名了）⇒ 当场拒，不猜（见下）；
        #   ② `state.json` 缺失/没 run_id 时，包自述的 run 名至少比「按文件名猜」准确。
        meta_raw = _read_json(base / IMPORT_META_NAME) if (base / IMPORT_META_NAME).is_file() else {}
        meta = meta_raw if str(meta_raw.get("magic") or "") == IMPORT_META_MAGIC else {}
        pkg_course = str(meta.get("course") or "")
        if course and pkg_course and pkg_course != course:
            raise ProtocolError(
                f"包内自述的课程（{pkg_course}，{IMPORT_META_NAME}）与本次导入的课程（{course}）"
                "不一致——拿 A 课的权重去评 B 课的语料，读数会看起来完全正常。"
                f"要么切到 {pkg_course} 再导，要么把文件名/控制台课程都改到 {pkg_course}"
            )
        run_id = str(st.get("run_id") or meta.get("run_id") or run_id)
        # state.json 里的 run_id 才是权威：与目录名（= 文件名猜的）不一致就落到它那个目录，
        # 否则同一个 run 的产物会分裂成两个目录（后一次导入又建一个）。
        if run_id != final.name:
            final = dest_root / re.sub(r"[^A-Za-z0-9._-]", "_", str(run_id))[:64]
        pkg_iters = list(iters)
        # 逐轮并进 final（**全程不出现目录改名/替换**，见 `_land_rounds` 的为什么）：轮次集合 =
        # 盘上已有的 ∪ 这个包的；同名轮先到者为准（`replace=True` 是人工裁决的逃生门，D5）。
        landed = _land_rounds(base, final, iters, replace=replace, log=log)
        _land_anchors(base, final, conflicts=landed["skipped_rounds"], log=log)
        # 读数从**包里**读（此刻 `base` 还在盘上）：`final/metrics.jsonl` 可能停在上一次导入的
        # 水位，只读它会让这一包的曲线静默进不了课程账本（控制台「各轮指标表」缺行）。
        n_eval, n_eval_sums = _merge_carried_eval_rows(base, dest_root)
        n_rows = _merge_carried_metric_rows(base, dest_root, run_id=final.name, log=log)
        # 体检在 `base` 还在盘上时做（读的是**包**里的字节：导入目录已被 merge 污染）。
        partial = int(st.get("last_it") or 0) > pkg_iters[-1]
        checks = _package_checks(
            base,
            pkg_iters,
            st,
            names=len(names),
            size=size,
            partial=partial,
            meta=meta,
            meta_raw=meta_raw,
        )
    finally:
        # 中转目录只用于解压，**永远不残留**（也不再有「改名成功/失败」这个中间态）。
        shutil.rmtree(work, ignore_errors=True)
    # 落地之后一律以**盘上真实**为准：早先导入留下的轮次仍在（先到者为准 ≠ 清空重来）。
    iters = _scan_iters(final)
    last_it = max(iters)
    final_ckpt = delta_files.find_existing(
        final / f"{ArtifactStore.IT_PREFIX}{last_it:03d}", "weights.json"
    ) or (final / f"{ArtifactStore.IT_PREFIX}{last_it:03d}" / "weights.json")
    log(
        f"[deliver] 导入完成：{final}（盘上 {len(iters)} 轮，it{iters[0]} → it{last_it}；"
        f"本包 it{pkg_iters[0]} → it{pkg_iters[-1]}：新增 {landed['kept_rounds']} 轮 / "
        f"跳过 {landed['skipped_rounds']} 轮 / 补件 {landed['healed_files']}，"
        f"状态 {st.get('state') or '?'}）"
    )
    if pkg_iters[-1] != last_it:
        log(
            f"[deliver] 注意：包内末轮 it{pkg_iters[-1]} ≠ 盘上末轮 it{last_it}"
            "（盘上有更靠后的轮次，先到者为准不清空）⇒ 评估用的是盘上末轮"
        )
    log(f"[deliver] 末轮权重：{final_ckpt}")
    failed = [c for c in checks if not c["ok"]]
    log(
        f"[deliver] 体检：{len(checks) - len(failed)}/{len(checks)} "
        + ("全绿" if not failed else "未过 " + "·".join(f"{c['id']}（{c['detail']}）" for c in failed))
    )
    return {
        "run_id": final.name,
        "dir": str(final),
        # 包体检（§6.2-1 / DoD-5c）：`{id, ok, detail}` 逐项；**报告不是门**（不阻断导入）。
        "checks": checks,
        "checks_ok": not failed,
        #: 半段快照（`state.json` 自报的末轮 > 包内末轮）——控制台顶栏那句「是否半段」的判据。
        "partial": partial,
        # 盘上真实（合并后）——`ckpt` 必须是盘上存在的那份权重（控制台拿它去跑 evalA）。
        "iters": iters,
        "last_it": last_it,
        "ckpt": str(final_ckpt),
        # 这个包自己带的那一段（与盘上集合不同时人对账用）。
        "package_iters": pkg_iters,
        "package_last_it": pkg_iters[-1],
        # D5 的读数（控制台 message / 日志用）：新增 / 因先到者为准跳过 / 补齐的件。
        "kept_rounds": int(landed["kept_rounds"]),
        "skipped_rounds": int(landed["skipped_rounds"]),
        "healed_files": int(landed["healed_files"]),
        "course": course,
        "course_in_name": name_course,
        # 包内自述（§6.2-2）：控制台据此提醒「包自述的课 ≠ 当前课」（**不静默切课**）——
        # 与课程不一致的包在前面已经被拦下，这里只是把**同一份事实**回传上屏。
        "package_course": pkg_course,
        "package_run_id": str(meta.get("run_id") or ""),
        "package_source": str(meta.get("source") or ""),
        "state": str(st.get("state") or ""),
        "rows": len(iters),
        "eval_rows": n_eval,
        "eval_summaries": n_eval_sums,
        "metric_rows": n_rows,
        "source_zip": str(src),
        "bytes": size,
    }


def _ledger_iter_its(ledger: Path) -> set[int]:
    """课程账本里已有的 `iteration.iter` 集合（读不到 / 半截行 ⇒ 跳过；**永不抛**）。

    为什么单独抽出来（2026-10-16，回灌腿 B0）：导入与**回灌**两条腿都要回答
    「本机账本里已经有哪些轮」——一份实现它们才不会漂（漏判的代价是重复回调或漏回灌）。
    """
    seen: set[int] = set()
    try:
        text = ledger.read_text(encoding="utf-8")
    except OSError:
        return seen
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            prev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(prev, dict) or prev.get("event") != "iteration":
            continue
        raw = prev.get("iter")
        if not isinstance(raw, (int, float, str)) or isinstance(raw, bool):
            continue
        try:
            seen.add(int(raw))
        except (TypeError, ValueError):
            continue
    return seen


def import_from_run(
    run_dir: str | Path,
    dest_root: str | Path,
    *,
    course: str = "",
    run_id: str = "",
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """**回灌腿**（B0，`plan/offline-open-ended-iters-and-loud-export` §3.2.1 定案①）：
    把 **hub 已收的那个 run 目录** 导入成控制台可评估的产物记录。

    为什么必须有它：「无限轮」的下一段包只能从**本机账本**起（`plan.start_it = 本机 last_it`），
    而云机跑完那一段时控制台**不在场**（产物在 hub 手里）—— 没有这条腿，段末之后本机
    账本/指标停在段起点 ⇒ 规则表判定「包不比权重旧」⇒ 不出下一段（J2 只能半自动）。

    与 `import_deliver_zip` 的差别：**不解 zip**。源就是 hub 落地的那份目录
    （`<traj>/deliver/<run_id>/` 交付镜像，或 `<traj>/remote-jobs/offline/<run_id>/`
    的补传原件 —— 两者同形状：`it-NNN/{weights.json|weights.delta,opt,row.json}`）。

    幂等（plan §3.2.1 的「必写」）：
      * 轮次目录是**不可变快照** ⇒ 只补缺的（已有的一律不重写）；
      * 账本行按 `it` 去重（与 `import_deliver_zip` 同表同规则）；
      * 判据「本机账本已到 ≥ 本段末轮」∧「轮次目录已齐」⇒ **不重复导**，
        回 `{imported: False, ...}`（调用方据此说一句「无需回灌」而不是再烧一遍）。
    """
    src = Path(run_dir)
    if not src.is_dir():
        raise ProtocolError(f"回灌源目录不存在: {src}")
    src_iters = _scan_iters(src)
    if not src_iters:
        raise ProtocolError(
            f"回灌源里没有 {ArtifactStore.IT_PREFIX}NNN/weights.json（或压缩版 "
            f"weights.delta）: {src}"
        )
    dest = Path(dest_root)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(run_id or src.name))[:64]
    if not name:
        raise ProtocolError(f"回灌的 run 名非法: {run_id!r} / 源目录名 {src.name!r}")
    final = dest / name
    dest.mkdir(parents=True, exist_ok=True)
    # 取件：源不在导入根下（补传原件）⇒ 与**人工导入腿共用同一份判据**（`_land_rounds`）：
    # 缺的轮与缺的件补上、同名不同字节保留先到的那一份（轮次是不可变快照）。
    # 取件放在「无需回灌」短路**之前**：短路只决定「要不要并账本」，不该让缺件永远补不上。
    landed = {"kept_rounds": 0, "skipped_rounds": 0, "healed_files": 0}
    readings_src = final
    if src.resolve() != final.resolve():
        landed = _land_rounds(src, final, src_iters, log=log)
        _land_anchors(src, final, conflicts=landed["skipped_rounds"], log=log)
        readings_src = src
        if landed["kept_rounds"] or landed["healed_files"] or landed["skipped_rounds"]:
            log(
                f"[deliver] 回灌取件：{src} → {final}（新增 {landed['kept_rounds']} 轮、"
                f"补件 {landed['healed_files']}、跳过 {landed['skipped_rounds']}；已有轮次不重写）"
            )
    iters = _scan_iters(final)
    last_it = max(iters)
    ledger = dest.parent / "training_log.jsonl"
    seen_its = _ledger_iter_its(ledger)
    local_last = max(seen_its) if seen_its else 0
    if local_last >= last_it and set(iters) <= seen_its:
        log(
            f"[deliver] 无需回灌：本机账本已到 it{local_last}（≥ 段末 it{last_it}）"
            "——空转不烧一次导入"
        )
        return {
            "run_id": final.name,
            "dir": str(final),
            "iters": iters,
            "last_it": last_it,
            "ckpt": str(final / f"{ArtifactStore.IT_PREFIX}{last_it:03d}" / "weights.json"),
            "course": course,
            "state": "",
            "rows": len(iters),
            "metric_rows": 0,
            "eval_rows": 0,
            "eval_summaries": 0,
            "imported": False,
            "reason": f"本机账本已到 it{local_last}（≥ 段末 it{last_it}）",
            "source_run": str(src),
            # 取件（含补缺件）在短路**之前**做过：两个读数照报，否则「补了件但账本已齐」看不出。
            "kept_rounds": int(landed["kept_rounds"]),
            "skipped_rounds": int(landed["skipped_rounds"]),
            "healed_files": int(landed["healed_files"]),
        }
    final_ckpt = delta_files.find_existing(
        final / f"{ArtifactStore.IT_PREFIX}{last_it:03d}", "weights.json"
    ) or (final / f"{ArtifactStore.IT_PREFIX}{last_it:03d}" / "weights.json")
    n_eval, n_eval_sums = _merge_carried_eval_rows(readings_src, dest)
    n_rows = _merge_carried_metric_rows(readings_src, dest, run_id=final.name, log=log)
    log(
        f"[deliver] 回灌完成：{final}（{len(iters)} 轮，it{iters[0]} → it{last_it}）"
        f"——本机账本/指标已推进到段末（下一段包从它起）"
    )
    return {
        "run_id": final.name,
        "dir": str(final),
        "iters": iters,
        "last_it": last_it,
        "ckpt": str(final_ckpt),
        "course": course,
        "state": str(_read_json(final / ArtifactStore.STATE_NAME).get("state") or ""),
        "rows": len(iters),
        "metric_rows": n_rows,
        "eval_rows": n_eval,
        "eval_summaries": n_eval_sums,
        "imported": True,
        "reason": "",
        "source_run": str(src),
        "kept_rounds": int(landed["kept_rounds"]),
        "skipped_rounds": int(landed["skipped_rounds"]),
        "healed_files": int(landed["healed_files"]),
    }


def _merge_carried_eval_rows(src_root: Path, dest_root: Path) -> tuple[int, int]:
    """把产物包里带的云机 A 层评估（逐局行 + summary）并进课程账本，返回
    `(新增逐局行数, 新增 summary 行数)`。

    读的是**包里**那一份（不是导入目录里那份）：导入是逐轮先到者为准的 merge，
    `final/eval_log.jsonl` 可能停在上一次导入的水位（plan §8.2-1）。

    为什么在导入时并：产物包是**云机评过的读数回到本机的唯一载体**（连 hub 都不在场时
    也成立），而控制台/门判只看 `tmp/<课程>/eval_log.jsonl`。盘上布局就是它的位置：
    导入根是 `tmp/<课程>/deliver`，账本在它的同级（`tmp/<课程>/eval_log.jsonl`）。

    summary 一并并进去（`biz.eval_rows.merge_eval_rows` 的单调规则）：控制台的 eval 列 /
    弹窗 / 开课回执与门判据都只认 summary 行，纯云腿没人替它算（2026-09-23）。

    任何失败都只记一笔：导入的主价值是「权重可评估」，少一份读数不是导入失败。
    """
    src_jsonl = src_root / ArtifactStore.EVAL_LOG_NAME
    if not src_jsonl.exists():
        return (0, 0)
    try:
        from worker.eval_rows import merge_eval_rows

        n_games, n_sums = merge_eval_rows(src_jsonl, Path(dest_root).parent / "eval_log.jsonl")
    except Exception as e:  # rl 包不在（截断快照）/磁盘错——不拖垮导入
        print(f"[deliver] 评估账本并入失败（忽略）: {type(e).__name__}: {e}", flush=True)
        return (0, 0)
    if n_games or n_sums:
        print(
            f"[deliver] 云机评估并入课程账本：+{n_games} 逐局 / +{n_sums} summary"
            f"（源 {src_jsonl.name} → {Path(dest_root).parent / 'eval_log.jsonl'}）",
            flush=True,
        )
    return (int(n_games), int(n_sums))


def _merge_carried_metric_rows(
    src_root: Path, dest_root: Path, *, run_id: str, log=lambda _m: None
) -> int:
    """把产物包里的逐轮训练行（`metrics.jsonl`）并进课程账本，返回新增行数。

    源是**包**（`src_root`），不是导入后的目录：导入逐轮先到者为准 ⇒ `final/metrics.jsonl`
    可能停在上一次导入的水位，只读它会让后到的包那几轮的曲线静默进不了课程账本。
    `run_id` = 落位目录名（账本行认它，与读数来源目录无关）。

    **为什么必须并**：控制台的「各轮指标表」只读课程账本（`tmp/<课程>/training_log.jsonl`
    的 `iteration` 事件，见 `dashboard/src/server/api/state-view.ts` → `readIterMetrics`）
    ——导入的产物不进账本时，权重/优化器都在盘上、末轮也能评，但**指标表一行都不显示**
    （用户 2026-09-22 实测：导完看不出这条腿跑到哪）。

    两件事，分开幂等：

      * **课程账本行**：由 `remote.artifacts.ledger_row_from_metrics` 搬运（与实时补传
        `hub.server` 共用同一张翻译表），打上 `source="deliver_import"`；账本里已有
        同 it 的 `iteration` 行就跳过（重复导入不写第二遍）。搬不到的（旧包没有
        `dimMeans`/`scoreStats`）**留空**——写 0 会被读成「真的零击杀」。
      * **逐局画像**：`it<N>/per-game.json`（读方优先它，见下）。控制台的「耗时/击杀/残血/
        道具」是**逐局**聚合出来的，而单局 manifest 只在跑它的那台机器上（云机轮末就被
        prune 删了）⇒ 产物行里的 `perGame` 必须在导入时落到读方能找到的地方。
        **总是写**（与账本行是否已存在无关：同 run 的包可能是升级后才带上 perGame 的）。

    任何失败只记一笔：导入的主价值是「权重可评估」，少一份曲线不是导入失败。
    """
    src_jsonl = src_root / ArtifactStore.METRICS_NAME
    if not src_jsonl.exists():
        return 0
    traj = Path(dest_root).parent
    dest = traj / "training_log.jsonl"
    try:
        rows: list[dict] = []
        for line in src_jsonl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                got = json.loads(line)
            except ValueError:
                continue  # 半截行（被 kill）——跳过，不毒死导入
            if isinstance(got, dict):
                rows.append(got)
        events = [
            ev
            for ev in (
                ledger_row_from_metrics(r, run_id=run_id, source="deliver_import")
                for r in rows
            )
            if ev is not None
        ]
        n_pg = write_per_game_files(traj, rows)
        if events:
            # 账本已有轮次（与回灌腿共用一份实现——两份必然漂，而漂的代价是重复写行）
            seen = _ledger_iter_its(dest)
            fresh = [ev for ev in events if int(ev["iter"]) not in seen]
            if fresh:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "a", encoding="utf-8") as f:
                    for ev in fresh:
                        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                log(
                    f"[deliver] 逐轮指标已并入课程账本：+{len(fresh)} 行（it{fresh[0]['iter']} → "
                    f"it{fresh[-1]['iter']} → {dest}）——控制台「各轮指标表」读的就是它"
                )
            else:
                log(f"[deliver] 课程账本已有这些轮次（{len(events)} 行）——不重复写")
        else:
            fresh = []
        if n_pg:
            log(
                f"[deliver] 逐局画像已落盘：{n_pg} 轮 → "
                f"{traj}/it<N>/per-game.json（控制台的耗时/击杀/残血/道具列读它）"
            )
    except Exception as e:  # 磁盘/权限/格式——不拖垮导入
        print(f"[deliver] 训练账本并入失败（忽略）: {type(e).__name__}: {e}", flush=True)
        return 0
    return len(fresh)


def write_per_game_files(traj: Path, rows: list[dict]) -> int:
    """把产物行的 `perGame`（逐局压缩画像）写成 `it<N>/per-game.json`，返回写了多少轮。

    位置与读方约定：`dashboard/src/server/iters.ts` 先看 `it<N>/per-game.json`，没有才去扫
    `it<N>/**/manifest.json`（本机/在线腿的老路径）。**单个文件而非 328 个小文件**：
    一盘 300 轮 × 328 局 = 10 万个文件对谁都是负担，而读方本来就要把一轮的局凑齐再聚合。
    没带 `perGame` 的行（旧包/旧代码）直接跳过。
    """
    n = 0
    for r in rows:
        raw_it = r.get("it")
        if not isinstance(raw_it, (int, float, str)):
            continue
        try:
            it = int(raw_it)
        except (TypeError, ValueError):
            continue
        pg = r.get("perGame")
        if it < 1 or not isinstance(pg, list) or not pg:
            continue
        it_dir = traj / f"it{it}"
        it_dir.mkdir(parents=True, exist_ok=True)
        (it_dir / ArtifactStore.PER_GAME_NAME).write_text(
            json.dumps(pg, ensure_ascii=False), encoding="utf-8"
        )
        n += 1
    return n


def _ledger_tail_is_run_complete(ledger: Path, it: int) -> bool:
    """账本尾行是否已是同一轮的 `run_complete`（幂等：重导/重转交不叠行）。"""
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return False
        if not isinstance(row, dict) or row.get("event") != "run_complete":
            return False
        return row.get("iter") == int(it)
    return False


def write_run_complete_for_run(
    dest_root: str | Path,
    run_id: str,
    *,
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """为**已导入**的 run 落 `run_complete`（T6，`plan/auto-offline-handoff §3.6`）。

    为什么这是**第二趟调用**而不是导入里顺手写：判决在控制台（导入 payload 的 `run_id`
    必须与 hub 段末摘要的 `run_id` 匹配 ∧ 摘要自报 `end_it_reached`——半段导入不得亮横幅），
    而落账在 python（`run_complete` 的唯一写者是 `worker/events.py`，TS 复刻 = 第二个实现源）。

    `it`/`iters` 只用产物目录里的事实：`last_it` = 最大 `it-NNN`；`iters` = `plan.json`
    的计划终点（旧包没有 plan.json ⇒ 退到 `last_it`，横幅照旧能显示，不编造更大数字）。
    幂等：账本尾行已是同轮的 `run_complete` 就不叠行（重导/重转交是常态）。
    """
    root = Path(dest_root)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(run_id))[:64]
    run_dir = root / safe
    if not safe or not run_dir.is_dir():
        raise ProtocolError(f"产物目录不存在: {run_dir}（先导入再转交 run_complete）")
    iters = _scan_iters(run_dir)
    if not iters:
        raise ProtocolError(f"产物目录里没有 it-NNN/weights.json: {run_dir}")
    last_it = max(iters)
    plan = _read_json(run_dir / "plan.json")
    raw_end = plan.get("end_it")
    end_it = int(raw_end) if isinstance(raw_end, int) and not isinstance(raw_end, bool) else 0
    planned = end_it if end_it > 0 else last_it
    # 课程账本与 `_merge_carried_metric_rows` 同一位（导入根的同级）；不在那里就建同级。
    ledger = root.parent / "training_log.jsonl"
    if _ledger_tail_is_run_complete(ledger, last_it):
        log(f"[deliver] 账本尾行已是 it{last_it} 的 run_complete——不重复写（幂等）")
        return {"run_id": safe, "it": last_it, "iters": planned, "written": False}
    # 延迟 import：与 `_merge_carried_eval_rows` 同源纪律（截断快照里 rl 包可能不在）。
    from worker.events import write_run_complete

    write_run_complete(ledger, last_it, planned, RUN_COMPLETE_REASON)
    log(
        f"[deliver] run_complete 已落账：it{last_it}/{planned} → {ledger}"
        "（控制台矩阵的「已完成」灰横幅下一拍上屏）"
    )
    return {"run_id": safe, "it": last_it, "iters": planned, "written": True}


def main(argv: list[str] | None = None) -> int:
    ap = house_parser(description="导入训练产物 zip（deliver-<课程>.zip）")
    ap.add_argument("--zip", help="产物 zip 路径（不跑 `--end-it-reached` / `--from-run` 时必填）")
    # 回灌腿（B0，2026-10-16，plan/offline-open-ended-iters-and-loud-export §3.2.1）：源不是
    # 人搬来的 zip，而是 **hub 已收的那个 run 目录**（交付镜像或补传原件）。
    ap.add_argument(
        "--from-run",
        default="",
        help="（B0 回灌）直接导入一个已落地的 run 目录（hub 已收的产物；不解 zip）",
    )
    ap.add_argument("--dest", required=True, help="落地根目录（每个 run 一个子目录）")
    ap.add_argument("--course", default="", help="控制台当前课程（对账文件名；可空）")
    # 逃生门（D5）：默认「同名轮先到者为准」，人工裁决（确认后到的那份才是真的）时才覆盖。
    ap.add_argument(
        "--replace",
        action="store_true",
        help="（--zip）人工裁决：同名轮用**包里**的字节覆盖盘上先到的那一份",
    )
    ap.add_argument("--json-only", action="store_true", help="只打机器可读那一行")
    # T6 转交（控制台第二趟调用）：为**已导入**的 run 落 run_complete，不重新解包。
    ap.add_argument(
        "--end-it-reached",
        action="store_true",
        help="（T6）为已导入的 run 落 run_complete（需 --run-id；不重新解包）",
    )
    ap.add_argument("--run-id", default="", help="配合 --end-it-reached：已导入的 run_id")
    args = ap.parse_args(argv)
    log = (lambda _m: None) if args.json_only else (lambda m: print(m, flush=True))
    if args.end_it_reached:
        if not args.run_id:
            print("[deliver] --end-it-reached 需要 --run-id", file=sys.stderr, flush=True)
            return 2
        try:
            got = write_run_complete_for_run(args.dest, args.run_id, log=log)
        except ProtocolError as e:
            print(f"[deliver] run_complete 未落账：{e}", file=sys.stderr, flush=True)
            return 2
        print(IMPORT_JSON_MARK + json.dumps(got, ensure_ascii=False), flush=True)
        return 0
    if args.from_run:
        try:
            got = import_from_run(
                args.from_run,
                args.dest,
                course=args.course,
                run_id=args.run_id,
                log=log,
            )
        except ProtocolError as e:
            print(f"[deliver] 回灌失败：{e}", file=sys.stderr, flush=True)
            return 2
        print(IMPORT_JSON_MARK + json.dumps(got, ensure_ascii=False), flush=True)
        return 0
    if not args.zip:
        ap.error("--zip 必填（除非 --end-it-reached / --from-run）")
        return 2
    try:
        got = import_deliver_zip(
            args.zip, args.dest, course=args.course, replace=bool(args.replace), log=log
        )
    except ProtocolError as e:
        print(f"[deliver] 导入失败：{e}", file=sys.stderr, flush=True)
        return 2
    print(IMPORT_JSON_MARK + json.dumps(got, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    run_main(main)
