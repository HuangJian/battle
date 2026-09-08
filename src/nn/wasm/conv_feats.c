/* conv_feats.c —— PPOStudent features 前向的 wasm32 SIMD 实现（S5 提速 ①，§368 提速②）。
 * 用 clang --target=wasm32 -O3 -msimd128 编译；无 libc：纯函数 + 静态对齐缓冲，
 * memory 由 JS 提供大区（权重/输入/输出/scratch）。
 * 布局与 infer.ts StudentModel 逐点一致（board=26；stem conv3x3 16->h relu；
 * d=8 × [depthwise 5x5 + pointwise 1x1 + relu + residual]；GAP -> pooled[h]）。
 *
 * §368（2026-09-08）：pointwise 1x1 由「外积排布等 LLVM 自动向量化」改为**手写
 * wasm_simd128 intrinsics**（4 输出通道 × 4 像素寄存器块 + relu/residual 融合写回）。
 * 实测 features：bun 8.10→5.46ms（1.48×）、node 4.59→3.43ms（1.35×），且**逐位相同**
 * （累加顺序未变：从 bias 起累加，relu 后再加 residual），单局产物逐字节一致。
 * stem / depthwise 保持自动向量化版本——实测把这两段也 intrinsics 化反而更慢
 * （tmp/split/conv_feats_v3.c：1.42×/1.27×，且不再逐位相同）。
 */
#include <wasm_simd128.h>
typedef float f32;
#define B 26
#define SP (B * B)
#define H 64
#define D 8
#define PAD3 (28 * 28 * 16)     /* stem pad: 16ch x 28x28 */
#define PAD5 (30 * 30 * H)      /* dw pad: 64ch x 30x30 */
static f32 __attribute__((aligned(64))) scratch[PAD3 + PAD5];

static inline void pad3(const f32* in, int inCh, f32* p) {
  const int w = B + 2;
  for (int c = 0; c < inCh; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    for (int i = 0; i < w * w; i++) dst[i] = 0.f;
    for (int r = 0; r < B; r++) {
      f32* drow = dst + (r + 1) * w + 1;
      for (int i = 0; i < B; i++) drow[i] = src[r * B + i];
    }
  }
}
static inline void pad5(const f32* in, f32* p) {
  const int w = B + 4;
  for (int c = 0; c < H; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    for (int i = 0; i < w * w; i++) dst[i] = 0.f;
    for (int r = 0; r < B; r++) {
      f32* drow = dst + (r + 2) * w + 2;
      for (int i = 0; i < B; i++) drow[i] = src[r * B + i];
    }
  }
}
static inline void relu_(f32* a, int n) { for (int i = 0; i < n; i++) if (a[i] < 0) a[i] = 0; }

/* conv3x3: out[oc] = relu(b[oc] + sum_{ic,kh,kw} w * pad3) —— 外积排布向量化。 */
static void conv3(const f32* pad, const f32* w, const f32* b, f32* out, int inCh, int outCh) {
  const int w3 = B + 2; /* 28 */
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * SP;
    const f32 bias = b[oc];
    for (int p = 0; p < SP; p++) o[p] = bias;
  }
  for (int ic = 0; ic < inCh; ic++) {
    const f32* src = pad + ic * w3 * w3;
    for (int oc = 0; oc < outCh; oc++) {
      const f32* wrow = w + (oc * inCh + ic) * 9;
      f32* o = out + oc * SP;
      for (int oh = 0; oh < B; oh++) {
        const f32* r0 = src + oh * w3; /* kh=0 -> row oh */
        const f32* r1 = r0 + w3;       /* kh=1 -> row oh+1 */
        const f32* r2 = r1 + w3;       /* kh=2 -> row oh+2 */
        for (int ow = 0; ow < B; ow++) {
          f32 acc = 0.f;
          acc += wrow[0] * r0[ow] + wrow[1] * r0[ow + 1] + wrow[2] * r0[ow + 2];
          acc += wrow[3] * r1[ow] + wrow[4] * r1[ow + 1] + wrow[5] * r1[ow + 2];
          acc += wrow[6] * r2[ow] + wrow[7] * r2[ow + 1] + wrow[8] * r2[ow + 2];
          o[oh * B + ow] += acc;
        }
      }
    }
  }
  for (int oc = 0; oc < outCh; oc++) relu_(out + oc * SP, SP);
}

