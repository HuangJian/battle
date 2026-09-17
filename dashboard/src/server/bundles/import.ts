/** import.ts — 训练产物导入（`deliver-<课程>.zip`）**并自动按课程配置跑 A 层评估**。
 *
 *  流程（用户 2026-09-17 需求）：
 *    浏览器上传 zip → 落到 `tmp/<课程>/deliver-uploads/`（留证）→ python
 *    `remote.deliver_zip` 解成产物目录（`tmp/<课程>/deliver/<run_id>/`，三道门在那边）
 *    → **接着起 evalA**（`rl/eval_a_once.py`，与训练每 eval_every 轮同口径的干净评估，
 *    语料就来自课程配置的 eval_stages/eval_games_per_stage）。
 *
 *  为什么评估复用 evalA 而不是自己拼一套：那条命令的语料口径、双轨种子、账本格式
 *  （`tmp/<课程>/eval_log.jsonl`）全部已经与控制台指标表对齐——重写一份 = 自造第二种读数。
 *
 *  **上传体是不可信输入**：文件名先跟当前课程对账（`deliver-<课程>.zip`），zip 内容的三道门
 *  在 python 一侧（zip-slip / 形状 / 课程对账）。这里只额外挡两件明显的事：非 .zip、超大。
 */

import { existsSync, mkdirSync, writeFileSync } from 'fs'
import path from 'path'
import { REPO_ROOT } from '../../core/paths'
import { launchEvalA } from '../eval-a-run'
import { runRunPythonSyncModule } from '../run-python'
import { deliverImportJsonMark, deliverFileNamePrefix } from './marks'

/** 上传原件留证目录（每次导入一份，不覆盖）。 */
export function deliverUploadDir(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, 'deliver-uploads')
}

/** 解压后的产物根目录（每个 run 一个子目录，由 python 一侧决定名字）。 */
export function deliverImportRoot(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, 'deliver')
}

/** 单次上传上限：产物 zip 含逐轮权重（几十轮 ≈ 几十 MB）；512MB 已是「拿错文件」的门。 */
export const DELIVER_MAX_BYTES = 512 * 1024 * 1024

/** 文件名与当前课程对账（返回非空 = 拒收原因）。 */
export function validateDeliverUpload(fileName: string, course: string): string | null {
  const base = path.basename(fileName || '')
  if (!base) return '缺少文件名'
  if (!/\.zip$/i.test(base)) return `只接受 .zip（收到 ${base}）`
  if (!course) return '缺少课程（先在顶部设置课程）'
  // `deliver-<课程>.zip` 是习惯命名；带了前缀就必须与当前课程一致（否则是拿了别的课的产物）
  const m = /^deliver-(.+)\.zip$/i.exec(base)
  if (m && m[1] !== course)
    return (
      `文件名里的课程（${m[1]}）与控制台当前课程（${course}）不一致——` +
      `切到 ${m[1]} 再导，或把文件名改成 deliver-${course}.zip`
    )
  return null
}

/** 上传文件名的安全落地名（去掉路径成分 + 只留安全字符 + 打时间戳，历史不覆盖）。 */
export function deliverUploadPath(course: string, fileName: string, stamp: string): string {
  const safe = path
    .basename(fileName)
    .replace(/[^A-Za-z0-9._-]/g, '_')
    .slice(0, 120)
  return path.join(deliverUploadDir(course), `${stamp}-${safe || 'deliver.zip'}`)
}

export interface DeliverImportPayload {
  run_id: string
  dir: string
  iters: number[]
  last_it: number
  ckpt: string
  state: string
  rows: number
  course: string
  source_zip: string
  bytes: number
}

/** 从 python 的 stdout 里切出机器可读那一行（前面的人读日志一分不丢）。 */
export function parseDeliverImportJson(stdout: string): DeliverImportPayload | null {
  for (const line of stdout.split(/\r?\n/)) {
    const i = line.indexOf(deliverImportJsonMark)
    if (i < 0) continue
    try {
      return JSON.parse(line.slice(i + deliverImportJsonMark.length)) as DeliverImportPayload
    } catch {
      return null
    }
  }
  return null
}

