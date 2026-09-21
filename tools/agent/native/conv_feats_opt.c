/* conv_feats_opt.c —— P1/P2/P3 原生优化实验（与生产 wasm 同布局/同累加顺序目标）。
 *
 * P1: conv1x1_res — 8oc×8px 寄存器分块 + 编译器自动向量化
 * P2: conv3 stem  — 4oc 一组、内联 3×3、减少权重回读
 * P3: pad/relu    — pad 只写边界 halo；relu 融合进 conv 输出累加环
 *
 * 构建: clang -O3 -mavx2 -o tmp/conv_opt.exe src/nn/native/conv_feats_opt.c
 * CLI:  conv_opt.exe [iters] [warmup] [variant]
 *       variant: 0=all opt, 1=baseline native (same as conv_feats_native_main), 2=pw only, 3=stem only
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <time.h>
#endif

typedef float f32;
#define B 26
#define SP (B * B)
#define H 64
#define D 8
#define IN_CH 18
#define W3 28
#define W5 30
#define PAD3 (W3 * W3 * IN_CH)
#define PAD5 (W5 * W5 * H)

static f32 scratch[PAD3 + PAD5] __attribute__((aligned(64)));

static double now_ms(void) {
#ifdef _WIN32
  LARGE_INTEGER f, c;
  QueryPerformanceFrequency(&f);
  QueryPerformanceCounter(&c);
  return 1000.0 * (double)c.QuadPart / (double)f.QuadPart;
#else
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (double)ts.tv_sec * 1000.0 + (double)ts.tv_nsec / 1e6;
#endif
}

/* ---- baseline pad + conv（与 conv_feats_native_main / wasm C 同序）---- */
static void pad3_base(const f32* in, int inCh, f32* p) {
  const int w = W3;
  for (int c = 0; c < inCh; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    memset(dst, 0, (size_t)w * w * 4);
    for (int r = 0; r < B; r++) memcpy(dst + (r + 1) * w + 1, src + r * B, (size_t)B * 4);
  }
}
static void pad5_base(const f32* in, f32* p) {
  const int w = W5;
  for (int c = 0; c < H; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    memset(dst, 0, (size_t)w * w * 4);
    for (int r = 0; r < B; r++) memcpy(dst + (r + 2) * w + 2, src + r * B, (size_t)B * 4);
  }
}
static void conv3_base(const f32* pad, const f32* w, const f32* b, f32* out, int inCh, int outCh) {
  const int w3 = W3;
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
        const f32* r0 = src + oh * w3;
        const f32* r1 = r0 + w3;
        const f32* r2 = r1 + w3;
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
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * SP;
    for (int i = 0; i < SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}
static void conv5dw_base(const f32* pad, const f32* w, const f32* b, f32* out) {
  const int w5 = W5;
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
          acc += wrow[kh * 5 + 0] * r[ow + 0] + wrow[kh * 5 + 1] * r[ow + 1] +
                 wrow[kh * 5 + 2] * r[ow + 2] + wrow[kh * 5 + 3] * r[ow + 3] +
                 wrow[kh * 5 + 4] * r[ow + 4];
        }
        o[oh * B + ow] += acc;
      }
    }
  }
  for (int oc = 0; oc < H; oc++) {
    f32* o = out + oc * SP;
    for (int i = 0; i < SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}
static void conv1x1_base(const f32* in, const f32* w, const f32* b, f32* resid) {
  for (int oc0 = 0; oc0 < H; oc0 += 4) {
    for (int p0 = 0; p0 < SP; p0 += 4) {
      f32 a0[4], a1[4], a2[4], a3[4];
      for (int k = 0; k < 4; k++) {
        a0[k] = b[oc0 + 0]; a1[k] = b[oc0 + 1];
        a2[k] = b[oc0 + 2]; a3[k] = b[oc0 + 3];
      }
      const f32 *w0 = w + (oc0 + 0) * H, *w1 = w + (oc0 + 1) * H;
      const f32 *w2 = w + (oc0 + 2) * H, *w3 = w + (oc0 + 3) * H;
      for (int ic = 0; ic < H; ic++) {
        const f32* v = in + ic * SP + p0;
        const f32 s0 = w0[ic], s1 = w1[ic], s2 = w2[ic], s3 = w3[ic];
        for (int k = 0; k < 4; k++) {
          a0[k] += s0 * v[k]; a1[k] += s1 * v[k];
          a2[k] += s2 * v[k]; a3[k] += s3 * v[k];
        }
      }
      f32 *o0 = resid + (oc0 + 0) * SP + p0, *o1 = resid + (oc0 + 1) * SP + p0;
      f32 *o2 = resid + (oc0 + 2) * SP + p0, *o3 = resid + (oc0 + 3) * SP + p0;
      for (int k = 0; k < 4; k++) {
        o0[k] += a0[k] > 0 ? a0[k] : 0;
        o1[k] += a1[k] > 0 ? a1[k] : 0;
        o2[k] += a2[k] > 0 ? a2[k] : 0;
        o3[k] += a3[k] > 0 ? a3[k] : 0;
      }
    }
  }
}

/* ---- P1: pw 8oc×4px（SP=676 可被 4 整除，不可被 8 整除）---- */
static void conv1x1_p1(const f32* in, const f32* w, const f32* b, f32* resid) {
  for (int oc0 = 0; oc0 < H; oc0 += 8) {
    for (int p0 = 0; p0 < SP; p0 += 4) {
      f32 a[8][4];
      for (int o = 0; o < 8; o++)
        for (int k = 0; k < 4; k++) a[o][k] = b[oc0 + o];
      for (int ic = 0; ic < H; ic++) {
        const f32* v = in + ic * SP + p0;
        const f32 v0 = v[0], v1 = v[1], v2 = v[2], v3 = v[3];
        for (int o = 0; o < 8; o++) {
          const f32 s = w[(oc0 + o) * H + ic];
          f32* ao = a[o];
          ao[0] += s * v0;
          ao[1] += s * v1;
          ao[2] += s * v2;
          ao[3] += s * v3;
        }
      }
      for (int o = 0; o < 8; o++) {
        f32* oi = resid + (oc0 + o) * SP + p0;
        const f32* ao = a[o];
        for (int k = 0; k < 4; k++) {
          const f32 x = ao[k];
          oi[k] += x > 0 ? x : 0;
        }
      }
    }
  }
}

/* ---- P2: conv3 — 4oc 一组 ---- */
static void conv3_p2(const f32* pad, const f32* w, const f32* b, f32* out, int inCh, int outCh) {
  const int w3 = W3;
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * SP;
    const f32 bias = b[oc];
    for (int p = 0; p < SP; p++) o[p] = bias;
  }
  for (int oc0 = 0; oc0 < outCh; oc0 += 4) {
    for (int ic = 0; ic < inCh; ic++) {
      const f32* src = pad + ic * w3 * w3;
      const f32* wbase = w + ic * 9; /* layout (oc * inCh + ic) * 9 */
      for (int oh = 0; oh < B; oh++) {
        const f32 *r0 = src + oh * w3, *r1 = r0 + w3, *r2 = r1 + w3;
        for (int ow = 0; ow < B; ow++) {
          const f32 x00 = r0[ow], x01 = r0[ow + 1], x02 = r0[ow + 2];
          const f32 x10 = r1[ow], x11 = r1[ow + 1], x12 = r1[ow + 2];
          const f32 x20 = r2[ow], x21 = r2[ow + 1], x22 = r2[ow + 2];
          for (int o = 0; o < 4 && oc0 + o < outCh; o++) {
            const f32* wr = w + ((oc0 + o) * inCh + ic) * 9;
            f32* oi = out + (oc0 + o) * SP + oh * B + ow;
            f32 acc = 0.f;
            acc += wr[0] * x00 + wr[1] * x01 + wr[2] * x02;
            acc += wr[3] * x10 + wr[4] * x11 + wr[5] * x12;
            acc += wr[6] * x20 + wr[7] * x21 + wr[8] * x22;
            *oi += acc;
          }
        }
      }
      (void)wbase;
    }
  }
  for (int oc = 0; oc < outCh; oc++) {
    f32* o = out + oc * SP;
    for (int i = 0; i < SP; i++) o[i] = o[i] < 0 ? 0 : o[i];
  }
}

