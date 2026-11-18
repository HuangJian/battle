"""G4：生产包不得新增 `except Exception` / `except BaseException` / 裸 `except:`
（`python.refactor.md` §8、§P0-7）。

存量按**文件计数**冻结在 `tests/golden/except_baseline.json`：只拦增长，不要求一次清零
（仓内门禁文化：冻结存量 + 禁止新增）。口径是 AST 里的 `ExceptHandler`，只看处理器类型
是否 `Exception` / `BaseException` / 省略；`except (OSError, ValueError)` 这类具体异常不计数，
注释与 docstring 里提到也不算。`tools/` 与 `worker/scripts/` 是一次性脚本，按 G3 同一先例豁免；
`tests/` 不在扫描面内。

为什么需要这条：P0-7 的错误分类（`ProtocolError` / `RetryableError` / `OSError`）只在少数关键
路径上生效，其余地方一律 `except Exception` 兜底，把「确定性失败」和「瞬时失败」抹成同一分支，
顺带吞掉 `MemoryError` 与真正的编码 bug。没有门禁时，新代码会继续用最省事的写法，清理永远
追不上新增。

## 抬格记账（每一次 +1 都要写在这里）

| 日期 | 文件 | Δ | 为什么只能宽捕 |
|---|---|---|---|
| 2026-11-15 | `worker/forensics.py` | +1（7 → 8） | `rss_mb()` 的契约是「任何失败返回 `(0.0, 0.0)`，绝不出错」，但旧实现只靠两个探针各自内部兜住 —— 探针自己抛（`_rss_mb_posix` 的 bug / 平台分派错了）就**把取证变成新的故障面**，恰是这条契约明令禁止的。新加的这一处是**进程边界兜底**：`except Exception` 里 `_note(errors, "rss", e)` 把成因写进快照的 `probe_errors`（不抛、不静默）——G4 的判据（「边界上也要留痕」）在这一处是满足的。同笔 G9 该文件 5 → 0。 |
| 2026-11-15 | `remote/notebook_runtime.py` | −7（7 → 0） | notebook cell 的 hub / worker_server 出站点：原来一律 `except Exception` 打成「FATAL: hub 不可达（X）——检查隧道 URL 是否过期」。那是最贵的一种误诊——真因可能是本模块自己的 bug（`AttributeError` / `KeyError`），而运维被指去查隧道。hub ping 与 `/ping` 探针改 `NET_ERRORS` / `PROBE_ERRORS`（探针多一个 `ValueError`：200 但 body 不是 JSON）；`md.version` 收窄成 `PackageNotFoundError`（「版本未知」不许替本模块的 bug 说话）；token 文件 `chmod` 收窄成 `OSError`（Colab/Kaggle 的 FUSE 挂载点不一定支持）。顺带修一处**`except BaseException` 吞 Ctrl-C**（`_run_probe` 的子进程探测，最长 60s × 2 核）：现在 `KeyboardInterrupt` 穿出去。判据 `tests/remote/test_notebook_runtime.py` +4（不可达仍 -2 / 程序员错穿出 / 探不起来回落 CPU / Ctrl-C 不被吞；各自改回宽捕实测红）。 |
| 2026-11-15 | `common/distribution.py` | −9（11 → 2） | 探活 / 探测 / 取消十来个出站点的失败返回值（`node_ping → None`、`weights_cached_on_node → False`）**不是「没答」而是业务结论**：宽捕把「连不上」与本模块自己的 bug 抹平后，现场表现是「全部节点突然不可用、份额落回本地」而真因无痕。改为 `NET_ERRORS`（`_BASE_NET_ERRORS` + `http.client.HTTPException`）；探活类再并上 `ValueError`（200 但 body 不是 JSON，见同文件的 `PING_ERRORS`）。留下的 2 处是**第三条边界**：`_closer`（对任意 `HTTPResponse` 尽力 `close()`，异常族由对端决定）与 `fetch_task` 的「非 `DistError` 的确定性失败」兜底（解包错种类不定，且必须保留真实 message）。判据 `tests/common/test_dist_ping_errors.py`（3 条）+ `test_dist_common_poll.py::test_poll_result_lets_a_programmer_error_through`（各自改回宽捕实测红）。 |
| 2026-11-14 | `remote/child_proc.py` | +1（6 → 7） | `pump_lines` 把「读流异常」与「回调异常」分开：回调是**两个代理各自传进来的行解析/记账代码**（`upload_proc` / `payload_proc`），它抛什么类型这里一概不知（`ValueError` / `KeyError` / 自己的异常族），而这条边界的义务是「坏行不进账、好行照读、收尾报数」。改成具体异常＝把「未知 bug 静默变成线程崩溃」。同函数里**读流**那一侧仍是宽捕 + `pass`（那是 EOF 的回声，见该函数 docstring），所以净 +1 是**新增的一条边界**，不是旧代码放松。 |

| 2026-11-15 | `remote/job_lifecycle.py` | −10（11 → 1） | 十条控制面通知的捕获面收敛成 `remote.http.NET_ERRORS`（OSError 覆盖 URLError/TimeoutError/SSLError、`HTTPException`、`WireSlowError`、两处防御性 `RuntimeError`）。**不是放松而是收紧**：原来 `AttributeError`/`TypeError`/`KeyError` 会被吞成「hub 不可达（租约兜底）」，现场永远像网络问题。留下那 1 处是 `on_drop` 回调边界（调用方传进来的删副本逻辑，异常族未知）。判据 `test_job_control_visibility.py::test_programmer_error_is_not_disguised_as_a_network_failure`（改回宽捕即红）。 |
| 2026-11-15 | `remote/lifecycle.py` / `remote/colab_bc.py` | −8（lifecycle 4 → 0，colab_bc 5 → 1） | 全是**窄捕**（G9 第八批）：遗言读数 `/proc`（OSError）/ NVML（RuntimeError）/ `statvfs`（OSError）、探测腿 `subprocess.run`（OSError / SubprocessError）。`colab_bc` 留下的那 1 处是 `mount_drive` —— `google.colab.drive.mount` 抛什么这里不知道，且该边界的义务是「失败仍继续、Drive 只用来放进度」，宽捕 + 日志是正确形状。 |
| 2026-11-15 | `remote/hub_http.py` | −10（10 → 0） | 传输薄壳的十处宽捕全部收敛成 `NET_ERRORS`（`common.errors` 的基底 + `remote.http` 追加 `WireSlowError`，都经 `common.protocol` 门面转发 ⇒ 该文件的依赖闭集不变）。**收紧而非放松**：`_request` 自己抛的 `AttributeError` / `TypeError` / `KeyError` 原来会被读成「hub 没答」，现场表现是「一直没答」的静默挂死环，真因永远看不到。判据 `test_hub_http_split.py::test_probe_does_not_disguise_a_programmer_error_as_transient`（改回宽捕即红，已验）。 |
| 2026-11-18 | `hub/offline_push.py` | −8（10 → 2） | 八个「读不到 ⇒ 不编」的宽捕：`_default_root` 把本模块的 bug 换成缺省 `Path.cwd()`（扫错目录的派发器比挂掉的危险）、`_pack_meta` 包的已是总函数（整段删）、`_still_mine` 是 fail-open（bug → 「永远是我的」）。收窄成 `_STORE_READ_ERRORS`（OSError / ValueError / KeyError），`hold_of → None` 显式化成 `or {}`。留下的 2 处是真边界且都留痕：pump 线程主循环、`_claim` 拒因。判据 `tests/hub/test_offline_push_dispatch.py` +3。 |
| 2026-11-18 | `hub/task_pack.py` | −6（6 → 0） | 三个读包助手 + 三个打控制台的调用点。前者是观测面（清单 / 段末判据），`except Exception` 把本模块的 `AttributeError`（索引 JSON 从对象变列表）读成「这包没有计划」⇒ 云机拿到空的段末判据、静默回退旧行为；后者把本模块的 bug 记成「控制台不可达 → 降级为手动导出」。改成两个具名集：`_PACK_UNREADABLE`（OSError / BadZipFile / EOFError / KeyError / NotImplementedError / ValueError）与 `_CONSOLE_NET_ERRORS`（OSError / HTTPException / ValueError）。判据 `tests/hub/test_offline_task_pack.py` +3（真失败形态 / 程序员错穿出 / 控制台不可达仍 unknown）。 |
| 2026-11-18 | `trainer/loop_transport.py` | −8（9 → 1） | 五处「读 rl-config 的课级覆盖」各罩一条 `except Exception: <用缺省>`。宽捕在这里罩住的**不是**配置读失败——`load_dist_config` 的契约是永不抛（文件缺失 / 坏 JSON / BOM 都返回 `None`）——而是本模块自己的 bug（配置块变列表 ⇒ `AttributeError`、键改名 ⇒ `TypeError`），处置却是「静默用缺省」：配置改了不生效而日志无字。改成三个**形状总函数**（`_config_block` / `_course_stem` / `_course_value`，形状不对喊一次）后没有可捕的东西；留下的 1 处是 `_with_node_failover` 的单节点边界（换下一个节点仍值得试，已 `log`）。判据 `test_wire_cf_tunnel.py::test_a_programmer_error_in_the_config_read_is_not_swallowed` + `test_course_push.py::test_hub_push_does_not_disguise_a_programmer_error`（把 `return_value` 改回 `side_effect` 即红）。 |
| 2026-11-18 | `remote/rollout_node.py` | −4（8 → 4） | 探活面。`_ping` 的结论是「节点死了 ⇒ 重启 agent」，旧宽捕把回执形状的 `AttributeError` 也读成不健康 ⇒ 重启环（`_status` 后果轻些：摘要变不可知）；两者与 `_probe_https` 共用一个集：`NET_ERRORS` + `http.client.HTTPException` + `ValueError`（200 但 body 不是 JSON）。`_install_bun_mirror` 只吞「网/盘/坏 zip」（`OSError` / `RuntimeError` / `BadZipFile` / `EOFError`）——它的返回值就是「镜像装没装上」，把自己 bug 记成「镜像不行」= 永久静默失效的优化路径。留下的 4 处：`_terminate` 的两级 kill（G9 冻结的收尾边界）、`_slot_count` 的 platform_utils 兜底、`_open_tailscale`（异常族由 `tailscale_boot.ensure` 定）。判据 `tests/remote/test_rollout_node.py` +2。 |
| 2026-11-18 | `remote/offline_deliver.py` | −6（12 → 6） | 补传腿的六处。① `probe()` 与 `_post()`：捕的应是 `_opener` 的路面（`OSError` / `http.client.HTTPException` / `RetryableError` = `_DELIVER_NET_ERRORS`），旧宽捕把 `_headers()` 的 `KeyError`、`_timeout_for` 的 `TypeError` 也记成「hub 不可达 / 没送达」⇒ 一条永不成功的坏配置伪装成网络抖动。② 构造器里 `sanitize_run_id` 的契约只有 `ProtocolError`；③ `backup_pending` 的判据面（`pending()`）与 `_prune_drawer` 的 `_ledger_its()` 只该失在盘与账行形状上（`OSError` / `ValueError` / `KeyError`）；④ `_err_text` 整段删 try（`bytes.decode("utf-8", "replace")` 按定义不抛，旧 `except Exception` 只能吞掉自己）；⑤ `_guard` 的 `BaseException` → `Exception`（后台线程不该吞 `SystemExit` / `KeyboardInterrupt`）。留下的 6 处都留了就地理由：`_cpu_seconds`（埋点契约「永不抛」）、`_guard` 外层（线程必须活）、`sync` 兜底（补传不得波及训练）、`_post_artifact_guarded`（毒轮次收敛）、`deliver_result`（段末摘要 best-effort）、`_guard` 内层 `pass`（日志写不出去 ≠ 线程该死，G9 冻结）。判据 `tests/remote/test_offline_deliver.py` +3（断连仍不可达 / 本模块 bug 穿出 / 非法 run_id 唯一失败 + 备份判据面）。 |
| 2026-11-18 | `hub/http_face.py` / `hub/store_offline.py` | −5（http_face 4 → 2，store_offline 3 → 0） | 两处都是「客户端/传输把字节弄坏了」与「我们自己读错了」共罩一条宽捕。① `_read_raw_body` / `_read_capped_body`：捕到的应该是 `OSError`（`ConnectionResetError` / `TimeoutError`）与 `ValueError`（非整数 `Content-Length`，两个助手都把解析包在同一条 try 里）；宽捕把 `rfile` 形状变了（`AttributeError`）也答成 400「read body failed」—— 给客户端的假诊断。改为 `_BODY_READ_ERRORS`，并从**公开入口** `do_POST` 验（400 vs 500）。② `decode_opt_tar` → `_unpack_wire` 的实测路面是 `binascii.Error`(ValueError) / `EOFError`（截断的 gzip，**不是** `BadZipFile`）/ `zlib.error`（魔数对但 deflate 坏，`gzip.decompress` 不包装）/ `BadGzipFile`(OSError) ⇒ `_WIRE_DECODE_ERRORS`；`_land_round_metrics` / `_land_offline_round_extras` 这两条「观测面不拖垮回传」的边界改为 `_LAND_ERRORS`（OSError / ValueError / TypeError / KeyError = 盘错 + 行形状错），`AttributeError` 不再被读成「这一轮度量落位失败」而静默。判据 `tests/hub/test_hub_routes_split.py::test_a_dead_body_is_a_400_but_our_own_bug_is_not`、`tests/hub/test_offline_backfeed.py::test_a_corrupt_opt_payload_costs_only_the_opt`（三种真坏法 + 反向 500）。 |
| 2026-11-18 | `worker/sibling_reuse.py` / `remote/bc_job.py` | −4（sibling_reuse 3 → 1，bc_job 4 → 2） | 同一类错法：把「可选优化的失败」与「我们的形状 bug」捕成同一分支。① `sibling_courses` 的 `load_course` 只该捕**文件面**（`OSError` / `ValueError`(jsonc + pydantic) / `KeyError`(缺 level)）；而紧跟的 `episode_identity_fp(other)` 对**过了 pydantic 的** `CourseConfig` 是全函数 ⇒ 它的 try 整段删掉（它再抛 = 我们的形状假设坏了，旧的 `except Exception: continue` 让训练照跑而复用默默死掉）。留下来的 1 处是 `episode_identity_fp(course)`：入参是**调用方**给的任意对象、复用又是可选优化 ⇒ 算不出指纹只能回 `[]`（已留注释）。② `_bc_fetch_resume` / `_bc_post_epoch` 捕的应是 `_request` 的三类路面（`OSError` / `RetryableError` / `ProtocolError`）⇒ `_BC_HTTP_ERRORS`；`AttributeError` / `TypeError` 现在一下穿出去，不再白等两轮退避、再把一半信息的失败写进 WARN。判据 `tests/worker/test_sibling_reuse.py::test_a_broken_course_file_is_skipped_but_our_shape_bug_is_not`、`tests/remote/test_bc_http_errors.py`（新，4 条：3 拍重试 / ProtocolError 同属瞬时 / 本模块 bug 只调 1 次 / epoch 腿回 False 且留 backlog 行）；两处还原成宽捕各实测红。 |
| 2026-11-16 | `common/fs.py` | −1（1 → 0） | 唯一的宽捕在 `_JsonlWriter._run` 里：它把写失败 `set_exception` 回给等 ack 的调用方 —— 这条边界**只为跨线程回传而存在**。P1-12 去掉写者线程后（每路径一把锁，写在调用方线程里做），异常天然就是调用方的异常，没有要接的东西可捕。 |
| 2026-11-14 | `remote/worker_proc.py` | +1（1 → 2） | `_release_cloud_machine` 原先把「探测 Colab kernel」与「调 `runtime.unassign()`」罩在同一条宽捕里 `pass`，等于把「机器没停」说成了「这里没事」。拆开后**探测**那一侧仍是宽捕（非 Colab / 子进程里 `get_ipython()` 为 None 是常态，具体化只会把 import 的形状写进日志），**调用**那一侧是第三方 API 边界（`google.colab.runtime.unassign` 抛什么这里不知道，且这条边界的义务恰是「失败必须留一行」）。净 +1 = 多出一条边界，不是旧代码放松；同笔 G9 该文件 2 → 1。 |
"""

