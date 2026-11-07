/** code-snapshot.ts — B7 控制台「重打代码快照」入口。
 *
 *  只重打 `<repo>/tmp/.code-snapshot/`，不重启 hub/trainer、不碰在跑的 job。
 *  `--anchor-pid` 指回长驻的控制台进程：短命 python 子进程退出后，快照的 anchor
 *  仍然活着，下一次发布不会立刻把它判成死锚。
 */

import { runRunPythonAsyncModule } from '../run-python'
import { done, type ActionResult } from './result'

function tail(text: string, n = 4): string[] {
  return text.trim().split(/\r?\n/).filter(Boolean).slice(-n)
}

export async function rebuildCodeSnapshot(): Promise<ActionResult> {
  const r = await runRunPythonAsyncModule(
    'remote.code_snapshot',
    ['--rebuild', '--anchor-pid', String(process.pid)],
    { timeoutMs: 120_000 },
  )
  if (r.timeout) {
    return done(false, '重打代码快照超时（120s）——检查 nn-training 的打包进程')
  }
  if (r.code !== 0) {
    return done(false, `重打代码快照失败（退出码 ${r.code ?? '—'}）`, [
      ...tail(r.stderr),
      ...tail(r.stdout),
    ])
  }
  let parsed: { sha256?: unknown; zip?: unknown } = {}
  try {
    const lines = r.stdout.trim().split(/\r?\n/)
    parsed = JSON.parse(lines[lines.length - 1] || '{}') as typeof parsed
  } catch {
    /* 输出不是 JSON 也不影响「进程成功」；下面按无 sha 报 */
  }
  const sha = typeof parsed.sha256 === 'string' ? parsed.sha256 : ''
  const zip = typeof parsed.zip === 'string' ? parsed.zip : ''
  return done(true, sha ? `代码快照已重打：${sha.slice(0, 12)}` : '代码快照已重打', [
    '新快照只对下一次发布的 job 生效；已在跑的 job 继续用原快照。',
    ...(zip ? [`文件：${zip}`] : []),
  ])
}
