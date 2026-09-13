/** export-eval-game-replay.test.ts — runEvalOne 的可选录制（replayDir 参数）：
 *  .replay 往返可解析、帧数 = ticks、envelope 语义（seed/outcome/status）、
 *  同 seed 两次重放逐字节一致（确定性契约）、不传 replayDir 时零副作用。 */

import { afterAll, describe, expect, it } from 'bun:test'
import { mkdtempSync, readdirSync, readFileSync, rmSync } from 'fs'
import os from 'os'
import path from 'path'
import { STAGES } from '../src/config/stages'
import { parseReplayFile } from '../src/replay/file'
import { runEvalOne } from '../tools/sim/export-eval-game'

const dirs: string[] = []
afterAll(() => {
  for (const d of dirs) rmSync(d, { recursive: true, force: true })
})

function replayFiles(dir: string): string[] {
  return readdirSync(dir).filter((f) => f.endsWith('.replay'))
}

/** god 策略（免权重文件）跑一小段：120 ticks 内无终局 → timeout 收场。 */
function runGod(replayDir = '', seed = 860001, maxTicks = 120) {
  return runEvalOne(0, STAGES[0], seed, 'hard', maxTicks, '', 'god', '', '', 0, 0, null, null, replayDir)
}

describe('export-eval-game --replay', () => {
  it('录制 → canonical 文件名落盘 → parseReplayFile 往返', () => {
    const replayDir = mkdtempSync(path.join(os.tmpdir(), 'eval-replay-'))
    dirs.push(replayDir)
    const res = runGod(replayDir)
    const files = replayFiles(replayDir)
    expect(files.length).toBe(1)
    // canonical：hard-s01-<status>-l<lives>-t<sec>-seed860001.replay（stage 段 1-based）
    expect(files[0]).toMatch(/^hard-s01-(timeout|died|base)-l\d+-t2-seed860001\.replay$/)

    const parsed = parseReplayFile(readFileSync(path.join(replayDir, files[0]), 'utf8'))
    if ('error' in parsed) throw new Error(`parse failed: ${parsed.error}`)
    const { replay, envelope } = parsed
    expect(envelope.source).toBe('sim')
    expect(replay.seed).toBe(860001)
    expect(replay.totalTicks).toBe(120)
    expect(replay.frames.length).toBe(121) // v1 帧流 = 1 版本字节 + 120 帧
    expect(envelope.sim?.status).toBe('timeout')
    expect(envelope.sim?.outcome).toBe(res.outcome)
    expect(replay.metadata.stage).toBe(0)
    expect(replay.tickHashes?.length).toBeGreaterThan(0)
    expect(res.finalLives).toBeGreaterThan(0)
  })

  it('同 seed 两次重放：帧流与 tick-hash 链一致（确定性契约）', () => {
    // 整文件不逐字节比较：initialSnapshot 的实体 id 来自 genId()（进程级计数器，
    // 跨 World 不重置——World.ts 有文档豁免），同进程第二局的 id 标签会平移。
    // 确定性契约比较的是玩法状态：frames（输入序列）与 tickHashes（id 重映射后的
    // 世界状态哈希，tickHash.ts 设计注记 3）。
    const a = mkdtempSync(path.join(os.tmpdir(), 'eval-replay-a-'))
    const b = mkdtempSync(path.join(os.tmpdir(), 'eval-replay-b-'))
    dirs.push(a, b)
    for (const dir of [a, b]) runGod(dir, 4242, 90)
    const pa = parseReplayFile(readFileSync(path.join(a, replayFiles(a)[0]), 'utf8'))
    const pb = parseReplayFile(readFileSync(path.join(b, replayFiles(b)[0]), 'utf8'))
    if ('error' in pa) throw new Error(pa.error)
    if ('error' in pb) throw new Error(pb.error)
    expect(pa.replay.frames).toEqual(pb.replay.frames)
    expect(pa.replay.tickHashes).toEqual(pb.replay.tickHashes)
    expect(pa.replay.totalTicks).toBe(pb.replay.totalTicks)
  })

  it('不传 replayDir：行为与历史版本一致（不产 .replay）', () => {
    const out = mkdtempSync(path.join(os.tmpdir(), 'eval-replay-off-'))
    dirs.push(out)
    const res = runGod()
    expect(replayFiles(out).length).toBe(0)
    expect(res.ticks).toBe(120)
    expect(res.outcome).toBe('max_ticks')
  })
})
