/** engine.ts — engine_epoch（plan/rl-eval-system.md §2.5）。
 *
 * `engine_epoch = sha256(codeHash)[0:16]`，其中 codeHash 展开自唯一 SSOT 清单
 * `tools/agent/codehash-files.txt`（rollout 门与 eval 门同源）。
 *
 * 2026-09-17 用户指令（rollout/eval 统一事实来源）：旧式
 * `sha256(git_commit + gameplay 指纹)[0:16]` 掺了 git commit ⇒ 任何与 rollout/eval
 * 无关的提交（dashboard / nn-training / docs）都把全节点判 stale、逼运维重启
 * sampler-agent；且 gameplay 集是 TS/Python 两侧手工镜像的第二份清单。现全部并入
 * codehash-files.txt：引擎（src/game/**）、config、RNG（src/utils/**）、God AI
 * （src/ai/**）、评估脚本与 det-golden 都在清单里 ⇒ codeHash 已覆盖 eval 语义，
 * epoch 退化为它的纯函数（不做**节点门**判据，节点门比的就是 codeHash）。
 *
 * 账本字段（EvalGameRow.engine）与 S10 记录级漂移哨兵沿用本值，故保留 16 hex 形态与
 * 字段名；其语义随之从「引擎漂移」变为「代码漂移」——范围比旧式更窄（无关提交不再触发）。
 */

import { execFileSync } from 'node:child_process'

/**
 * codeHash 展开与配方唯一源 = tools/agent/codehash-files.ts（SSOT 清单 +
 * collectCodeHashEntries + computeCodeHash/engineEpochFromCodeHash，集内文件，
 * 改清单/改配方即改变节点门判据）。本模块只做训练机侧 plumbing（git_commit 注入）。
 */
export {
  collectCodeHashEntries,
  computeCodeHash,
  engineEpochFromCodeHash,
  REPO_ROOT,
} from '../../../tools/agent/codehash-files'
import { computeCodeHash, engineEpochFromCodeHash } from '../../../tools/agent/codehash-files'

/** 训练机 git_commit（纯观测字段；拿不到传 'nogit'）。 */
export function gitCommit(repoRoot: string): string {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' }).trim()
  } catch {
    return 'nogit'
  }
}

/** engine_epoch = sha256(codeHash)[0:16]（与 dist_common.compute_engine_epoch 同式）。 */
export function engineEpoch(codeHash: string): string {
  return engineEpochFromCodeHash(codeHash)
}

/** 一步算出 epoch + codeHash（同一次展开，避免重复 tree walk）。 */
export function computeEngineEpoch(): {
  engine_epoch: string
  codeHash: string
} {
  const codeHash = computeCodeHash()
  return { engine_epoch: engineEpochFromCodeHash(codeHash), codeHash }
}
