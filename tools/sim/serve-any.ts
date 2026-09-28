/**
 * serve-any.ts — 长驻 worker 的**同质入口**：一个进程服务所有 mode。
 *
 * 为什么（2026-09-28，DECISIONS §2026-09-28-goalnn-persist-homogeneous-serve）：
 * agent 的 persist 池原先**按导出器分种类**（rollout 一类、eval 一类……），因为 worker 的入口
 * 在 spawn 时就烧死在 argv[0]（`bun <exporter>.ts --serve`），一个 worker 只会跑那一个导出器。
 * 于是池里出现「腿」：预热的主腿按**盘上权重 mtime** 猜（那只是「上一代跑过什么」），猜错腿时
 * 池满 ⇒ 这一波每一局都退回一次性 spawn（a95 实测 **3.1s/局** vs 对腿 **1.55s/局**），换腿还得
 * 退役空闲 worker 再补满（一次 **~39s**）。而这两个导出器都是 `runServe(main)` 的**无状态外壳**
 * （每局新建 World；模块级无可变状态），分家的原始理由——别动进 codeHash 红线的
 * `export-rl-rollout.ts` —— 自 2026-08-31 起已不成立（`codehash-files.txt` 把四个导出器 +
 * serve-loop 全收进去了）。所以：**一个入口，按每行的 mode 分派**，池里不再有腿。
 *
 * 协议（`serve-loop.ts` 是唯一实现）：stdin 每行 = 一个任务的 argv（JSON 数组），
 * 本入口的第一个元素是 **mode token**（`rollout` / `eval` / `goal` / `intent`），其余照旧是
 * 该导出器的 argv（**不含入口路径**）。跑完 `__SERVE_OK__` / 失败 `__SERVE_ERR__ <msg>`。
 *
 * mode 表与 `tools/agent/persist-pool.ts` 的 `PERSIST_MODE_BY_ENTRY` 同集（单测对拍，见
 * `tests/serve-any.test.ts`）：两侧任何一边加了导出器而另一边没跟上，都会红。
 *
 * 未知/缺失 mode **抛**（不静默跑默认网格）——这条与 serve-loop 对「合法但非数组的 JSON」的处理
 * 同族：静默跑错比响亮报错贵得多（worker 会一脸正常地把默认网格当成这一局的结果交出去）。
 * 同质化也意味着：**mode 分派错了不会被进程边界挡住**，只能靠这条响亮报错 + 等价性钉子。
 */
import { runServe } from './serve-loop'
import { main as rolloutMain } from './export-rl-rollout'
import { main as evalMain } from './export-eval-game'
import { main as goalMain } from './export-goal-rollout'
import { main as intentMain } from './export-intent-rollout'

/** mode token → 导出器 main（= `PERSIST_MODE_BY_ENTRY` 的值域）。 */
export const SERVE_MODES: Readonly<Record<string, (argv: string[]) => void>> = {
  rollout: rolloutMain,
  eval: evalMain,
  goal: goalMain,
  intent: intentMain,
}

/** 一条任务行 = `[mode, ...导出器 argv]`。未知/缺失 mode ⇒ 抛（响亮，不许静默兜底）。 */
export function dispatch(argv: string[]): void {
  const mode = argv[0]
  const main = mode === undefined ? undefined : SERVE_MODES[mode]
  if (!main)
    throw new Error(
      `serve-any: unknown mode ${
        mode === undefined ? '(missing)' : `'${mode}'`
      } — 合法：${Object.keys(SERVE_MODES).join('|')}`,
    )
  main(argv.slice(1))
}

if (import.meta.main) {
  if (process.argv.includes('--serve')) runServe(dispatch)
  else {
    console.error(
      'usage: bun tools/sim/serve-any.ts --serve   # stdin 每行 = [mode, ...exporter argv]',
    )
    process.exit(2)
  }
}