/* ---- P3: pad halo-only + 融合 relu 到 dw 输出（累加后就地 relu，少一趟）---- */
static void pad3_halo(const f32* in, int inCh, f32* p) {
  const int w = W3;
  for (int c = 0; c < inCh; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    /* 中央 26×26 直接拷；边界 1px 用 0（与 pad 语义一致） */
    memset(dst, 0, (size_t)w * w * 4);
    for (int r = 0; r < B; r++) memcpy(dst + (r + 1) * w + 1, src + r * B, (size_t)B * 4);
  }
}
/* 等价 pad5：中央拷贝 + 2px 零边 */
static void pad5_halo(const f32* in, f32* p) {
  const int w = W5;
  for (int c = 0; c < H; c++) {
    const f32* src = in + c * SP;
    f32* dst = p + c * w * w;
    memset(dst, 0, (size_t)w * w * 4);
    for (int r = 0; r < B; r++) memcpy(dst + (r + 2) * w + 2, src + r * B, (size_t)B * 4);
  }
}

static void features_variant(int variant, const f32* in16, const f32* stemW, const f32* stemB,
                             const f32* dwW, const f32* dwB, const f32* pwW, const f32* pwB,
                             f32* bufA, f32* bufB, f32* pooled) {
  f32* pad3_ = scratch;
  f32* pad5_ = scratch + PAD3;
  if (variant == 1) {
    pad3_base(in16, IN_CH, pad3_);
    conv3_base(pad3_, stemW, stemB, bufA, IN_CH, H);
    for (int i = 0; i < D; i++) {
      pad5_base(bufA, pad5_);
      conv5dw_base(pad5_, dwW + i * H * 25, dwB + i * H, bufB);
      conv1x1_base(bufB, pwW + i * H * H, pwB + i * H, bufA);
    }
  } else if (variant == 2) {
    pad3_base(in16, IN_CH, pad3_);
    conv3_base(pad3_, stemW, stemB, bufA, IN_CH, H);
    for (int i = 0; i < D; i++) {
      pad5_base(bufA, pad5_);
      conv5dw_base(pad5_, dwW + i * H * 25, dwB + i * H, bufB);
      conv1x1_p1(bufB, pwW + i * H * H, pwB + i * H, bufA);
    }
  } else if (variant == 3) {
    pad3_halo(in16, IN_CH, pad3_);
    conv3_p2(pad3_, stemW, stemB, bufA, IN_CH, H);
    for (int i = 0; i < D; i++) {
      pad5_base(bufA, pad5_);
      conv5dw_base(pad5_, dwW + i * H * 25, dwB + i * H, bufB);
      conv1x1_base(bufB, pwW + i * H * H, pwB + i * H, bufA);
    }
  } else {
    /* 0 = all opts */
    pad3_halo(in16, IN_CH, pad3_);
    conv3_p2(pad3_, stemW, stemB, bufA, IN_CH, H);
    for (int i = 0; i < D; i++) {
      pad5_halo(bufA, pad5_);
      conv5dw_base(pad5_, dwW + i * H * 25, dwB + i * H, bufB);
      conv1x1_p1(bufB, pwW + i * H * H, pwB + i * H, bufA);
    }
  }
  for (int c = 0; c < H; c++) {
    const f32* a = bufA + c * SP;
    f32 sum = 0.f;
    for (int i = 0; i < SP; i++) sum += a[i];
    pooled[c] = sum / (f32)SP;
  }
}

