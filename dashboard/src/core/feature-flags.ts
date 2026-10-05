/** feature-flags.ts — 控制台功能开关（环境变量、**惰性读取**）。
 *
 * 先例：`offline-eval-backfill.ts::offlineEvalBackfillDisabled()`（惰性函数，单测直接注入 env）。
 *
 * **为什么是函数而不是模块 `const`**：`const` 在 import 时即定值，单测的
 * 「缺省关 / env=1 开」两个方向就只能靠动态 import + 缓存破坏或拆进程；函数每次调用读 env
 * ⇒ 用例 `process.env` 注入 + `finally` 还原即可。
 *
 * ⚠️ `src/web/**` 一律**不得** import 本模块：`server/build.ts` 的 Bun.build `define` 只替换
 * `process.env.NODE_ENV`，其余 `process.env.*` 会原样留在客户端 bundle 里 ⇒ 浏览器里
 * `process` 未定义、模块求值即 `ReferenceError`（首页 hydrate 直接挂）。客户端只认服务端
 * stamp 进 state 的字段（如 `evalboardEnabled`）。
 * 实测与理由：plan/dashboard-memory-evalboard-off.plan.md §5 R4 / tests/evalboard-disabled.test.ts。
 */

/**
 * evalBoard（评估板）功能开关：**缺省关**（`BCITY_EVALBOARD=1` 打开）。
 *
 * 缺省关 —— 与 `NN_SERVE_POOL` 的「缺省开、开关关」相反：本功能缺省就不该跑
 * （用户 2026-10-04 口径「evalBoard 一直未实际使用」），且它是控制台常驻 1.7GB 的主因（R2）。
 *
 * ⚠️ 别和 `EVALBOARD_DATA`（**数据根覆盖**）混：一个是功能开关，一个是数据路径。
 */
export function evalboardEnabled(): boolean {
  return (process.env.BCITY_EVALBOARD ?? '0') !== '0'
}
