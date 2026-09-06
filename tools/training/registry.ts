/** registry.ts — 受管进程 PID 账本（按组件分文件，天然无并行注册竞态，§339）。

 *  账本文件：nn-training/tmp/training-start/registry.json（单文件足够——本工具链
 *  所有 spawn 走监督器串行登记，不再有 hub-start 并行阶段互相覆盖的问题）。
 *  legacy：hub-start 时代的 nn-training/tmp/hub-start/registry.<name>.json 在
 *  load/clear 时一并消费，保证旧账本里的进程也能被 --kill 收编。
 */

import { readFileSync, unlinkSync, writeFileSync } from 'fs'
import path from 'path'
import { LOG_DIR, START_LOG_DIR } from './paths'
import type { Component, Registry, RegistryEntry } from './types'

const REGISTRY_PATH = path.join(START_LOG_DIR, 'registry.json')

/** hub-start 旧账本目录（legacy 迁移读取）。 */
const LEGACY_DIR = path.join(LOG_DIR, 'hub-start')
const LEGACY_COMPS: Component[] = ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop']

const REG_KEYS: Component[] = [
  'selfNode',
  'hubServer',
  'cloudflared',
  'trainingLoop',
  'workerServe',
]

export function loadRegistry(): Registry {
  const reg: Registry = {}
  try {
    Object.assign(reg, JSON.parse(readFileSync(REGISTRY_PATH, 'utf-8')) as Registry)
  } catch {
    /* not started */
  }
  // legacy 迁移：新账本缺的组件从 hub-start 分文件账本补齐（一次性收编旧进程）。
  for (const name of LEGACY_COMPS) {
    if (reg[name]) continue
    try {
      const e = JSON.parse(
        readFileSync(path.join(LEGACY_DIR, `registry.${name}.json`), 'utf-8'),
      ) as RegistryEntry
      if (e && typeof e.pid === 'number') reg[name] = e
    } catch {
      /* absent */
    }
  }
  return reg
}

export function saveRegistry(reg: Registry): void {
  writeFileSync(REGISTRY_PATH, JSON.stringify(reg, null, 2), 'utf-8')
}

/** 登记或更新一个组件条目。 */
export function saveComponent(name: Component, entry: RegistryEntry): void {
  const reg = loadRegistry()
  reg[name] = entry
  saveRegistry(reg)
}

export function clearRegistry(): void {
  try {
    unlinkSync(REGISTRY_PATH)
  } catch {
    /* absent */
  }
  // legacy：hub-start 分文件账本一并清（已收编消费后不再重复判活）。
  for (const name of LEGACY_COMPS) {
    try {
      unlinkSync(path.join(LEGACY_DIR, `registry.${name}.json`))
    } catch {
      /* absent */
    }
  }
  try {
    unlinkSync(path.join(LEGACY_DIR, 'registry.json'))
  } catch {
    /* absent */
  }
}

/** 账本里全部登记的组件（legacy 合并后）。 */
export function registryComponents(): Array<[Component, RegistryEntry]> {
  const reg = loadRegistry()
  const out: Array<[Component, RegistryEntry]> = []
  for (const k of REG_KEYS) {
    const e = reg[k]
    if (e) out.push([k, e])
  }
  return out
}
