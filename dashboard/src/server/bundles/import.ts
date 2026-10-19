/** import.ts — 训练产物导入（`deliver-<课程>.zip`）**并自动按课程配置跑 A 层评估**。
 *
 *  流程（用户 2026-09-17 需求）：
 *    浏览器上传 zip → 落到 `tmp/<课程>/deliver-uploads/`（留证）→ python
 *    `remote.deliver_zip` 解成产物目录（`tmp/<课程>/deliver/<run_id>/`，三道门在那边）
 *    → **接着起 evalA**（`trainer/eval_a_once.py`，与训练每 eval_every 轮同口径的干净评估，
 *    语料就来自课程配置的 eval_stages/eval_games_per_stage）。
 *
 *  为什么评估复用 evalA 而不是自己拼一套：那条命令的语料口径、双轨种子、账本格式
 *  （`tmp/<课程>/eval_log.jsonl`）全部已经与控制台指标表对齐——重写一份 = 自造第二种读数。
 *
 *  **上传体是不可信输入**：文件名先跟当前课程对账（`deliver-<课程>.zip`），zip 内容的三道门
 *  在 python 一侧（zip-slip / 形状 / 课程对账）。这里只额外挡两件明显的事：非 .zip、超大。
 *
 *  ★ 2026-10-17（plan §7-1）：导入**全程不检查课程是否在训/有没有开课标记**。整条链
 *  （落盘 → 解包 → 并账本 → 起 evalA）都是文件层的事，与「课在不在跑」无关；课停之后
 *  人恰恰最需要把最后一段产物收进来。唯一与 hub 有关的一步（T6 段末横幅转交）**只是
 *  装饰**：hub 不可达就照导、在 detail 里说明一句（见 `handleDeliverUpload`）。
 *
 *  ⚠ 因此 `tmp/<课程>/deliver/` 是**课程的长期资产，不是临时物**：它跟 `tmp/<课程>/` 下
 *  的其它东西（日志、留证原件）不是一个保留等级。`tools/tmp-clean.py` 为此显式豁免 run
 *  根里的 `deliver/`（§7-3）——课停了、人正要导入时，正是那个目录最值钱的时候。
 */

import { existsSync, mkdirSync, readdirSync, statSync, unlinkSync, writeFileSync } from 'fs'
import { writeFile } from 'fs/promises'
import path from 'path'
import { loadConfig } from '../../core/config'
import { REPO_ROOT, tmpCourseDir } from '../../core/paths'
import { hubOfflineAdmin, liveHub } from '../../stack/hub-admin'
import type { OfflineResultView } from '../../web/view'
import { launchEvalA } from '../eval-a-run'
import { type RunPythonResult, runRunPythonAsyncModule } from '../run-python'
import { deliverImportJsonMark, deliverFileNamePrefix } from './marks'

/** 上传原件留证目录（每次导入一份，不覆盖）。 */
export function deliverUploadDir(course: string): string {
  return path.join(tmpCourseDir(course), 'deliver-uploads')
}

