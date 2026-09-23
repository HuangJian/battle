/* conv_cli.c —— native features CLI（字节对拍 + 离线基准的参考可执行文件）。
 *
 * 算法**不在这里**：本文件只做 stdin/stdout 编解码，计算全部来自
 * `conv.c`（与生产共享库同源）。T0 的「native 与 wasm 逐位一致」由
 * tests/native-parity.test.ts 用共享库断言；本 CLI 保留是因为它是最容易在任意
 * 机器上复现的独立参考（无需 JS/FFI）。
 *
 * 二进制协议（小端）:
 *   magic u32 = 0x434F4E56 ('CONV')
 *   in16  f32[IN_CH*SP]  IN_CH=18 SP=676
 *   wblob f32[CF_BLOB_FLOATS]（顺序见 conv_native.h：stemW/stemB/dwW/dwB/pwW/pwB）
 * 输出 stdout:
 *   pooled f32[64]
 *   bufA   f32[64*SP]
 *
 * 构建: bun tools/agent/native-build.ts（钉死的 flags；禁 -march=native）
 */
#include <stdio.h>
#include <stdlib.h>

#include "conv_native.h"

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#define MAGIC 0x434F4E56u

static int read_all(void* p, size_t n) { return fread(p, 1, n, stdin) == n; }

int main(void) {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  unsigned magic = 0;
  if (!read_all(&magic, 4) || magic != MAGIC) {
    fprintf(stderr, "bad magic\n");
    return 2;
  }
  const size_t n_in = (size_t)CF_IN_CH * CF_SP;
  const size_t n_buf = (size_t)CF_H * CF_SP;
  float* in16 = (float*)malloc(n_in * 4);
  float* wblob = (float*)malloc((size_t)CF_BLOB_FLOATS * 4);
  float* pooled = (float*)malloc(CF_H * 4);
  float* bufA = (float*)malloc(n_buf * 4);
  if (!in16 || !wblob || !pooled || !bufA) {
    fprintf(stderr, "oom\n");
    return 4;
  }
  if (!read_all(in16, n_in * 4) || !read_all(wblob, (size_t)CF_BLOB_FLOATS * 4)) {
    fprintf(stderr, "short stdin\n");
    return 3;
  }
  if (cf_student_features(wblob, in16, pooled, bufA) != 0) {
    fprintf(stderr, "kernel failed\n");
    return 5;
  }
  fwrite(pooled, 4, CF_H, stdout);
  fwrite(bufA, 4, n_buf, stdout);
  fflush(stdout);
  return 0;
}
