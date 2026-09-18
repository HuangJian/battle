"""remote/offline_deliver.py —— 产物**补传**（中途能连上 hub 就自动恢复在线回传）。

用户需求（2026-09-17）后半句：任务包搬上云机后**全程自主完成训练**，产物以官方方式
（Kaggle output / Colab Drive）打包下载；「如果训练中途发现可以联通 hub，云机也能自动
恢复产物在线回传」。

**为什么是「补传」而不是「改回在线」**：全离线/半离线的契约是「产物落节点本地目录即
交付完成」（`remote/artifacts.py`）。补传是**第二份拷贝**——节点每轮落盘之后顺手问一句
「hub 在不在」，在就把这一轮推上去。三条硬性质，缺一条这个功能都会变成负担：

  1. **训练永不因网络停摆**：所有异常在模块内消化（`sync()` / `deliver_result()` 永不
     抛），连不上就静默跳过、下轮再试。没有重试预算、没有退避等待、没有阻塞调用。
  2. **重启续投**：已投递项记在产物目录的 `delivered.json`（原子写）——新会话带着同一个
     目录接上时，**积压的轮次会自动补上**（`pending()` 是「磁盘上有、记账里没有」），
     不依赖任何内存状态。
  3. **幂等且首写锁定**：hub 侧按 `(run_id, it)` 落盘，重复投递返回 duplicate；同一轮
     的权重是**不可变**的（同一 it 重算会得到不同权重——那是另一条腿，不是覆盖）。

**几个刻意的判定**（都有代价，写在这里免得后来者「顺手改回去」）：

  * *探活走带 token 的 `GET /ping`，不新增无鉴权 `/health`*：探针要回答的是「**我能不能
    用**这条链」。只证可达的探针会让「token 错」在第一次上传 ~1.9MB 体之后才暴露，还多
    一个对公网泄露「hub 在线」的端点。
  * *401/403 → **本会话停用**补传*（响亮记一行），不重试：hub 的 D9 闭锁是「同 IP 五次
    无效鉴权封 3600s」，拿错 token 每轮重试等于亲手把自己封掉；而且 token 错是配置问题，
    重试一百次也不会对。
  * *400/413 → 本会话停用*：体是我们自己造的，被拒说明形状不对（版本不匹配），重试无意义。
  * *5xx / 网络异常 → 只记日志、下轮再试*（按 key 节流，不给日志刷屏）。
  * *传输编码复用 `encode_weights_json`/`encode_opt_tar`（gzip+base64）*：比裸容器多 33%
    体量（~0.5s/轮 @3.5Mbps 实测隧道），换来的是**同一个已被两端测过的编解码器**，且
    hub 落盘路径与 result 链路逐字同形。这是 best-effort 的旁路，不值得为它再引入一种
    容器格式（备选与代价见 DECISIONS §2026-09-17-goalnn-offline-reconnect-delivery）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from remote import net_http
from remote.artifacts import ArtifactStore, atomic_write_json, sha256_bytes
from remote.protocol import (
    AUTH_HEADER,
    OFFLINE_ARTIFACT_BODY_MAX,
    OFFLINE_ARTIFACT_PATH,
    OFFLINE_DELIVERED_NAME,
    OFFLINE_RESULT_BODY_MAX,
    OFFLINE_RESULT_PATH,
    encode_opt_tar,
    encode_weights_json,
    sanitize_run_id,
)

#: 探活结果的缓存秒数（训练一轮动辄几分钟，没必要每轮都探两次：`sync()` 前探一次即可，
#: 只读的 `/ping` 也便宜——但 hub 不可达时每次 `urlopen` 都要等超时，才是真成本）。
PROBE_TTL_SEC = 60.0
#: 单次 `sync()` 最多投递几轮。一个刚连上的积压段可能攒了十几轮，全推会让「落到训练循环里
#: 的那一次调用」耗时不可控（每轮 ~1.9MB + 往返）。少量多次：下轮继续补。
SYNC_CAP = 4
#: 单次 HTTP 超时（秒）。上传体 ~1.9MB，60s 对 3.5Mbps 隧道（~4.4s）极宽裕。
HTTP_TIMEOUT_SEC = 60.0
#: 同一类不可用日志的最小间隔（秒）——hub 关机时每轮一条就够了。
LOG_THROTTLE_SEC = 300.0


def _log_default(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [deliver] {msg}", flush=True)


def _urllib_opener(url: str, data: bytes, headers: dict, timeout: float) -> tuple[int, bytes]:
    """默认 HTTP（POST；`data=None` 即 GET）。返回 (status, body)；**不抛** HTTPError。"""
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        # 回环（本机 hub）绕开环境代理；隧道/公网 URL 保持 urllib 默认。
        with net_http.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), e.read()


class OfflineDeliverer:
    """一段自主训练的产物补传器（best-effort；每个公开方法都**不会抛**）。

    只读产物目录、只写一个 `delivered.json`——它不参与训练的任何判定（不是调度器的一部分），
    所以「补传坏了」永远只意味着「产物停在节点本地」，而那是本来的交付面。
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        run_id: str,
        artifacts_dir: str | Path,
        timeout: float = HTTP_TIMEOUT_SEC,
        probe_ttl: float = PROBE_TTL_SEC,
        sync_cap: int = SYNC_CAP,
        opener: Callable[[str, bytes, dict, float], tuple[int, bytes]] | None = None,
        now_fn: Callable[[], float] | None = None,
        log: Callable[[str], None] = _log_default,
    ) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "")
        #: run_id 同时是 hub 侧目录名：**在本端就先校验**（错在这里 = 一行明确日志，
        #: 而不是每轮一个 400）。
        try:
            self.run_id = sanitize_run_id(run_id)
        except Exception:
            self.run_id = ""
        self.root = Path(artifacts_dir)
        self.timeout = float(timeout)
        self.probe_ttl = float(probe_ttl)
        self.sync_cap = int(sync_cap)
        self._opener = opener or _urllib_opener
        self._now = now_fn or time.time
        self.log = log
        self.enabled = bool(self.base_url and self.token and self.run_id)
        #: 本会话停用（bad token / 体被拒）——一次性配置问题，重试无意义（见模块 docstring）。
        self.disabled_reason = ""
        self._reachable: bool | None = None
        self._probed_at = 0.0
        self._last_log: dict[str, float] = {}
        self._delivered: set[int] = set()
        self._result_done = False
        self._manifest_cache: dict | None = None
        self._load_ledger()
        if not self.enabled and not self.disabled_reason:
            missing = []
            if not self.base_url:
                missing.append("hub_url")
            if not self.token:
                missing.append("token")
            if not self.run_id:
                missing.append(f"合法 run_id（收到 {str(run_id)[:40]!r}）")
            self.log(f"补传未启用（缺 {' / '.join(missing)}）——产物只落本地产物目录")

    # ------------------------------------------------------------ 记账（delivered.json）

    @property
    def ledger_path(self) -> Path:
        return self.root / OFFLINE_DELIVERED_NAME

    def _load_ledger(self) -> None:
        """读已投递项（缺失/损坏 → 空：那只会导致重复投递，hub 侧幂等兜住）。"""
        p = self.ledger_path
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("run_id") != self.run_id:
            return  # 别的 run 的记账（同一个目录被复用）——不认
        for it in data.get("artifacts") or []:
            if isinstance(it, int) and not isinstance(it, bool):
                self._delivered.add(it)
        self._result_done = bool(data.get("result_done"))

    def _save_ledger(self) -> None:
        atomic_write_json(
            self.ledger_path,
            {
                "run_id": self.run_id,
                "hub_url": self.base_url,
                "artifacts": sorted(self._delivered),
                "result_done": self._result_done,
                "updated_at": self._now(),
            },
        )

    # ------------------------------------------------------------ 探活

    def probe(self, *, force: bool = False) -> bool:
        """hub 可达且**可用**（token 有效）？结果按 TTL 缓存。永不抛。"""
        if not self.enabled:
            return False
        now = self._now()
        if not force and self._reachable is not None and now - self._probed_at < self.probe_ttl:
            return self._reachable
        self._probed_at = now
        try:
            status, _body = self._opener(
                f"{self.base_url}/ping", b"", self._headers(), min(self.timeout, 20.0)
            )
        except Exception as e:  # 网络层任何异常（DNS/连接被拒/超时/TLS）= 不可达
            self._reachable = False
            self._throttled_log(
                "unreachable", f"hub 不可达（{type(e).__name__}: {e}）——本轮跳过补传"
            )
            return False
        if status == 200:
            self._reachable = True
            return True
        self._reachable = False
        if status in (401, 403):
            # 停用而不是重试：见模块 docstring（D9 闭锁 + 配置问题重试无意义）。
            self._disable(f"鉴权被拒（HTTP {status}）——token 不对/过期；本会话已停用补传")
        else:
            self._throttled_log("probe", f"hub 探活返回 HTTP {status}——本轮跳过补传")
        return False

    def _headers(self) -> dict[str, str]:
        return {AUTH_HEADER: f"Bearer {self.token}", "Content-Type": "application/octet-stream"}

    def _disable(self, why: str) -> None:
        if not self.disabled_reason:
            self.disabled_reason = why
            self.log(f"停用补传：{why}（产物照常落本地，训练不受影响）")

    def _throttled_log(self, key: str, msg: str) -> None:
        now = self._now()
        if now - self._last_log.get(key, 0.0) < LOG_THROTTLE_SEC:
            return
        self._last_log[key] = now
        self.log(msg)

    # ------------------------------------------------------------ 待投递集

    def pending(self) -> list[int]:
        """磁盘上有、记账里没有的轮次（升序）。**这是「重启续投」的全部实现**。"""
        out: list[int] = []
        try:
            dirs = sorted(self.root.glob(f"{ArtifactStore.IT_PREFIX}*"))
        except OSError:
            return out
        for d in dirs:
            if not d.is_dir():
                continue
            name = d.name[len(ArtifactStore.IT_PREFIX) :]
            if not name.isdigit():
                continue
            it = int(name)
            if it in self._delivered:
                continue
            if (d / "weights.json").exists():
                out.append(it)
        return out

    def _manifest(self) -> dict:
        """产物目录里的 manifest 快照（血缘字段随每次投递带上；读失败 → 空）。"""
        if self._manifest_cache is None:
            p = self.root / ArtifactStore.MANIFEST_NAME
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                self._manifest_cache = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                self._manifest_cache = {}
        return self._manifest_cache

    def _row_for(self, it: int) -> dict | None:
        """账本里第 `it` 轮那一行（跨会话补投时用它——本会话的内存账本不一定有）。"""
        p = self.root / ArtifactStore.METRICS_NAME
        if not p.exists():
            return None
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and int(row.get("it", -1)) == int(it):
                    return row
        except OSError:
            return None
        return None

    # ------------------------------------------------------------ 投递

    def sync(self) -> int:
        """把待投递轮次尽量推上去，返回本轮投递成功数。**永不抛**。"""
        try:
            return self._sync()
        except Exception as e:  # 兜底：补传的任何意外都不得波及训练
            self.log(f"补传异常（忽略，训练继续）：{type(e).__name__}: {e}")
            return 0

    def _sync(self) -> int:
        if not self.enabled or self.disabled_reason:
            return 0
        todo = self.pending()
        if not todo:
            return 0
        if not self.probe():
            return 0
        done = 0
        for it in todo[: self.sync_cap]:
            if not self._post_artifact(it):
                break  # 传输坏了就别接着打——剩下的下轮再补
            done += 1
        if done:
            self._save_ledger()
            left = len(self.pending())
            self.log(
                f"补传 {done} 轮（it{todo[0]}…it{todo[done - 1]}）→ {self.base_url}"
                + (f"；仍积压 {left} 轮，下轮继续" if left else "")
            )
        return done

    def _post_artifact(self, it: int) -> bool:
        """投递一轮产物（权重 + opt + 账本行）。True = 这一轮可以记为已投递。"""
        d = ArtifactStore(self.root, run_id=self.run_id)
        wp = d.weights_path(it)
        try:
            wj = wp.read_bytes()
        except OSError as e:
            # 扫描时还在、读时没了（会话被杀/人删了）＝瞬时状态：**不标记已投递**，
            # 下轮重扫时它已经不在 `pending()` 里了（`pending` 只认存在的权重文件）。
            self.log(f"补传 it{it} 本轮跳过：读不到权重 {wp}（{e}）")
            return False
        if not wj:
            self.log(f"补传 it{it} 本轮跳过：权重文件是空的（{wp}）")
            return False
        opt = b""
        op = d.opt_path(it)
        if op.exists():
            try:
                opt = op.read_bytes()
            except OSError:
                opt = b""  # 动量缺失的代价是「hub 侧续跑 Adam 归零」，不是投递失败
        m = self._manifest()
        body = {
            "run_id": self.run_id,
            "it": int(it),
            "weights_fp": sha256_bytes(wj),
            "weights_json": encode_weights_json(wj),
            "opt_fp": sha256_bytes(opt) if opt else "",
            "opt_tar_b64": encode_opt_tar(opt) if opt else "",
            # 账本行**随本轮一起走**（hub 侧要拿它画曲线/对账）；跨会话补投时内存里没有，
            # 从 metrics.jsonl 按 it 找回来（找不到就只发权重，不含 row）。
            "row": self._row_for(it),
            # 血缘（hub 侧据此把这条腿接回某个 run；全是 manifest 里的原值）
            "plan_sha256": str(m.get("plan_sha256", "") or ""),
            "course_fp": str(m.get("course_fp", "") or ""),
            "commit": str(m.get("commit", "") or ""),
            "source_dir": str(self.root)[-300:],
            "ts": self._now(),
        }
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > OFFLINE_ARTIFACT_BODY_MAX:
            self._disable(f"it{it} 补传体 {len(raw)}B 超上限——本会话停用补传")
            return False
        status, resp = self._post(OFFLINE_ARTIFACT_PATH, raw)
        if not status:  # 没送达（_post 已记一笔）
            return False
        if status in (200, 201, 409):
            self._delivered.add(it)
            if status == 409:
                self.log(f"补传 it{it}：hub 已有这一轮（幂等，记为已投递）")
            return True
        if status in (401, 403):
            self._disable(f"补传 it{it} 被拒（HTTP {status}）——本会话停用补传")
            return False
        if status in (400, 413, 422):
            self._disable(
                f"补传 it{it} 体被拒（HTTP {status}: {_err_text(resp)}）——本会话停用补传"
                "（体是自己造的，重试不会变对）"
            )
            return False
        self._reachable = False
        self._throttled_log(
            "post", f"补传 it{it} 失败（HTTP {status}: {_err_text(resp)}）——本轮放弃，下轮再试"
        )
        return False

    def deliver_result(self, *, it_end: int, state: str, summary: dict | None = None) -> bool:
        """段末摘要（跑到哪、什么状态、失败原因）。best-effort；**永不抛**。

        与逐轮产物的差别：摘要是**会变的**（同一 run 续跑后 `it_end` 更大），hub 侧覆盖写
        ——它不是不可变快照，而「这条腿现在到哪了」的最新答案。
        """
        try:
            if not self.enabled or self.disabled_reason:
                return False
            if not self.probe():
                return False
            m = self._manifest()
            body = {
                "run_id": self.run_id,
                "it_end": int(it_end),
                "state": str(state),
                "delivered": len(self._delivered),
                "summary": dict(summary or {}),
                "plan_sha256": str(m.get("plan_sha256", "") or ""),
                "course_fp": str(m.get("course_fp", "") or ""),
                "commit": str(m.get("commit", "") or ""),
                "source_dir": str(self.root)[-300:],
                "ts": self._now(),
            }
            raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > OFFLINE_RESULT_BODY_MAX:
                raw = json.dumps(
                    {**body, "summary": {"truncated": True}}, ensure_ascii=False
                ).encode("utf-8")
            status, _resp = self._post(OFFLINE_RESULT_PATH, raw)
            if not status:
                return False  # 没送达（_post 已记一笔）
            if status in (200, 201):
                if not self._result_done:
                    self._result_done = True
                    self._save_ledger()
                self.log(f"段末摘要已补传：it{it_end} {state} → {self.base_url}")
                return True
            if status in (401, 403):
                self._disable(f"段末摘要被拒（HTTP {status}）——本会话停用补传")
            else:
                self._reachable = False
                self._throttled_log("result", f"段末摘要补传失败（HTTP {status}）——留给下次会话")
            return False
        except Exception as e:
            self.log(f"段末摘要补传异常（忽略）：{type(e).__name__}: {e}")
            return False

    def _post(self, path: str, raw: bytes) -> tuple[int, bytes]:
        """POST 一趟。**永不抛**：传输层异常 → `(0, b"")`（状态 0 只在此出现 = 没送达）。

        为什么要在这儿收口而不是在 `sync()` 外层兜：走外层兜会让「本轮已投递的几轮」
        留在内存里却没落 `delivered.json`（异常把 `_save_ledger` 跳过了）——下次会话重传
        一遍。全程可重入的代价不该由记账来承担。
        """
        try:
            return self._opener(f"{self.base_url}{path}", raw, self._headers(), self.timeout)
        except Exception as e:
            self._reachable = False
            self._throttled_log(
                "transport", f"补传传输异常（{type(e).__name__}: {e}）——本轮跳过，下轮再试"
            )
            return 0, b""

    # ------------------------------------------------------------ 状态（日志/自检用）

    def status(self) -> dict:
        """一行式状态（给启动日志与 operator 看：补传在不在、投了几轮、卡在哪）。"""
        return {
            "enabled": self.enabled,
            "hub_url": self.base_url,
            "run_id": self.run_id,
            "delivered": len(self._delivered),
            "pending": len(self.pending()),
            "result_done": self._result_done,
            "disabled_reason": self.disabled_reason,
        }


