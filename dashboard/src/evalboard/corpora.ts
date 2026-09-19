/** corpora.ts — 判决语料注册表（P2，2026-09-19「中方案」）。
 *
 * 与 `ladder.json` 分家（见 `corpora.json` 的 `_comment`）：ladder rung 承载
 * 「arena 阶梯几何 + seg=k%16 推进」的语义，判决语料是「课程关卡文件 stages ×
 * 池外 seed 段 × 多 ckpt」——混进 rungs 会污染阶梯推进与去重键空间。
 *
 * 双侧契约：Python 侧 `nn-training/rl/batch_eval.py::{load_corpora,corpus_doc,
 * plan_verdict_units}` 是同协议的镜像实现（展开 unit 在 Python，本模块只做
 * 声明层的读取/校验与 console 侧身份派生）。改一侧必须同步另一侧。
 */

import { existsSync, readFileSync } from 'fs'
import path from 'path'

/** 语料声明：课程关卡文件 + 池外 seed 段。 */
export interface VerdictCorpus {
  id: string
  /** 关卡名（解析为 `nn-training/levels/<level>.jsonc`）。 */
  level: string
  /** 池外段首种子（与训练池/双轨段/已用池外段不相交，§15.1）。 */
  seed0: number
  /** 每关局数（总局数 = 关卡数 × games_per_stage）。 */
  games_per_stage: number
  policy?: 'nn' | 'god'
  note?: string
}

export interface CorporaDoc {
  version: string
  corpora: VerdictCorpus[]
}

/** 注册表路径（`EVALBOARD_CORPORA` 覆盖——与 `EVALBOARD_DATA` 同惯例，冒烟/单测用）。 */
export function corporaPath(repoRoot: string): string {
  return (
    process.env.EVALBOARD_CORPORA ??
    path.join(repoRoot, 'dashboard', 'src', 'evalboard', 'corpora.json')
  )
}

/**
 * 读注册表并做**形态校验**（坏行响亮失败，不静默跳过 —— 静默跳过会让判决跑在
 * 空语料上，读数看似正常却什么都没测）。
 */
export function loadCorpora(repoRoot: string): CorporaDoc {
  const p = corporaPath(repoRoot)
  if (!existsSync(p)) throw new Error(`corpora.json 缺失: ${p}`)
  const doc = JSON.parse(readFileSync(p, 'utf-8')) as CorporaDoc
  if (!doc || !Array.isArray(doc.corpora)) throw new Error(`corpora.json 形态非法: ${p}`)
  const seen = new Set<string>()
  for (const c of doc.corpora) {
    if (!c || typeof c.id !== 'string' || !c.id) throw new Error('corpora[].id 缺失')
    if (seen.has(c.id)) throw new Error(`corpora id 重复: ${c.id}`)
    seen.add(c.id)
    if (typeof c.level !== 'string' || !c.level) throw new Error(`corpus ${c.id}: level 缺失`)
    if (!Number.isInteger(c.seed0) || c.seed0 <= 0)
      throw new Error(`corpus ${c.id}: seed0 非法 (${c.seed0})`)
    if (!Number.isInteger(c.games_per_stage) || c.games_per_stage <= 0)
      throw new Error(`corpus ${c.id}: games_per_stage 非法 (${c.games_per_stage})`)
    if (c.policy !== undefined && c.policy !== 'nn' && c.policy !== 'god')
      throw new Error(`corpus ${c.id}: policy 非法 (${c.policy})`)
  }
  return doc
}

/** 按 id 取语料；未知 id 抛（响亮失败）。 */
export function corpusOf(repoRoot: string, id: string): VerdictCorpus {
  const c = loadCorpora(repoRoot).corpora.find((x) => x.id === id)
  if (!c) throw new Error(`未知判决语料 id: ${id}（见 ${corporaPath(repoRoot)}）`)
  return c
}

/** 语料 seed 集（`seed0 + 0..games_per_stage-1`）——与 Python planner 同式。 */
export function seedsOfCorpus(c: VerdictCorpus): number[] {
  return Array.from({ length: c.games_per_stage }, (_, i) => c.seed0 + i)
}

/**
 * 判决批身份（去重键）：语料 id + ckpt 标签序列。
 * **顺序敏感** —— 配对读数的 ckpt 顺序是语义的一部分，不是排序无关集。
 */
export function verdictKeyOf(
  corpus: string,
  ckpts: Array<{ label?: string; path: string }>,
): string {
  const labels = ckpts.map((c) => c.label || c.path)
  return `verdict|${corpus}|${labels.join(',')}`
}
