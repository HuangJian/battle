#!/usr/bin/env bun
/**
 * paired-pd.ts — 同种子配对 verdict：pd / Δ / McNemar 精确检验 + 分关。
 * 口径：docs/nn.progress.md §50（x3-step：锚点 200 对 b01=7/b10=4 pd=5.5%；
 * 800-verdict b01=12/b10=26 pd=4.75%）；T5「换尺」判决的 pd 尺。
 *
 * 输入：eval-course-ckpt.ts 的两份 JSONL（同 course / 同 games / 同 seed0
 * ⇒ 逐行天然配对；两行都有 stageId+seed 时逐行校验配对键，失配 >0 直接退出）。
 *
 * 用法：
 *   bun tools/sim/paired-pd.ts --base <baseline.jsonl> --cand <candidate.jsonl>
 *     [--out <report.md>] [--need-delta 0.05] [--need-wins 616] [--alpha 0.05]
 *
 * 输出：pooled（n / base胜 / cand胜 / Δpp / b01 / b10 / pd / McNemar 单侧+双侧p）
 * ＋分 stageId＋门禁行（Δ≥need-delta 且 单侧p<alpha 且 candWins≥need-wins ⇒ PASS）。
 * 退出码恒 0（纯报告器；FAIL 不改退出码——判决由读者按预注册口径做，不由工具代判）。
 */
import { readFileSync, writeFileSync } from 'node:fs'

export interface VerdictRow {
  win: boolean | number
  seed?: number
  stageId?: number
  stageName?: string
}

export interface StageStat {
  stageId: string
  stageName: string
  n: number
  baseWins: number
  candWins: number
  deltaPp: number
  b01: number
  b10: number
}

export interface PairedSummary {
  n: number
  baseWins: number
  candWins: number
  deltaPp: number
  b01: number
  b10: number
  pd: number
  /** 单侧精确 p（H1: cand > base；成功 = 仅 cand 胜的 b01 对）。 */
  pOneSided: number
  pTwoSided: number
  perStage: StageStat[]
}

/** P(X>=k), X~Bin(n, 0.5)——递推算项，n 到几千无溢出。 */
export function binomUpper(k: number, n: number): number {
  if (n === 0) return 1
  if (k <= 0) return 1
  if (k > n) return 0
  let term = Math.pow(0.5, n) // i=0 项
  let cum = 0 // P(X>=k)
  // 先走到 i=k，再累加尾部（头部和由对称性得，不必逐项）。
  for (let i = 0; i < k; i++) term = (term * (n - i)) / (i + 1)
  for (let i = k; i <= n; i++) {
    cum += term
    term = (term * (n - i)) / (i + 1)
  }
  return Math.min(1, cum)
}

export function mcnemar(b01: number, b10: number): { pOneSided: number; pTwoSided: number } {
  const n = b01 + b10
  if (n === 0) return { pOneSided: 1, pTwoSided: 1 }
  const pGe = binomUpper(b01, n) // P(X>=b01)：cand 方向
  const pLe = 1 - binomUpper(b01 + 1, n) // P(X<=b01)
  return { pOneSided: pGe, pTwoSided: Math.min(1, 2 * Math.min(pGe, pLe)) }
}

const asWin = (w: boolean | number): number => (w === true || w === 1 ? 1 : 0)

export function summarize(base: VerdictRow[], cand: VerdictRow[]): PairedSummary {
  if (base.length !== cand.length)
    throw new Error(`行数不等：base=${base.length} cand=${cand.length}`)
  const n = base.length
  let baseWins = 0
  let candWins = 0
  let b01 = 0
  let b10 = 0
  const stages = new Map<string, { name: string; idx: number[] }>()
  for (let i = 0; i < n; i++) {
    const b = asWin(base[i].win)
    const c = asWin(cand[i].win)
    baseWins += b
    candWins += c
    if (b === 0 && c === 1) b01++
    if (b === 1 && c === 0) b10++
    const sid = String(base[i].stageId ?? cand[i].stageId ?? 'all')
    let s = stages.get(sid)
    if (!s) {
      s = { name: String(base[i].stageName ?? cand[i].stageName ?? ''), idx: [] }
      stages.set(sid, s)
    }
    s.idx.push(i)
  }
  const perStage: StageStat[] = [...stages.entries()].map(([sid, s]) => {
    const bb = s.idx.reduce((a, i) => a + asWin(base[i].win), 0)
    const cc = s.idx.reduce((a, i) => a + asWin(cand[i].win), 0)
    const o1 = s.idx.filter((i) => asWin(base[i].win) === 0 && asWin(cand[i].win) === 1).length
    const o0 = s.idx.filter((i) => asWin(base[i].win) === 1 && asWin(cand[i].win) === 0).length
    return {
      stageId: sid,
      stageName: s.name,
      n: s.idx.length,
      baseWins: bb,
      candWins: cc,
      deltaPp: ((cc - bb) / s.idx.length) * 100,
      b01: o1,
      b10: o0,
    }
  })
  const { pOneSided, pTwoSided } = mcnemar(b01, b10)
  return {
    n,
    baseWins,
    candWins,
    deltaPp: ((candWins - baseWins) / n) * 100,
    b01,
    b10,
    pd: (b01 + b10) / n,
    pOneSided,
    pTwoSided,
    perStage,
  }
}

