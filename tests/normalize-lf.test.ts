import { describe, expect, it } from 'bun:test'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { classifyDrift, codeHashSetPaths, repairOne } from '../tools/githook/normalize-lf'

/**
 * EOL 归一器（tools/githook/normalize-lf.ts）的不变量 —— 2026-10-20 mac 节点门事故的修法。
 *
 * 事故：codeHash = 集内文件**工作区原始字节**的哈希；某个 Windows 侧写文件工具把已提交
 * 文件写成 CRLF ⇒ `git status` 干净（core.autocrlf=input + `* text=auto eol=lf` 归一，
 * 行尾污染隐形）⇒ 但集内哈希已变 ⇒ 训练机期望值与任何干净 checkout 都不等 ⇒ 全集群
 * 节点判 stale、mac 被排除在 rollout/eval 外。
 *
 * 这里钉四条：
 *   ① 分类判据三分支：逐字节一致 / 纯行尾（可修）/ 真实内容差异（**绝不碰**）；
 *   ② 修复只把**纯行尾污染**的工作树拉回索引字节，真实内容一字不动（并行安全铁律）；
 *   ③ 二进制（.wasm/.dll/.dylib 等集内分发物）永不参与；
 *   ④ 钩子真的调了它，且**在 dirty 清单之前**（归一后「因行尾而不一致」的文件不再算
 *      未暂存改动）。
 */

const HOOK_PATH = resolve(import.meta.dir, '..', 'tools/githook/pre-commit')
const HOOK = readFileSync(HOOK_PATH, 'utf8')

const LF = Buffer.from('const a = 1\nconst b = 2\n', 'latin1')
const CRLF = Buffer.from('const a = 1\r\nconst b = 2\r\n', 'latin1')
const REAL = Buffer.from('const a = 1\r\nconst b = 3\n', 'latin1')

/** 临时文件（tmp/ 已 gitignored）；不删除——沙箱删除保护 + hook 零删除铁律。 */
function tmpFile(name: string, content: Buffer): string {
  const dir = resolve(import.meta.dir, '..', 'tmp', 'normalize-lf-test')
  mkdirSync(dir, { recursive: true })
  const p = resolve(dir, name)
  writeFileSync(p, content)
  return p
}

describe('EOL 归一器 — 漂移分类', () => {
  it('逐字节一致 ⇒ same', () => {
    expect(classifyDrift(LF, LF)).toBe('same')
    expect(classifyDrift(CRLF, CRLF)).toBe('same')
  })

  it('工作区 CRLF / 索引 LF ⇒ eol（纯行尾，可修）', () => {
    expect(classifyDrift(CRLF, LF)).toBe('eol')
  })

  it('内容真变了 ⇒ real（绝不碰）', () => {
    expect(classifyDrift(REAL, LF)).toBe('real')
    expect(classifyDrift(Buffer.from('x\n'), Buffer.from('y\n'))).toBe('real')
  })

  it('二进制 ⇒ real（集内 prebuilt 分发物不参与行尾转换）', () => {
    expect(classifyDrift(Buffer.from([0x00, 0x01]), Buffer.from([0x00, 0x02]))).toBe('real')
  })
})

describe('EOL 归一器 — 修复行为', () => {
  it('纯行尾污染 ⇒ 工作树被拉回索引字节（LF）', () => {
    const p = tmpFile('repair-eol.ts', CRLF)
    expect(repairOne(p, LF)).toBe('repaired')
    expect(readFileSync(p).equals(LF)).toBe(true)
  })

  it('本就一致 ⇒ 不动', () => {
    const p = tmpFile('repair-same.ts', LF)
    expect(repairOne(p, LF)).toBe('same')
    expect(readFileSync(p).equals(LF)).toBe(true)
  })

  it('真实内容差异 ⇒ 一字不动（并行安全铁律：不能吃掉他人在途编辑）', () => {
    const p = tmpFile('repair-real.ts', REAL)
    expect(repairOne(p, LF)).toBe('real')
    expect(readFileSync(p).equals(REAL)).toBe(true)
  })
})

describe('EOL 归一器 — 覆盖面与接线', () => {
  it('codehash 集含节点门敏感面（src/game、src/nn、tools/sim、tools/agent）', () => {
    const set = codeHashSetPaths()
    expect(set.length).toBeGreaterThan(50)
    expect(set.some((p) => p.startsWith('src/game/'))).toBe(true)
    expect(set.some((p) => p.startsWith('src/nn/'))).toBe(true)
    expect(set.some((p) => p.startsWith('tools/sim/'))).toBe(true)
    expect(set.some((p) => p.startsWith('tools/agent/'))).toBe(true)
  })

  it('钩子调了归一器，且在 dirty 清单之前', () => {
    const call = HOOK.indexOf('normalize-lf.ts --staged-list')
    expect(call).toBeGreaterThan(-1)
    const dirty = HOOK.indexOf(
      'git diff --name-only --diff-filter="$STAGED_FILTER" > "$DIRTY_LIST"',
    )
    expect(dirty).toBeGreaterThan(-1)
    expect(call).toBeLessThan(dirty)
  })
})