/** python 导入器日志（stdout/stderr 都进这里，失败时人能看到三道门里是哪一道）。 */
export function deliverImportLogPath(course: string): string {
  return path.join(REPO_ROOT, 'tmp', course, 'deliver-import.log')
}

export interface DeliverImportResult {
  ok: boolean
  message: string
  detail?: string[]
  payload?: DeliverImportPayload
}

/** 跑 python 导入器（同步：调用方要它的结果来决定「要不要接着起评估」）。
 *
 *  `runner` 是**可注入的接缝**（缺省就是真 python）：导入的判定（三道门 / 轮次发现）
 *  在 python 一侧、由 `nn-training/tests/test_deliver_zip.py` 钉死；这里注入替身是为了
 *  在不跑 python 的前提下把**控制台这一侧**的契约（argv 形状 / 标记解析 / 失败转述 / 要不要
 *  接着起评估）也测到——两边各测自己那一半，不在中间再叠一层端到端。
 */
export function importDeliverZip(
  opts: { course: string; zipPath: string; logFile?: string; destRoot?: string },
  runner: typeof runRunPythonSyncModule = runRunPythonSyncModule,
): DeliverImportResult {
  // `destRoot` 与 `logFile` 一样是**测试接缝**：缺省落课程目录（生产语义），测试指到临时目录
  // ——否则一次测试就会在真实 `tmp/<课程>/` 里建 deliver/ 并往 deliver-import.log 追写。
  const root = opts.destRoot ?? deliverImportRoot(opts.course)
  mkdirSync(root, { recursive: true })
  const r = runner('remote.deliver_zip', [
    '--zip',
    opts.zipPath,
    '--dest',
    root,
    '--course',
    opts.course,
  ])
  // 导入器的人读输出整段留档（失败时那几行就是全部诊断信息）
  try {
    writeFileSync(
      opts.logFile ?? deliverImportLogPath(opts.course),
      `$ python -m remote.deliver_zip --zip ${opts.zipPath} --dest ${root} --course ${opts.course}\n` +
        `[exit ${r.code}${r.timeout ? ' TIMEOUT' : ''}]\n${r.stdout}\n${r.stderr}`,
      { encoding: 'utf-8', flag: 'a' },
    )
  } catch {
    /* 留档失败不影响导入结果 */
  }
  if (r.timeout)
    return {
      ok: false,
      message: '导入超时（>300s）——包是不是太大了？',
      detail: [lastLine(r.stdout)],
    }
  const payload = parseDeliverImportJson(r.stdout)
  if (r.code !== 0 || !payload) {
    return {
      ok: false,
      message: `导入失败：${lastLine(r.stderr) || lastLine(r.stdout) || `exit ${r.code}`}`,
      detail: tail(r.stderr || r.stdout, 4),
    }
  }
  return {
    ok: true,
    message: `已导入 ${payload.run_id}（${payload.iters.length} 轮，it${payload.iters[0]} → it${payload.last_it}）`,
    payload,
  }
}

function lastLine(s: string): string {
  const lines = s.split(/\r?\n/).filter((l) => l.trim())
  return lines.length > 0 ? lines[lines.length - 1].trim() : ''
}

function tail(s: string, n: number): string[] {
  return s
    .split(/\r?\n/)
    .filter((l) => l.trim())
    .slice(-n)
}

/** 把上传的 zip 落盘（写进 `deliver-uploads/`，留证）。返回落地路径。 */
export function saveDeliverUpload(course: string, fileName: string, bytes: ArrayBuffer): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const dest = deliverUploadPath(course, fileName, stamp)
  mkdirSync(path.dirname(dest), { recursive: true })
  writeFileSync(dest, new Uint8Array(bytes))
  return dest
}

