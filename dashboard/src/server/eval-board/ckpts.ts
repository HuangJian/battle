/** ckpts.ts — ckpt / iter 发现与评估权重清单视图（只读元数据，不读内容）。 */
import { readdirSync, statSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import type { EvalCkptFile, EvalCkptsView } from '../../web/view'

/** `*.it<N>.*.json` 文件名 → N（R3/D-b：iter 不靠前端手传）。无匹配 ⇒ null。 */
export function iterFromCkpt(ckpt: string): number | null {
  const m = /\.it(\d+)\./.exec(path.basename(ckpt))
  return m ? Number(m[1]) : null
}

// ────────────────────────── R7 ckpt/iter 发现（只读元数据，不读内容） ──────────────────────────

const WEIGHTS_DIR = path.join(REPO_ROOT, 'nn-training', 'weights')
/** 单次返回文件数上限（腿很大时防 payload 爆）。 */
const CKPT_MAX_FILES = 500

/**
 * GET /api/evalCkpts（R7）：扫 `nn-training/weights/<leg>/` + `tmp/<course>/weights.json`。
 * 硬约束：**只 stat，不读内容**（996 文件 / 434MB，读内容会把 TTL 打穿）。
 * 懒加载：无 `leg` 时只返回腿列表；`leg` = 某腿名时返回该腿文件明细；
 * `course` 与某腿同名则默认取该腿（首页 iter select 一次请求）。
 */
export function buildEvalCkptsView(course = '', leg = ''): EvalCkptsView {
  const legs: EvalCkptsView['legs'] = []
  let names: string[] = []
  try {
    names = readdirSync(WEIGHTS_DIR, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
      .sort()
  } catch {
    names = []
  }
  for (const name of names) {
    let count = 0
    try {
      count = readdirSync(path.join(WEIGHTS_DIR, name)).filter((f) => f.endsWith('.json')).length
    } catch {
      /* 不可读腿跳过明细，仅计 0 */
    }
    legs.push({ leg: name, count })
  }
  // 路径安全：leg 必须命中已知腿名（防 ../ 穿越）。
  const target =
    (leg && names.includes(leg) ? leg : '') || (course && names.includes(course) ? course : '')
  const files: EvalCkptFile[] = []
  let truncated = false
  if (target) {
    let jsonFiles: string[] = []
    try {
      jsonFiles = readdirSync(path.join(WEIGHTS_DIR, target)).filter((f) => f.endsWith('.json'))
    } catch {
      jsonFiles = []
    }
    for (const f of jsonFiles) {
      if (files.length >= CKPT_MAX_FILES) {
        truncated = true
        break
      }
      const abs = path.join(WEIGHTS_DIR, target, f)
      let st: ReturnType<typeof statSync>
      try {
        st = statSync(abs)
      } catch {
        continue
      }
      files.push({
        leg: target,
        path: path.relative(REPO_ROOT, abs).replace(/\\/g, '/'),
        mtime: st.mtimeMs,
        sizeBytes: st.size,
        iter: iterFromCkpt(f),
      })
    }
    files.sort((a, b) => b.mtime - a.mtime)
  }
  // 活动权重兜底（tmp/<course>/weights.json，若存在）。
  if (course) {
    const act = path.join(REPO_ROOT, 'tmp', course, 'weights.json')
    try {
      const st = statSync(act)
      files.unshift({
        leg: '(active)',
        path: path.relative(REPO_ROOT, act).replace(/\\/g, '/'),
        mtime: st.mtimeMs,
        sizeBytes: st.size,
        iter: iterFromCkpt(act),
      })
    } catch {
      /* 无活动权重 */
    }
  }
  return { course, legs, files, truncated }
}

/** R4-G1 心跳（runner_state.json）：训练侧唯一写者，console 只读；缺失 ⇒ null。 */
