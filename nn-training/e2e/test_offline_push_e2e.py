"""e2e/test_offline_push_e2e.py —— **自主云机 push 模式**的完整工作流（真 hub 子进程 + 假云机）。

## 真在哪 / 假在哪

**真**：`hub.server --offline-push`（独立子进程）、`OfflinePushDispatcher`（提议 → 传包 → 202 受理 →
接管 → 逐轮拉取 → 入账 → 归档 → 段末收摊 → 释放）、`OfflinePushWorkers`（能力键 + `enabled` 判据 +
热重载）、HTTP 全线、云机那份 `PushFaceState` + `make_face_server`（云机真正暴露的端点）。
**假**：只假**训练本身**（rollout/PPO 不可能在 CI 里跑）—— 用
`tests/hub/test_offline_push_dispatch._Worker`：它按协议把 outbox 写好（`rounds/*.json` + `state` +
`result`），字节形状与真机同形。

## 为什么必须有这条 e2e（2026-10-25 一天里踩的两次都在这条链上）

1. **云机注册成功、日志一路正常，hub 一个单都不派** —— 根因是 `nodes[]` 里 `enabled=false`
   被 `protocol.push_worker_from_node` 的第一道闸静默剔除，而注册照样 200/updated。
   ⇒ 本 e2e 从「节点启用 ⇒ 一定派」到「节点停用 ⇒ 立刻释放、并出现在 `blocked` 里」两头钉住。
2. 回传链（包上传 / 202 / 接管 / 拉轮 / 归档 / 账本）此前只有单测里的进程内 hub 覆盖，
   真子进程里的启动路径（`--offline-push` 装配、`--push-config`、归档根 env）没有人跑过。

## 形态

`_Worker`（假云机，真 face server）+ `--push-config` 指向**只含一台 **enabled** 节点**的临时表
⇒ 不碰仓库里的 `rl-config.json`（e2e 绝不能改真配置）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import net_http
from common.protocol import COURSE_ENABLE_MARKER
from tests.hub.test_offline_push_dispatch import COURSE, TOKEN, _Worker
from tests.subproc_util import spawn_bound_port

HUB_TOKEN = "e2e-push-token"


# ------------------------------------------------------------------ 夹具


def _course(traj: Path) -> None:
    """课的盘上形状（与生产同构）+ 一份最小任务包（`tools.task.json` + `plan.json`）。"""
    d = traj / COURSE
    (d / "remote-jobs").mkdir(parents=True, exist_ok=True)
    (d / "training_log.jsonl").touch()
    (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    with zipfile.ZipFile(d / f"task-{COURSE}.zip", "w") as zf:
        zf.writestr(
            "tools.task.json", json.dumps({"run_id": "run-1", "it": 0, "commit": "c0ffee"})
        )
        zf.writestr("plan.json", json.dumps({"start_it": 0, "end_it": 4}))


def _push_config(path: Path, node: dict | None) -> None:
    """hub 的 `--push-config`（独立于仓库 rl-config）：只有一台节点，便于「启用/停用」翻转。"""
    path.write_text(
        json.dumps({"version": 1, "nodes": [node] if node else []}, ensure_ascii=False),
        encoding="utf-8",
    )


def _node(url: str, *, enabled: bool = True) -> dict:
    return {
        "id": "cloud-e2e",
        "url": url,
        "authKey": TOKEN,
        "offline_push": True,
        "enabled": enabled,
    }


def _http(base: str, path: str, *, method: str = "GET") -> tuple[int, dict]:
    req = urllib.request.Request(
        base + path, headers={"Authorization": f"Bearer {HUB_TOKEN}"}, method=method
    )
    try:
        with net_http.urlopen(req, timeout=20.0) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8")) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw.decode("utf-8")) if raw else {})
        except ValueError:
            return e.code, {}
    except Exception:
        return 0, {}


def _wait(pred, timeout: float = 60.0, step: float = 0.1) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:
            pass
        # sleep-ok: 轮询步长（等的是谓词，超时只当挂起兜底）
        time.sleep(step)
    try:
        return bool(pred())
    except Exception:
        return False


class _Hub:
    """`hub.server --offline-push` 子进程（控制台那条 argv 的同构缩小版）。"""

    def __init__(self, traj: Path, push_cfg: Path) -> None:
        self.traj = traj
        self.push_cfg = push_cfg
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.base = ""
        self._port = 0

    def start(self) -> None:
        argv = [
            sys.executable,
            "-u",
            "-m",
            "hub.server",
            "--port",
            "{port}",  # 由 `spawn_bound_port` 填：它探测 → 交给子进程 → 等子进程自报 listening
            "--host",
            "127.0.0.1",
            "--token",
            HUB_TOKEN,
            "--traj-root",
            str(self.traj),
            "--discover",
            "--discover-sec",
            "0.2",
            "--lock-file",
            str(self.traj / "hub.lock"),
            "--offline-push",
            "--push-config",
            str(self.push_cfg),
        ]

        def _argv(port: int) -> list[str]:
            return [a.replace("{port}", str(port)) for a in argv]

        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            # 归档根隔离：e2e 绝不能往真 `nn-training/weights/` 撒文件（控制台的 ckpt 选择器会当真轮次）
            "BCITY_WEIGHTS_ARCHIVE_ROOT": str(self.traj / "weights-archive"),
            "BCITY_CONSOLE_URL": "",  # 不触发导包腿
            "PYTHONUNBUFFERED": "1",
        }
        bound = spawn_bound_port(_argv, cwd=str(ROOT), env=env)
        self.proc = bound.proc
        self._port = int(bound.port)
        self.base = f"http://127.0.0.1:{self._port}"

        def _pump() -> None:
            """把子进程输出收进 `self.lines`（失败时贴给断言看）。**文本/字节都收**：
            `spawn_bound_port` 用 `text=True` 起进程，而别处可能给 bytes。"""
            assert self.proc is not None and self.proc.stdout is not None
            while True:
                raw = self.proc.stdout.readline()
                if not raw:
                    return
                text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                self.lines.append(text.rstrip())

        threading.Thread(target=_pump, name="hub-e2e-log", daemon=True).start()
        assert _wait(lambda: _http(self.base, "/admin/queue")[0] == 200, 40.0), self.output()

    def output(self) -> str:
        return "\n".join(self.lines[-40:])

    def close(self) -> None:
        if self.proc is None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


# ------------------------------------------------------------------ 用例


#: 这条 e2e 起**真 hub 子进程**（~2s 就绪）+ 走完「派单 → 受理 → 接管 → 拉轮到账」，
#: 再等一次「停用 ⇒ 释放」（hub 的派发拍是 10s 级）⇒ 40s 上下。放宽到 120s：
#: 抓的是「挂死」，不是「够不够快」——墙钟本身由 hub 的拍长决定，与本机性能无关。
@pytest.mark.time_budget(120)
def test_push_mode_whole_workflow(tmp_path: Path) -> None:
    """一台**已启用**的自主云机接手一门课：派单 → 受理 → 接管 → 拉一轮 → 归档/入账 →
    停用 ⇒ 立刻释放并出现在 `blocked` 里。

    这条链上每一段都是今天踩过的：
      * 「注册成功但 hub 一个单不派」（`enabled=false` 静默剔除）；
      * 「202 受理之后才接管」（旧实现 202 前就 claim，云机侧一起手就 409）；
      * 「一轮产物回传完毕」要有一行 + 归档要有件（否则按 it 号拼链会拼错分支）。
    """
    traj = tmp_path / "traj"
    _course(traj)
    worker = _Worker(tmp_path)  # 假云机：真 face server + 按协议写 outbox
    push_cfg = tmp_path / "push-config.json"
    _push_config(push_cfg, _node(worker.url, enabled=True))

    hub = _Hub(traj, push_cfg)
    hub.start()
    try:
        # ① 派单成立：包被传到云机（`_Worker` 记下了 task），本课被接管
        assert _wait(lambda: worker.task is not None, 60.0), hub.output()
        task = worker.task or {}
        assert str(task.get("course")) == COURSE, task
        assert int(task.get("resume_it") or 0) >= 0, task

        # ② 接管成立（`/offline/hold` 的 live 只认进度；受理 = 已 claim）
        assert _wait(
            lambda: _http(hub.base, f"/offline/hold?course={COURSE}")[1].get("state") == "live",
            60.0,
        ), hub.output() + "\n" + str(_http(hub.base, f"/offline/hold?course={COURSE}"))

        # ③ 拉轮入账：产物树有 `it-001/`，课程账本有 `offline_backfeed` 行，归档有件
        off = traj / COURSE / "remote-jobs" / "offline"
        assert _wait(lambda: any(p.name == "it-001" for p in off.rglob("it-001")), 60.0), (
            hub.output()
        )
        ledger = (traj / COURSE / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
        backfeed = [
            json.loads(ln)
            for ln in ledger
            if ln.strip() and json.loads(ln).get("source") == "offline_backfeed"
        ]
        assert backfeed, ledger[-5:]
        assert {int(r.get("iter") or 0) for r in backfeed} >= {1}, backfeed
        # 归档形状：`<归档根>/<课>/<prefix>.it<N>.<时间戳>.json`（`biz.archive.backup_weights` 的契约）
        archived = list((traj / "weights-archive").rglob("*.it1.*"))
        assert archived, list((traj / "weights-archive").glob("*"))
        # 派发计数（读面自证：这条链真的走过 dispatcher，不是测试手搓的产物）
        st, doc = _http(hub.base, "/admin/offline-push-workers")
        assert st == 200 and int(doc.get("dispatched") or 0) >= 1, doc

        # ④ 停用这台云机 ⇒ 立刻释放本课，且它出现在 `blocked` 里（今天那条静默事故的反面）
        _push_config(push_cfg, _node(worker.url, enabled=False))
        assert _wait(
            lambda: _http(hub.base, f"/offline/hold?course={COURSE}")[1].get("held") is False,
            90.0,
        ), hub.output()
        assert _wait(
            lambda: any(
                r.get("id") == "cloud-e2e"
                for r in (_http(hub.base, "/admin/offline-push-workers")[1].get("workers") or {})
                .get("blocked")
                or []
            ),
            30.0,
        ), _http(hub.base, "/admin/offline-push-workers")[1]
    finally:
        hub.close()


if __name__ == "__main__":  # 便于单跑排障：`python e2e/test_offline_push_e2e.py`
    raise SystemExit(pytest.main([__file__, "-q"]))