/** 导入之后接着起评估：**复用 evalA**（同一条命令、同一份语料口径、同一个互斥键）。
 *
 *  评估哪一轮 = 包里**末轮**（`ckpt`）：`--iter` 用末轮号，于是读数挂在「这条腿跑到哪」
 *  那一行上（半离线/补传腿的 it 号与课程全局一致）。
 *
 *  包导成功但评估起不来 **不算导入失败**：产物已经落地可读（那是这一半的全部价值），
 *  所以结果是 ok + 一句明确的「评估没起来，原因 X」（人可以再去点 evalA 按钮）。
 */
export function launchPostImportEval(
  course: string,
  payload: DeliverImportPayload,
  deps: Pick<DeliverUploadDeps, 'launchEval'> = realDeps,
): { ok: boolean; message: string } {
  if (!existsSync(payload.ckpt))
    return { ok: false, message: `末轮权重不在盘上（${payload.ckpt}）——导入目录被动过？` }
  return deps.launchEval(course, payload.ckpt, payload.last_it)
}

/** `POST /api/deliverUpload`（multipart）的完整处理：落盘 → python 导入 → 自动评估。
 *
 *  响应体：`{ok, message, detail?, import?: {...}, evalStarted: bool}`——message 是**一句话
 *  结论**（面板直接上屏），detail 是失败时的诊断行。
 */
export interface DeliverUploadDeps {
  /** 上传落盘（测试注入替身 → 不往真实 tmp 里写）。 */
  save: typeof saveDeliverUpload
  /** python 导入器（测试注入替身 → 不跑 python）。 */
  importZip: typeof importDeliverZip
  /** 导入后自动评估（测试注入替身 → 不真起 evalA：那会真跑几十局）。 */
  launchEval: typeof launchEvalA
}

const realDeps: DeliverUploadDeps = {
  save: saveDeliverUpload,
  importZip: importDeliverZip,
  launchEval: launchEvalA,
}

export async function handleDeliverUpload(
  req: Request,
  course: string,
  deps: DeliverUploadDeps = realDeps,
): Promise<Response> {
  const bad0 = course ? null : '缺少 course（先在顶部设置课程）'
  if (bad0) return jsonResp({ ok: false, message: bad0 }, 400)
  let zipPath = ''
  try {
    const form = await req.formData()
    const file = form.get('file')
    if (!(file instanceof File)) return jsonResp({ ok: false, message: '缺少文件' }, 400)
    const bad = validateDeliverUpload(file.name, course)
    if (bad) return jsonResp({ ok: false, message: bad }, 400)
    if (file.size > DELIVER_MAX_BYTES)
      return jsonResp(
        {
          ok: false,
          message: `文件过大（${Math.round(file.size / 1024 / 1024)}MB > ${DELIVER_MAX_BYTES / 1024 / 1024}MB）`,
        },
        413,
      )
    zipPath = deps.save(course, file.name, await file.arrayBuffer())
    const imported = deps.importZip({ course, zipPath })
    if (!imported.ok || !imported.payload)
      return jsonResp({ ok: false, message: imported.message, detail: imported.detail }, 400)
    const payload = imported.payload
    const ev = launchPostImportEval(course, payload, deps)
    return jsonResp(
      {
        ok: true,
        message: imported.message + '；' + ev.message,
        detail: [
          `产物目录：${path.relative(REPO_ROOT, payload.dir).replace(/\\/g, '/')}`,
          `末轮权重：it${payload.last_it}`,
        ],
        import: payload,
        evalStarted: ev.ok,
      },
      200,
    )
  } catch (e) {
    return jsonResp(
      {
        ok: false,
        message: `导入失败：${e instanceof Error ? e.message : String(e)}`,
        detail: zipPath ? [`已落盘：${zipPath}`] : undefined,
      },
      500,
    )
  }
}

function jsonResp(body: Record<string, unknown>, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

//: 供面板/测试引用（避免各自硬编码同一串）
export { deliverFileNamePrefix }
