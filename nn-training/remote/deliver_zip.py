"""remote/deliver_zip.py —— 训练产物 zip（`deliver-<课程>.zip`）的**导入**入口。

控制台上的那条腿：导出任务包 → 上传 Kaggle/Colab 跑完 → 把产出的 zip 交回来 →
导入并**自动按课程配置跑 A 层评估**。本模块只管「把 zip 变成一份可评估的产物目录」，
评估由控制台接着发起（`rl/eval_a_once.py`，复用现有 evalA 动作）。

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
     这是最贵的一种错。

CLI（控制台调用；stdout 末行是机器可读的 `DELIVER_IMPORT_JSON={...}`）：

    python -m remote.deliver_zip --zip <deliver-X.zip> --dest <tmp/<course>/deliver> \\
        [--course X] [--json-only]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

# 产物账本行 → 课程账本行的搬运**只在 remote.artifacts 实现一份**（人工导入与实时补传共用）：
# 两份翻译必然漂开，而「两腿同字段」正是那张表存在的意义。
from remote.artifacts import ArtifactStore, ledger_row_from_metrics
from remote.bundle import BUNDLE_INDEX, safe_extract_zip
from remote.protocol import ProtocolError

#: 机器可读结果的标记（控制台按它切 stdout 的末行；前面的人读日志随便打）。
IMPORT_JSON_MARK = "DELIVER_IMPORT_JSON="
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
    # 课程名 = 目录名的安全子集（与 dashboard 的 validateCourseName 同精神）
    return name if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) else ""


def _scan_iters(root: Path) -> list[int]:
    """产物目录里的轮次（`it-NNN/weights.json` 存在者，升序）。"""
    out: list[int] = []
    for d in sorted(root.glob(f"{ArtifactStore.IT_PREFIX}*")):
        if not d.is_dir():
            continue
        name = d.name[len(ArtifactStore.IT_PREFIX) :]
        if not name.isdigit():
            continue
        if (d / "weights.json").exists():
            out.append(int(name))
    return out


def _read_json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def import_deliver_zip(
    zip_path: str | Path,
    dest_root: str | Path,
    *,
    course: str = "",
    log=lambda msg: print(msg, flush=True),
) -> dict:
    """把产物 zip 解到 `dest_root/<run_id>/`，返回导入句柄 dict。

    返回：`{run_id, dir, iters, last_it, ckpt, course, course_in_name, state, source_zip}`；
    `ckpt` = 末轮权重路径（控制台拿它去跑 evalA）。任一门不过抛 `ProtocolError`，
    且**不会留下半截目录**（先解到 `<dest>/<run_id>.tmp-<pid>` 再原子改名）。
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
                f"这个包里没有 {ArtifactStore.IT_PREFIX}NNN/weights.json——不是训练产物 zip"
                f"（包内顶层: {sorted(p.name for p in list(base.iterdir())[:8])}）"
            )
        st = _read_json(base / ArtifactStore.STATE_NAME)
        run_id = str(st.get("run_id") or run_id)
        # state.json 里的 run_id 才是权威：与目录名（= 文件名猜的）不一致就改名落地，
        # 否则同一个 run 的产物会分裂成两个目录（后一次导入又建一个）。
        if run_id != final.name:
            final = dest_root / re.sub(r"[^A-Za-z0-9._-]", "_", str(run_id))[:64]
        last_it = max(iters)
        if final.exists():
            shutil.rmtree(final, ignore_errors=True)
        # 原子收尾：把下探到的那层（通常就是 work 自己）改名成最终目录——中途失败只会
        # 留下一个 `.tmp-<pid>` 壳，永远不会留下半截的正式目录（后续读方按目录名认 run）。
        base.rename(final)
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)  # 套层时剩下的空壳
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    final_ckpt = final / f"{ArtifactStore.IT_PREFIX}{last_it:03d}" / "weights.json"
    log(
        f"[deliver] 导入完成：{final}（{len(iters)} 轮，it{iters[0]} → it{last_it}，"
        f"状态 {st.get('state') or '?'}）"
    )
    log(f"[deliver] 末轮权重：{final_ckpt}")
    n_eval, n_eval_sums = _merge_carried_eval_rows(final, dest_root)
    n_rows = _merge_carried_metric_rows(final, dest_root, log=log)
    return {
        "run_id": final.name,
        "dir": str(final),
        "iters": iters,
        "last_it": last_it,
        "ckpt": str(final_ckpt),
        "course": course,
        "course_in_name": name_course,
        "state": str(st.get("state") or ""),
        "rows": len(iters),
        "eval_rows": n_eval,
        "eval_summaries": n_eval_sums,
        "metric_rows": n_rows,
        "source_zip": str(src),
        "bytes": size,
    }


