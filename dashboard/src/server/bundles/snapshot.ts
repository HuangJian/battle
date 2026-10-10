/** snapshot.ts — 集群代码快照（`<repo>/tmp/.code-snapshot/snapshot.json`）的**只读**读取。
 *
 *  为什么控制台要读它（plan/cluster-code-snapshot §4.2）：任务包「是否过期」的**代码维度**
 *  不再看「源文件 mtime vs 包 mtime」。会话冻结语义下那条判据会把「改了源码但没重启」误判成
 *  过期 ⇒ 作废 + 重导，而重导出来的**还是同一份代码**：白烧一次分钟级导出、并让云机在一段
 *  404 等包窗口里干等（导出失败时还会因为 `restorePackIfMissing` 用 `renameSync` 保留旧 mtime
 *  而反复触发）。新判据是「包里的 `code.zip` 是不是**当前集群快照**那一份」。
 *
 *  快照由 python 侧建（`nn-training/remote/code_snapshot.py`）——**内容寻址**：
 *    `<repo>/tmp/.code-snapshot/snapshot.json`  +  `code.<sha12>.zip`
 *  控制台只读元数据，不碰 zip 字节、不写任何东西。
 *
 *  两条刻意的边界：
 *   · **零依赖**：只用 `fs`/`path`。读包内 `manifest.json` 需要 zip 库（dashboard 至今零运行时
 *     依赖是刻意不变式）⇒ 包内 sha 走 python 落的旁挂件（`remote/bundle.py::bundle_meta_path`，
 *     控制台侧 `taskBundleMeta()` 读它）。
 *   · **锚存活是廉价口径**：`pidAlive(anchor.pid)`，本侧**不做**命令行指纹（TS 没有那条探活）。
 *     pid 复用会假「活着」⇒ 可能信任一份旧快照；这个方向的风险由「锚死/无快照 ⇒ 回落 mtime
 *     口径」（保守）与启动日志里的 sha12 兜住（plan §4.2 / §8.1）。
 */

import { readFileSync } from 'fs'
import path from 'path'
import { pidAlive } from '../../core/net'
import { REPO_ROOT } from '../../core/paths'

/** 快照目录名（相对 `<repo>/tmp`；python：`code_snapshot.SNAPSHOT_DIR_NAME`）。 */
export const CODE_SNAPSHOT_DIR_NAME = '.code-snapshot'
/** 元数据文件名（python：`code_snapshot.META_NAME`）。 */
export const CODE_SNAPSHOT_META_NAME = 'snapshot.json'
/** 元数据魔数（python：`code_snapshot.SNAPSHOT_MAGIC`）。 */
export const CODE_SNAPSHOT_MAGIC = 'battle2-code-snapshot'
/** 元数据协议版本（python：`code_snapshot.SNAPSHOT_PROTO`；对不上 ⇒ 当作没有快照）。 */
export const CODE_SNAPSHOT_PROTO = 1

/** 一份集群代码快照的**只读**视图（只有判据需要的字段）。 */
export interface ClusterSnapshot {
  /** 元数据里记的 zip 文件名（内容寻址：`code.<sha12>.zip`）。 */
  zip: string
  /** 快照字节的 sha256（= `manifest.code_sha256` = 包内 `code.zip` 的 sha）。 */
  sha256: string
  anchorKind: string
  anchorPid: number
  packedAtEpoch: number
  /** 锚进程是否还活着（**廉价口径**：只问 `pidAlive`，不做命令行指纹）。 */
  anchorAlive: boolean
}

/** 快照目录（`<root>/tmp/.code-snapshot`）。
 *
 *  `root` 不给时先看 `BCITY_CODE_SNAPSHOT_DIR`（**与 python 侧同名同义**：
 *  `nn-training/remote/code_snapshot.py::snapshot_dir()`；单测把两侧一起重定向到夹具目录，
 *  「控制台看到的快照」与「trainer 建的那份」才不会各读一处）。显式传 `root` 优先于 env
 *  （老调用点/老用例零改动）；env 是**惰性**读的（每次调用现读，不在模块加载时定死）。 */
export function codeSnapshotDir(root?: string): string {
  if (root === undefined) {
    const override = process.env.BCITY_CODE_SNAPSHOT_DIR
    if (override) return override
    root = REPO_ROOT
  }
  return path.join(root, 'tmp', CODE_SNAPSHOT_DIR_NAME)
}

/** 快照元数据路径。 */
export function codeSnapshotMetaPath(root?: string): string {
  return path.join(codeSnapshotDir(root), CODE_SNAPSHOT_META_NAME)
}

/** 读集群代码快照；没有/读不动/魔数不符/协议不认 ⇒ `null`（**永不抛**）。
 *
 *  「没有快照」与「快照坏了」在判据里同一条路（回落 mtime 口径）——不区分它们是因为两者
 *  对控制台的含义相同：**当前集群快照 sha 不可知**。
 */
export function readClusterSnapshot(root?: string): ClusterSnapshot | null {
  let raw: string
  try {
    raw = readFileSync(codeSnapshotMetaPath(root), 'utf-8')
  } catch {
    return null
  }
  let meta: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    meta = parsed as Record<string, unknown>
  } catch {
    return null
  }
  if (meta.magic !== CODE_SNAPSHOT_MAGIC || meta.proto !== CODE_SNAPSHOT_PROTO) return null
  const sha = typeof meta.sha256 === 'string' ? meta.sha256 : ''
  const zip = typeof meta.zip === 'string' ? meta.zip : ''
  if (!sha || !zip) return null
  const anchor = (meta.anchor ?? {}) as Record<string, unknown>
  const anchorPid = Number(anchor.pid ?? 0) || 0
  return {
    zip,
    sha256: sha,
    anchorKind: String(anchor.kind ?? ''),
    anchorPid,
    packedAtEpoch: Number(meta.packed_at_epoch ?? 0) || 0,
    anchorAlive: pidAlive(anchorPid),
  }
}