/** 解压后的产物根目录（每个 run 一个子目录，由 python 一侧决定名字）。 */
export function deliverImportRoot(course: string): string {
  return path.join(tmpCourseDir(course), 'deliver')
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
  // 课程段 = `@` **之前**那一段（python 侧 `deliver_zip.course_from_filename` 同一规则，两处同改）：
  // 导出/一键包把留历史用的附注放在 `@` 之后（`deliver-<课>@<run_id>-it<N>.zip`，plan §5.2）
  // ——附注不是课程，拿它比对会把本来正确的包报成「别的课的产物」。
  const seg = m ? m[1].split('@')[0] : ''
  if (m && seg !== course)
    return (
      `文件名里的课程（${seg}）与控制台当前课程（${course}）不一致——` +
      `切到 ${seg} 再导，或把文件名改成 deliver-${course}.zip`
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
  /** 并进课程账本的逐轮训练行数（= 导入后「各轮指标表」会显示几轮）。
   *
   *  ★ 2026-09-22：导入只落产物目录时，指标表**一行都不显示**（那张表只读
   *  `tmp/<课程>/training_log.jsonl` 的 `iteration` 事件）⇒ python 导入器现在顺手把
   *  产物包的 `metrics.jsonl` 搬进账本，这个数就是搬了几行（0 = 账本里已有/包内没有）。 */
  metric_rows?: number
  /** 并进课程账本的 eval 逐局行 / summary 行数（云机评估回到本机的唯一载体；0 = 包内没有）。 */
  eval_rows?: number
  eval_summaries?: number
  /** 落位的三个计数（plan §8.2-2：**两条腿共用一份判据**）：新增的轮次数、
   *  因「已有不同字节」跳过的轮次数（先到者为准）、补齐的缺件数。
   *
   *  为什么必须报出来：重导同名 run 时「什么都新增不了」与「导入了 5 轮」在**只报总轮数**
   *  的旧文案下长得一模一样，而这两件事的处置完全不同（前者要人裁决，后者是常态）。 */
  kept_rounds?: number
  skipped_rounds?: number
  healed_files?: number
  /** 包体检逐项（plan §6.2-1 / DoD-5c）：`{id, ok, detail}`。**报告不是门**——
   *  任何一项不过都不阻断导入，所以它只在结论/详情里露面，不进 `ok` 的判据。 */
  checks?: Array<{ id: string; ok: boolean; detail: string }>
  checks_ok?: boolean
  /** 包内自述（`IMPORT_META.json`，§5.2/§6.2-2）：一键包/导出包才有。
   *  课程段与本次导入不一致的包在 python 侧就被拒了，所以这里留在成功路径上是**同一条事实
   *  的上屏**（人不静默切课，看得见「这个包自报是哪个课/哪一段/什么来源」）。 */
  package_course?: string
  package_run_id?: string
  package_source?: string
  /** 半段快照（`state.json` 自报的末轮 > 包内末轮）：导得了，但读数只到包内那一段。 */
  partial?: boolean
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
  return path.join(tmpCourseDir(course), 'deliver-import.log')
}

/** 留证目录保留几份（plan §6.2-5）：每次导入一份原件（现场 43MB/次）——只留最近几份。 */
export const DELIVER_UPLOADS_KEEP = 3

// ───────────────────── 响亮报错：已知失败 → 人话 + 出路（§6.2-4） ─────────────────────

/** 报错翻译的上下文（日志绝对路径是这条里最要紧的一件：人得知道去哪儿看全量留档）。 */
export interface DeliverFailureContext {
  logFile: string
  destRoot: string
  course: string
}

interface FailureRule {
  /** 门的编号（进 detail：人一眼看出挂在哪一道上）。 */
  id: string
  re: RegExp
  /** 结论句：**可执行的人话**（铁律：不出现裸 `WinError`/`Traceback`/`PermissionError`）。 */
  message: (text: string, ctx: DeliverFailureContext) => string
  /** 「下一步做什么」——每条都要能直接照做。 */
  actions: string[]
}

/** 包内顶层那串（python 的 shape 门会打它）：转成人读的一句。 */
function topNamesIn(text: string): string {
  const m = /包内顶层[:：]\s*(\[.*?\])/.exec(text)
  return m ? `（包内顶层：${m[1]}）` : ''
}

/** python 转述的课程冲突那句（它本身就带两个课名 + 动作）——去掉前缀直接当结论。 */
function cleanPythonLine(text: string): string {
  const line = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => /课程|weights\.json|zip/.test(l))
  return (line ?? '（python 侧没有给出可读的一行）').replace(/^\[deliver\]\s*/, '')
}

/** 已知失败 → 人话（plan §6.2-4）。顺序即优先级：越具体的形状排前面。 */
const FAILURE_RULES: FailureRule[] = [
  {
    id: 'blocked-by-handle',
    re: /WinError 5\b|WinError 32\b|Access is denied|PermissionError|另一个程序正在使用|process cannot access/i,
    message: () =>
      '落位时被本机进程占住（Windows 上目录/文件改名要求整树没有句柄）——产物往往已经导进去了',
    actions: [
      '先看日志末尾（下方）确认已导入到哪一轮：已落位的轮次不会丢，账本/指标表也会跟着更新',
      '再跑一次这把导入（同名轮先到者为准，不会覆盖已落位的字节）',
      '仍失败：关掉占着产物目录的进程（编辑器/杀软/资源管理器窗口），或人工走 python 的 --replace 逃生门',
    ],
  },
  {
    id: 'target-exists',
    re: /WinError 183\b|already exists|目标目录已存在/i,
    message: (_t, ctx) =>
      `目标目录已存在（这个 run 已经导入过）——产物目录是 ${ctx.destRoot}/<run_id>/`,
    actions: [
      '要区分「同一条腿的续导」（先到者为准，正常）还是「两条腿用了同一个 run 名」（改用另一个 run 名或人工裁决）',
      '看一眼控制台「各轮指标表」：已有轮次会照旧显示，不会被覆盖',
    ],
  },
  {
    id: 'not-a-zip',
    re: /不是合法的 zip|BadZipFile|not a zipfile|File is not a zip file/i,
    message: () => '文件不是 zip（下载被截断？）',
    actions: ['重新下载一次再传（对比文件大小：截断的包明显偏小）'],
  },
  {
    id: 'wrong-package',
    re: /BUNDLE_INDEX|任务包/,
    message: () => '拿错了包：这是发给云机跑的**任务包**，不是云机跑完落回的产物 zip',
    actions: ['要交回来的是产物 zip（里面有 it-NNN/weights.json 那种）'],
  },
  {
    id: 'shape',
    re: /没有 it-\d*\/weights\.json|不是训练产物 zip/,
    message: (t) => `包里没有逐轮权重（it-NNN/weights.json）——不是训练产物 zip${topNamesIn(t)}`,
    actions: ['核对上面那串顶层名：拿成回灌目录/空包/别的课的包都会长这样'],
  },
  {
    id: 'course-mismatch',
    re: /课程（.+）与控制台当前课程|自述的课程/,
    message: (t) => cleanPythonLine(t),
    actions: [],
  },
  {
    id: 'disk-full',
    re: /ENOSPC|No space left|没有足够的空间|磁盘满/i,
    message: () => '目标盘满——产物与留证目录（tmp/<课>/deliver-uploads/）会一起把它撑爆',
    actions: ['清掉旧的 deliver-uploads/（每次导入留一份原件，几十 MB/次）后重试'],
  },
  {
    id: 'timeout',
    re: /TIMEOUT/,
    message: () => '导入超时（>300s）——包是不是太大了？',
    actions: ['先看日志末尾：落位是逐轮的，已经落下的那几轮不会丢'],
  },
]

