/**
 * architecture-layering.test.ts — MANIFEST / plan §3.1 R1 + R13 ③：src/** 无 .tsx、无 preact import；web/** 禁服务端 import
 *
 * 分层：src/** 与 dashboard/src/web/** 的静态分层断言
 *
 * 依据 plan/Training-Console-Preact.md（§7 交互纯函数化 / R13 ③ 分层 / DS-E1 pool 契约）。
 * 自 training-console-preact.test.ts 按 src 分层拆出；用例逐一不变
 * （拆分前后 dashboard 合计 306 pass 对账）。
 */

import { describe, expect, it } from 'bun:test'
import { readFileSync } from 'fs'
import { join } from 'path'
import { DASHBOARD_ROOT, REPO_ROOT } from '../src/core/paths'

// ────────────────────────── 分层边界（R13 ③ + 铁律 R1，静态扫描） ──────────────────────────
/** 递归列 `.ts(x)`（Bun.Glob 原生扫描；`dot: true` 与旧 walker 同面——含 `.build`，
 *  后者由下面的用例显式跳过）。 */
function walkFiles(dir: string): string[] {
  return [...new Bun.Glob('**/*.{ts,tsx}').scanSync({ cwd: dir, dot: true })].map((rel) =>
    join(dir, rel),
  )
}

describe('分层边界（MANIFEST/plan §3.1 R1 + R13 ③）', () => {
  it('src/** 无 .tsx、无 preact import（主游戏零影响）', () => {
    const files = walkFiles(join(REPO_ROOT, 'src'))
    expect(files.filter((f) => f.endsWith('.tsx'))).toEqual([])
    for (const f of files) {
      const t = readFileSync(f, 'utf8')
      expect(t, f).not.toMatch(/from ['"]preact/)
      expect(t, f).not.toMatch(/from ['"]preact\/hooks/)
    }
  })

  it('dashboard/src/web/** 不得 import 服务端模块（node:/fs/Bun./server(api|actions)）', () => {
    // 客户端源码根与浏览器 app 各扫一遍（搬迁后为 src/web 与 src/web/app 两层）。
    const dirs = [join(DASHBOARD_ROOT, 'src', 'web'), join(DASHBOARD_ROOT, 'src', 'web', 'app')]
    const banned = [/from ['"]node:/, /from ['"]fs['"]/, /\bBun\./, /server\/(api|actions)/]
    for (const dir of dirs) {
      for (const f of walkFiles(dir)) {
        if (f.includes('.build')) continue // 跳过构建产物目录
        const t = readFileSync(f, 'utf8')
        for (const re of banned) {
          expect(t.match(re), `${f} 违反分层铁律 ${re}`).toBeNull()
        }
      }
    }
  })
})