from __future__ import annotations

import ast
import json
from functools import cache
from pathlib import Path

from tests.helpers.budget import sanity_floor

ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = ROOT / "tests" / "golden" / "except_baseline.json"

#: 生产包；与 G3 同一扫描面。`tools/` 不在内，`worker/scripts/` 在下方按前缀豁免。
PROD_DIRS = ("biz", "common", "hub", "remote", "train", "trainer", "worker")
EXEMPT_PREFIXES = ("worker/scripts/",)

#: 计数的处理器类型名。裸 `except:`（`node.type is None`）单独计，语义上等价于
#: `except BaseException`，是最宽的一种。
BROAD_NAMES = frozenset({"Exception", "BaseException"})


def _is_broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    return isinstance(handler.type, ast.Name) and handler.type.id in BROAD_NAMES


@cache
def _scan() -> dict[str, int]:
    out: dict[str, int] = {}
    for d in PROD_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT_PREFIXES):
                continue
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            n = sum(
                1
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler) and _is_broad(node)
            )
            if n:
                out[rel] = n
    return out


@cache
def _baseline() -> dict[str, int]:
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def test_no_new_broad_except_handlers() -> None:
    actual = _scan()
    baseline = _baseline()
    growth = {
        rel: (n, baseline.get(rel, 0))
        for rel, n in sorted(actual.items())
        if n > baseline.get(rel, 0)
    }
    detail = "\n".join(f"  {rel}: {n} 处（基线 {base}）" for rel, (n, base) in growth.items())
    assert not growth, (
        "生产包新增了宽泛的异常处理器（`except Exception` / `except BaseException` / 裸 `except:`）：\n"
        f"{detail}\n"
        "  内部路径只捕具体异常（`ProtocolError` / `RetryableError` / `OSError` / `ValueError`）；"
        "确实位于进程边界时，也要 `logger.exception` 留痕并返回明确失败码。"
    )