/** 兜底：不在表里的失败——**依然**给日志绝对路径与末尾八行（人不怕未知，怕看不见）。 */
const FALLBACK_RULE: FailureRule = {
  id: 'unclassified',
  re: /$^/,
  message: () => '导入失败（python 侧）：见下方日志末尾八行',
  actions: ['先看日志末尾的报错行；那是唯一的诊断面'],
}

/** 把 python 的失败输出翻成「一句人话 + 下一步 + 去哪儿看全量」。
 *
 *  为什么必须在这一层收口（2026-10-17，plan §6.2-4）：现场只看到
 *  `导入失败：PermissionError: [WinError 5] Access is denied: 'D:\gi…` —— 路径被 UI 截断、
 *  没有任何指引，而这类失败**九成是已知的**（句柄占住 / 目标已存在 / 拿错包 / 盘满 / 超时）。
 *  所以这张表把「挂在哪道门、下一步做什么」写死在代码里，而不是留给排障时读日志的人。
 *
 *  铁律（DoD-5a）：结论句里不出现裸 `WinError`/`Traceback`/`PermissionError` —— 原始文本
 *  一律只进 `detail`（末尾八行 + 日志绝对路径）。
 */
export function explainDeliverFailure(
  stderr: string,
  stdout: string,
  ctx: DeliverFailureContext,
): { message: string; detail: string[]; gate: string } {
  const text = `${stderr}\n${stdout}`
  const rule = FAILURE_RULES.find((r) => r.re.test(text)) ?? FALLBACK_RULE
  const message =
    rule.id === 'unclassified' ? rule.message(text, ctx) : `导入失败：${rule.message(text, ctx)}`
  return {
    message,
    detail: [
      `挂在的门：${rule.id}`,
      ...rule.actions.map((a) => `出路：${a}`),
      ...tail(stderr || stdout, 8),
      `完整日志（绝对路径）：${ctx.logFile}`,
    ],
    gate: rule.id,
  }
}

// ────────────────────────── T6：跑满 ⇒ run_complete（灰横幅）转交 ──────────────────────────

/** hub 侧该课各 run 的**段末摘要**（`/admin/offline.results[课]`）；hub 不可达/旧版 → 空结果。
 *
 *  为什么必须问 hub：云机跑完的那一刻控制台不在场（账本的唯一合法写者是 python，
 *  而 python 只在导入路径上跑）。hub 的段末摘要就是「这一段跑满了吗」的**唯一**外部证据；
 *  拿不到就不转交（`null` = 不可知，而不是「没跑满」）。
 *
 *  ★ 2026-10-17（§7-2）：返回体从「摘要或 null」改成 `{results, hubUp}`——「hub 不可达」与
 *  「hub 齐了但没这一段」在旧形状下长得一模一样，而横幅未落账的原因得说对的那一个。 */
export interface HubOfflineResults {
  /** 该课各 run 的段末摘要；hub 可达但没有这一课/这一段 ⇒ null。 */
  results: Record<string, OfflineResultView> | null
  /** hub **应答了吗**（`/admin/queue` 有回音）。
   *
   *  为什么必须与 `results` 分开（plan §7-2）：`results === null` 有两个成因——「hub 根本没
   *  起来」（停课后常态）与「hub 活着、只是没有这一段的摘要」。横幅未落账的原因必须说对：
   *  前者要显式告诉人「不影响导入，是 hub 不可达」，后者没必要多嘴（新段还没上 hub）。
   *  把两件事挤进一个 `null` 就只能编一个原因，而编错的原因会把人引去修根本没坏的东西。 */
  hubUp: boolean
}

