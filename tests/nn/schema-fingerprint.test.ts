/**
 * schema-fingerprint.test.ts — obs v3 SCHEMA_FINGERPRINT 双端锚（hy X4）。
 *
 * obs spec §3.4-7 / §4：schema.py 与 obs-encoder.ts 导出同名常量，任一端常量
 * 变动（通道数/标量布局/打包值域/镜像索引/弹速桶）指纹必变。本测试把 TS 侧钉在
 * 与 Python 侧**同一字面量**上；`nn-training/tests/test_schema_fingerprint.py`
 * 把 Python 侧钉在同一值——任一端漏同步即红，而不是等 golden 前向对不上才发现。
 */
import { describe, it, expect } from 'bun:test'
import {
  SCHEMA_FINGERPRINT,
  OBS_SCHEMA_MAJOR,
  OBS_CHANNELS,
  SCALAR_DIM,
  SCALAR_X_INDICES,
  SCALAR_NAMES,
} from '../../src/nn/obs-encoder'

// 必须与 nn-training/schema.py::SCHEMA_FINGERPRINT 逐字相同（两边单测共锚）。
const FINGERPRINT = '06142cb1'

// 必须与 nn-training/schema.py::SCALAR_LAYOUT 的第二元逐字同序（双端共锚）。
// 只钉维度挡不住「交换两个标量含义」——语义序列进指纹后这种漏同步才现形。
const SCALAR_LAYOUT_NAMES: (typeof SCALAR_NAMES)[number][] = [
  'slack',
  'baseDeadline',
  'lives',
  'level',
  'fireProgress',
  'turnCooldownRemaining',
  'ringCompleteness',
  'enemiesOnField',
  'spawnQueueRemaining',
  'tier_none',
  'tier_rookie',
  'tier_soldier',
  'tier_veteran',
  'tier_commander',
  'nearestEnemyDist',
  'nearestEnemyRelX',
  'nearestEnemyRelY',
  'nearestBaseDist',
  'nearestBaseRelX',
  'playerHp',
  'playerShield',
  'freeze',
  'stuck',
  'boat',
  'baseHp',
  'fence',
  'score',
  'emp',
  'iceVy',
  'iceVx',
]

describe('SCHEMA_FINGERPRINT (obs v3 双端锚)', () => {
  it('TS 指纹 == Python schema.SCHEMA_FINGERPRINT（共享字面锚）', () => {
    expect(SCHEMA_FINGERPRINT).toBe(FINGERPRINT)
  })

  it('配套常量与指纹同版（v3：16ch / 30sc / X=[15,18,29]）', () => {
    expect(OBS_SCHEMA_MAJOR).toBe(3)
    expect(OBS_CHANNELS).toBe(16)
    expect(SCALAR_DIM).toBe(30)
    expect(SCALAR_X_INDICES).toEqual([15, 18, 29])
  })

  it('标量语义序列与 schema.py SCALAR_LAYOUT 逐字同序（指纹已含）', () => {
    expect(SCALAR_NAMES.length).toBe(SCALAR_DIM)
    expect([...SCALAR_NAMES]).toEqual(SCALAR_LAYOUT_NAMES)
  })
})
