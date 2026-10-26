/** error-fingerprint.ts — 错误指纹（服务端专用）。
 *
 *  ★ 为什么住这里而不是 `web/view/error-types.ts`（plan §4.1 P0-1）：它要 `Bun.CryptoHasher`
 *  （Bun 专有全局），而 `dashboard/src/web/**` 有**禁词**闸（`tests/architecture-layering.test.ts`：
 *  `from 'node:'` 与 `Bun.`）与 bundle 禁词闸（`server/build.ts` 的 `FORBIDDEN`：`node:` 与
 *  `Bun.`），且 `web/view/index.ts` 头注把这条写成了目录纪律（「本目录内只允许类型与纯函数：
 *  无 `node:` 模块、无 Bun 全局」）。同类事故有案：`docs/nn/console.md:1443`（客户端里写
 *  `node:` 对象键 ⇒ 三份 bundle 全红）。客户端只需要服务端投影里的 `fp` 字符串，所以拆分零代价。
 *
 *  短 hash 用 `new Bun.CryptoHasher('sha256').update(s).digest('hex').slice(0, 16)`：
 *  同款见 `evalboard/ladder.ts:65`、`evalboard/runner.ts:121`、`server/offline-eval-backfill.ts:284`
 *  ——**不**新造第三种 hash，也**不**去复用 `core/fingerprint-cache.ts`（那是「按 key 缓存重算」
 *  的原语，不是 hash 工具：复用它拿到的是一个缓存而不是一个函数）。
 */

/** 十六进制串（sha1/sha256/短 sha 前缀都吃；≥8 位才动 —— 太短会误伤普通词）。 */
const HEX_RE = /\b[0-9a-f]{8,}\b/gi
/** 数字序列（轮次 / 耗时 / 端口 / PID / 局数……）。 */
const NUM_RE = /\d+/g
/** ISO 时刻与 `[HH:MM:SS]` 前缀（同一错误的两次出现就差在这里）。 */
const ISO_RE = /\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?z?/gi
const CLOCK_RE = /\[\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]/g
/** 绝对路径前缀：只留 `tmp/` 之后的那一段（同一份日志在不同机器/不同盘符下前缀不同）。 */
const ABS_PATH_RE = /[a-z]:[\\/][^\s"'`]*?[\\/]tmp[\\/]|(?:^|\s)\/(?:[^\s"'`]*?\/)+tmp\//gi

/** 归一化：去 hex / 数字 / 时刻 / 绝对路径前缀，折叠空白，小写。
 *
 *  为什么必须归一（plan §4.1）：不归一时「`it=17` 失败」与「`it=18` 失败」就是两条，
 *  一次连续失败刷出几十条 —— 那正是「没人看日志」的成因在详情页上的复刻。
 *  归一化过激的风险（把**不同**错误折成一条）由 `component`/`source` 参与指纹 + 用例兜底。 */
export function normalizeErrorText(s: string): string {
  return s
    .replace(ISO_RE, ' ')
    .replace(CLOCK_RE, ' ')
    .replace(HEX_RE, ' ')
    .replace(ABS_PATH_RE, ' tmp/')
    .replace(NUM_RE, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

/** `fp` = `shortHash(source | component | normalize(message))`。
 *
 *  ★ **`scope` 不进指纹**（plan §4.1 v3/P1-3）：`scope` = 课名/job id，进指纹会让「同一台机器上的
 *  同一条错误」在 10 门课上变成 10 条 ⇒ 首页汇总条计数虚高、`/errors` 十行同一条错 ——
 *  正是归一化要防的刷屏。跨课折叠后 `scope` 取**最近一次命中**的那一个（人打开详情页看到的
 *  仍是当下那门课）。 */
export function errorFingerprint(source: string, component: string, message: string): string {
  const canon = `${source}|${component}|${normalizeErrorText(message)}`
  return new Bun.CryptoHasher('sha256').update(canon).digest('hex').slice(0, 16)
}
