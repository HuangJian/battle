/** windows-command-line.ts — 从 Windows 当前进程的原始命令行里取回 `bun test` 后边的参数。
 *
 *  Bun 1.4 在 `[test] preload` 里会把 `process.argv` 改写成「当前测试文件」（Linux 也一样），
 *  所以转交门禁不能靠 argv。Linux 走 `/proc/self/cmdline`；Windows 没有 procfs，改问 WMI：
 *  当前 pid 的 `Win32_Process.CommandLine` 仍是用户敲的那一行。
 *
 *  只做参数抽取，不执行命令；CI（Linux）永远走不到这条分支。 */
export function splitWindowsCommandLine(line: string): string[] {
  const out: string[] = []
  let cur = ''
  let inQuotes = false
  let slashes = 0
  const flush = (): void => {
    if (cur.length > 0) out.push(cur)
    cur = ''
  }
  for (const ch of line) {
    if (ch === '\\') {
      slashes++
      cur += ch
      continue
    }
    if (ch === '"') {
      // Windows 反斜杠转义：`\\"` = 一个字面反斜杠 + 引号切换；`\"` = 字面引号。
      cur = cur.slice(0, cur.length - slashes) + '\\'.repeat(slashes >> 1)
      if (slashes % 2 === 0) inQuotes = !inQuotes
      else cur += '"'
      slashes = 0
      continue
    }
    if ((ch === ' ' || ch === '\t') && !inQuotes) {
      flush()
      slashes = 0
      continue
    }
    slashes = 0
    cur += ch
  }
  flush()
  return out
}

/** 取 `bun test` 子命令后面的参数；找不到返回 null（不是空数组）。 */
export function testArgsFromCommandLine(line: string): string[] | null {
  const args = splitWindowsCommandLine(line)
  const i = args.findIndex((a) => a.toLowerCase() === 'test')
  return i >= 0 ? args.slice(i + 1) : null
}