/** 问 hub 要该课各 run 的**段末摘要** + 它到底答没答（`HubOfflineResults`）。 */
export async function fetchHubOfflineResults(course: string): Promise<HubOfflineResults> {
  try {
    const cfg = loadConfig()
    const live = await liveHub(cfg, course)
    if (!live) return { results: null, hubUp: false }
    const admin = await hubOfflineAdmin(live.url, String(cfg.rl?.remote_token ?? ''))
    return { results: admin?.results?.[course] ?? null, hubUp: true }
  } catch {
    return { results: null, hubUp: false } // 观测面坏了不该把导入带崩（下一拍快照会再探）
  }
}

/** T6 转交判据（**纯函数**，可单测）：
 *
 *  ① hub 段末摘要的 `run_id` 与本次导入的 `run_id` **匹配**（谁是谁的段必须对上）；
 *  ② 该摘要自报 `end_it_reached`（云机说跑满了）；
 *  ③ 摘要的末轮号与导入产物末轮号**一致**——半段导入（同一 run 的旧包/ LATEST.zip）
 *     即使 hub 说跑满也不得亮横幅（否则「导入 50 轮却宣布整段完成」）。
 */
export function endItReachedForRun(
  payload: Pick<DeliverImportPayload, 'run_id' | 'last_it'>,
  hubResults: Record<string, OfflineResultView> | null | undefined,
): boolean {
  const rec = hubResults?.[payload.run_id]
  return rec?.endItReached === true && rec.itEnd === payload.last_it
}

/** 该 run 的段末种类（`open` / `course` / `segment`；`''` = 不可知）。纯函数。 */
export function segEndKindForRun(
  payload: Pick<DeliverImportPayload, 'run_id'>,
  hubResults: Record<string, OfflineResultView> | null | undefined,
): string {
  return hubResults?.[payload.run_id]?.segEndKind ?? ''
}

/** 段末种类是不是「**不是课末**」（`open` = 无限课的一段；`segment` = 有限课程被截短的一段）。
 *
 *  这三态的分界是评审 P1-3 点名的那一格：无限课**每一段**都 `end_it_reached`，若照旧转交
 *  `run_complete`，控制台就会“每段亮一次已完成”（而课根本没完）。降级口径：`''`（旧 hub /
 *  旧云机 / 非段末）= **不可知** ⇒ 按旧行为转交，不静默少打一次真课末的横幅。
 */
export function segmentNotCourseEnd(kind: string): boolean {
  return kind === 'open' || kind === 'segment'
}

/** 第二趟 python：为已导入的 run 落 `run_complete`（**不**重新解包 zip）。
 *
 *  账本的唯一写者是 python（`worker/events.py::write_run_complete`）；控制台只把判决
 *  传进去（`--end-it-reached --run-id …`），不自己碰账本。失败返回 `{ok:false}` 且
 *  **不改导入的 ok**——权重已经落地可评估（这一半的全部价值），少的只是完成横幅。 */
export async function writeRunCompleteWithPython(
  course: string,
  runId: string,
  opts: { destRoot?: string; logFile?: string } = {},
  runner: DeliverZipRunner = runRunPythonAsyncModule,
): Promise<{ ok: boolean; message: string }> {
  const root = opts.destRoot ?? deliverImportRoot(course)
  const r = await runner('remote.deliver_zip', [
    '--end-it-reached',
    '--run-id',
    runId,
    '--dest',
    root,
    '--course',
    course,
  ])
  try {
    writeFileSync(
      opts.logFile ?? deliverImportLogPath(course),
      `$ python -m remote.deliver_zip --end-it-reached --run-id ${runId} --dest ${root} --course ${course}\n` +
        `[exit ${r.code}${r.timeout ? ' TIMEOUT' : ''}]\n${r.stdout}\n${r.stderr}`,
      { encoding: 'utf-8', flag: 'a' },
    )
  } catch {
    /* 留档失败不影响转交结果 */
  }
  if (r.timeout) return { ok: false, message: '完成横幅未落账（python 超时）' }
  if (r.code === 0) return { ok: true, message: '已完成（run_complete 已落账，灰横幅下一拍上屏）' }
  const line = lastLine(r.stderr) || lastLine(r.stdout) || `exit ${r.code}`
  return { ok: false, message: `完成横幅未落账（${line}）——产物已导入，可在矩阵上手动确认` }
}

