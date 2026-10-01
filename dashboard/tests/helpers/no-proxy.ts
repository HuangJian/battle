/** no-proxy.ts — 测试预载（`--preload`）：**测试进程里一切出网直连**，绕开开发机的
 *  HTTP(S)_PROXY。
 *
 *  为什么：用例是 hermetic 的——出网只该打到 `Bun.serve` 起的假服务或一个**注定连不上**的
 *  回环端口。但代理环境（dev 机常见）会把这两种都交给代理，而 Bun 只认**精确主机**，
 *  不认 `127.*` 这类通配写法（实测：NO_PROXY 里写着 `127.*` 仍然走代理）：
 *    · 「hub 不可达」类用例从 ~0.01s 的 ECONNREFUSED 变成 1.5–4s 的代理等超时；
 *    · 非回环探测（buildStateView 之类按配置地址探）同样每个文件白搭 ~1.5s。
 *  实测（119 文件 / 1232 用例，同一台机）：用例时长总和 67.5s → 16.5s，
 *  单文件 `cloud-halt.test.ts` 12.0s → 0.06s，全目录墙钟 11.4s → 4.4s。
 *
 *  做法：把 NO_PROXY / no_proxy **两个拼写都**置为 `*`（Bun 在两个都在时读小写那个），
 *  再调生产的 `core/net.shapeLoopbackNoProxy()` 追加精确回环主机——它是 console/launch
 *  启动时用的同一条规则，也是「`*` 万一不被认」时的兜底（只写进程 env，必须在任何
 *  fetch 之前跑；Bun 逐请求读 env）。
 *
 *  边界：只动本测试进程的 env，不碰被测代码、断言与产物；无代理环境（CI）下这两行只是
 *  多设了一个没人读的 NO_PROXY —— 本地行为因此与 CI 一致，而不是反过来。
 *
 *  挂载点：`package.json` 的 test 脚本 `--preload`（门禁命令组成的单一来源在脚本里，
 *  所以不用 bunfig.toml 的 [test] preload）。注意 bun 要求 `./` 前缀的路径，否则报
 *  preload not found。
 */
import { shapeLoopbackNoProxy } from '../../src/core/net'

process.env.NO_PROXY = '*'
process.env.no_proxy = '*'
shapeLoopbackNoProxy()