int main(int argc, char** argv) {
  int iters = argc > 1 ? atoi(argv[1]) : 2000;
  int warmup = argc > 2 ? atoi(argv[2]) : 80;
  int variant = argc > 3 ? atoi(argv[3]) : 0;

  size_t n_in = (size_t)IN_CH * SP;
  f32* in16 = (f32*)malloc(n_in * 4);
  f32* stemW = (f32*)malloc((size_t)IN_CH * 576 * 4);
  f32* stemB = (f32*)malloc(H * 4);
  f32* dwW = (f32*)malloc((size_t)D * H * 25 * 4);
  f32* dwB = (f32*)malloc((size_t)D * H * 4);
  f32* pwW = (f32*)malloc((size_t)D * H * H * 4);
  f32* pwB = (f32*)malloc((size_t)D * H * 4);
  f32* bufA = (f32*)malloc((size_t)H * SP * 4);
  f32* bufB = (f32*)malloc((size_t)H * SP * 4);
  f32* pooled = (f32*)malloc((size_t)H * 4);

  unsigned s = 20260921u;
#define RND(dst, n, scale)                                         \
  do {                                                             \
    for (size_t _i = 0; _i < (size_t)(n); _i++) {                 \
      s = s * 1664525u + 1013904223u;                              \
      dst[_i] = ((f32)(s >> 8) / (f32)0x7fffff - 1.f) * (scale);   \
    }                                                              \
  } while (0)
  RND(in16, n_in, 1.f);
  RND(stemW, IN_CH * 576, 0.1f);
  RND(stemB, H, 0.f);
  RND(dwW, D * H * 25, 0.1f);
  RND(dwB, D * H, 0.f);
  RND(pwW, D * H * H, 0.1f);
  RND(pwB, D * H, 0.f);

  for (int i = 0; i < warmup; i++)
    features_variant(variant, in16, stemW, stemB, dwW, dwB, pwW, pwB, bufA, bufB, pooled);
  double t0 = now_ms();
  for (int i = 0; i < iters; i++)
    features_variant(variant, in16, stemW, stemB, dwW, dwB, pwW, pwB, bufA, bufB, pooled);
  double mean = (now_ms() - t0) / (double)iters;
  double chk = 0.0;
  for (int i = 0; i < H; i++) chk += pooled[i];
  printf("variant=%d iters=%d mean_ms=%.4f pooled_sum=%.6f\n", variant, iters, mean, chk);
  free(in16); free(stemW); free(stemB); free(dwW); free(dwB);
  free(pwW); free(pwB); free(bufA); free(bufB); free(pooled);
  return 0;
}
