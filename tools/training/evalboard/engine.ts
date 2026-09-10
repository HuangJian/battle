/** engine.ts — engine_epoch（plan/rl-eval-system.md §2.5）。
 *
 * `engine_epoch = hash(训练机 git_commit + gameplay 文件集指纹)`。
 * gameplay 文件集 = src/game/** + src/config/** + src/utils/**（含 RNG）
 * + src/ai/**（God AI）+ tools/sim/export-eval-game.ts；
 * God 侧借用 freeze golden tools/det-golden.v1.sha256 作行为指纹分量。
 *
 * 为什么必须另立（§2.5）：codehash-files.txt 集内只有 src/nn + tools/agent/*
 * + tools/sim/export-* + src/types.ts + src/game/SimulationCombat.ts，不覆盖
 * 引擎主体/config/RNG/God AI。改这些不改名 codeHash ⇒ stale 节点产出不同
 * gameplay 仍过门。不扩 codehash-files.txt（升级波前科 + 两套生命周期）。
 */

import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'

/**
 * gameplay 文件集的表与配方唯一源 = tools/agent/codehash-files.ts
 * （GAMEPLAY_SPECS + collectSpecEntries + gameplayFingerprint，集内文件，
 * 改表/改配方即触发节点升级波）。本模块只做训练机侧 plumbing（git_commit 注入）。
 */
export { GAMEPLAY_SPECS, collectSpecEntries, gameplayFingerprint } from '../../agent/codehash-files'
import { gameplayFingerprint } from '../../agent/codehash-files'

/** 训练机 git_commit（拿不到传 'nogit'，见 engineEpoch 注释）。 */
export function gitCommit(repoRoot: string): string {
  try {
    return execFileSync('git', ['rev-parse', 'HEAD'], { cwd: repoRoot, encoding: 'utf8' }).trim()
  } catch {
    return 'nogit'
  }
}

/**
 * engine_epoch = sha256(git_commit + '\n' + gameplayFingerprint)[0:16]。
 * `gitCommit` 由调用方传入（训练机 `git rev-parse HEAD`；拿不到传 'nogit'，
 * 此时 epoch 只保证"同机同代码一致"，跨机比较需配对 git_commit 字段）。
 */
export function engineEpoch(gitCommit: string, gameplayHash: string): string {
  return createHash('sha256').update(`${gitCommit}\n${gameplayHash}`).digest('hex').slice(0, 16)
}

/** 一步算出 epoch（repoRoot + gitCommit）。 */
export function computeEngineEpoch(
  repoRoot: string,
  gitCommit: string,
): {
  engine_epoch: string
  gameplayHash: string
} {
  const gameplayHash = gameplayFingerprint(repoRoot)
  return { engine_epoch: engineEpoch(gitCommit, gameplayHash), gameplayHash }
}
