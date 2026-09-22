/** evalboard-incremental-ingest.test.ts ↔ dashboard/src/server/eval-board/ingest.ts 的
 *  增量入账：按字节偏移只读 eval_log 的新增尾巴 + 去重键集 memo。 */
import { afterEach, beforeEach, describe, expect, it } from 'bun:test'
import {
  appendFileSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  statSync,
  truncateSync,
  writeFileSync,
} from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { ingestCourseEvalLog, knownKeysFor } from '../src/server/eval-board/ingest'
import {
  readTail,
  readTailDrained,
  TAIL_MAX_BYTES,
  type TailPos as EvalLogPos,
} from '../src/server/eval-board/tail-read'
import { dedupKeyOf, gamesDir, loadDedupKeys, monthFile } from '../src/evalboard/store'

const COURSE = 'inc-test-course'

let DIR = ''
let LOGS = ''
let DATA = ''
let LOG = ''
let prevLogs: string | undefined
let prevData: string | undefined

beforeEach(() => {
  DIR = mkdtempSync(path.join(tmpdir(), 'evb-inc-'))
  LOGS = path.join(DIR, 'tmp')
  DATA = path.join(DIR, 'data', 'evalboard')
  LOG = path.join(LOGS, COURSE, 'eval_log.jsonl')
  mkdirSync(path.dirname(LOG), { recursive: true })
  prevLogs = process.env.BCITY_TMP_LOGS_DIR
  prevData = process.env.EVALBOARD_DATA
  process.env.BCITY_TMP_LOGS_DIR = LOGS
  process.env.EVALBOARD_DATA = DATA
})

afterEach(() => {
  if (prevLogs === undefined) delete process.env.BCITY_TMP_LOGS_DIR
  else process.env.BCITY_TMP_LOGS_DIR = prevLogs
  if (prevData === undefined) delete process.env.EVALBOARD_DATA
  else process.env.EVALBOARD_DATA = prevData
  rmSync(DIR, { recursive: true, force: true })
})

/** 一行 eval 事件（`!dup` 标记：只用于「不该再被读到的旧字节」场景）。 */
const evalLine = (seed: number, tag = ''): string =>
  `${JSON.stringify({
    event: 'eval',
    iter: 10,
    wver: 'w1',
    stage: 0,
    seed,
    outcome: 'stage_clear',
    win: 1,
    tag,
  })}\n`

const storeLines = (): string[] => {
  const out: string[] = []
  const dir = gamesDir(DATA)
  try {
    for (const f of readdirSync(dir)) {
      out.push(...readFileSync(path.join(dir, f), 'utf-8').split('\n').filter(Boolean))
    }
  } catch {
    /* 无 store */
  }
  return out
}

describe('readTail（按字节偏移读新增尾巴）', () => {
  it('首读全量；末行没写完则留到下一拍，写完才消费', () => {
    writeFileSync(LOG, evalLine(860001) + evalLine(860002) + '{"event":"eval","seed":860003')
    const first = readTail(LOG, undefined)
    expect(first.reset).toBe(true)
    expect(first.lines.length).toBe(2)

    // 半行仍留在文件里且**没有被消费**（下一拍它才是完整的一行）
    const half = readTail(LOG, first.next)
    expect(half.lines).toEqual([])
    expect(half.next.offset).toBe(first.next.offset)

    appendFileSync(LOG, '}\n')
    const rest = readTail(LOG, first.next)
    expect(rest.lines.length).toBe(1)
    expect(JSON.parse(rest.lines[0]).seed).toBe(860003)

    // 再读一拍：没有新字节
    expect(readTail(LOG, rest.next).lines).toEqual([])
  })

  it('截断 → 从头重读（reset）', () => {
    writeFileSync(LOG, `${evalLine(860001)}${evalLine(860002)}`)
    const first = readTail(LOG, undefined)
    truncateSync(LOG, 0)
    appendFileSync(LOG, evalLine(860003))
    const after = readTail(LOG, first.next)
    expect(after.reset).toBe(true)
    expect(after.lines.length).toBe(1)
    expect(JSON.parse(after.lines[0]).seed).toBe(860003)
  })

  it('同尺寸原地改写（体积与 inode 都不变）→ 偏移不可信，整份重读', () => {
    writeFileSync(LOG, evalLine(860001))
    const first = readTail(LOG, undefined)
    writeFileSync(LOG, evalLine(860009)) // 同字节数：只有 mtime 变了
    const after = readTail(LOG, first.next)
    expect(after.reset).toBe(true)
    expect(JSON.parse(after.lines[0]).seed).toBe(860009)
  })

  it('没有新字节时偏移不动（快照字段只有同一份记录的推进）', () => {
    writeFileSync(LOG, evalLine(860001))
    const first = readTail(LOG, undefined)
    const again = readTail(LOG, first.next)
    expect(again.lines).toEqual([])
    expect(again.reset).toBe(false)
    expect(again.next.offset).toBe(first.next.offset)
    expect(again.next.size).toBe(first.next.size)
  })

  it('单行比窗口还长时整窗消费，不卡死', () => {
    writeFileSync(LOG, `{"event":"eval","pad":"${'x'.repeat(200)}"}\n`)
    let prev: EvalLogPos | undefined
    let lines: string[] = []
    // 有界循环：偏移必须每拍前进，否则永远读不动（这里的 20 拍 >> 文件/32 字节）
    for (let i = 0; i < 20 && lines.length === 0; i++) {
      const t = readTail(LOG, prev, 32)
      expect(t.next.offset).toBeGreaterThan(prev?.offset ?? -1)
      prev = t.next
      lines = t.lines
    }
    expect(lines.length).toBe(1)
    expect(TAIL_MAX_BYTES).toBeGreaterThan(32)
  })

  it('文件大于单次窗口 → readTailDrained 一次读完整条尾巴（分片/日志超 4MB 的必经路）', () => {
    const many = Array.from({ length: 40 }, (_, i) => evalLine(860001 + i))
    writeFileSync(LOG, many.join(''))
    // 单窗口：只读到窗口内的行（会丢尾巴——正是不能直接上生产的原因）
    const oneWindow = readTail(LOG, undefined, 256)
    expect(oneWindow.lines.length).toBeLessThan(40)
    const drained = readTailDrained(LOG, undefined, 256)
    expect(drained.lines.length).toBe(40)
    expect(drained.next.offset).toBe(statSync(LOG).size)
    // 增量尾巴同样能跨窗口读完
    appendFileSync(LOG, many.slice(0, 10).join(''))
    const more = readTailDrained(LOG, drained.next, 256)
    expect(more.lines.length).toBe(10)
    expect(more.reset).toBe(false)
  })

  it('文件不存在 → reset 且不抛', () => {
    const r = readTail(path.join(DIR, 'nope.jsonl'), undefined)
    expect(r.reset).toBe(true)
    expect(r.lines).toEqual([])
  })
})

