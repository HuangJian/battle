/* conv_feats_native.c —— native features 内核（**唯一**实现；共享库与 CLI 都链接它）。
 *
 * 与 `src/nn/wasm/conv_feats.c` 的关系：同累加顺序的**独立实现**（那边是 wasm_simd128
 * intrinsics，是产品字节的定义者，不能动）。两者一致性由 tests/native-parity.test.ts
 * 逐字节断言，而不是靠"看起来一样"。
 *
 * 构建（钉死 flags，禁 -march=native —— FMA 收缩会破坏跨节点逐位一致）：
 *   bun tools/agent/native-build.ts        # 共享库 + CLI + native-build.json 指纹
 */
#include "conv_feats_native.h"

typedef float f32;

/* ---------------- 免 libc（freestanding）：本内核零外部符号 ----------------
 *
 * 为什么内核不许碰 libc（2026-09-21 交叉编译 prebuilt 库时需要）：节点机器上
 * 未必有 clang，于是共享库要**本机交叉编译**后随仓库分发（tools/agent/native-build.ts
 * --cross）。而交叉编译时手上只有 Windows 的 MSVC 头/库、没有 Linux/macOS 的
 * sysroot —— 一旦 include <string.h> 或引用 memset/memcpy，链接期就没有可用的
 * 实现（`-nostdlib`）。padding 的零填与整行拷贝是内核唯一的两个 libc 用途，本地实现
 * 掉即可换来「零未定义符号」的产物：
 *   · ELF/Darwin 由链接期 `--no-undefined` 强制（有 libcall 直接链接失败，不静默）；
 *   · 因此这些产物**不带 DT_NEEDED/LC_LOAD_DYLIB**，glibc/musl/bionic/libSystem
 *     都能 dlopen —— Android/Termux（bionic）也在覆盖内。
 *
 * 语义与 memset/memcpy 逐字节等价（float 按值赋值，无类型双关），故不影响
 * 「native 与 wasm 逐位一致」这一契约。
 */
#if defined(_WIN32) && defined(CF_FREESTANDING)
/* clang-cl/MSVC x64：**任何**浮点使用都会让编译器引用 `_fltused`（CRT 靠它决定是否
 * 加载浮点支持）。免 CRT 的 DLL 必须自带这个符号，否则 lld-link 报
 * `undefined symbol: _fltused`（arm64 不需要——该引用是 x64 代码生成的产物）。 */
int _fltused = 0x9875;
#endif

static void cf_zero(f32* p, int n) {
  for (int i = 0; i < n; i++) p[i] = 0.f;
}

static void cf_copy(f32* dst, const f32* src, int n) {
  for (int i = 0; i < n; i++) dst[i] = src[i];
}

#define W3 (CF_BOARD + 2) /* 28 */
#define W5 (CF_BOARD + 4) /* 30 */
#define PAD3 (W3 * W3 * CF_IN_CH)
#define PAD5 (W5 * W5 * CF_H)

static f32 scratch[PAD3 + PAD5];
/* bufB 必须与 pad5 分开：conv5dw 每通道「先读满 pad5 的该通道、再写整张 bufB」，
 * 与 pad5 重叠会把尚未读到的通道冲掉（CLI 版用的是独立 malloc）。 */
static f32 bufB_store[CF_H * CF_SP];

static void pad3(const f32* in, int inCh, f32* p) {
  const int w = W3;
  for (int c = 0; c < inCh; c++) {
    const f32* src = in + c * CF_SP;
    f32* dst = p + c * w * w;
    cf_zero(dst, w * w);
    for (int r = 0; r < CF_BOARD; r++) cf_copy(dst + (r + 1) * w + 1, src + r * CF_BOARD, CF_BOARD);
  }
}

static void pad5(const f32* in, f32* p) {
  const int w = W5;
  for (int c = 0; c < CF_H; c++) {
    const f32* src = in + c * CF_SP;
    f32* dst = p + c * w * w;
    cf_zero(dst, w * w);
    for (int r = 0; r < CF_BOARD; r++) cf_copy(dst + (r + 2) * w + 2, src + r * CF_BOARD, CF_BOARD);
  }
}

static void conv3(const f32* pad, const f32* w, const f32* b, f32* out, int inCh, int outCh) {
  const int w3 = W3;
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * CF_SP;
    const f32 bias = b[oc];
    for (int p = 0; p < CF_SP; p++) o[p] = bias;
  }
  for (int ic = 0; ic < inCh; ic++) {
    const f32* src = pad + ic * w3 * w3;
    for (int oc = 0; oc < outCh; oc++) {
      const f32* wrow = w + (oc * inCh + ic) * 9;
      f32* o = out + oc * CF_SP;
      for (int oh = 0; oh < CF_BOARD; oh++) {
        const f32 *r0 = src + oh * w3, *r1 = r0 + w3, *r2 = r1 + w3;
        for (int ow = 0; ow < CF_BOARD; ow++) {
          f32 acc = 0.f;
          acc += wrow[0] * r0[ow] + wrow[1] * r0[ow + 1] + wrow[2] * r0[ow + 2];
          acc += wrow[3] * r1[ow] + wrow[4] * r1[ow + 1] + wrow[5] * r1[ow + 2];
          acc += wrow[6] * r2[ow] + wrow[7] * r2[ow + 1] + wrow[8] * r2[ow + 2];
          o[oh * CF_BOARD + ow] += acc;
        }
      }
    }
  }
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * CF_SP;
    for (int i = 0; i < CF_SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}

