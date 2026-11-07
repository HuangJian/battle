/** BC 代码快照旁挂（B7）：面板读取最近 job 的 sha 与 stale 标记。 */

import { afterEach, describe, expect, it } from 'bun:test'
import { mkdirSync, mkdtempSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import path from 'path'
import { buildBcEpochsView } from '../src/server/api/ledger'

const oldTmp = process.env.BCITY_TMP_LOGS_DIR
const oldCur = process.env.BCITY_CURRICULA_DIR

function withRoots(root: string, curricula: string): void {
  process.env.BCITY_TMP_LOGS_DIR = root
  process.env.BCITY_CURRICULA_DIR = curricula
}

afterEach(() => {
  if (oldTmp === undefined) delete process.env.BCITY_TMP_LOGS_DIR
  else process.env.BCITY_TMP_LOGS_DIR = oldTmp
  if (oldCur === undefined) delete process.env.BCITY_CURRICULA_DIR
  else process.env.BCITY_CURRICULA_DIR = oldCur
})

describe('buildBcEpochsView.codeSnapshot', () => {
  it('读最近 BC job 的 manifest.code_sha256 + code-freshness.json', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'tc-bc-snap-'))
    const curricula = mkdtempSync(path.join(tmpdir(), 'tc-bc-cur-'))
    const job = path.join(root, 'bc-x', 'remote-jobs', 'jid-new')
    mkdirSync(job, { recursive: true })
    writeFileSync(
      path.join(job, 'manifest.json'),
      JSON.stringify({
        job_id: 'jid-new',
        kind: 'bc',
        code_sha256: 'a'.repeat(64),
        commit: 'dead',
      }),
      'utf8',
    )
    writeFileSync(
      path.join(job, 'code-freshness.json'),
      JSON.stringify({ workspace_sha: 'b'.repeat(64), stale: true, ts: 123 }),
      'utf8',
    )
    withRoots(root, curricula)

    const view = await buildBcEpochsView('bc-x')
    expect(view.codeSnapshot).toEqual({
      jobId: 'jid-new',
      snapshotSha: 'a'.repeat(64),
      workspaceSha: 'b'.repeat(64),
      stale: true,
      commit: 'dead',
      ts: 123,
    })
  })

  it('旧 job 没有旁挂 ⇒ stale=null，仍显示 manifest 的 sha；无 job ⇒ null', async () => {
    const root = mkdtempSync(path.join(tmpdir(), 'tc-bc-snap-'))
    const curricula = mkdtempSync(path.join(tmpdir(), 'tc-bc-cur-'))
    const job = path.join(root, 'bc-y', 'remote-jobs', 'jid-old')
    mkdirSync(job, { recursive: true })
    writeFileSync(
      path.join(job, 'manifest.json'),
      JSON.stringify({ job_id: 'jid-old', kind: 'bc', code_sha256: 'c'.repeat(64) }),
      'utf8',
    )
    withRoots(root, curricula)

    const old = await buildBcEpochsView('bc-y')
    expect(old.codeSnapshot?.snapshotSha).toBe('c'.repeat(64))
    expect(old.codeSnapshot?.stale).toBeNull()

    const none = await buildBcEpochsView('bc-none')
    expect(none.codeSnapshot).toBeNull()
  })
})