describe('ingestCourseEvalLog（增量）', () => {
  it('首读全量、再读只认新增行', () => {
    writeFileSync(LOG, evalLine(860001) + evalLine(860002))
    expect(ingestCourseEvalLog(COURSE)).toBe(2)
    expect(ingestCourseEvalLog(COURSE)).toBe(0)
    appendFileSync(LOG, evalLine(860003))
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    expect(storeLines().length).toBe(3)
  })

  it('末尾半行不入账，补齐后下一拍入账', () => {
    writeFileSync(LOG, evalLine(860001))
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    appendFileSync(LOG, '{"event":"eval","iter":10,"wver":"w1","stage":0,"seed":860002,')
    expect(ingestCourseEvalLog(COURSE)).toBe(0)
    appendFileSync(LOG, '"outcome":"stage_clear","win":1}\n')
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    expect(storeLines().length).toBe(2)
  })

  it('已消费的字节不再重读（旧行只有被改写才回看，且是全读而非部分读）', () => {
    writeFileSync(LOG, evalLine(860001) + evalLine(860002))
    expect(ingestCourseEvalLog(COURSE)).toBe(2)
    // 同尺寸覆盖**首行**：偏移不可信 ⇒ 整份重读；旧行去重丢掉，只有新行入账
    writeFileSync(LOG, evalLine(860009) + evalLine(860002))
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    expect(storeLines().length).toBe(3)
  })

  it('日志被截断重开（轮转）→ 从头重读、新行照常入账', () => {
    writeFileSync(LOG, evalLine(860001))
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    truncateSync(LOG, 0)
    appendFileSync(LOG, evalLine(860002))
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    expect(storeLines().length).toBe(2)
  })

  it('无课程 / 无日志 → 0', () => {
    expect(ingestCourseEvalLog('')).toBe(0)
    expect(ingestCourseEvalLog('no-such-course')).toBe(0)
  })
})

describe('knownKeysFor（去重键集 memo）', () => {
  it('store 未变则复用同一份键集；本进程 append 后仍命中', () => {
    const before = knownKeysFor(DATA)
    writeFileSync(LOG, evalLine(860001))
    expect(ingestCourseEvalLog(COURSE)).toBe(1)
    const again = knownKeysFor(DATA)
    expect(again).toBe(before) // 指纹被自己的 append 刷新过，不自我失效
    expect(
      again.has(dedupKeyOf({ run_id: COURSE, iter: 10, wver: 'w1', stage_id: '0', seed: 860001 })),
    ).toBe(true)
  })

  it('外部进程改了 store（回填/另一个控制台）→ 重载并看到新键', () => {
    const before = knownKeysFor(DATA)
    const row = {
      run_id: 'other',
      iter: 1,
      wver: 'w9',
      stage_id: '0',
      seed: 860050,
    }
    const dir = gamesDir(DATA)
    mkdirSync(dir, { recursive: true })
    appendFileSync(
      path.join(dir, monthFile(new Date().toISOString())),
      `${JSON.stringify({ ...row, schema: 1 })}\n`,
    )
    const after = knownKeysFor(DATA)
    expect(after).not.toBe(before)
    expect(after.has(dedupKeyOf(row))).toBe(true)
    expect(after.size).toBe(loadDedupKeys(DATA).size)
  })
})