def _merge_carried_eval_rows(final: Path, dest_root: Path) -> tuple[int, int]:
    """把产物包里带的云机 A 层评估（逐局行 + summary）并进课程账本，返回
    `(新增逐局行数, 新增 summary 行数)`。

    为什么在导入时并：产物包是**云机评过的读数回到本机的唯一载体**（连 hub 都不在场时
    也成立），而控制台/门判只看 `tmp/<课程>/eval_log.jsonl`。盘上布局就是它的位置：
    导入根是 `tmp/<课程>/deliver`，账本在它的同级（`tmp/<课程>/eval_log.jsonl`）。

    summary 一并并进去（`rl.eval_local.merge_eval_rows` 的单调规则）：控制台的 eval 列 /
    弹窗 / 开课回执与门判据都只认 summary 行，纯云腿没人替它算（2026-09-23）。

    任何失败都只记一笔：导入的主价值是「权重可评估」，少一份读数不是导入失败。
    """
    src_jsonl = final / ArtifactStore.EVAL_LOG_NAME
    if not src_jsonl.exists():
        return (0, 0)
    try:
        from rl.eval_local import merge_eval_rows

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
    final: Path, dest_root: Path, *, log=lambda _m: None
) -> int:
    """把产物包里的逐轮训练行（`metrics.jsonl`）并进课程账本，返回新增行数。

    **为什么必须并**：控制台的「各轮指标表」只读课程账本（`tmp/<课程>/training_log.jsonl`
    的 `iteration` 事件，见 `dashboard/src/server/api/state-view.ts` → `readIterMetrics`）
    ——导入的产物不进账本时，权重/优化器都在盘上、末轮也能评，但**指标表一行都不显示**
    （用户 2026-09-22 实测：导完看不出这条腿跑到哪）。

    两件事，分开幂等：

      * **课程账本行**：由 `remote.artifacts.ledger_row_from_metrics` 搬运（与实时补传
        `remote/hub_server` 共用同一张翻译表），打上 `source="deliver_import"`；账本里已有
        同 it 的 `iteration` 行就跳过（重复导入不写第二遍）。搬不到的（旧包没有
        `dimMeans`/`scoreStats`）**留空**——写 0 会被读成「真的零击杀」。
      * **逐局画像**：`it<N>/per-game.json`（读方优先它，见下）。控制台的「耗时/击杀/残血/
        道具」是**逐局**聚合出来的，而单局 manifest 只在跑它的那台机器上（云机轮末就被
        prune 删了）⇒ 产物行里的 `perGame` 必须在导入时落到读方能找到的地方。
        **总是写**（与账本行是否已存在无关：同 run 的包可能是升级后才带上 perGame 的）。

    任何失败只记一笔：导入的主价值是「权重可评估」，少一份曲线不是导入失败。
    """
    src_jsonl = final / ArtifactStore.METRICS_NAME
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
                ledger_row_from_metrics(r, run_id=final.name, source="deliver_import")
                for r in rows
            )
            if ev is not None
        ]
        n_pg = write_per_game_files(traj, rows)
        if events:
            seen: set[int] = set()
            if dest.exists():
                for line in dest.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        prev = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(prev, dict) or prev.get("event") != "iteration":
                        continue
                    raw_prev_it = prev.get("iter")
                    if not isinstance(raw_prev_it, (int, float, str)):
                        continue
                    try:
                        seen.add(int(raw_prev_it))
                    except (TypeError, ValueError):
                        continue
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导入训练产物 zip（deliver-<课程>.zip）")
    ap.add_argument("--zip", required=True, help="产物 zip 路径")
    ap.add_argument("--dest", required=True, help="落地根目录（每个 run 一个子目录）")
    ap.add_argument("--course", default="", help="控制台当前课程（对账文件名；可空）")
    ap.add_argument("--json-only", action="store_true", help="只打机器可读那一行")
    args = ap.parse_args(argv)
    try:
        got = import_deliver_zip(
            args.zip,
            args.dest,
            course=args.course,
            log=(lambda _m: None) if args.json_only else (lambda m: print(m, flush=True)),
        )
    except ProtocolError as e:
        print(f"[deliver] 导入失败：{e}", file=sys.stderr, flush=True)
        return 2
    print(IMPORT_JSON_MARK + json.dumps(got, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
