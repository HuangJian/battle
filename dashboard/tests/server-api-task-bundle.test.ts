/**
 * server-api-task-bundle.test.ts — 任务包导出 / 训练产物导入（`task-<课程>.zip` /
 * `deliver-<课程>.zip`）。
 *
 * 分层：src/server/bundles/** + src/server/api/route.ts 的 exportTaskBundle 动作
 *
 * 两侧各测自己那一半（不叠第二层端到端）：
 *   * python 一侧的三道门（zip-slip / 拿错包 / 轮次发现）由
 *     `nn-training/tests/test_deliver_zip.py` 钉死；
 *   * 这里钉**控制台这一侧**：argv 形状、产出文件信息、拒启理由、上传体的门、
 *     与 python 共享的跨语言常量（直接读 python 源码对账——改一边忘另一边会红）、
 *     以及「导入成功后要不要接着起评估」。
 *
 * 一律不真跑 python / 不真起 evalA（前者慢且依赖 venv，后者会真跑几十局游戏）。
 */

import { describe, expect, it } from 'bun:test'
import { mkdtempSync, readFileSync, writeFileSync } from 'fs'
import os from 'os'
import path from 'path'
import { REPO_ROOT } from '../src/core/paths'
import {
  DELIVER_MAX_BYTES,
  deliverFileNamePrefix,
  deliverImportJsonMark,
  deliverImportRoot,
  deliverUploadDir,
  importDeliverZip,
  launchPostImportEval,
  parseDeliverImportJson,
  handleDeliverUpload,
  validateDeliverUpload,
  type DeliverImportPayload,
  type DeliverUploadDeps,
} from '../src/server/bundles'
import {
  exportGuardReason,
  launchTaskBundleExport,
  TASK_BUNDLE_BUSY_KEY,
  taskBundleArgs,
  taskBundleFileName,
  taskBundleInfo,
  taskBundleLogPath,
  taskBundlePath,
} from '../src/server/bundles/export'
import { busy, release } from '../src/server/actions/result'

const COURSE = 'x1-rebirth-a2'

/** 测试沙箱（`destRoot` + `logFile`）：`importDeliverZip` 缺省会写真实 `tmp/<课程>/`
 *  （生产语义）——测试必须指到临时目录，否则一次 `bun test` 就在仓里留下 deliver/ 与
 *  一份追写过的 deliver-import.log。这里用 `mkdtempSync` 造一次性目录。 */
function sandboxPaths(): { destRoot: string; logFile: string } {
  const d = mkdtempSync(path.join(os.tmpdir(), 'bcity-bundle-'))
  return { destRoot: path.join(d, 'deliver'), logFile: path.join(d, 'import.log') }
}

// ────────────────────────── 跨语言常量（改一边忘另一边 → 红） ──────────────────────────

describe('与 python 共享的跨语言常量', () => {
  const py = readFileSync(path.join(REPO_ROOT, 'nn-training', 'remote', 'deliver_zip.py'), 'utf8')

  it('IMPORT_JSON_MARK 与 remote/deliver_zip.py 逐字节相同', () => {
    // 两边写岔了是**静默**的：导入器打的那行没人认得出 → 控制台报「导入失败」，
    // 而真因（解包失败？标记改名？）不在这一侧。
    expect(py).toContain(`IMPORT_JSON_MARK = "${deliverImportJsonMark}"`)
  })

  it('DELIVER_PREFIX 与 python 一致（文件名约定是同一条契约）', () => {
    expect(py).toContain(`DELIVER_PREFIX = "${deliverFileNamePrefix}"`)
  })
})

// ────────────────────────── 导出侧（argv / 路径 / 拒启理由） ──────────────────────────