// ───────────────────── B0：回灌腿（hub 已收 run → 本机账本） ─────────────────────
//
// 为什么必须在控制台这一侧（plan/offline-open-ended-iters-and-loud-export §3.2.1 定案①）：
// 「无限轮」的下一段包**只能从本机账本起**（`plan.start_it = 本机 last_it`），而云机跑完那
// 一段时控制台不在场（产物在 hub 手里）——没有回灌，段末之后本机账本/指标停在段起点。
//
// 取件源：hub 把每轮回传都镜像到 `<traj>/deliver/<run_id>/`（`hub/store_offline` ①），
// 补传原件在 `<traj>/remote-jobs/offline/<run_id>/`（hub 自己的落点）——两者同形状，
// 所以两个根都扫，去重取末轮号最大的那份（= 刚跑完的那一段）。

export interface LandedRun {
  runId: string
  dir: string
  /** 最大 `it-NNN`。 */
  lastIt: number
}

/** 本课**已落地的 run 目录**（交付镜像 + 补传原件；按末轮号升序，同 run 只留末轮号大的那份）。 */
export function landedHubRuns(course: string): LandedRun[] {
  const traj = tmpCourseDir(course)
  const roots = [path.join(traj, 'deliver'), path.join(traj, 'remote-jobs', 'offline')]
  const best = new Map<string, LandedRun>()
  for (const root of roots) {
    let names: string[]
    try {
      names = readdirSync(root)
    } catch {
      continue // 这一半还没落过东西（常态）
    }
    for (const name of names) {
      const dir = path.join(root, name)
      let lastIt = 0
      try {
        for (const sub of readdirSync(dir)) {
          const m = /^it-(\d+)$/.exec(sub)
          if (m) lastIt = Math.max(lastIt, Number(m[1]))
        }
      } catch {
        continue
      }
      if (lastIt <= 0) continue
      const prev = best.get(name)
      if (!prev || lastIt > prev.lastIt) best.set(name, { runId: name, dir, lastIt })
    }
  }
  return [...best.values()].sort((a, b) => a.lastIt - b.lastIt)
}

/** 回灌的结果（`ran=false` = 没有可取的段末产物，不是失败）。 */
export interface BackfillResult {
  ok: boolean
  ran: boolean
  note: string
}

/** **段末回灌**（B0）：「hub 已收的末段产物 → 本机账本/指标」。幂等由 python 侧保证。
 *
 *  调用点：`autoOfflineHandoff`（hub 在段末事件里主动触发的那一次）——**先回灌、后判包**：
 *  回灌推进了本机账本/权重，规则表才认得出「包已过期 ⇒ 该重导」（否则它只说「已有任务包，
 *  云机可直接取」——而那份包正是刚跑完的旧段）。
 *
 *  失败不连坐：回灌起不来只显一行，导出腿照走（无回灌的后果是「下一段仍从旧起点」——
 *  那是退化，不是故障；退化的可见面就是这条 note）。
 */
export async function backfillHubRun(
  course: string,
  runner: DeliverZipRunner = runRunPythonAsyncModule,
): Promise<BackfillResult> {
  if (process.env.BCITY_NO_AUTO_TASK_BUNDLE)
    return { ok: true, ran: false, note: '（逃生阀：未回灌段末产物）' }
  const runs = landedHubRuns(course)
  if (!runs.length) return { ok: true, ran: false, note: '' }
  const best = runs[runs.length - 1]
  let r: RunPythonResult
  try {
    r = await runner('remote.deliver_zip', [
      '--from-run',
      best.dir,
      '--run-id',
      best.runId,
      '--dest',
      deliverImportRoot(course),
      '--course',
      course,
    ])
  } catch (e) {
    return {
      ok: false,
      ran: false,
      note: `段末回灌未能启动（${e instanceof Error ? e.message : String(e)}）`,
    }
  }
  try {
    writeFileSync(
      deliverImportLogPath(course),
      `$ python -m remote.deliver_zip --from-run ${best.dir} --run-id ${best.runId} --dest ${deliverImportRoot(course)} --course ${course}\n` +
        `[回灌 exit ${r.code}${r.timeout ? ' TIMEOUT' : ''}]\n${r.stdout}\n${r.stderr}`,
      { encoding: 'utf-8', flag: 'a' },
    )
  } catch {
    /* 留档失败不影响回灌结果 */
  }
  const payload = parseDeliverImportJson(r.stdout) as
    | (DeliverImportPayload & { imported?: boolean; reason?: string })
    | null
  if (r.timeout)
    return { ok: false, ran: true, note: `段末回灌超时（${best.runId}）——产物仍在盘上，可手动导入` }
  if (r.code !== 0 || !payload)
    return {
      ok: false,
      ran: true,
      note: `段末回灌失败（${best.runId}）：${lastLine(r.stderr) || lastLine(r.stdout) || `exit ${r.code}`}`,
    }
  if (payload.imported === false)
    return {
      ok: true,
      ran: true,
      note: `段末产物无需回灌（${payload.reason || `本机已到 it${payload.last_it}`}）`,
    }
  return {
    ok: true,
    ran: true,
    note: `已回灌段末产物 ${payload.run_id}（it${payload.iters[0]} → it${payload.last_it}）——本机账本已推进`,
  }
}