/* depthwise 5x5 (h=h): out[oc] = relu(b + sum kh,kw w[oc] * pad5[oc]) */
static void conv5dw(const f32* pad, const f32* w, const f32* b, f32* out) {
  const int w5 = B + 4; /* 30 */
  for (int oc = 0; oc < H; oc++) {
    f32* o = out + oc * SP;
    const f32 bias = b[oc];
    for (int p = 0; p < SP; p++) o[p] = bias;
    const f32* src = pad + oc * w5 * w5;
    const f32* wrow = w + oc * 25;
    for (int oh = 0; oh < B; oh++) {
      for (int ow = 0; ow < B; ow++) {
        f32 acc = 0.f;
        for (int kh = 0; kh < 5; kh++) {
          const f32* r = src + (oh + kh) * w5;
          acc += wrow[kh * 5 + 0] * r[ow + 0] + wrow[kh * 5 + 1] * r[ow + 1]
               + wrow[kh * 5 + 2] * r[ow + 2] + wrow[kh * 5 + 3] * r[ow + 3]
               + wrow[kh * 5 + 4] * r[ow + 4];
        }
        o[oh * B + ow] += acc;
      }
    }
  }
  for (int oc = 0; oc < H; oc++) relu_(out + oc * SP, SP);
}

/* pointwise 1x1 + relu + residual 融合（**手写 wasm_simd128 intrinsics**）。
 * 4 个输出通道 × 4 个像素 寄存器分块：acc[4] 各持 f32x4，内层只做
 * splat(w) * v128_load(in) + acc —— 与旧实现同一累加顺序 ⇒ **逐位相同**。 */
#ifndef RLAX
#define MACC(acc, s, v) wasm_f32x4_add(acc, wasm_f32x4_mul(wasm_f32x4_splat(s), v))
#else
#define MACC(acc, s, v) wasm_f32x4_relaxed_madd(wasm_f32x4_splat(s), v, acc)
#endif
static void conv1x1_res(const f32* in, const f32* w, const f32* b, f32* resid) {
  const v128_t zero = wasm_f32x4_const(0.f, 0.f, 0.f, 0.f);
  for (int oc0 = 0; oc0 < H; oc0 += 4) {
    for (int p0 = 0; p0 < SP; p0 += 4) {
      v128_t a0 = wasm_f32x4_splat(b[oc0 + 0]);
      v128_t a1 = wasm_f32x4_splat(b[oc0 + 1]);
      v128_t a2 = wasm_f32x4_splat(b[oc0 + 2]);
      v128_t a3 = wasm_f32x4_splat(b[oc0 + 3]);
      const f32* w0 = w + (oc0 + 0) * H;
      const f32* w1 = w + (oc0 + 1) * H;
      const f32* w2 = w + (oc0 + 2) * H;
      const f32* w3 = w + (oc0 + 3) * H;
      for (int ic = 0; ic < H; ic++) {
        const v128_t v = wasm_v128_load(in + ic * SP + p0);
        const f32 s0 = w0[ic], s1 = w1[ic], s2 = w2[ic], s3 = w3[ic];
        a0 = MACC(a0, s0, v);
        a1 = MACC(a1, s1, v);
        a2 = MACC(a2, s2, v);
        a3 = MACC(a3, s3, v);
      }
      f32* o0 = resid + (oc0 + 0) * SP + p0;
      f32* o1 = resid + (oc0 + 1) * SP + p0;
      f32* o2 = resid + (oc0 + 2) * SP + p0;
      f32* o3 = resid + (oc0 + 3) * SP + p0;
      wasm_v128_store(o0, wasm_f32x4_add(wasm_f32x4_max(a0, zero), wasm_v128_load(o0)));
      wasm_v128_store(o1, wasm_f32x4_add(wasm_f32x4_max(a1, zero), wasm_v128_load(o1)));
      wasm_v128_store(o2, wasm_f32x4_add(wasm_f32x4_max(a2, zero), wasm_v128_load(o2)));
      wasm_v128_store(o3, wasm_f32x4_add(wasm_f32x4_max(a3, zero), wasm_v128_load(o3)));
    }
  }
}

/* 全前向：in16(16ch) + stemW/stemB + dwW/dwB/pwW/pwB + pooled(64) 输出。
 * scratch 自动用文件静态区；权重/输入/输出指针均为 wasm 线性内存偏移。 */
__attribute__((export_name("features")))
void features(const f32* in16, const f32* stemW, const f32* stemB,
              const f32* dwW, const f32* dwB, const f32* pwW, const f32* pwB,
              f32* bufA, f32* bufB, f32* bufC, f32* pooled) {
  f32* pad3_ = scratch;
  f32* pad5_ = scratch + PAD3;
  pad3(in16, 16, pad3_);
  conv3(pad3_, stemW, stemB, bufA, 16, H);
  for (int i = 0; i < D; i++) {
    pad5(bufA, pad5_);
    conv5dw(pad5_, dwW + i * H * 25, dwB + i * H, bufB);
    conv1x1_res(bufB, pwW + i * H * H, pwB + i * H, bufA);
  }
  for (int c = 0; c < H; c++) {
    const f32* a = bufA + c * SP;
    f32 sum = 0.f;
    for (int i = 0; i < SP; i++) sum += a[i];
    pooled[c] = sum / (f32)SP;
  }
}
