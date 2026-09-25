"""remote/hub/store_offline.py — 离线段的自回传产物（第二份拷贝 + 续跑锚点）。

`_JobStore` 的六个域混入之一（S4 第十四刀）。它独立成簇的理由是**与 job 队列完全隔离**：
不写 `job_pending` / `job_completed`（补传没有 job 也没有租约，不存在「谁来领」的问题），
只落 `offline/<run_id>/it-NNN/` 与**自己那本**账（`metrics.jsonl`，与 job 队列的 jsonl
**不同文件**——混进同一本账会让「一行一 job」的读方（控制台 / 池重建）出现怪行）。

`complete_rounds` 的判据是「三件齐全」（`RESUME_PARTS`）：续跑要么重放 Adam 动量（缺 opt
就是动量归零），要么丢指标行（那轮在曲线上消失）——两者都是「看起来能跑但读数少一截」。
所以本簇的落盘一律走 `_write_bytes`（原子写）。

## 依赖方向

`store_offline → {common.protocol, common.fs, remote.artifacts}`（向下）；跨域调用只有
`self._append_ledger`（住 `store_ledger`）。**不 import 任何兄弟混入**。

## 随簇搬来的 `_write_bytes`

它原先住 `hub_server.py`（模块级），唯一调用方就是本簇的逐件落盘与段末覆盖写。它是
`common.fs.atomic_write_bytes` 的**转调**——之所以还留一层名字，是因为 `common/fs.py` 与
`common/__init__.py` 头部把「曾有两份同名实现」记进了本仓的重复清单（历史，不必改）。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from common.fs import atomic_write_bytes
from common.protocol import ProtocolError, decode_opt_tar, decode_weights_json, sanitize_run_id
from remote.artifacts import ArtifactStore, ledger_row_from_metrics

#: 仓库根（`nn-training/remote/hub/store_offline.py` 上溯 4 层）——课程配置与权重归档的相对路径基准。
REPO_ROOT = Path(__file__).resolve().parents[3]
#: 权重归档根（`nn-training/weights/`；即 `rl.archive.backup_weights` 的缺省目录）。
WEIGHTS_ARCHIVE_ROOT = REPO_ROOT / "nn-training" / "weights"
#: 归档根的**测试隔离开关**（2026-09-23）：设了就改用它。为什么必须有：回传轮会往归档根写
#: `<课>.it<N>.<时间戳>.json`，而工装用例不得往真归档目录撒文件——那些文件会被控制台的
#: evalA 权重选择器（`eval-board/ckpts.ts` 扫 `nn-training/weights/<leg>/`）当成真训练轮次
#: 列出来。env 形态（而不是只留可 patch 的模块常量）是因为 e2e 那条是真子进程
#: （`e2e/test_offline_training_e2e.py` 拉 `remote.hub_server`），patch 传不进去。
WEIGHTS_ARCHIVE_ROOT_ENV = "BCITY_WEIGHTS_ARCHIVE_ROOT"


def _weights_archive_root() -> Path:
    """当前归档根（`BCITY_WEIGHTS_ARCHIVE_ROOT` 优先，缺省 `nn-training/weights`）。

    **调用时读** env（不是 import 时算一次）——这样测试能在同一进程里 monkeypatch，
    也让这条路径与 `remote/game_watch` 的看门狗常量同样“只读一份绑定”。
    """
    raw = os.environ.get(WEIGHTS_ARCHIVE_ROOT_ENV, "")
    return Path(raw) if raw else WEIGHTS_ARCHIVE_ROOT


#: 课程配置目录（`nn-training/curricula/<课>.jsonc`）——归档 prefix/dir 的单一事实来源。
CURRICULA_DIR = REPO_ROOT / "nn-training" / "curricula"


def _write_bytes(path: Path, data: bytes) -> None:
    """原子写（半截权重比没有权重更危险）——转调 `common.fs.atomic_write_bytes`。

    唯一实现见 `common.fs.atomic_write_bytes`（与 `remote/artifacts.atomic_write_bytes`
    同源；本仓那次去重的记录在 `common/fs.py` 头部）。
    """
    atomic_write_bytes(path, data)


class OfflineRoundsMixin:
    """域混入：见模块头部。"""

    # ---- 由组合类 / 兄弟簇提供（混入只见 `self`，实现不在本模块）----
    #: 全 store **唯一**的那把锁（`_AuthGuard.__init__` 里建；见 `hub_server._JobStore` 头部）
    _lock: Lock
    #: 时钟（`now_fn` 注入点，住 `_AuthGuard`）
    _now: Any
    #: 构造身份：组合类 `__init__` 直接声明
    job_root: Path
    jsonl_path: Path
    #: 兄弟簇 `store_ledger` 的方法（补传产物要与 job 账本**分家**落自己那本，但仍走同一套追加原语）
    _append_ledger: Any

    # ---- 产物补传（offline 腿；2026-09-17）----
    # 语义：节点自主段的**第二份拷贝**。产物本来就已经落在节点本地目录里（那是它的交付
    # 面）；这里接收的是「中途发现 hub 可达」时顺手推上来的那一份，让控制面不用等人搬 zip。
    # 与 job 队列**完全隔离**：不写 job_pending/job_completed（补传没有 job 也没有租约，
    # 这条腿不存在「谁来领」的问题），只落 `offline/<run_id>/` 与账本 audit 事件。
    #: 补传落位根目录名（`<job_root>/offline/<run_id>/`）。
    OFFLINE_DIR = "offline"

    #: 补传账本文件名（`offline/<run_id>/` 下；与 job 队列的 jsonl **不同文件**——
    #: 补传不是 job，混进同一本账会让「一行一 job」的读方（控制台/池重建）出现怪行）。
    OFFLINE_METRICS_NAME = "metrics.jsonl"

    #: 段末摘要文件名（同一目录；覆盖写）。
    OFFLINE_RESULT_NAME = "result.json"

    #: 续跑锚点必须**同轮齐全**的三件（用户 2026-09-22 口径：缺一件就退到更早轮）。
    RESUME_PARTS: tuple[str, ...] = ("weights.json", "opt.tar", "row.json")

    #: 回传轮的**交付镜像**目录名（`<traj>/deliver/<run_id>/it-NNN/`）。`deliver/` 此前只有
    #: 人工导入写（`dashboard/src/server/bundles/import.ts`）⇒「导入的段」与「回传的段」分居
    #: 两棵树、找东西要翻两处（用户 2026-09-23 口径：统一命名空间）。
    DELIVER_DIR = "deliver"
    #: 段内**活动权重**指针（`<traj>/weights.json`）——本机循环与回传腿共用同一个文件。
    ACTIVE_WEIGHTS_NAME = "weights.json"

    def complete_rounds(self) -> dict[int, dict]:
        """自回传产物（`offline/<run_id>/it-NNN/`）里**三件齐全**的轮次：`{it: {run_id, dir}}`。

        齐全 = weights + opt + row 都在：续跑要么重放 Adam 动量（缺 opt 就是动量归零），
        要么丢指标行（那轮在曲线上消失）——两者都是「看起来能跑但读数少一截」。
        """
        out: dict[int, dict] = {}
        base = self.job_root / self.OFFLINE_DIR
        try:
            run_dirs = sorted(p for p in base.iterdir() if p.is_dir())
        except OSError:
            return out
        for run_dir in run_dirs:
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
                if not all((it_dir / n).is_file() for n in self.RESUME_PARTS):
                    continue
                out[it] = {"run_id": run_dir.name, "dir": str(it_dir)}
        return out

    def offline_run_dir(self, run_id: object) -> Path:
        """补传落位目录。`run_id` 来自远端 ⇒ 必须先过 `sanitize_run_id`（它会是目录名）。"""
        return self.job_root / self.OFFLINE_DIR / sanitize_run_id(run_id)

    def store_offline_artifact(self, body: dict) -> dict:
        """落一轮补传产物，返回 {"status": "accepted"|"duplicate", "it": n, "run_id": r}。

        校验（任一不过抛 ProtocolError → 400，且**不落盘任何东西**）：
          * `run_id` 合法（目录名的唯一防护面）；
          * `it` 是非负整数；
          * `weights_json` 能解码出**非空**字节；
          * **声明指纹与实际字节相符**——传输损坏（截断/串包）必须在入口拦住，否则一条
            损坏的权重会以「hub 上的产物」身份进入 eval/续跑，而真因在几千行日志之外。

        幂等：`it-NNN/weights.json` 已存在 ⇒ duplicate（**不改写**）。同一轮权重是不可变
        快照：覆盖它意味着「谁先到」决定了历史，而补传天然会重传（重连、重启续投）。
        """
        run_id = sanitize_run_id(body.get("run_id"))
        it = body.get("it")
        if not isinstance(it, int) or isinstance(it, bool) or it < 0:
            raise ProtocolError(f"补传 it 非法（要求非负整数）: {it!r}")
        wj_raw = body.get("weights_json")
        if not isinstance(wj_raw, str) or not wj_raw:
            raise ProtocolError("补传缺 weights_json（权重是这一轮唯一不可再生的东西）")
        wj = decode_weights_json(wj_raw)
        if not wj:
            raise ProtocolError("补传 weights_json 解码后为空")
        declared = str(body.get("weights_fp", "") or "")
        got = hashlib.sha256(wj).hexdigest()
        if declared and declared != got:
            raise ProtocolError(
                f"补传 it{it} 的权重指纹不符：声明 {declared[:16]}… 实得 {got[:16]}…"
                "（传输损坏）——拒收"
            )
        row = body.get("row")
        # 账本行自称的权重指纹必须与实收字节一致：这是**节点自己产的**一致性证据
        # （`ArtifactStore.checkpoint` 写权重后当场算的 sha）。不符 = 产物目录内部不一致
        # （人改过 / 半截写入），把这样的权重收成「hub 上的产物」比拒收危险得多。
        if isinstance(row, dict) and row.get("weights_fp"):
            row_fp = str(row["weights_fp"])
            if row_fp != got:
                raise ProtocolError(
                    f"补传 it{it} 的账本行与权重不符：行记 {row_fp[:16]}… 实得 {got[:16]}…"
                    "（产物目录内部不一致）——拒收"
                )
        opt = b""
        opt_raw = body.get("opt_tar_b64")
        if isinstance(opt_raw, str) and opt_raw:
            try:
                opt = decode_opt_tar(opt_raw)
            except Exception:  # opt 损坏不必拒整轮：代价只是「hub 侧续跑 Adam 归零」
                opt = b""
        with self._lock:
            d = self.offline_run_dir(run_id)
            it_dir = d / f"it-{int(it):03d}"
            if (it_dir / "weights.json").exists():
                return {"status": "duplicate", "run_id": run_id, "it": int(it)}
            it_dir.mkdir(parents=True, exist_ok=True)
            _write_bytes(it_dir / "weights.json", wj)
            if opt:
                _write_bytes(it_dir / "opt.tar", opt)
            _write_bytes(
                it_dir / "row.json",
                json.dumps(
                    row if isinstance(row, dict) else {"it": int(it)},
                    ensure_ascii=False,
                    indent=1,
                ).encode("utf-8"),
            )
            if not (d / "run.json").exists():
                _write_bytes(
                    d / "run.json",
                    json.dumps(
                        {
                            "run_id": run_id,
                            "plan_sha256": str(body.get("plan_sha256", "") or ""),
                            "course_fp": str(body.get("course_fp", "") or ""),
                            "commit": str(body.get("commit", "") or ""),
                            "source_dir": str(body.get("source_dir", "") or "")[:300],
                            "first_seen": self._now(),
                        },
                        ensure_ascii=False,
                        indent=1,
                    ).encode("utf-8"),
                )
            # 账本一行 = 一轮（只在**接新**时追加；重复投递不再写——否则同一轮会出现两行，
            # 而这个文件的读方（人/控制台）按 it 画曲线）。
            with open(d / self.OFFLINE_METRICS_NAME, "a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "event": "offline_artifact",
                            "run_id": run_id,
                            "it": int(it),
                            "weights_fp": got,
                            "opt_bytes": len(opt),
                            **(row if isinstance(row, dict) else {}),
                            "ts": self._now(),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            self._append_ledger(
                {
                    "event": "offline_artifact",
                    "run_id": run_id,
                    "it": int(it),
                    "weights_fp": got,
                    "ts": self._now(),
                }
            )
            self._land_round_metrics(row, run_id=run_id, it=int(it))
            self._land_offline_round_extras(
                it=int(it), run_id=run_id, weights_json=wj, opt=opt, row=row
            )
        return {"status": "accepted", "run_id": run_id, "it": int(it)}

    def _land_round_metrics(self, row: object, *, run_id: str, it: int) -> None:
        """把这一轮的度量搬进**课程侧**（实时回传也能让控制台指标表动起来）。

        用户之问（2026-09-22）：「云机通过网络请求回传，会算这些数据回显吗？」——之前**不会**：
        回传只落 `remote-jobs/offline/<run_id>/it-NNN/{weights,opt,row}.json` + 一条
        `offline_artifact` 事件（没有 `iteration` 事件，也没人把逐局画像铺到读方能找到的地方）
        ⇒ 权重/优化器都在、末轮也能评，但控制台的「各轮指标表」（含耗时/击杀/残血/道具）
        一行不显示，而且「没有 `iteration` 事件」这件事连人工导入都能修正、实时回传不能。

        现在：与人工导入（`remote/deliver_zip`）走**同一张翻译表**
        （`remote.artifacts.ledger_row_from_metrics`）+ 同一个逐局画像落点
        （`<课程>/it<N>/per-game.json`），两路结果逐字段一致。

        只住课程目录（`jsonl_path` 的父目录）：hub 的 `--jsonl` 就是
        `<traj_root>/training_log.jsonl`，课程侧与它是同一个根。重复投递（duplicate）根本走不到
        这里---只有接新才写，所以同一轮不会出现两行。任何失败只记日志：回传的主价值是权重到岸。
        """
        if not isinstance(row, dict):
            return
        try:
            ev = ledger_row_from_metrics(row, run_id=run_id, source="offline_backfeed")
            traj = self.jsonl_path.parent
            it_dir = traj / f"it{it}"
            pg = row.get("perGame")
            if isinstance(pg, list) and pg:
                it_dir.mkdir(parents=True, exist_ok=True)
                (it_dir / ArtifactStore.PER_GAME_NAME).write_text(
                    json.dumps(pg, ensure_ascii=False), encoding="utf-8"
                )
            if ev is not None:
                self._append_ledger(ev)
        except Exception as e:  # 观测面不拖垮回传
            print(
                f"[hub-server] 补传 it{it} 的课程侧度量落位失败（忽略）：{type(e).__name__}: {e}",
                flush=True,
            )

    def _ledger_has_newer_iter(self, it: int) -> bool:
        """课程账本里是否存在**比 `it` 更新**的已落轮次（`iteration.iter` / `offline_artifact.it`）。

        为什么用账本而不是另开一个 sidecar：这张账本**两条腿都写**（本机循环每轮写
        `iteration`、回传腿经 `_land_round_metrics` 也写），所以「课程已知的最新轮」是两腿
        合用的单一判据 —— 不需要额外状态，也就不会出现「另一条腿推进了我不知道」的打架。

        只认这两种事件：`job_pending`/`job_cancelled` 之类虽然也带 `it`，但它们说的是**某台
        机器上的一个 job**（发了还没落权重），不是「这一轮已落」——拿它们当判据会让一个发了
        又取消的更大 it 永久挡住活动权重推进。
        账本不存在 ⇒ False（新课程，没有任何更新轮次）；存在却读不到 ⇒ True（保守不推进：
        宁可不写，也不覆盖可能更新的权重）。
        """
        best = 0
        if not self.jsonl_path.exists():
            return False  # 新课程还没有账本 ⇒ 没有任何更新轮次（不是“读不到”）
        try:
            text = self.jsonl_path.read_text(encoding="utf-8")
        except OSError:
            return True  # 存在却读不到 ⇒ 保守不推进
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue  # 坏行跳过：一个坏行不该冻结活动权重
            if not isinstance(ev, dict):
                continue
            kind = ev.get("event")
            if kind == "iteration":
                v = ev.get("iter")
            elif kind == "offline_artifact":
                v = ev.get("it")
            else:
                continue
            if isinstance(v, int) and not isinstance(v, bool) and v > best:
                best = v
        return best > int(it)

    def _course_backup_target(self, course: str) -> tuple[str, str]:
        """课程配置里的归档 `(prefix, dir)`；缺配置/读不动 ⇒ `(课名, <归档根>/<课>)`。

        单一事实来源 = `nn-training/curricula/<课>.jsonc`（与 `rl/config.py` 同一份文件，
        命名约定与 dashboard 的课程发现一致：`<课>.jsonc` / `<课>.bc.jsonc`）。
        课程没声明归档键时按**同构缺省**（课名 + `<归档根>/<课>`）—— 与
        `TrainingLoop._export_weights` 的缺省（按 mode 前缀）**不是**同一套，所以这里必须先
        读课程：两种缺省混用会把不同课程的归档倒进同一个目录。

        `backup_dir` 是**仓根相对**路径（`rl.archive.backup_weights` 的契约，课程值形如
        `nn-training/weights/<课>`）；绝对路径原样用。
        """
        for name in (f"{course}.jsonc", f"{course}.bc.jsonc"):
            p = CURRICULA_DIR / name
            if not p.is_file():
                continue
            try:
                from rl.jsonc import strip_comments

                cfg = json.loads(strip_comments(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
            if not isinstance(cfg, dict):
                continue
            prefix = str(cfg.get("backup_prefix") or course)
            raw = cfg.get("backup_dir")
            if isinstance(raw, str) and raw:
                bdir = Path(raw)
                bdir = bdir if bdir.is_absolute() else REPO_ROOT / bdir
            else:
                bdir = _weights_archive_root() / course
            return prefix, str(bdir)
        return course, str(_weights_archive_root() / course)

    def _land_offline_round_extras(
        self, *, it: int, run_id: str, weights_json: bytes, opt: bytes, row: object
    ) -> None:
        """回传轮在**课程侧**的三处落位（2026-09-23 用户口径；全部 best-effort）。

        ① **交付镜像**：同一轮也写进 `<traj>/deliver/<run_id>/it-NNN/`。`deliver/` 此前只有
           人工导入写 ⇒「导入的段」与「回传的段」分居两棵树，找东西要翻两处。
        ② **活动权重推进**：`<traj>/weights.json` 在**确证没有更新的轮次**时原子替换。此前它
           整段不动（x20-demo-mix 实测停在段起点指纹），而 `rl/eval_replays_once` 的「活动
           权重」兜底、本机续跑、控制台显示读的都是它 ⇒ 整段期间「当前权重」是假的。
        ③ **归档**：`<归档根>/<课>/<prefix>.it<N>.<时间戳>.json`（同 `rl.archive.backup_weights`）。
           控制台 evalA 的 iter 选择器只扫那个目录（`eval-board/ckpts.ts`）⇒ 不归档就看不见
           回传段的任何一轮 —— 这才是真正意义上的「权重没落盘」。

        为什么 best-effort：回传的主价值是**权重到岸**（调用方已经三校验地落定了），这三处是
        可见性/观感 —— 任何一步失败都只记一行，不该把一轮合法回传判负。
        """
        traj = self.jsonl_path.parent
        course = traj.name
        it_dir = self.offline_run_dir(run_id) / f"it-{int(it):03d}"
        row_bytes = json.dumps(
            row if isinstance(row, dict) else {"it": int(it)}, ensure_ascii=False, indent=1
        ).encode("utf-8")
        # ① 交付镜像（幂等：已存在即不重写；与导入腿同路径同文件名 ⇒ 两腿同构）
        mirror = traj / self.DELIVER_DIR / run_id / f"it-{int(it):03d}"
        try:
            if not (mirror / "weights.json").exists():
                mirror.mkdir(parents=True, exist_ok=True)
                _write_bytes(mirror / "weights.json", weights_json)
                if opt:
                    _write_bytes(mirror / "opt.tar", opt)
                _write_bytes(mirror / "row.json", row_bytes)
        except OSError as e:
            print(f"[hub-server] 补传 it{it} 交付镜像失败（忽略）：{e}", flush=True)
        # ② 活动权重推进（只在没有更新的轮次时；判据是账本，两腿共用）
        if not self._ledger_has_newer_iter(int(it)):
            try:
                _write_bytes(traj / self.ACTIVE_WEIGHTS_NAME, weights_json)
                fp12 = hashlib.sha256(weights_json).hexdigest()[:12]
                print(
                    f"[hub-server] 活动权重推进 → it{it}（{fp12}…，源 run={run_id}）",
                    flush=True,
                )
            except OSError as e:
                print(f"[hub-server] 补传 it{it} 活动权重推进失败（忽略）：{e}", flush=True)
        # ③ 归档（evalA 的 iter 选择器只扫归档目录；**同一轮只写一次**）
        try:
            from rl.archive import backup_weights

            prefix, bdir = self._course_backup_target(course)
            # 幂等：这一轮的归档已在就不再写。归档名带时间戳（同轮重跑用改名区分），任其重写
            # 会让归档目录堆积同轮副本；而本轮的权重是不可变快照（存储层拒绝覆写），所以
            # “已在”就是全部要告诉我们的信息。
            if sorted(Path(bdir).glob(f"{prefix}.it{int(it)}.*.json")):
                print(f"[hub-server] 补传 it{it} 归档已在（跳过重复）", flush=True)
            else:
                dst = backup_weights(
                    str(it_dir / "weights.json"), int(it), prefix=prefix, backup_dir=bdir
                )
                print(
                    f"[hub-server] 补传 it{it} 权重已归档 → {dst}"
                    if dst
                    else f"[hub-server] 补传 it{it} 归档未成（见上一条 WARN）",
                    flush=True,
                )
        except Exception as e:  # 归档不该拖垮回传（含 rl 包不在的截断快照）
            print(
                f"[hub-server] 补传 it{it} 归档不可用（忽略）：{type(e).__name__}: {e}",
                flush=True,
            )

    def store_offline_result(self, body: dict) -> dict:
        """落段末摘要（**覆盖写**：它是「这条腿现在到哪了」的最新答案，不是不可变快照）。"""
        run_id = sanitize_run_id(body.get("run_id"))
        it_end = body.get("it_end")
        if not isinstance(it_end, int) or isinstance(it_end, bool) or it_end < 0:
            raise ProtocolError(f"补传 it_end 非法（要求非负整数）: {it_end!r}")
        state = str(body.get("state", "") or "")[:40]
        rec: dict = {
            "run_id": run_id,
            "it_end": int(it_end),
            "state": state,
            "delivered": (body.get("delivered") if isinstance(body.get("delivered"), int) else 0),
            "summary": body.get("summary") if isinstance(body.get("summary"), dict) else {},
            "plan_sha256": str(body.get("plan_sha256", "") or ""),
            "course_fp": str(body.get("course_fp", "") or ""),
            "commit": str(body.get("commit", "") or ""),
            "source_dir": str(body.get("source_dir", "") or "")[:300],
            "received_at": self._now(),
        }
        with self._lock:
            d = self.offline_run_dir(run_id)
            d.mkdir(parents=True, exist_ok=True)
            _write_bytes(
                d / self.OFFLINE_RESULT_NAME,
                json.dumps(rec, ensure_ascii=False, indent=1).encode("utf-8"),
            )
            self._append_ledger(
                {
                    "event": "offline_result",
                    "run_id": run_id,
                    "it_end": int(it_end),
                    "state": state,
                    "ts": self._now(),
                }
            )
        return {"status": "accepted", "run_id": run_id, "it_end": int(it_end)}