/** 逐行校验配对键；返回失配数（无键可验时返回 -1）。 */
export function checkPairing(base: VerdictRow[], cand: VerdictRow[]): number {
  let checkable = 0
  let mism = 0
  for (let i = 0; i < base.length; i++) {
    const bk = base[i].stageId !== undefined && base[i].seed !== undefined
    const ck = cand[i].stageId !== undefined && cand[i].seed !== undefined
    if (bk && ck) {
      checkable++
      if (base[i].stageId !== cand[i].stageId || base[i].seed !== cand[i].seed) mism++
    }
  }
  return checkable === 0 ? -1 : mism
}

export function renderText(
  s: PairedSummary,
  gate: { needDelta: number; needWins: number; alpha: number },
): string {
  const L: string[] = []
  L.push(
    `pooled n=${s.n} base=${s.baseWins}(${((100 * s.baseWins) / s.n).toFixed(2)}%) ` +
      `cand=${s.candWins}(${((100 * s.candWins) / s.n).toFixed(2)}%) ` +
      `Δ=${s.deltaPp >= 0 ? '+' : ''}${s.deltaPp.toFixed(2)}pp`,
  )
  L.push(
    `b01=${s.b01} b10=${s.b10} pd=${(100 * s.pd).toFixed(2)}% ` +
      `McNemar单侧(cand>base)p=${s.pOneSided.toFixed(4)} 双侧p=${s.pTwoSided.toFixed(4)}`,
  )
  for (const st of s.perStage) {
    const nm = st.stageName ? `(${st.stageName})` : ''
    L.push(
      `stage${st.stageId}${nm} n=${st.n} base=${st.baseWins} cand=${st.candWins} ` +
        `Δ=${st.deltaPp >= 0 ? '+' : ''}${st.deltaPp.toFixed(2)}pp b01=${st.b01} b10=${st.b10}`,
    )
  }
  const pass =
    s.deltaPp / 100 >= gate.needDelta && s.pOneSided < gate.alpha && s.candWins >= gate.needWins
  L.push(
    `GATE Δ≥${(gate.needDelta * 100).toFixed(2)}pp & 单侧p<${gate.alpha} & candWins≥${gate.needWins} ⇒ ${pass ? 'PASS' : 'FAIL'}`,
  )
  return L.join('\n')
}

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

export function readRows(path: string): VerdictRow[] {
  return readFileSync(path, 'utf8')
    .split('\n')
    .filter((l) => l.trim().length > 0)
    .map((l) => JSON.parse(l) as VerdictRow)
}

function main(): void {
  const basePath = arg('base')
  const candPath = arg('cand')
  if (!basePath || !candPath) {
    console.error('[paired-pd] --base <baseline.jsonl> --cand <candidate.jsonl> required')
    process.exit(2)
  }
  const base = readRows(basePath)
  const cand = readRows(candPath)
  const mism = checkPairing(base, cand)
  if (mism === -1) console.error('[paired-pd] warn: 无 stageId+seed 键，按行序配对')
  if (mism > 0) {
    console.error(
      `[paired-pd] 配对键失配 ${mism} 行，拒绝判决（检查两批是否同 course/games/seed0）`,
    )
    process.exit(2)
  }
  const s = summarize(base, cand)
  const text = renderText(s, {
    needDelta: Number(arg('need-delta') ?? 0.05),
    needWins: Number(arg('need-wins') ?? 0),
    alpha: Number(arg('alpha') ?? 0.05),
  })
  console.log(text)
  const out = arg('out')
  if (out) writeFileSync(out, `# paired-pd\n\n\`\`\`\n${text}\n\`\`\`\n`)
}

if (import.meta.main) main()
