/** component-groups.ts — 组件卡分组（R3-3：**服务面**（单例角色）vs **课程面**（按课程））。
 *
 *  为什么分组，而不是继续排一行：
 *
 *   - 单例角色（selfNode / hub / 隧道 / **trainer**）与当前查看的课程**无关**——0 门课也在、
 *     100 门课也是一份。混在按课的行里，操作员会去「给这门课再起一个 hub / trainer」
 *     （而它们是共享的：第二个实例抢同一个端口、或两套调度器抢同一批 traj），
 *     或者把「共享 hub 没起」读成「这门课自己的 hub 没起」；
 *   - 课程面（本机 worker / 本机伪节点）恰好相反：卡片上的对象**就是当前查看的那门课**。
 *     换课程 = 换对象，这一点必须在视觉上说出来（否则「启动」按下去，起的可能是另一门课）。
 *
 *  ★ **成员资格只由 `ComponentView.scope` 决定**（服务端按 `core/registry.componentScope` 填）。
 *  这里**不写**「哪些 key 属于哪一族」的名单——名单一旦与账本槽位规则漂开，症状是某个组件从
 *  UI 上**消失**（而它还在被启动、被监督、被冒烟），且读代码的人只看到一行 filter、不知所为何来。
 *  本模块只声明：**族 → 标题/说明**、**族内化妆顺序**、以及 `NODE_FACE_COMPONENTS` 这一个
 *  **显式例外**（渲染在节点行的组件）。
 */

import type { ComponentScope, ComponentView } from './console-types'

/** 族标识（`node` 族不渲染在组件卡行，见 `NODE_FACE_COMPONENTS`）。
 *
 *  ★ 本文件在**客户端** bundle 里：别写出未加引号的 `node:` 键（`{ node: … }`）——构建期的
 *  禁词门禁把 `node:` 当「引入了 node 内置模块」（`server/build.ts` 的 FORBIDDEN），bundle 会红。
 *  2026-09-19 实际踩到：`Record<ComponentFamilyId, …>` 里那个 `node: []` 就让三份 bundle 全挂。
 */
export type ComponentFamilyId = 'service' | 'course' | 'node'

/** 渲染在**节点行**（`NodePills` / `WorkerRegistry`）而不是组件卡行的组件。
 *
 *  `worker_server` 的语义轴是**节点/GPU 身份**（它的 id / url / concurrency 都是节点的，
 *  hub 的 push 派发与竞速也按节点算），而账本键今天仍按课程——卡片行再渲染一份，就会与节点行
 *  出现「同一件事两个入口」（而两个入口的启停按钮语义不同：一个起本课伪节点，一个起真 GPU）。
 *
 *  声明成**数据**而不是面板里的一行 `filter`：静默过滤会让「有这个受管组件」这件事从 UI 上
 *  消失。它照样能被预设拉起、能被停、能被冒烟 —— 读面必须至少**显式**说不渲染它、以及为什么。
 */
export const NODE_FACE_COMPONENTS: readonly string[] = ['workerServe']

/** 族元数据（标题上屏；hint 是组标题的悬停/副标题——它回答「这一族的键是什么」）。
 *
 *  只有**会渲染成组**的两族有元数据：`'node'` 族是词汇（`NODE_FACE_COMPONENTS` 的注脚），
 *  它在节点行渲染、没有组标题。给它编一份用不上的标题，只会让人以为「某处会画这一族」。
 */
export const FAMILY_META: Record<'service' | 'course', { title: string; hint: string }> = {
  service: {
    title: '服务面 · 单例',
    hint:
      '与课程数量无关：一个进程服务所有并行课程（0 门课也在，100 门课也只有一份）。' +
      '「共享」= hub / 隧道（一个进程服务所有课程）；「单例」= 本机 agent（全机一份）。',
  },
  course: {
    title: '课程面 · 按课程',
    hint: '卡片上的对象是**当前查看的那门课**——换课程 = 换对象（启动前先看清顶栏选的是哪门课）。',
  },
}

/** 组内展示顺序（**纯化妆**：未列出的 key 落到组尾，绝不丢弃——成员资格只由 scope 决定）。 */
const ORDER: Record<'service' | 'course', readonly string[]> = {
  // 服务面按「谁依赖谁」排：agent（一切动作的落点）→ hub（调度中枢）→ 隧道（入站通道）
  // → trainer（消费 hub 的作业队列、服务所有课程）。
  service: ['selfNode', 'hubServer', 'cloudflared', 'trainingLoop'],
  // 课程面：本机 worker 在前（最常按的那一个），本机伪节点在后。
  // 注：trainer 已于 2026-09-19（R3-5）收敛为共享单例 ⇒ 它属于**服务面**，不在此列
  // （ORDER 只是化妆顺序：真成员资格由 `scope` 决定，写错这里不会让组件错族）。
  course: ['localWorker', 'workerServe'],
}

const FAMILY_OF_SCOPE: Record<ComponentScope, 'service' | 'course'> = {
  singleton: 'service',
  shared: 'service',
  course: 'course',
}

export interface ComponentFamilyView {
  id: ComponentFamilyId
  title: string
  hint: string
  rows: ComponentView[]
}

function sortByOrder(rows: ComponentView[], order: readonly string[]): ComponentView[] {
  const rank = (k: string): number => {
    const i = order.indexOf(k)
    return i < 0 ? order.length : i
  }
  return [...rows].sort((a, b) => rank(a.key) - rank(b.key))
}

/** 组件卡分组：`[服务面, 课程面]`（空组不渲染——没东西可说时不留空壳）。
 *
 *  `scope` 缺省/未知 ⇒ 课程面（单侧保守，与 `ComponentView.scope` 的注释同规）。
 */
export function cardFamilies(components: readonly ComponentView[]): ComponentFamilyView[] {
  const buckets: Record<'service' | 'course', ComponentView[]> = { service: [], course: [] }
  for (const c of components) {
    if (NODE_FACE_COMPONENTS.includes(c.key)) continue
    buckets[FAMILY_OF_SCOPE[c.scope ?? 'course']].push(c)
  }
  const out: ComponentFamilyView[] = []
  for (const id of ['service', 'course'] as const) {
    if (buckets[id].length === 0) continue
    out.push({ id, ...FAMILY_META[id], rows: sortByOrder(buckets[id], ORDER[id]) })
  }
  return out
}

/** 作用域徽章：**只说 scope 说不出来的那件事**。
 *
 *  - `shared` ⇒ 「共享」（一个进程服务所有课程）——不说，操作员会以为「这门课自己的 hub 停了」
 *    而去重复启动（第二个 hub 抢同一端口）；
 *  - `singleton` ⇒ 「单例」（全机一份）；
 *  - `course` ⇒ **null**：按课程是这张卡片的**默认语义**（组标题已说「课程面 · 按课程」），
 *    每行再挂一个「本课」只是噪声——徽章的密度决定了它还读不读得出来。
 */
export function scopeBadge(c: ComponentView): { text: string; cls: string; title: string } | null {
  if (c.scope === 'shared') {
    return {
      text: '共享',
      cls: 'tc-cc__scope--shared',
      title:
        '共享实例：一个进程服务所有并行课程（启动/停止/冒烟的操作对象是同一个），' +
        '不属于任何单门课；重复启动会与现有实例抢同一端口。',
    }
  }
  if (c.scope === 'singleton') {
    return {
      text: '单例',
      cls: 'tc-cc__scope--singleton',
      title: '单例：全机一份（所有课程共用同一个 agent——它服务任意课程的 rollout/eval 请求）。',
    }
  }
  return null
}