describe('任务包导出', () => {
  it('argv 走 trainer 自己的 --export-bundle（不在这侧重造包内容）', () => {
    const args = taskBundleArgs(COURSE)
    expect(args[0]).toBe('nn-training/run_rl.py')
    expect(args).toContain(COURSE)
    // 整段剩余：-1（跑到课程末尾）；★ §3 起 argv 不再带 --ppo（单一 PPO 路径）
    expect(args[args.indexOf('--run-iters') + 1]).toBe('-1')
    expect(args).not.toContain('--ppo')
    expect(args[args.indexOf('--export-bundle') + 1]).toBe(taskBundlePath(COURSE))
  })

  it('产出路径/文件名/日志都在课程目录下（人一眼能找到）', () => {
    expect(taskBundleFileName(COURSE)).toBe(`task-${COURSE}.zip`)
    expect(taskBundlePath(COURSE)).toBe(path.join(REPO_ROOT, 'tmp', COURSE, `task-${COURSE}.zip`))
    expect(taskBundleLogPath(COURSE).endsWith(path.join(COURSE, 'export-bundle.log'))).toBe(true)
  })

  it('没导出过时 info.exists=false（下载按钮据此禁用）', () => {
    const info = taskBundleInfo('__never-exported-course__')
    expect(info.exists).toBe(false)
    expect(info.bytes).toBe(0)
    expect(info.name).toBe('task-__never-exported-course__.zip')
  })

  it('拒启理由：训练在跑要说清为什么、缺起点权重也要说清（纯函数）', () => {
    expect(exportGuardReason({ lockHolder: null, weightsExists: true })).toBeNull()
    const held = exportGuardReason({ lockHolder: 4321, weightsExists: true })
    expect(held).toContain('4321')
    const noW = exportGuardReason({ lockHolder: null, weightsExists: false })
    expect(noW).toContain('weights.json')
  })

  it('上一次导出还在跑 → 拒启（互斥键由子进程退出释放，不在请求里删）', () => {
    // 这一条盯的是**互斥键的生命期**：若键在请求返回时就 `delete`（最初的写法），
    // 第二次点击会起第二个 run_rl 去抢同一门课的锁——锁是 python 侧的，控制台只
    // 会看到「启动成功」而日志里多一段锁冲突。键的释放路径是轮询 pid，见
    // `watchExportExit`；这里只需要它**在键存在时拒启**，且理由先于业务校验。
    const key = 'bundle:export'
    expect(TASK_BUNDLE_BUSY_KEY).toBe(key)
    busy.add(key)
    try {
      const r = launchTaskBundleExport(COURSE)
      expect(r.ok).toBe(false)
      expect(r.message).toContain('还没跑完')
    } finally {
      release(key)
    }
  })
})

// ────────────────────────── 导入侧（上传门 / 结果转述 / 自动评估） ──────────────────────────