export interface DeliverImportResult {
  ok: boolean
  message: string
  detail?: string[]
  payload?: DeliverImportPayload
}

/** 跑 python 导入器（**异步**：调用方 `await` 它的结果来决定「要不要接着起评估」）。
 *
 *  ★ 2026-09-22：从 `spawnSync` 换成异步子进程（`runRunPythonAsyncModule`）。旧写法把**整个
 *  事件循环**按住十几秒：导入期间控制台的 `/api/state`、5s 快照刷新器、其它查看者的读取
 *  全部排队（“导入时整个控制台卡住”），而 SWR 的「重算丢后台」也名存实亡。异步口径下
 *  子进程交给 libuv，导入照旧要等（**这次 POST 本来就要等结果**），但控制台其余部分照常服务。
 *
 *  `runner` 是**可注入的接缝**（缺省就是真 python）：导入的判定（三道门 / 轮次发现）
 *  在 python 一侧、由 `nn-training/tests/remote/test_deliver_zip.py` 钉死；这里注入替身是为了
 *  在不跑 python 的前提下把**控制台这一侧**的契约（argv 形状 / 标记解析 / 失败转述 / 要不要
 *  接着起评估）也测到——两边各测自己那一半，不在中间再叠一层端到端。
 *  执行体可以是同步替身（`await` 一个非 promise 是常量代价），但**生产路径**没有同步实现。
 */
export type DeliverZipRunner = (
  module: string,
  args: string[],
  opts?: { timeoutMs?: number },
) => RunPythonResult | Promise<RunPythonResult>

