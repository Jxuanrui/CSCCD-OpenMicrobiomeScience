"""R 统计沙箱：经 microbiome_R 环境执行预注册分析（偏 Spearman + BH 校正）。

只接受结构化参数（表/列引用），不接受自由 R 代码——MRA PEP"命令白名单"精神在
研究循环里的 MVP 落法。方法预注册：秩残差化（rank ~ 协变量 lm 残差）后
Spearman 关联，BH 多重校正；确定性可复现（同输入同输出）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pandas as pd

RSCRIPT = Path(os.environ.get("RSCRIPT_BIN", ""))  # 部署机 R 沙箱路径经环境变量注入

_R_SCRIPT = r"""
args <- commandArgs(trailingOnly = TRUE)
exp_tsv <- args[1]; feat_tsv <- args[2]; cov_tsv <- args[3]; out_tsv <- args[4]
E <- read.delim(exp_tsv, row.names = 1, check.names = FALSE)
F <- read.delim(feat_tsv, row.names = 1, check.names = FALSE)
C <- read.delim(cov_tsv, row.names = 1, check.names = FALSE)
ids <- Reduce(intersect, list(rownames(E), rownames(F), rownames(C)))
E <- E[ids, , drop = FALSE]; F <- F[ids, , drop = FALSE]; C <- C[ids, , drop = FALSE]
cov_rank <- function(x) {
  r <- rank(x, ties.method = "average")
  resid(lm(r ~ ., data = C))[seq_along(r)]
}
e <- cov_rank(E[[colnames(E)[1]]])
n <- length(ids)
res <- t(sapply(colnames(F), function(g) {
  rho <- suppressWarnings(cor(e, cov_rank(F[[g]]), method = "spearman"))
  p <- suppressWarnings(cor.test(e, cov_rank(F[[g]]), method = "spearman",
                                  exact = FALSE)$p.value)
  c(rho = unname(rho), p = unname(p))
}))
res <- as.data.frame(res)
res$q <- p.adjust(res$p, method = "BH")
res$feature <- rownames(res)
res$n <- n
res <- res[, c("feature", "rho", "p", "q", "n")]
write.table(res, out_tsv, sep = "\t", quote = FALSE, row.names = FALSE)
"""


def run_partial_spearman(
    exposure: pd.Series,
    features: pd.DataFrame,
    covariates: pd.DataFrame,
    timeout_seconds: int = 900,
) -> pd.DataFrame:
    """单暴露 × 多特征偏 Spearman（秩残差控制协变量），返回 feature/rho/p/q_BH/n。"""
    if not RSCRIPT.is_file():
        raise FileNotFoundError(f"Rscript 缺失：{RSCRIPT}（可用 RSCRIPT_BIN 覆盖）")
    with tempfile.TemporaryDirectory(prefix="mra_r_") as tmp:
        tmp = Path(tmp)
        exposure.rename("exposure").to_frame().to_csv(tmp / "exp.tsv", sep="\t")
        features.to_csv(tmp / "feat.tsv", sep="\t")
        covariates.to_csv(tmp / "cov.tsv", sep="\t")
        script = tmp / "assoc.R"
        script.write_text(_R_SCRIPT, encoding="utf-8")
        out = tmp / "res.tsv"
        proc = subprocess.run(
            [str(RSCRIPT), str(script), str(tmp / "exp.tsv"), str(tmp / "feat.tsv"),
             str(tmp / "cov.tsv"), str(out)],
            capture_output=True, text=True, timeout=timeout_seconds,
        )
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError(f"R 分析失败：{proc.stderr[-2000:]}")
        return pd.read_csv(out, sep="\t")
