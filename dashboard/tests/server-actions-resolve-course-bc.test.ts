/**
 * server-actions-resolve-course-bc.test.ts — §384 种子路径读课程 bc 字段；未知课程回落 legacy 硬编码
 *
 * 分层：src/server/actions（resolveCourseBc）
 *
 * 自 training-console.test.ts 按 src 分层拆出。
 * 夹具（env 重定向 + 被测模块）见 ./helpers/console-fixture.ts。
 */

import { actions } from './helpers/console-fixture'
import { describe, expect, it } from 'bun:test'

describe('console/actions.resolveCourseBc（§384：种子路径读课程 bc 字段）', () => {
  it('p3-rd1/vk1 → 课程 bc（.ckpt.60）；未知课程 → legacy 硬编码', () => {
    expect(actions.resolveCourseBc('p3-rd1')).toContain('weights.json.ckpt.60')
    expect(actions.resolveCourseBc('p3-vk1')).toContain('weights.json.ckpt.60')
    expect(actions.resolveCourseBc('no-such-course-xyz').endsWith('weights.json')).toBe(true)
    expect(actions.resolveCourseBc('no-such-course-xyz')).not.toContain('ckpt')
  })
})
