"""remote/hub/queue_resume.py — 续跑锚点 · 半离线/全离线补传产物 · 课程路径。

`_HubQueue` 的七个域混入之一（S4 第十五刀）。它回答的是「这门课现在**能接上哪一轮**」：

* `resume_sources` —— 两个来源、同一台机器：云机**自回传**落下的
  `<job_root>/offline/<run_id>/it-NNN/`，与控制台「导入产物」解出的
  `<traj>/<课>/deliver/<run_id>/it-NNN/`（`remote.deliver_zip` 的落地布局，与回传**同一
  形状**，所以两边判据能共用）。两个来源都要：用户口径是「云机回传**或者**人工导入后」再领
  任务都要能接上——只认回传就漏了手动那条路，而手动那条恰恰是 hub 不在场时的唯一路。
* `resume_anchor` —— 最新一轮**同轮齐全**的锚点（`None` = 没有可交回的进度）。**绝不**用
  「部分齐全」的轮次凑数：那会静默丢掉 Adam 动量或那一轮的指标。
* `merge_eval_rows` —— 把补传来的云机 A 层评估行并进**课程账本** `eval_log.jsonl`。为什么
  hub 要做：那是控制台/门判唯一读的账本，而云机那侧只看得见自己的产物目录。
* `offline_progress` / `locate_offline_course` / `store_offline_*` —— 补传的观测与落位。

## 「归不到」为什么是 `None` 而不是空串

单课程队列（与旧单课程 hub）的课程名**就是空串**（`tmp/nocourse` 那套约定）。空串兼作缺失
值会让单课程下的每一次补传都 400（2026-09-18 实测）。同一条理由也写在 `course_of`。

## 依赖方向

`queue_resume → {common.protocol, remote.hub.store}`（向下）。**唯一一处 `rl` 引用**
（`merge_eval_rows` 里的 `from rl.eval_local import append_eval_rows`）是**延迟** import，
且是本簇原样搬过来的——`hub_server` 里它本来就在函数体内（`assert_remote_module` 的
「传输/落盘层 L2-pure」那条口径因此对它不适用，守卫按「延迟 + 只此一处」正面钉住）。

## `traj_root` 推不出来时是 `None`

发现模式 = `--traj-root`；单课程模式由 `--job-root`（= `<traj>/<课>/remote-jobs`）回推两级。
推不出来（测试里的裸 job_root）⇒ `None`，调用方据此**拒服务**而不是猜路径。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from common.protocol import ProtocolError, sanitize_run_id
from remote.hub.queue_peer import QueuePeer
from remote.hub.store import _JobStore


class QueueResumeMixin(QueuePeer):
    """域混入：见模块头部。"""

    # ---- 由组合类 `__init__` / 兄弟簇提供（混入只见 `self`；声明一律是裸注解）----
    _discover_root: Path | None
    _order: list[str]
    _solo: _JobStore | None
    _stores: dict[str, _JobStore]

    def traj_root(self) -> Path | None:
        """课程根目录（`<traj>/<课>/{remote-jobs,training_log.jsonl}` 的 `<traj>`）。

        发现模式 = `--traj-root`；单课程模式由 `--job-root`（= `<traj>/<课>/remote-jobs`）
        回推两级。推不出来（测试里的裸 job_root）⇒ None，调用方据此拒服务而不是猜路径。
        """
        if self._discover_root is not None:
            return self._discover_root
        if self._solo is not None:
            return self._solo.job_root.parent.parent
        return None

    def course_dir(self, course: str) -> Path:
        """课程目录 `<traj>/<课>`（`job_root` = `<traj>/<课>/remote-jobs` 回推一级）。"""
        return self._stores[course].job_root.parent

    def resume_sources(self, course: str) -> list[dict]:
        """一门课的续跑锚点来源（**两个来源、同一台机器**）：自回传 + 人工导入。

        * `backfeed`：云机补传落下的 `<job_root>/offline/<run_id>/it-NNN/`；
        * `import`  ：控制台「导入产物」解出的 `<traj>/<课>/deliver/<run_id>/it-NNN/`
          （`remote.deliver_zip` 的落地布局，与回传同一形状——所以两边的判据能共用）。

        两个来源都要：用户口径是「云机回传**或者**人工导入 权重/opt/指标 后」再领任务都要
        能接上——只认回传就漏了手动那条路（而手动那条恰恰是 hub 不在场时的唯一路）。
        """
        st = self._stores[course]
        out: list[dict] = []
        for it, info in st.complete_rounds().items():
            out.append(
                {"it": int(it), "run_id": info["run_id"], "source": "backfeed", "dir": Path(info["dir"])}
            )
        deliver_root = st.job_root.parent / "deliver"
        try:
            runs = sorted(p for p in deliver_root.iterdir() if p.is_dir())
        except OSError:
            runs = []
        for run_dir in runs:
            try:
                it_dirs = sorted(p for p in run_dir.iterdir() if p.is_dir())
            except OSError:
                continue
            for it_dir in it_dirs:
                if not it_dir.name.startswith("it-"):
                    continue
                try:
                    it = int(it_dir.name[3:])
                except ValueError:
                    continue
                if not all((it_dir / n).is_file() for n in _JobStore.RESUME_PARTS):
                    continue
                out.append(
                    {"it": int(it), "run_id": run_dir.name, "source": "import", "dir": it_dir}
                )
        return out

    def merge_eval_rows(self, course: str, rows: object) -> tuple[int, int]:
        """把离线补传来的云机 A 层评估（逐局行 + summary 行）并进**课程账本**
        `eval_log.jsonl`，返回 `(新增逐局行数, 新增 summary 行数)`。

        为什么要 hub 做这一步：那是控制台/门判唯一读的账本（`<traj>/<课>/eval_log.jsonl`），
        而云机那侧只看得见自己的产物目录——不并进去，整段的评估读数要等「跑完人工导入」
        才存在，而「一条跑偏的腿」正是这条腿要尽早看见的东西。

        去重按 `(iter, wver, stage, seed)`（`rl.eval_local.eval_row_key`）：补传天然会重传
        （重连/重启续投），重复行会让曲线出现两个同一点。

        **summary 也要并**（单调：只在该 `(iter,wver)` 还没有、或新来的 `games` 更多时追）：
        曾经不并，靠的是「课程侧按合并后的台账重算」——纯云腿没有课程侧循环，那条退路
        不存在 ⇒ 指标表 eval 列 / eval 弹窗 / 开课回执 / 门判据对整段读数全瞎
        （2026-09-23 用户实测 it50–110 读数全在却不显示）。
        """
        if not isinstance(rows, list) or not rows:
            return (0, 0)
        from rl.eval_local import append_eval_rows, append_eval_summaries

        ledger = self._stores[course].job_root.parent / "eval_log.jsonl"
        good = [r for r in rows if isinstance(r, dict)]
        games = append_eval_rows(ledger, good)
        summaries = append_eval_summaries(ledger, good)
        return (games, summaries)

    def resume_anchor(self, course: str) -> dict | None:
        """最新一轮**同轮齐全**的续跑锚点（`None` = 没有可交回的进度）。

        选法（用户 2026-09-22 口径「必须同轮齐全，否则退到更早轮」）：从最大的 it 往下找，
        第一个三件齐全的轮次就是锚点；同一 it 有两个来源时取目录 mtime 更新的那个
        （回传与导入可能各有一份，人刚导完的那份更可信）。**绝不**用「部分齐全」的轮次
        凑数——那会静默丢掉 Adam 动量或那一轮的指标。
        """
        by_it: dict[int, list[dict]] = {}
        for cand in self.resume_sources(course):
            by_it.setdefault(int(cand["it"]), []).append(cand)
        for it in sorted(by_it, reverse=True):
            cands = by_it[it]
            if len(cands) > 1:
                try:
                    cands = sorted(cands, key=lambda c: c["dir"].stat().st_mtime, reverse=True)
                except OSError:
                    pass
            best = cands[0]
            d = Path(best["dir"])
            try:
                row = json.loads((d / "row.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                row = {}
            wfp = str((row or {}).get("weights_fp", "") or "")
            if not wfp:
                try:
                    wfp = hashlib.sha256((d / "weights.json").read_bytes()).hexdigest()
                except OSError:
                    wfp = ""
            return {
                "course": course,
                "it": int(it),
                "run_id": str(best["run_id"]),
                "source": str(best["source"]),
                "weights_fp": wfp,
                "opt_bytes": int(row.get("opt_bytes", 0) or 0),
                "metrics": row if isinstance(row, dict) else {},
                "_dir": str(d),
            }
        return None

    def task_pack_path(self, course: str) -> Path:
        """整段任务包落点：`<traj>/<课>/task-<课>.zip`（控制台导出的就是它）。

        课程名进的是磁盘路径 ⇒ 在这里断掉分隔符/`..`（与 `parse_course_arg` 同一条边界）。
        hub 不知道 traj 根 ⇒ ProtocolError（响亮，不猜）。
        """
        root = self.traj_root()
        if root is None:
            raise ProtocolError("hub 不知道课程根目录（--traj-root / --discover 未给）")
        name = (course or "").strip()
        if not name or name in (".", "..") or any(ch in name for ch in ("/", "\\", "\x00")):
            raise ProtocolError(f"课程名非法: {course!r}（不得含路径分隔符/空名）")
        if ".." in name:
            raise ProtocolError(f"课程名非法: {course!r}（不得含 ..）")
        return root / name / f"task-{name}.zip"

    def offline_progress(self) -> dict[str, dict]:
        """每课程已收到的离线进度（补传产物）：`{课: {run_id: {its: [...], count, last_mtime}}}`。

        为什么单开一个读面：段内进度**只能**从产物目录看出来（hub 不跑那几轮，账本里没有
        它们的行），而控制台要在长段期间看到进度曲线——「它在跑」与「它挂了」的唯一区别
        就是最近一轮的时间戳。只读列目录，不解析产物（解析权重不在观测面做）。
        """
        out: dict[str, dict] = {}
        for course in self._order:
            runs: dict[str, dict] = {}
            base = self._stores[course].job_root / _JobStore.OFFLINE_DIR
            try:
                run_dirs = sorted(p for p in base.iterdir() if p.is_dir())
            except OSError:
                run_dirs = []
            for run_dir in run_dirs:
                its: list[int] = []
                last = 0.0
                try:
                    for it_dir in run_dir.iterdir():
                        if not it_dir.is_dir() or not it_dir.name.startswith("it-"):
                            continue
                        try:
                            its.append(int(it_dir.name[3:]))
                            last = max(last, it_dir.stat().st_mtime)
                        except (ValueError, OSError):
                            continue
                except OSError:
                    continue
                runs[run_dir.name] = {
                    "its": sorted(its),
                    "count": len(its),
                    "last_mtime": last,
                }
            if runs:
                out[course] = runs
        return out

    # ---- 离线产物补传（路由：显式 course > 已有 offline 目录 > 400） ----
    def locate_offline_course(self, body: dict, query_course: str = "") -> str | None:
        """定一段补传产物归哪门课程；**归不到返回 None**（不是空串）。

        ① 体里的 `course`/`course_name`（节点从 job manifest 抄来，最可靠）；
        ② `?course=` 查询参数（运维手工补传）；
        ③ 已有 `offline/<run_id>/` 目录的课程 —— 补传天然会重传续投，第一条推送
           建目录、后续自动归位（幂等）。

        为什么返回 None 而不用空串表「归不到」：单课程队列（与旧单课程 hub）的课程名
        **就是空串**，空串兼作缺失值会让单课程下的每一次补传都 400（2026-09-18 实测）。
        """
        named = str(body.get("course") or body.get("course_name") or query_course or "").strip()
        if named in self._stores:
            return named
        run_id = str(body.get("run_id") or "")
        if run_id:
            try:
                safe = sanitize_run_id(run_id)
            except ProtocolError:
                return None
            for course in self._order:
                try:
                    if (self._stores[course].job_root / _JobStore.OFFLINE_DIR / safe).exists():
                        return course
                except OSError:
                    continue
        return None

    def store_offline_artifact(self, course: str, body: dict) -> dict:
        return self._stores[course].store_offline_artifact(body)

    def store_offline_result(self, course: str, body: dict) -> dict:
        return self._stores[course].store_offline_result(body)