def _err_text(resp: bytes, max_chars: int = 300) -> str:
    """把 hub 的错误体压成一行（远端文本不可信：截断 + 去换行）。"""
    try:
        s = resp.decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        obj = json.loads(s)
        if isinstance(obj, dict) and obj.get("error"):
            s = str(obj["error"])
    except ValueError:
        pass
    return " ".join(s.split())[:max_chars]


def make_deliverer(
    *,
    hub_url: str,
    hub_token: str,
    run_id: str,
    artifacts_dir: str | Path,
    log: Callable[[str], None] = _log_default,
) -> OfflineDeliverer | None:
    """构造补传器：**缺 hub_url 或 token 就返回 None**（= 这条腿没有补传，不是错误）。

    调用方（`run_loop`）因此只需 `if d is not None`，不必自己判断「参数齐不齐」。
    """
    if not str(hub_url or "").strip() or not str(hub_token or "").strip():
        return None
    return OfflineDeliverer(
        base_url=hub_url, token=hub_token, run_id=run_id, artifacts_dir=artifacts_dir, log=log
    )


#: 供调用方一致性检查（测试与 operator 脚本读它，避免抄第二份路径常量）。
DELIVERED_NAME = OFFLINE_DELIVERED_NAME
__all__ = ["DELIVERED_NAME", "OfflineDeliverer", "make_deliverer"]
