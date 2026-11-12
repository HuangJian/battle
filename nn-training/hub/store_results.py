"""hub/store_results.py — 回传落位：worker 结果 / 失败标记 / BC 每 epoch 快照。

`_JobStore` 的六个域混入之一（S4 第十四刀）。共同点：**写进 job 目录、服务回传与训练侧
读回**，且都不动租约。

* `store_result` —— PPO 结果落盘（读取端在 `store_ledger.get_result`）；
* `store_job_failure` —— 节点**确定性**失败（bun 装不上 / TS 运行时取不到 / argv 非法）
  的终局标记：训练侧立刻停腿，不再等 25min 超时；
* `store_bc_epoch` / `get_bc_resume` / `get_bc_metrics` —— BC 云腿的每 epoch 权重 +
  指标（单文件覆盖存最新 resume，磁盘有界：每 job 恒 1 份权重；指标行按 epoch 幂等，
  迟到的旧 epoch 只补行、不倒回 resume）。

## 依赖方向

`store_results → {common.protocol}`（向下）；跨域调用只有 `self._job_dir`。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from common.protocol import FAIL_NAME

#: 课程级 resume 索引目录（相对 job 根）：`bc-resume/by-course/<course_key>/<it>.json`。
#: 它是 per-jid `bc-resume.json` 的**别名**，不是第二份权威：写入点只有 `store_bc_epoch`，
#: 读取点只有 `get_bc_resume` 的 fallback。
BC_COURSE_INDEX_DIR = "bc-resume/by-course"


def _safe_course_key(raw: object) -> str:
    """manifest 里的课程键 → 可安全拼进路径的段；非法/缺失 ⇒ 空串（不写索引）。"""
    key = raw.strip() if isinstance(raw, str) else ""
    if (
        not key
        or key in (".", "..")
        or "/" in key
        or "\\" in key
        or ":" in key
        or "\x00" in key
    ):
        return ""
    return key


def _read_json_dict(path: Path) -> dict | None:
    """读一个 JSON 对象；缺失/坏文件/非对象 ⇒ None（索引是别名，坏文件等同缺失）。"""
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _bc_index_target(jd: Path) -> tuple[str, int] | None:
    """从 job manifest 取 `(course_key, it)`；不是 BC job / 旧 manifest 缺键 ⇒ None。"""
    m = _read_json_dict(jd / "manifest.json")
    if not isinstance(m, dict) or str(m.get("kind", "")) != "bc":
        return None
    key = _safe_course_key(m.get("course_key"))
    it = m.get("it")
    if not key or not isinstance(it, int) or isinstance(it, bool) or it < 1:
        return None
    return key, it


def _bc_index_path(jd: Path, course_key: str, it: int) -> Path:
    return Path(jd).parent / BC_COURSE_INDEX_DIR / course_key / f"{int(it)}.json"


def _write_bc_course_index(
    jd: Path, target: tuple[str, int], body: dict, epoch: int
) -> None:
    """原子覆盖课程级索引；旧 epoch（迟到的补发）**不得倒回**。调用方已持 `_lock`。"""
    path = _bc_index_path(jd, target[0], target[1])
    prev = _read_json_dict(path)
    prev_epoch = int(prev.get("epoch", 0) or 0) if isinstance(prev, dict) else 0
    if int(epoch) < prev_epoch:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _bc_metric_rows(jd: Path) -> list[dict]:
    """读该 job 已有的 `bc-metrics.jsonl` 行（坏行跳过；文件不在 ⇒ []）。"""
    p = jd / ResultsMixin.BC_METRICS_NAME
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    out.append(e)
    except OSError:
        return []
    return out


def _append_bc_metric_row(jd: Path, epoch: int, metrics: dict, ts: float) -> bool:
    """追加一行 epoch 指标；**同一 epoch 已有行 ⇒ 跳过**（幂等）。返回是否写入。

    幂等是隧道 530 的必需项：请求已落盘、响应丢在 Cloudflare 上时 worker 会重发同一份
    body（`remote/bc_job.py::_bc_post_epoch` 的重试），无条件 append 就是控制台里
    bc-v6-train 的重复 ep2/ep4（吃掉了「最新 6 个 epoch」的名额）。补发链路（迟到的旧
    epoch 重放）也靠它防重。
    """
    if any(int(r.get("epoch", 0) or 0) == int(epoch) for r in _bc_metric_rows(jd)):
        return False
    row = {"epoch": int(epoch), **metrics, "ts": ts}
    with open(jd / ResultsMixin.BC_METRICS_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return True


class ResultsMixin:
    """域混入：见模块头部。"""

    # ---- 由组合类 / 兄弟簇提供（混入只见 `self`，实现不在本模块）----
    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock
    #: 时钟（`now_fn` 注入点，住 `_AuthGuard`）
    _now: Any
    #: 兄弟簇 `store_leases` 的状态（显式授权备份的回传放行票）
    _backup_authorized: set[str]
    #: 兄弟簇 `store_ledger` 的方法
    _job_dir: Any
    #: 兄弟簇 `store_leases` 的方法（★M5：epoch 是 BC 唯一的进度信号，落盘即打点）
    note_job_progress: Any
    #: 兄弟簇 `store_leases` 的方法（★2026-11-12：job 收官/失败 ⇒ 解除课程级 BC 独占）
    _bc_lock_clear: Any

    # ---- 结果 ----
    def store_result(self, job_id: str, result: dict) -> bool:
        """落盘 worker 回传结果（result/ 目录）。返回 False = 该 job 已有结果（防重复写回）。"""
        with self._lock:
            rdir = self._job_dir(job_id) / "result"
            if rdir.exists():
                return False
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # weights_json / opt_tar 以 base64 存于 result.json（< 数 MB，可接受）
            # 备份授权随胜负结束（同一份 job 不会再有人回传）：及时收紧 token 闸。
            self._backup_authorized.discard(job_id)
            self._bc_lock_clear(job_id)  # 收官 ⇒ 下一份 BC job 立刻可领（不等 900s 窗）
            return True

    def store_job_failure(self, job_id: str, rec: dict) -> bool:
        """落盘节点确定性失败（`fail.json`）。返回 False = 已有结果 / 已有失败记录。

        两条首写规则，都是为了「训练侧看到的那一条」不被后到的写方改掉：
          * **有结果就不收失败**——结果已落盘时失败是过时信息（迟到的失败回报不得
            盖掉成功的产物，与 `store_result` 的首写锁定同向）；
          * **首个失败原因胜出**——多节点都失败时，第一个报上来的才是训练侧读到的
            那条，后到的只保留在值里（不再改动）。"""
        with self._lock:
            jd = self._job_dir(job_id)
            if (jd / "result").exists():
                return False
            dst = jd / FAIL_NAME
            if dst.exists():
                return False
            jd.mkdir(parents=True, exist_ok=True)
            # 原子写：tmp + replace（中断的 POST 不留半截失败记录——半截 JSON 会让
            # _get_result 把它当「没有失败」继续等满超时，正是要治的那个病）。
            tmp = jd / (FAIL_NAME + ".tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, dst)
            self._bc_lock_clear(job_id)
            return True

    # ---- BC 每 epoch 回传（2026-09-13，plan/bc-cloud-integration.plan.md）----
    #: 单文件覆盖存最新 resume（磁盘有界：每 job 恒 1 份权重，~0.5MB）；指标追加 jsonl。
    BC_RESUME_NAME = "bc-resume.json"

    BC_METRICS_NAME = "bc-metrics.jsonl"

    #: 每 epoch 的**不可变权重快照**（#12）：`epoch-weights/ep{N}.json`。
    #: `bc-resume.json` 仍只留最新一份（续训权威）；这里是给人/评估/对照用的历史。
    BC_EPOCH_WEIGHTS_DIR = "epoch-weights"

    #: epoch POST 体上限（weights ~0.5MB b64 后 ~0.7MB；4MB 已极宽裕）
    BC_EPOCH_BODY_MAX = 4 * 1024 * 1024

    def store_bc_epoch(self, job_id: str, body: dict) -> bool:
        """BC epoch 回传落盘：bc-resume.json（单文件原子覆盖 = 最新 epoch 权重）+
        bc-metrics.jsonl（按 epoch 追加一行指标，同 epoch 重发幂等）。

        **迟到的旧 epoch 不得倒回 resume**（2026-11-04 补发链路）：隧道 530 丢掉的 ep5
        会在 ep6 成功后由 worker 重放，若照旧覆盖，resume 退回 ep5 ⇒ 重启/换机会重训
        已完成的 ep6。eps 相等（同一 epoch 的重试）照旧覆盖——权重没有更新可言。
        返回 False = 体非法。调用方已验租约。"""
        # ★M5（Q5）：epoch 是 BC 作业**唯一**的进度信号（心跳只续租、不当活性）⇒ 落盘成功
        # 即打一个进度锚：`_lease_state` 的 bc 判活、`bc_drain_of`、900s 让出回收都读它。
        # 打点住 store（写入点唯一），handler 不另写一行。
        self.note_job_progress(job_id)
        with self._lock:
            epoch = body.get("epoch")
            if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
                return False
            if not isinstance(body.get("weights"), str) or not body["weights"]:
                return False
            jd = self._job_dir(job_id)
            jd.mkdir(parents=True, exist_ok=True)
            target = _bc_index_target(jd)
            # 每 epoch 快照与 resume 覆盖**解耦**：迟到的旧 epoch 虽然不回倒 resume，
            # 仍要按自己的名字留下快照（历史不该被吞）；同 epoch 重发原子覆盖同名文件。
            snap = jd / self.BC_EPOCH_WEIGHTS_DIR / f"ep{int(epoch)}.json"
            snap.parent.mkdir(parents=True, exist_ok=True)
            snap_tmp = snap.parent / (snap.name + ".tmp")
            snap_tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            os.replace(snap_tmp, snap)
            # 只拿**本 job** 的旧 resume 做「不倒回」判据；不能走 get_bc_resume（它会在 per-job
            # 缺失时回退课程索引，导致新 job 的 ep1 被旧课程的 ep40 挡住、永远不落盘）。
            prev = _read_json_dict(jd / self.BC_RESUME_NAME)
            prev_epoch = int(prev.get("epoch", 0) or 0) if isinstance(prev, dict) else 0
            if epoch >= prev_epoch:
                # 原子覆盖：tmp + replace——中断的 POST 不留半截 resume
                tmp = jd / (self.BC_RESUME_NAME + ".tmp")
                tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, jd / self.BC_RESUME_NAME)
                if target is not None:
                    _write_bc_course_index(jd, target, body, epoch)
            metrics = body.get("metrics")
            if isinstance(metrics, dict):
                _append_bc_metric_row(jd, epoch, metrics, self._now())
            return True

    def get_bc_resume(self, job_id: str) -> dict | None:
        """本 job 的 resume；缺失时回退到课程级 `(course_key, it)` 索引。

        重开（预算 40→80）会发布新 jid，而新 job 自己没有历史 resume；索引让 worker 仍能从
        ep40 接续。per-jid 文件存在时**永远优先**（索引只是别名，不改变同 jid 语义）。
        """
        jd = self._job_dir(job_id)
        loaded = _read_json_dict(jd / self.BC_RESUME_NAME)
        if loaded is not None:
            return loaded
        target = _bc_index_target(jd)
        if target is None:
            return None
        return _read_json_dict(_bc_index_path(jd, target[0], target[1]))

    def clear_bc_course_index(self, course: str) -> int:
        """删除课程级 `(course,it)` 续训索引（#8 控制台「清空进度」）。

        只删索引**别名**，不碰 per-jid `bc-resume.json`、`epoch-weights/`、账本或归档权重。
        在跑 job 的下一次 epoch 回传仍会重建索引（所以调用方应先暂停课程）。
        返回删掉的轮次文件数（课程没有索引 ⇒ 0，幂等）；课程键非法 ⇒ ValueError。
        """
        key = _safe_course_key(course)
        if not key:
            raise ValueError(f"非法课程键 {course!r}")
        # `_job_dir(<任一 jid>)` 的父目录 = job 根（`<job_root>/<jid>`）。
        root = Path(self._job_dir("_probe_")).parent / BC_COURSE_INDEX_DIR / key
        with self._lock:
            if not root.is_dir():
                return 0
            removed = 0
            for p in list(root.glob("*.json")) + list(root.glob("*.json.tmp")):
                try:
                    p.unlink()
                except OSError:
                    continue
                if p.name.endswith(".json"):
                    removed += 1
            try:
                root.rmdir()
            except OSError:
                pass  # 还有别的文件（异常残留）就留着，别为清理把目录强删
            return removed

    def get_bc_metrics(self, job_id: str) -> list[dict]:
        return _bc_metric_rows(self._job_dir(job_id))

    def get_bc_epoch_weights(self, job_id: str, epoch: int) -> dict | None:
        """按 epoch 取不可变快照（#12）；旧 job / 尚未回传 / 非法 epoch ⇒ None。"""
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
            return None
        return _read_json_dict(
            self._job_dir(job_id) / self.BC_EPOCH_WEIGHTS_DIR / f"ep{int(epoch)}.json"
        )