def test_baseline_only_shrinks() -> None:
    """基线里的文件计数只许持平或下降；修完后应顺手把基线数改小（否则门禁会一直放行旧额度）。"""
    actual = _scan()
    baseline = _baseline()
    stale = {
        rel: (base, actual.get(rel, 0))
        for rel, base in sorted(baseline.items())
        if actual.get(rel, 0) < base
    }
    detail = "\n".join(f"  {rel}: 基线 {base}，实际 {n}" for rel, (base, n) in stale.items())
    assert not stale, (
        "以下文件的宽泛异常处理器已经减少，但 `tests/golden/except_baseline.json` 还是旧数字：\n"
        f"{detail}\n"
        "  把基线数改成实际值（或删掉归零的条目），让额度不再回流。"
    )


def test_scan_sees_the_known_production_surface() -> None:
    """扫描入口自检：基线非空、实际扫描确实命中已知文件、豁免面没有混进来。"""
    baseline = _baseline()
    actual = _scan()
    assert baseline.get("remote/train_core.py", 0) > 0
    assert baseline.get("common/log.py", 0) > 0
    assert actual.get("remote/train_core.py", 0) > 0, (
        "扫描入口坏了（AST 没数到已知 except Exception）"
    )
    assert sum(actual.values()) >= sanity_floor(baseline), (
        f"生产面计数 {sum(actual.values())} 低于基线九成（{sanity_floor(baseline)}）"
        "—— 扫描可能漏了包（这条只防扫描入口坏，不是质量目标）"
    )
    assert len(baseline) >= 100
    assert not any(rel.startswith(EXEMPT_PREFIXES) for rel in baseline)
    assert not any(rel.startswith("tools/") for rel in baseline)


def test_specific_handlers_and_regex_are_not_counted() -> None:
    """口径自检：具体异常、`except*`、注释里的字样都不算。"""
    src = """
try:
    pass
except (OSError, ValueError):
    pass
except ProtocolError:
    pass
# except Exception: 只是注释
"""
    tree = ast.parse(src)
    broad = [
        node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler) and _is_broad(node)
    ]
    assert broad == []

    bare = ast.parse("try:\n    pass\nexcept:\n    pass\n")
    assert (
        sum(1 for node in ast.walk(bare) if isinstance(node, ast.ExceptHandler) and _is_broad(node))
        == 1
    )