export async function importDeliverZip(
  opts: { course: string; zipPath: string; logFile?: string; destRoot?: string },
  runner: DeliverZipRunner = runRunPythonAsyncModule,
): Promise<DeliverImportResult> {
  // `destRoot` 与 `logFile` 一样是**测试接缝**：缺省落课程目录（生产语义），测试指到临时目录
  // ——否则一次测试就会在真实 `tmp/<课程>/` 里建 deliver/ 并往 deliver-import.log 追写。
  const root = opts.destRoot ?? deliverImportRoot(opts.course)
  mkdirSync(root, { recursive: true })
  // argv 形状 = 「zip / 落地根 / 课程」；执行体是异步的，但这里**必须** await（导入结果就是
  // 这一半全部的输出：没有它就没法决定要不要接着起评估）。
  const r = await runner('remote.deliver_zip', [
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
  // 失败的翻译表（§6.2-4）：超时与其它失败走同一张表，只在结论句上分家（超时不是「导入失败」
  // 这个形状，它是「还没跑完」）。`detail` 一律带：门编号 + 出路 + 末尾八行 + 日志绝对路径。
  const failCtx = {
    logFile: opts.logFile ?? deliverImportLogPath(opts.course),
    destRoot: root,
    course: opts.course,
  }
  if (r.timeout) {
    const ex = explainDeliverFailure(`TIMEOUT\n${r.stderr}`, r.stdout, failCtx)
    return { ok: false, message: ex.message, detail: ex.detail }
  }
  const payload = parseDeliverImportJson(r.stdout)
  if (r.code !== 0 || !payload) {
    const ex = explainDeliverFailure(r.stderr || `exit ${r.code}`, r.stdout, failCtx)
    return { ok: false, message: ex.message, detail: ex.detail }
  }
  return {
    ok: true,
    // 「指标表看得见几轮」要写进结论：导入成功后用户第一眼看的正是那张表，而最贵的一种
    // 失败是「导进去了但表是空的」（2026-09-22 实测）。
    message:
      `已导入 ${payload.run_id}（${payload.iters.length} 轮，it${payload.iters[0]} → ` +
      `it${payload.last_it}）` +
      (payload.metric_rows ? `；逐轮指标 +${payload.metric_rows} 行` : '') +
      // 同名轮先到者为准（plan §8.2-2）：跳过的那些**已有不同字节**，要人才能裁决
      // （`--replace` 逃生门），所以必须写进结论而不是留在日志里。
      (payload.skipped_rounds
        ? `；新增 ${payload.kept_rounds ?? 0} 轮、跳过 ${payload.skipped_rounds} 轮（已有不同字节，先到者为准）`
        : '') +
      (payload.healed_files ? `；补齐缺件 ${payload.healed_files} 件` : '') +
      // 体检结论（DoD-5c：成功路径上也要看得见）：全绿就一句带过，有未过项就逐项点名
      // ——「导进去了但读数不可信」是这张表存在的全部理由。
      (payload.checks?.length
        ? payload.checks_ok
          ? `；体检 ${payload.checks.length}/${payload.checks.length} 全绿`
          : `；体检未过：${payload.checks
              .filter((c) => !c.ok)
              .map((c) => `${c.id}（${c.detail}）`)
              .join('、')}`
        : '') +
      (payload.partial ? '；**半段**（state 自报的末轮比包内靠后）' : ''),
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

/** 留证目录的保留策略（plan §6.2-5）：只留最近 `keep` 份，删时响亮一声。
 *
 *  为什么需要：`deliver-uploads/` 每次导入留一份原件（现场 43MB/次）——只涨不落，而它是
 *  「导入历史」，人真正会回头看的只有最近几次（再老的包去 hub/抽屉捞）。
 *  判据：目录里**按名字倒序**（`saveDeliverUpload` 的名字以 ISO 时间戳开头）取前 keep 份。
 *  **永不抛**（清理失败不能反过来把导入弄失败）。 */
export function pruneDeliverUploads(
  course: string,
  keep = DELIVER_UPLOADS_KEEP,
  /** 目录接缝（缺省 = 课程的留证目录）：用例要能在一个临时目录上量「保留 N 份」而不碰真留证目录。 */
  dir: string = deliverUploadDir(course),
): { removed: string[]; bytes: number } {
  const out: { removed: string[]; bytes: number } = { removed: [], bytes: 0 }
  try {
    const files = readdirSync(dir)
      .filter((n) => /\.zip$/i.test(n))
      .sort()
      .reverse()
    for (const n of files.slice(Math.max(0, keep))) {
      const p = path.join(dir, n)
      try {
        out.bytes += statSync(p).size
        unlinkSync(p)
        out.removed.push(n)
      } catch {
        /* 单份删不掉不影响其余（也不影响导入） */
      }
    }
  } catch {
    /* 目录不存在/读不了：没有可清的 */
  }
  return out
}

/** 把上传的 zip 落盘（写进 `deliver-uploads/`，留证）。返回落地路径。
 *
 *  **异步写**（2026-09-22，与导入器同一条理由）：单次上传上限 512MB，`writeFileSync` 写这么大
 *  一块会按住事件循环（磁盘慢时秒级），而这一段时间控制台什么都没法服务。 */
export async function saveDeliverUpload(
  course: string,
  fileName: string,
  bytes: ArrayBuffer,
): Promise<string> {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const dest = deliverUploadPath(course, fileName, stamp)
  mkdirSync(path.dirname(dest), { recursive: true })
  await writeFile(dest, new Uint8Array(bytes))
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
 *
 *  ★ 停课口径（plan §7/DoD-6a）：**不看开课标记、不看在不在训**。课停了之后可能发生的
 *  两件小事都不阻断导入，只是各占一句：① 评估没起来（起不来的原因照原话说）；
 *  ② hub 不可达 ⇒ 段末横幅没落账（`bannerNote`）。两者都不改 `ok`。
 */
export interface DeliverUploadDeps {
  /** 上传落盘（异步写；测试注入替身 → 不往真实 tmp 里写）。 */
  save: typeof saveDeliverUpload
  /** 留证目录保留策略（§6.2-5；测试注入替身 → 不碰真实 `tmp/<课>/deliver-uploads/`）。 */
  pruneUploads: typeof pruneDeliverUploads
  /** python 导入器（测试注入替身 → 不跑 python）。 */
  importZip: typeof importDeliverZip
  /** 导入后自动评估（测试注入替身 → 不真起 evalA：那会真跑几十局）。 */
  launchEval: typeof launchEvalA
  /** T6：hub 侧段末摘要（判决输入；测试注入替身 → 不碰真 hub）。 */
  offlineResults: typeof fetchHubOfflineResults
  /** T6：第二趟 python 落 run_complete（测试注入替身 → 不跑 python）。 */
  writeRunComplete: typeof writeRunCompleteWithPython
}

const realDeps: DeliverUploadDeps = {
  save: saveDeliverUpload,
  pruneUploads: pruneDeliverUploads,
  importZip: importDeliverZip,
  launchEval: launchEvalA,
  offlineResults: fetchHubOfflineResults,
  writeRunComplete: writeRunCompleteWithPython,
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
    zipPath = await deps.save(course, file.name, await file.arrayBuffer())
    // 留证目录保留策略（§6.2-5）：入口就在**刚存完**这一刻（这一份是最新的，删的总是更旧的）。
    // 删除是「响亮一行」而不是默默做：detail 里报清删了几份，否则 43MB×N 是什么时候没的没人知道。
    const pruned = deps.pruneUploads(course)
    const pruneNote = pruned.removed.length
      ? `留证目录已清理：删掉 ${pruned.removed.length} 份旧原件（保留最近 ${DELIVER_UPLOADS_KEEP} 份）`
      : ''
    const imported = await deps.importZip({ course, zipPath })
    if (!imported.ok || !imported.payload)
      return jsonResp(
        {
          ok: false,
          message: imported.message,
          detail: [pruneNote, ...(imported.detail ?? [])].filter(Boolean),
        },
        400,
      )
    const payload = imported.payload
    // ★ T6（plan/auto-offline-handoff §3.6）：完成态转交。判决在这里（run_id 对齐 ∧ 自报
    //   跑满 ∧ 末轮号一致——半段导入不亮横幅），落账在 python（账本唯一写者）。
    //   ★ J2（2026-10-16，P1-3）：**只有课末才转交**——无限课的每一段都自报 `end_it_reached`，
    //   照旧转交就是「每段亮一次已完成」（O5 的灰横幅/停车态，而课还要跑很多段）。
    let endItReached = false
    let segEndKind = ''
    let bannerNote = ''
    let runCompleteOk = false
    let hubResults: Record<string, OfflineResultView> | null = null
    // hub 应答没应答（§7-2）：只用来选「横幅未落账」那一句的措辞——不参与任何判决。
    let hubUp = false
    try {
      const hub = await deps.offlineResults(course)
      hubResults = hub.results
      hubUp = hub.hubUp
      endItReached = endItReachedForRun(payload, hubResults)
    } catch {
      endItReached = false // hub 观测面坏了 = 无从判决 ⇒ 不转交（不是「没跑满」）
      hubUp = false
    }
    segEndKind = endItReached ? segEndKindForRun(payload, hubResults) : ''
    const segmentOnly = segmentNotCourseEnd(segEndKind)
    if (endItReached && !segmentOnly) {
      const wr = await deps.writeRunComplete(course, payload.run_id)
      runCompleteOk = wr.ok
      bannerNote = wr.message
    } else if (segmentOnly) {
      // 「本段完成」另立一个读数（不转交 run_complete）：它是段末滚动腿的可见面——
      // 人看得到「这段跑完了、下一段由 hub 的段末事件滚」，而课的完成态没被误亮。
      bannerNote =
        segEndKind === 'open'
          ? '本段完成（开区间段）——课程未到尾，不亮「已完成」；下一段由段末事件自动滚'
          : '本段完成（有限课程被截短的一段）——课未到尾，不亮「已完成」'
    } else if (!hubUp) {
      // §7-2：停课后 hub 常已关，而转交判据**只能**问 hub（它是不在控制台的那一腿的唯一
      // 证据来源）⇒ 拿不到就不转交。但这件事**必须说出来**：旧的 `catch` 静默把
      // `endItReached` 设成 false，人看到的是一个没有任何解释的「没有横幅」。
      bannerNote = '段末横幅未落账：hub 不可达（不影响导入）'
    }
    const ev = launchPostImportEval(course, payload, deps)
    return jsonResp(
      {
        ok: true,
        message: [imported.message, bannerNote, ev.message].filter(Boolean).join('；'),
        detail: [
          `产物目录：${path.relative(REPO_ROOT, payload.dir).replace(/\\/g, '/')}`,
          `末轮权重：it${payload.last_it}`,
          // 包内自述（§6.2-2）：不静默切课，但让人看得见「这个包自报是哪一门/哪一段/什么来源」
          ...(payload.package_course
            ? [
                `包内自述：课程 ${payload.package_course}·run ${payload.package_run_id || '-'}·` +
                  `来源 ${payload.package_source || '-'}`,
              ]
            : []),
          ...(pruneNote ? [pruneNote] : []),
          // 包体检整屏展开（§6.2-1：「这张表给用户看」）——否则未过项只能从 message 里猜。
          ...(payload.checks ?? []).map((c) => `${c.ok ? '✓' : '✗'} ${c.id}：${c.detail}`),
        ],
        import: payload,
        endItReached,
        segEndKind,
        segmentComplete: segmentOnly,
        runComplete: runCompleteOk,
        evalStarted: ev.ok,
      },
      200,
    )
  } catch (e) {
    // 控制台这一侧的异常（存盘/落证目录/评估调度）也过同一张翻译表：现场的 `PermissionError`
    // 有一半是在这里冒出来的（存盘时目录被占），而裸 WinError 对人不构成任何指引。
    const ex = explainDeliverFailure(
      e instanceof Error ? `${e.name}: ${e.message}` : String(e),
      '',
      { logFile: deliverImportLogPath(course), destRoot: deliverImportRoot(course), course },
    )
    return jsonResp(
      {
        ok: false,
        message: ex.message,
        detail: [...ex.detail, ...(zipPath ? [`已落盘：${zipPath}`] : [])],
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
