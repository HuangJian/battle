/** eval-bc-run.test.ts — BC 手动 eval 的 argv 与课程级 busy 互斥（不真起 python）。 */

import { afterEach, describe, expect, it } from 'bun:test'
import path from 'path'
import { busy } from '../src/server/actions'
import { evalBcArgs, evalBcBusyKey, evalBcRunning, launchEvalBc } from '../src/server/eval-bc-run'

const COURSE = 'bc-test-course'

afterEach(() => {
  busy.delete(evalBcBusyKey(COURSE))
})

describe('evalBcArgs', () => {
  it('指向 eval_bc_once.py 并带 course/it/epoch/hub', () => {
    const argv = evalBcArgs(COURSE, 2, 17, 'http://127.0.0.1:8787')
    expect(argv[0].endsWith(path.join('trainer', 'eval_bc_once.py'))).toBe(true)
    expect(argv.slice(1)).toEqual([
      '--course',
      COURSE,
      '--it',
      '2',
      '--epoch',
      '17',
      '--hub-url',
      'http://127.0.0.1:8787',
    ])
  })

  it('没有 hub URL 时不传空 flag', () => {
    expect(evalBcArgs(COURSE, 1, 1, '')).not.toContain('--hub-url')
  })
})

describe('launchEvalBc busy 互斥', () => {
  it('同课程已有 eval ⇒ 返回 busy，不启动第二个进程', () => {
    busy.add(evalBcBusyKey(COURSE))
    expect(evalBcRunning(COURSE)).toBe(true)
    const r = launchEvalBc(COURSE, 2, 17)
    expect(r.ok).toBe(false)
    expect(r.code).toBe('busy')
    expect(r.message).toContain('已有 BC eval')
  })

  it('别的课程不共享互斥键', () => {
    busy.add(evalBcBusyKey('other-course'))
    expect(evalBcRunning(COURSE)).toBe(false)
    busy.delete(evalBcBusyKey('other-course'))
  })
})
