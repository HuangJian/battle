/** gate-entry-windows-argv.test.ts — 裸 `bun test` 在 Windows 上的转交参数抽取。
 *
 *  2026-11-23 用户报「bun run test 绿、bun test 很多报错」：gate-entry 只认
 *  `/proc/self/cmdline`，Windows 读不到 ⇒ 不转交门禁、同进程跑全套 ⇒ 环境污染红。
 *  这里钉住 WMI 原始命令行的 tokenizer，不需要真起 PowerShell。 */
import { describe, expect, it } from 'bun:test'

import { splitWindowsCommandLine, testArgsFromCommandLine } from './helpers/windows-command-line'

describe('Windows 原始命令行 → bun test 参数', () => {
  it('普通命令：取 test 子命令之后的全部参数', () => {
    expect(
      splitWindowsCommandLine(String.raw`C:\bun.exe test --coverage tests\foo.test.ts`),
    ).toEqual([String.raw`C:\bun.exe`, 'test', '--coverage', String.raw`tests\foo.test.ts`])
    expect(
      testArgsFromCommandLine(String.raw`C:\bun.exe test --coverage tests\foo.test.ts`),
    ).toEqual(['--coverage', String.raw`tests\foo.test.ts`])
  })

  it('带空格的可执行路径与 positional 引号照常解出', () => {
    const line = String.raw`"C:\Program Files\bun.exe" test "tests\my file.test.ts"`
    expect(testArgsFromCommandLine(line)).toEqual([String.raw`tests\my file.test.ts`])
  })

  it('路径里含 test 字样不误判：只认独立 token', () => {
    expect(testArgsFromCommandLine(String.raw`C:\test\bun.exe test`)).toEqual([])
    expect(testArgsFromCommandLine(String.raw`C:\bun.exe --version`)).toBeNull()
  })

  it('Windows 反斜杠转义：\\" = 字面引号，\\ -> 一个反斜杠 + 引号切换', () => {
    expect(splitWindowsCommandLine(String.raw`bun test "a\"b" c`)).toEqual([
      'bun',
      'test',
      'a"b',
      'c',
    ])
    expect(splitWindowsCommandLine(String.raw`bun test "C:\dir\\" x`)).toEqual([
      'bun',
      'test',
      'C:\\dir\\',
      'x',
    ])
  })
})
