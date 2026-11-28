/**
 * prereg.ts — P′-0 机器可读预注册的断言器（纯函数，可单测）。
 *
 * 计划 §8.3/§8.6：每道门必须写成 `metric + 分母 + 阈值 + 池 + seed + 命令`，
 * 只写文字不算门。本函数把 `plan/hier-options-prereg.v1.json` 的结构固定住：
 *   · 三池互斥（seed 区间两两不交）且 stage 集合一致；
 *   · 阈值存在且为数字（不是 "X%" 文字）；
 *   · 每张卡的形状命令非空；
 *   · 700 开发目标 = 0.85、90 两阶段、95 延后。
 */

export interface PreregValidation {
  ok: boolean
  errors: string[]
}

export interface SeedRange {
  from: number
  to: number
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** 解析 "123-456"（含端点）。非法返回 null。 */
export function parseSeedSpec(spec: unknown): SeedRange | null {
  if (typeof spec !== 'string') return null
  const m = /^(\d+)-(\d+)$/.exec(spec.trim())
  if (!m) return null
  const from = Number(m[1])
  const to = Number(m[2])
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || to < from) return null
  return { from, to }
}

function nonEmptyString(v: unknown): v is string {
  return typeof v === 'string' && v.trim().length > 0
}

function positiveInt(v: unknown): boolean {
  return typeof v === 'number' && Number.isInteger(v) && v > 0
}

function numberIn01(v: unknown): boolean {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 1
}

export function validatePrereg(data: unknown): PreregValidation {
  const errors: string[] = []
  const err = (m: string): void => {
    errors.push(m)
  }
  if (!isObj(data)) return { ok: false, errors: ['prereg root is not an object'] }

  if (data.version !== 1) err('version must be 1')

  const task = data.task
  if (!isObj(task)) err('task missing')
  else {
    if (task.level !== 'classic35') err('task.level must be classic35')
    if (task.difficulty !== 'hard') err('task.difficulty must be hard')
    if (task.lives !== 3) err('task.lives must be 3')
    if (task.stars !== 0) err('task.stars must be 0')
    if (task.enemies !== 20) err('task.enemies must be 20')
    if (task.maxTicks !== 36000) err('task.maxTicks must be 36000')
  }

  const stages = data.stages
  if (!isObj(stages) || !nonEmptyString(stages.ids)) err('stages.ids missing')

  const pools = data.pools
  const poolNames = ['mining', 'selection', 'sealed'] as const
  const ranges: Array<{ name: string; range: SeedRange }> = []
  let poolStages: string | null = null
  if (!isObj(pools)) {
    err('pools missing')
  } else {
    for (const name of poolNames) {
      const p = pools[name]
      if (!isObj(p)) {
        err(`pools.${name} missing`)
        continue
      }
      if (!nonEmptyString(p.stages)) err(`pools.${name}.stages missing`)
      else if (poolStages === null) poolStages = p.stages
      else if (p.stages !== poolStages) err(`pools.${name}.stages differs from pools.mining`)
      const range = parseSeedSpec(p.seeds)
      if (!range) err(`pools.${name}.seeds is not a valid "from-to" range`)
      else ranges.push({ name, range })
    }
  }
  for (let i = 0; i < ranges.length; i++) {
    for (let j = i + 1; j < ranges.length; j++) {
      const a = ranges[i]
      const b = ranges[j]
      if (a.range.from <= b.range.to && b.range.from <= a.range.to) {
        err(`pools.${a.name}.seeds overlaps pools.${b.name}.seeds`)
      }
    }
  }

  const baseline = data.baseline
  if (!isObj(baseline)) err('baseline missing')
  else {
    if (!nonEmptyString(baseline.name)) err('baseline.name missing')
    if (typeof baseline.b8 !== 'boolean') err('baseline.b8 must be boolean')
    if (typeof baseline.b10 !== 'boolean') err('baseline.b10 must be boolean')
    if (baseline.playerLevel !== 0) err('baseline.playerLevel must be 0')
  }

  const options = data.options
  if (!isObj(options)) err('options missing')
  else {
    if (options.classes !== 3) err('options.classes must be 3')
    if (!Array.isArray(options.modifiers) || options.modifiers.length !== 2) {
      err('options.modifiers must list targetKind and pathMode')
    }
    const commit = options.commit
    if (
      !Array.isArray(commit) ||
      commit.length !== 3 ||
      commit[0] !== 120 ||
      commit[1] !== 240 ||
      commit[2] !== 360
    ) {
      err('options.commit must be [120, 240, 360]')
    }
  }

  const p2a = data.p2a
  if (!isObj(p2a)) {
    err('p2a missing')
  } else {
    for (const arm of ['A', 'B'] as const) {
      const a = p2a[arm]
      if (!isObj(a)) {
        err(`p2a.${arm} missing`)
        continue
      }
      if (!nonEmptyString(a.metric)) err(`p2a.${arm}.metric missing`)
      if (!positiveInt(a.confirm_n)) err(`p2a.${arm}.confirm_n must be a positive integer`)
      if (typeof a.confirm_lcb !== 'number') err(`p2a.${arm}.confirm_lcb must be a number`)
    }
    const A = p2a.A
    if (isObj(A) && !positiveInt(A.screening_n)) err('p2a.A.screening_n must be a positive integer')
    const B = p2a.B
    if (isObj(B) && B.mode !== 'option-only') err('p2a.B.mode must be option-only')
  }

  const p2b = data.p2b
  if (!isObj(p2b)) err('p2b missing')
  else {
    if (!positiveInt(p2b.pilot_n)) err('p2b.pilot_n must be a positive integer')
    if (!positiveInt(p2b.confirm_n)) err('p2b.confirm_n must be a positive integer')
    if (!nonEmptyString(p2b.return_primary)) err('p2b.return_primary missing')
    if (!Array.isArray(p2b.H_confirm) || p2b.H_confirm.length === 0) {
      err('p2b.H_confirm must be a non-empty array')
    }
  }

  const p3 = data.p3
  if (!isObj(p3)) err('p3 missing')
  else {
    if (p3.byteIdentical !== true) err('p3.byteIdentical must be true')
    if (!positiveInt(p3.quality_n)) err('p3.quality_n must be a positive integer')
    if (typeof p3.otherMax !== 'number') err('p3.otherMax must be a number')
  }

  const p4 = data.p4
  if (!isObj(p4)) err('p4 missing')
  else {
    if (!numberIn01(p4.top1Acc)) err('p4.top1Acc must be a number in [0,1]')
    if (typeof p4.nonInferior !== 'number') err('p4.nonInferior must be a number')
  }

  const ev = data.eval
  if (!isObj(ev)) {
    err('eval missing')
  } else {
    if (ev.dev700Target !== 0.85) err('eval.dev700Target must be 0.85')
    if (!nonEmptyString(ev.p90)) err('eval.p90 missing')
    if (ev.p95 !== 'deferred') err("eval.p95 must be 'deferred'")
  }

  const commands = data.commands
  if (!isObj(commands)) {
    err('commands missing')
  } else {
    for (const key of ['p0', 'p1', 'p2a', 'p2b', 'b8', 'b10']) {
      if (!nonEmptyString(commands[key])) err(`commands.${key} missing`)
    }
  }

  return { ok: errors.length === 0, errors }
}
