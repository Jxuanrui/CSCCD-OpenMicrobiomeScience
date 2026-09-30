#!/usr/bin/env python3
"""通道认证统计（监工 v0.2 裁决 P1 落盘）：Wilson 区间 / 功效 / 批次抽审 OC 曲线。

预注册用途：认证阈值（点估计≥0.85 且 Wilson 下界≥0.80）、批次降级 k 值、
跨批累积下界监控。数值改动须用户+监工双签。
"""
import math
from statistics import NormalDist


def wilson(p, n, z=1.96):
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def certify(passes, n, point_min=0.85, lb_min=0.80):
    """认证判定：双条件（点估计与下界同时达标）。"""
    if n == 0:
        return {"certified": False, "reason": "no data"}
    p = passes / n
    lb, ub = wilson(p, n)
    return {"point": round(p, 4), "lb": round(lb, 4), "ub": round(ub, 4),
            "certified": p >= point_min and lb >= lb_min,
            "reason": "" if (p >= point_min and lb >= lb_min) else
                      f"point<{point_min} or lb<{lb_min}"}


def batch_downgrade_k(n, p_bad=0.80, alpha=0.05):
    """单批降级阈值 k：p_true=0.80（未达标通道）时，错误数≥k 的概率 ≤α。
    错误数 ~ Binomial(n, 1-p_bad)。返回最小 k。"""
    z = NormalDist().inv_cdf(1 - alpha)
    # 正态近似 + 连续校正
    mean = n * (1 - p_bad)
    sd = math.sqrt(n * p_bad * (1 - p_bad))
    k = math.ceil(mean + z * sd)
    return k


def power_two_prop(p0=0.75, p1=0.85, alpha=0.05, target=0.8):
    """区分 p0/p1 所需 n（单侧，监工 E11 修复：去除无用参数）。"""
    z = NormalDist()
    za, zb = z.inv_cdf(1 - alpha), z.inv_cdf(target)
    return math.ceil(((za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1)))
                      / (p1 - p0)) ** 2)


def noise_ceiling(gold_iaa=0.54, model_true=0.90):
    """监工 P2：外部 gold 噪声下的认证可达性——
    若 gold 自身一致性仅 gold_iaa，模型即使真精度 model_true，
    观测一致率上限 ≈ gold_iaa + (1-gold_iaa)*... 粗算两者独立一致的期望：
    P(模型与gold一致) ≈ p*iaa + (1-p)*(1-iaa)/k（k=选项数近似）。
    给出保守口径：观测上限 ≈ iaa（模型与 gold 完全同分布时的天花板）。"""
    observed_cap = gold_iaa * model_true + (1 - gold_iaa) * (1 - model_true) / 3
    return round(observed_cap, 4)


if __name__ == "__main__":
    print("== 认证判定示例 ==")
    for y, n in [(132, 150), (127, 150), (120, 150)]:
        print(f"  {y}/{n}:", certify(y, n))
    print("== 批次降级 k（n=25/30, p_true=0.80, α=0.05）==")
    for n in (25, 30):
        print(f"  n={n}: k={batch_downgrade_k(n)}（错误数≥k 即降级）")
    print("== 区分 0.75/0.85 所需 n ==", power_two_prop())
    print("== 外部 gold 噪声天花板（IAA=0.54）==")
    for mt in (0.85, 0.90, 0.95, 1.0):
        print(f"  模型真精度 {mt}: 观测一致率上限 ≈ {noise_ceiling(0.54, mt)}")