static void conv5dw(const f32* pad, const f32* w, const f32* b, f32* out) {
  const int w5 = W5;
  for (int oc = 0; oc < CF_H; oc++) {
    f32* o = out + oc * CF_SP;
    const f32 bias = b[oc];
    for (int p = 0; p < CF_SP; p++) o[p] = bias;
    const f32* src = pad + oc * w5 * w5;
    const f32* wrow = w + oc * 25;
    for (int oh = 0; oh < CF_BOARD; oh++) {
      for (int ow = 0; ow < CF_BOARD; ow++) {
        f32 acc = 0.f;
        for (int kh = 0; kh < 5; kh++) {
          const f32* r = src + (oh + kh) * w5;
          acc += wrow[kh * 5 + 0] * r[ow + 0] + wrow[kh * 5 + 1] * r[ow + 1] +
                 wrow[kh * 5 + 2] * r[ow + 2] + wrow[kh * 5 + 3] * r[ow + 3] +
                 wrow[kh * 5 + 4] * r[ow + 4];
        }
        o[oh * CF_BOARD + ow] += acc;
      }
    }
  }
  for (int oc = 0; oc < CF_H; oc++) {
    f32* o = out + oc * CF_SP;
    for (int i = 0; i < CF_SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}

/* 与 wasm conv1x1_res 同累加序：4oc×4px，bias 起按 ic，relu 后 +residual。 */
static void conv1x1_res(const f32* in, const f32* w, const f32* b, f32* resid) {
  for (int oc0 = 0; oc0 < CF_H; oc0 += 4) {
    for (int p0 = 0; p0 < CF_SP; p0 += 4) {
      f32 a0[4], a1[4], a2[4], a3[4];
      for (int k = 0; k < 4; k++) {
        a0[k] = b[oc0 + 0];
        a1[k] = b[oc0 + 1];
        a2[k] = b[oc0 + 2];
        a3[k] = b[oc0 + 3];
      }
      const f32 *w0 = w + oc0 * CF_H, *w1 = w + (oc0 + 1) * CF_H;
      const f32 *w2 = w + (oc0 + 2) * CF_H, *w3 = w + (oc0 + 3) * CF_H;
      for (int ic = 0; ic < CF_H; ic++) {
        const f32* v = in + ic * CF_SP + p0;
        const f32 s0 = w0[ic], s1 = w1[ic], s2 = w2[ic], s3 = w3[ic];
        for (int k = 0; k < 4; k++) {
          a0[k] += s0 * v[k];
          a1[k] += s1 * v[k];
          a2[k] += s2 * v[k];
          a3[k] += s3 * v[k];
        }
      }
      f32 *o0 = resid + oc0 * CF_SP + p0, *o1 = resid + (oc0 + 1) * CF_SP + p0;
      f32 *o2 = resid + (oc0 + 2) * CF_SP + p0, *o3 = resid + (oc0 + 3) * CF_SP + p0;
      for (int k = 0; k < 4; k++) {
        o0[k] += a0[k] > 0 ? a0[k] : 0;
        o1[k] += a1[k] > 0 ? a1[k] : 0;
        o2[k] += a2[k] > 0 ? a2[k] : 0;
        o3[k] += a3[k] > 0 ? a3[k] : 0;
      }
    }
  }
}

unsigned cf_abi(void) { return (unsigned)CF_ABI; }

int cf_student_features(const float* wblob, const float* in16, float* pooled, float* bufA) {
  if (!wblob || !in16 || !pooled || !bufA) return 1;
  const f32* stemW = wblob;
  const f32* stemB = stemW + CF_STEM_W;
  const f32* dwW = stemB + CF_H;
  const f32* dwB = dwW + CF_DW_W;
  const f32* pwW = dwB + CF_D * CF_H;
  const f32* pwB = pwW + CF_PW_W;

  f32* pad3_ = scratch;
  f32* pad5_ = scratch + PAD3;
  f32* bufB = bufB_store;

  pad3(in16, CF_IN_CH, pad3_);
  conv3(pad3_, stemW, stemB, bufA, CF_IN_CH, CF_H);
  for (int i = 0; i < CF_D; i++) {
    pad5(bufA, pad5_);
    conv5dw(pad5_, dwW + i * CF_H * 25, dwB + i * CF_H, bufB);
    conv1x1_res(bufB, pwW + i * CF_H * CF_H, pwB + i * CF_H, bufA);
  }
  for (int c = 0; c < CF_H; c++) {
    const f32* a = bufA + c * CF_SP;
    f32 sum = 0.f;
    for (int i = 0; i < CF_SP; i++) sum += a[i];
    pooled[c] = sum / (f32)CF_SP;
  }
  return 0;
}
