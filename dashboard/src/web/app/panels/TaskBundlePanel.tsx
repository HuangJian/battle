/** TaskBundlePanel.tsx — 「任务包」面板：把一段任务交给云机，再把产物收回来评估。
 *
 *  两条能力（用户 2026-09-17）：
 *    ① **导出** `task-<课程>.zip`：整段剩余（课程文件说跑到哪）× 课程 + 起点权重 + 代码
 *       快照 → 上传 Kaggle/Colab，云机自主跑完（连 hub 都不用在线）；
 *    ② **导入** `deliver-<课程>.zip`：把跑完的产物交回来 → 落到 `tmp/<课程>/deliver/` →
 *       **自动按课程配置跑 A 层评估**（读数回填控制台指标表）。
 *
 *  交互纪律：两个动作都是「起一次」——导出是 detached 长任务（靠产物文件出现判断完成），
 *  导入是同步 HTTP（响应里就带回结论）。面板只轮询**产出文件态**，不猜进程状态。
 *
 *  局域网只读：按钮**保持正常外观可点击**（`readOnly` 只换悬停提示），真点击由服务端
 *  403 + flash 兜底——物理禁用会把整区渲染成灰败破碎（见 `ComponentCards.RO_TITLE`）。
 */

import { useEffect, useRef, useState } from 'preact/hooks'
import {
  fetchTaskBundleInfo,
  postAction,
  taskBundleDownloadUrl,
  uploadDeliverZip,
  type TaskBundleInfo,
} from '../lib/api-client'
import { InlineNotice } from '../../components/InlineNotice'

export interface TaskBundlePanelProps {
  course: string
  enabled?: boolean
  /** 局域网只读视图：按钮不禁用，只换悬停提示（动作边界在服务端）。 */
  readOnly?: boolean
}

/** 只读视图的动作按钮悬停提示（与 `ComponentCards.RO_TITLE` 同文案）。 */
const RO_TITLE = '只读模式：操作仅限本机 localhost'

function fmtBytes(n: number): string {
  if (n <= 0) return '—'
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function TaskBundlePanel({
  course,
  enabled = true,
  readOnly = false,
}: TaskBundlePanelProps) {
  const [info, setInfo] = useState<TaskBundleInfo | null>(null)
  const [flash, setFlash] = useState('')
  const [busy, setBusy] = useState<'export' | 'import' | null>(null)
  const [exporting, setExporting] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)
  const beforeMtime = useRef(0)

  const reload = async () => {
    if (!course) return
    try {
      setInfo(await fetchTaskBundleInfo(course))
    } catch {
      /* 面板不因一次拉取失败而报错（下次轮询再来） */
    }
  }

  // 导出中：2.5s 一次看产物文件（出现 = 完成）——不猜进程，只看结果。
  useEffect(() => {
    if (!course || !enabled) return
    void reload()
    if (!exporting) return
    const t = setInterval(() => {
      void fetchTaskBundleInfo(course)
        .then((i) => {
          setInfo(i)
          if (i.exists && i.mtimeMs !== beforeMtime.current) {
            setExporting(false)
            setFlash(`任务包已生成：${i.name}（${fmtBytes(i.bytes)}）——点「下载」取走`)
          }
        })
        .catch(() => undefined)
    }, 2500)
    return () => clearInterval(t)
  }, [course, enabled, exporting])

  const doExport = async () => {
    setBusy('export')
    setFlash('')
    const r = await postAction('exportTaskBundle', { course })
    setBusy(null)
    if (!r.ok) {
      setFlash(r.message)
      return
    }
    beforeMtime.current = info?.mtimeMs ?? 0
    setExporting(true)
    setFlash(`${r.message}（生成中，完成后这里会出现下载）`)
  }

  const doImport = async () => {
    // 没选文件不「物理禁用」按钮：按钮保持可点，由这里把话说清楚（与只读视图同理
    // ——灰掉的按钮不告诉人为什么）。
    if (!file) {
      setFlash('先选一个产物 zip（deliver-<课程>.zip）')
      return
    }
    setBusy('import')
    setFlash(`导入中：${file.name}（解包 + 校验 + 起评估，稍等）…`)
    const r = await uploadDeliverZip(course, file)
    setBusy(null)
    const tail = r.detail && r.detail.length > 0 ? `\n${r.detail.join('\n')}` : ''
    setFlash(`${r.message}${tail}`)
    if (r.ok) {
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <section className="tc-card" aria-label="任务包">
      <header className="tc-card__head">
        <h3 className="tc-card__title">任务包（离线交出去 / 收回来）</h3>
        <span className="tc-caption">
          导出 = 整段剩余 + 课程 + 起点权重 + 代码快照；上传 Kaggle/Colab 自主跑完（不要求 hub
          在线），产物以 <code>deliver-{course || '<课程>'}.zip</code> 交回来即自动按课程配置评估。
        </span>
      </header>

      {/* 行布局用 .tc-line（2026-09-20：此前写 `tc-row` + 内联 flex 补丁 —— `.tc-row` 当时已被
          StatusRow 的芯片定义静默覆盖，行根本不成行，于是有人用内联 style 就地打了补丁）。 */}
      <div className="tc-line">
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          disabled={!course || busy !== null || exporting}
          title={
            readOnly
              ? RO_TITLE
              : '导出整段剩余为 task-<课程>.zip（需先停止训练：包里的起点就是当前进度）'
          }
          onClick={() => void doExport()}
        >
          {busy === 'export' ? '导出中…' : exporting ? '生成中…' : '导出任务包'}
        </button>
        {info?.exists ? (
          <a
            className="tc-btn tc-btn--sm"
            href={taskBundleDownloadUrl(course)}
            download={info.name}
            title={`下载 ${info.name}`}
          >
            下载 {info.name}（{fmtBytes(info.bytes)}）
          </a>
        ) : (
          <span className="tc-caption">还没有导出过任务包</span>
        )}
        {info?.log ? <span className="tc-caption">日志 {info.log}</span> : null}
      </div>

      <div className="tc-line">
        <input
          ref={fileRef}
          type="file"
          accept=".zip,application/zip"
          aria-label="选择训练产物 zip"
          disabled={!course || busy !== null}
          onChange={(e) => {
            const f = (e.currentTarget as HTMLInputElement).files?.[0] ?? null
            setFile(f)
            setFlash(f ? `已选择 ${f.name}（${fmtBytes(f.size)}）` : '')
          }}
        />
        <button
          type="button"
          className="tc-btn tc-btn--sm"
          disabled={!course || busy !== null}
          title={readOnly ? RO_TITLE : '导入训练产物 zip，完成后自动按课程配置跑 A 层评估（evalA）'}
          onClick={() => void doImport()}
        >
          {busy === 'import' ? '导入中…' : '导入并评估'}
        </button>
      </div>

      {flash ? <InlineNotice wrap>{flash}</InlineNotice> : null}
    </section>
  )
}
