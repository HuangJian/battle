/** component-groups.ts — 组件卡分组（R3-3：**服务面**（单例角色）vs **课程面**（按课程））。
 *
 *  为什么分组，而不是继续排一行（且**分组标题本身就是作用域标签**，行内不再重复）：
 *
 *   - 单例角色（**trainer** / hub / selfNode / 隧道 / **本机 worker**，族内顺序即此序）与当前查看的课程**无关**
 *     ——0 门课也在、100 门课也是一份。混在按课的行里，操作员会去「给这门课再起一个 hub /
 *     trainer / worker」（而它们是共享的：第二个实例抢同一个端口、两套调度器抢同一批 traj、
 *     多份 worker 抢同一份 hub 队列），或者把「共享 hub 没起」读成「这门课自己的 hub 没起」；
 *   - 课程面恰好相反：卡片上的对象**就是当前查看的那门课**。换课程 = 换对象，这一点必须在视觉上
 *     说出来（否则「启动」按下去，起的可能是另一门课）。**2026-09-19 起这一族没有成员**
 *     （账本键收敛的终点：进程面全在服务面），但机制保留——它是 `scope` 的函数，不是名单。
 *
 *  ★ **成员资格只由 `ComponentView.scope` 决定**（服务端按 `core/registry.componentScope` 填）。
 *  这里**不写**「哪些 key 属于哪一族」的名单——名单一旦与账本槽位规则漂开，症状是某个组件从
 *  UI 上**消失**（而它还在被启动、被监督、被冒烟），且读代码的人只看到一行 filter、不知所为何来。
 *  本模块只声明：**族 → 标题/说明** 与 **族内化妆顺序**。
 *
 *  （旧的 `NODE_FACE_COMPONENTS = ['workerServe']` 例外名单已随「本机伪节点退出受管组件」
 *  在 2026-09-19 删除：`worker_server` 此后只是**节点登记表里的数据**（`nodes[]` 的
 *  gpu_push / local_push 条目）与节点行，不再是账本里的受管组件 ⇒ 不需要第二份名单。）
 */

import type { ComponentScope, ComponentView } from './console-types'

/** 族标识。★ 本文件在**客户端** bundle 里：别写出未加引号的 `node:` 键（`{ node: … }`）——
 *  构建期的禁词门禁把 `node:` 当「引入了 node 内置模块」（`server/build.ts` 的 FORBIDDEN）。
 *  2026-09-19 实际踩到：`Record<ComponentFamilyId, …>` 里那个 `node: []` 就让三份 bundle 全挂。 */
export type ComponentFamilyId = 'service' | 'course'

/** 族元数据（标题上屏；hint 是组标题的悬停/副标题——它回答「这一族的键是什么」）。
 *
 *  2026-09-20（用户指令）：「服务面 · 单例」→「**服务**」（「课程面 · 按课程」→「课程」同理）：
 *  标题只说**这一族是什么**，作用域细节由 `hint` 承担——逐行的「共享/单例」徽章已删，
 *  标题也不必再挂一个全族同值的后缀。
 */
export const FAMILY_META: Record<'service' | 'course', { title: string; hint: string }> = {
  service: {
    title: '服务',
    hint:
      '与课程数量无关：一个进程服务所有并行课程（0 门课也在，100 门课也只有一份）——' +
      '门房（hub）/ 跑腿（隧道）/ 管事（trainer）/ 丹徒（本机 worker）各一个进程服务所有课程，' +
      '采办（本机采样 agent）则全机一份。逐行悬停有各自的用途说明。',
  },
  course: {
    title: '课程',
    hint: '卡片上的对象是**当前查看的那门课**——换课程 = 换对象（启动前先看清侧栏选的是哪门课）。',
  },
}

/** 组内展示顺序（**纯化妆**：未列出的 key 落到组尾，绝不丢弃——成员资格只由 scope 决定）。 */
const ORDER: Record<'service' | 'course', readonly string[]> = {
  // 服务面顺序 = **用户指令 2026-09-20**（不再是「谁依赖谁」的推演）：
  //   trainer（训练实际在跑的那个）→ hub（作业中枢）→ agent（本机控制面）→ 隧道（入站通道）
  //   → 本机 worker（消费同一份队列的活）。
  // 读序理由：这一行最常问「训练在不在跑 / 队列通不通」，于是把 trainer 与 hub 提到最前；
  // agent / 隧道 / worker 是支撑设施，靠后不档视线。
  service: ['trainingLoop', 'hubServer', 'selfNode', 'cloudflared', 'localWorker'],
  // 课程面：**当前空**（2026-09-19 收敛完成：selfNode 单例，hub / 隧道 / trainer / 本机 worker
  // 四条共享，本机伪节点退出受管组件 —— 于是 `cardFamilies` 只渲染服务面一组，这不是坏了）。
  // `course` 族本身保留：它是 scope 的函数，日后真出现按课程的卡片会自然落进去，不必改代码。
  // 注：trainer（R3-5）与本机 worker（2026-09-19）都已收敛为共享进程 ⇒ 属于**服务面**，
  // 不在此列（ORDER 只是化妆顺序：真成员资格由 `scope` 决定，写错这里不会让组件错族）。
  course: [],
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
  for (const c of components) buckets[FAMILY_OF_SCOPE[c.scope ?? 'course']].push(c)
  const out: ComponentFamilyView[] = []
  for (const id of ['service', 'course'] as const) {
    if (buckets[id].length === 0) continue
    out.push({ id, ...FAMILY_META[id], rows: sortByOrder(buckets[id], ORDER[id]) })
  }
  return out
}

// ★ `scopeBadge`（逐行「共享」/「单例」徽章）已于 2026-09-20 删除（用户指令：服务面每行为它不需要
// 显示共享/单例）。它当初成立的偷设是「一行可能属于任意作用域」——而现在服务面**整族**就是同一个
// 作用域（`hub`/`隧道`/`trainer`/本机 worker 共享 + selfNode 单例），族标题与 `FAMILY_META.hint`
// 已经说了一次；逐行再挂就是同一句话重复 N 遍（徽章的密度决定了它还读不读得出来）。
// 成员资格仍由 `scope` 决定（`FAMILY_OF_SCOPE`）——删的是标签，不是分组判据。