describe('产物导入', () => {
  it('文件名必须与当前课程一致（拿错课产物是不可逆的读数错位）', () => {
    expect(validateDeliverUpload(`deliver-${COURSE}.zip`, COURSE)).toBeNull()
    expect(validateDeliverUpload('deliver-other.zip', COURSE)).toContain('other')
    expect(validateDeliverUpload('artifact.zip', COURSE)).toBeNull() // 不按习惯命名 → 不猜
    expect(validateDeliverUpload('deliver-x1.zip', '')).toContain('课程')
    expect(validateDeliverUpload('deliver-x1.tar.gz', COURSE)).toContain('.zip')
  })

  it('解析机器可读结果行（人读日志在前，标记行在末）', () => {
    const payload = {
      run_id: 'r1',
      dir: '/x/r1',
      iters: [1, 2],
      last_it: 2,
      ckpt: '/x/r1/it-002/weights.json',
      state: 'complete',
      rows: 2,
      course: COURSE,
      source_zip: '/x/deliver.zip',
      bytes: 10,
    }
    const stdout = `[deliver] 导入完成：…\n[deliver] 末轮权重：…\n${deliverImportJsonMark}${JSON.stringify(payload)}\n`
    expect(parseDeliverImportJson(stdout)?.last_it).toBe(2)
    expect(parseDeliverImportJson('[deliver] 只有人读日志\n')).toBeNull()
    expect(parseDeliverImportJson(`${deliverImportJsonMark}{坏 json`)).toBeNull()
  })

  it('python 导入器 argv：--zip/--dest/--course，dest 是课程下的 deliver 根', () => {
    const calls: Array<{ module: string; args: string[] }> = []
    const runner = ((module: string, args: string[]) => {
      calls.push({ module, args })
      return {
        code: 0,
        stdout: `${deliverImportJsonMark}${JSON.stringify({
          run_id: 'r1',
          dir: deliverImportRoot(COURSE) + '/r1',
          iters: [3, 4],
          last_it: 4,
          ckpt: deliverImportRoot(COURSE) + '/r1/it-004/weights.json',
          state: 'complete',
          rows: 2,
          course: COURSE,
          source_zip: '/x/z.zip',
          bytes: 3,
        })}`,
        stderr: '',
        timeout: false,
      }
    }) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = importDeliverZip(
      { course: COURSE, zipPath: '/x/deliver.zip', ...sandboxPaths() },
      runner,
    )
    expect(r.ok).toBe(true)
    expect(r.payload?.last_it).toBe(4)
    expect(calls).toHaveLength(1)
    expect(calls[0].module).toBe('remote.deliver_zip')
    // argv 形状 = 「zip / 落地根 / 课程」；落地根**缺省**就是课程目录下的 deliver/
    // （生产语义——上面注入的 destRoot 只是测试沙箱）。
    expect(calls[0].args.slice(0, 2)).toEqual(['--zip', '/x/deliver.zip'])
    expect(calls[0].args[2]).toBe('--dest')
    expect(calls[0].args[4]).toBe('--course')
    expect(calls[0].args[5]).toBe(COURSE)
    expect(deliverImportRoot(COURSE)).toBe(path.join(REPO_ROOT, 'tmp', COURSE, 'deliver'))
  })

  it('python 报错 → 控制台把它的最后一行转成人话（不吞掉真因）', () => {
    const runner = (() => ({
      code: 2,
      stdout: '',
      stderr: '[deliver] 导入失败：这个包里没有 it-NNN/weights.json——不是训练产物 zip\n',
      timeout: false,
    })) as unknown as Parameters<typeof importDeliverZip>[1]
    const r = importDeliverZip({ course: COURSE, zipPath: '/x/bad.zip', ...sandboxPaths() }, runner)
    expect(r.ok).toBe(false)
    expect(r.message).toContain('weights.json')
  })

  // 真造一份「末轮权重」：`launchPostImportEval` 的存在性门是生产逻辑的一部分
  // （目录被动过 = 不静默），测试就让它真的过门。
  const ckptDir = mkdtempSync(path.join(os.tmpdir(), 'bcity-deliver-'))
  const ckptPath = path.join(ckptDir, 'it-004-weights.json')
  writeFileSync(ckptPath, '{}')

  const deps = (over: Partial<DeliverUploadDeps> = {}): DeliverUploadDeps => ({
    save: () => '/tmp/kept.zip',
    importZip: () => ({
      ok: true,
      message: '已导入 r1（2 轮，it3 → it4）',
      payload: {
        run_id: 'r1',
        dir: '/tmp/r1',
        iters: [3, 4],
        last_it: 4,
        ckpt: ckptPath,
        state: 'complete',
        rows: 2,
        course: COURSE,
        source_zip: '/tmp/kept.zip',
        bytes: 3,
      } satisfies DeliverImportPayload,
    }),
    launchEval: (course: string, ckpt: string, iter: number) => {
      launched.push({ course, ckpt, iter })
      return { ok: true, message: `evalA 已启动 it${iter}` }
    },
    ...over,
  })
  const launched: Array<{ course: string; ckpt: string; iter: number }> = []

  const upload = async (fileName: string, bytes = 'x', d = deps()) => {
    const form = new FormData()
    form.append('file', new File([bytes], fileName))
    return handleDeliverUpload(
      new Request('http://x/api/deliverUpload', { method: 'POST', body: form }),
      COURSE,
      d,
    )
  }

  it('上传成功后**接着起评估**（按课程语料，评末轮），响应含结论与产物目录', async () => {
    launched.length = 0
    const r = await upload(`deliver-${COURSE}.zip`)
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(String(body.message)).toContain('evalA 已启动 it4')
    expect(body.evalStarted).toBe(true)
    expect(launched).toHaveLength(1)
    expect(launched[0].iter).toBe(4)
  })

  it('评估起不来不算导入失败（产物已落地可读，那是这一半的全部价值）', async () => {
    const r = await upload(
      `deliver-${COURSE}.zip`,
      'x',
      deps({ launchEval: () => ({ ok: false, message: 'evalA 已在运行' }) }),
    )
    const body = (await r.json()) as Record<string, unknown>
    expect(r.status).toBe(200)
    expect(body.ok).toBe(true)
    expect(body.evalStarted).toBe(false)
    expect(String(body.message)).toContain('evalA 已在运行')
  })

  it('拿错课的文件名 / 非 zip / 超大 → 拒收且不调 python', async () => {
    let called = 0
    const d = deps({
      importZip: () => {
        called += 1
        return { ok: false, message: '不该被调用' }
      },
    })
    expect((await upload('deliver-other.zip', 'x', d)).status).toBe(400)
    expect((await upload('deliver-x.zip.tar.gz', 'x', d)).status).toBe(400)
    expect(called).toBe(0)
    expect(DELIVER_MAX_BYTES).toBeGreaterThan(0)
  })

  it('没有文件 → 400（面板先说清要选文件）', async () => {
    const form = new FormData()
    const r = await handleDeliverUpload(
      new Request('http://x/api/deliverUpload', { method: 'POST', body: form }),
      COURSE,
      deps(),
    )
    expect(r.status).toBe(400)
  })

  it('上传原件落在课程下的 deliver-uploads/（留证，不覆盖历史）', () => {
    expect(deliverUploadDir(COURSE)).toBe(path.join(REPO_ROOT, 'tmp', COURSE, 'deliver-uploads'))
    expect(deliverImportRoot(COURSE)).toBe(path.join(REPO_ROOT, 'tmp', COURSE, 'deliver'))
  })

  it('末轮权重不在盘上 → 不静默：明确说「导入目录被动过」', () => {
    const r = launchPostImportEval(
      COURSE,
      {
        run_id: 'r',
        dir: '/nope',
        iters: [1],
        last_it: 1,
        ckpt: '/nope/it-001/weights.json',
        state: '',
        rows: 1,
        course: COURSE,
        source_zip: '',
        bytes: 0,
      },
      { launchEval: () => ({ ok: true, message: '不该被调用' }) },
    )
    expect(r.ok).toBe(false)
    expect(r.message).toContain('不在盘上')
  })
})
