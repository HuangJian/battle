"""hub/deltas.py — 回传 **delta 的 hub 侧就地落地**（2026-10-15 用户口径）。

## 口径（一句话）

**delta 只活在「回传进程 + hub 落地器」之间**：云机（PPO / 自主腿）按形态位回传压缩版，
hub **收到就地在 hub 进程里重建为整份**，此后 hub 树（回传树 / 交付镜像 / 归档 / 活动指针）
里的一切都是整份；派发侧因此永远只发整份（云机已有就不传 —— 靠既有的
`init_weights_fp` / `opt_sha` 指纹 + 内容寻址 `blob_cache`，本来就在）。trainer / TS 两端
**看不到 delta**。

## 为什么 hub 能算（别被 `test_hub_import_graph` 吓住）

那条门禁钉的是**启动期 import 闭包**（hub-server 起来时不得拉 numpy/torch —— 2026-10-14
事故），而不是「hub 永远不许用它们」：hub-server 由控制台用 venv python 起
（`dashboard/src/stack/specs.ts` 的 `resolveVenvPython()`），numpy/torch 运行时都在。
所以这里一律**函数体内惰性 import**：启动闭包干净，落地时才算。

代价（实测，`tmp/_delta_bench.py`）：weights 拆分/重建各 ~3ms、opt 重建 ~12ms（含
`torch.load` 基准）、torch 冷 import ~1.8s（每进程一次）。相对一轮 PPO 可忽略。

## 落地失败怎么办（**绝不静默、绝不丢字节**）

容器要的 `base_sha` 在 hub 侧找不到（例：某轮回传漏了、链断）⇒ 原件**原样留在**
`*.delta`（字节是这一轮唯一的证据），只在返回值与日志里响亮记一笔。这种情况下那一轮
**不算锚点**（`hub/queue_resume.resume_anchor` 只发整份）——交给人工/清扫工具（
`tools/hub_delta_sweep.py`：能落就落，落不了才按用户口径删）。
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import remote.delta_files as delta_files

__all__ = ["land_round"]

#: 三件里哪两件有压缩版（`row.json` 没有）。
_PARTS: tuple[tuple[str, str], ...] = (("weights.json", "weights"), ("opt.tar", "opt"))


def land_round(it_dir: str | Path, *, traj: str | Path | None, course: str, log=print) -> dict:
    """把 `it_dir` 里的压缩版就地落成整份（`weights.delta` → `weights.json`、`opt.delta` → `opt.tar`）。

    返回 `{part: "full" | "landed" | "failed" | "absent", "note": <失败原因>}`（`note` 空 = 全好）：

      * `full`   —— 本来就不是容器（零成本：只读 4 字节）；
      * `landed` —— 就地重建成功（`remote.weights_standard` / `remote.opt_standard` 的落地器，
        「基准逐字节对上才算数」的那套；候选根含 traj 树与课程归档 ⇒ 上一轮/起点那份找得到）；
      * `failed` —— 基准不在 hub 侧（**原件保留**，日志/返回值里响亮说清）；
      * `absent` —— 这一轮没有这件。

    为什么在 hub 里做：离线段（纯自主腿）根本没有别的「接收侧进程」可以做这件事——hub 是
    唯一恒在的接收者。published 腿那边早就是同一形状（`remote/hub_client.verify_and_land`
    在接收侧落地），这里是把它补齐到离线段。
    """
    it_dir = Path(it_dir)
    out: dict = {"note": ""}
    for name, part in _PARTS:
        src = delta_files.find_existing(it_dir, name)
        if src is None:
            out[part] = "absent"
            continue
        if not delta_files.any_container_file(src):
            out[part] = "full"
            continue
        try:
            _land_one(part, src, traj=traj, course=course, log=log)
            out[part] = "landed"
        except Exception as e:  # 落地失败不该丢字节：原件保留 + 响亮
            out[part] = "failed"
            one = f"{part}: {type(e).__name__}: {e}"
            # 两件都失败时**都要说**（覆盖 = 只报最后一件，另一件的原因就没人知道了）。
            out["note"] = f"{out['note']}；{one}" if out["note"] else one
            log(f"[hub] 回传轮 {it_dir.name} 的 {part} 就地落地失败（原件保留在 {src.name}）：{one}")
    return out


def _land_one(part: str, src: Path, *, traj: str | Path | None, course: str, log) -> None:
    """单件落地（惰性 import 两个落地器：hub 的启动闭包不得拉 numpy/torch，见模块头）。"""
    traj_root = str(traj) if traj is not None else None
    extra = (Path(traj),) if traj is not None else ()
    if part == "weights":
        import remote.weights_standard as weights_standard

        weights_standard.ensure_standard_weights(
            src, course=course, traj_root=traj_root, extra_roots=extra, log=log
        )
        return
    import remote.opt_standard as opt_standard

    opt_standard.ensure_standard_opt(
        src, traj_root=traj_root, extra_roots=extra, log=log
    )


def land_into_stage(raw_by_part: dict[str, bytes], *, traj: str | Path | None, course: str, log=print) -> dict:
    """**受理路径专用**：把「还没落位的原始字节」先落到暂存目录、就地落地，返回整份字节。

    返回 `{"weights.json"|"opt.tar": 整份字节（缺席 = `b""`）, "landed": {件: 状态},
    "note": str（失败原因，两件都失败时以「；」串起来）, "raw": {件: 原件字节}}`。
    为什么先落暂存再落地：`it-NNN/` 里那份是**不可变快照**（首写锁定 + 409 冲突那套），
    而落地要先把容器写到盘上（落地器的入口是路径，不是字节）⇒ 先在暂存目录里算完整份，
    再走原有的幂等/冲突判据，最后原子提交。

    暂存目录在 `tempfile.mkdtemp`（系统临时区）——不往课程树里塞 `.stage-*` 垃圾；
    落地器的候选根不受影响（它靠 `traj_root`/额外根找基准，不靠暂存目录的位置）。
    """
    stage = Path(tempfile.mkdtemp(prefix="hub-land-"))
    # 两件的键**恒定存在**（缺席 = 空字节）：调用方按 `staged["opt.tar"]` 取，不必各自判缺失。
    out: dict = {"landed": {}, "note": "", "weights.json": b"", "opt.tar": b"", "raw": {}}
    try:
        for name, part in _PARTS:
            raw = raw_by_part.get(part) or b""
            if not raw:
                continue
            p = stage / delta_files.named_for(name, raw)
            p.write_bytes(raw)
        res = land_round(stage, traj=traj, course=course, log=log)
        out["landed"] = {k: v for k, v in res.items() if k != "note"}
        out["note"] = res.get("note", "")
        for name, part in _PARTS:
            raw = raw_by_part.get(part) or b""
            if not raw:
                continue
            whole = stage / name  # 落地成功时是整份；失败/缺席时回落到原件
            out[name] = whole.read_bytes() if whole.is_file() else raw
        # 原件（压缩版）也要留给 hub 树：名字自述形态、两版都留（与两个落地器同一条约定）
        out["raw"] = {
            part: raw_by_part.get(part) or b""
            for _name, part in _PARTS
            if raw_by_part.get(part)
        }
        return out
    finally:
        shutil.rmtree(stage, ignore_errors=True)
