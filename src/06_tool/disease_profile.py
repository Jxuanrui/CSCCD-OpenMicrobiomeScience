"""疾病档案制（阶段 2 W2.1）——疾病身份的唯一定义处。

背景：改造前，高血压的身份（MeSH 号/词根/DOID/检索式/外证源）散布在
subgraph/interact/hypothesize 三个模块的 argparse 默认值里——换病要人工
改十几处，漏一处下游全错（tool_line_review §一病根）。

规则（stage2_spec §五）：
1. 档案=configs/diseases/<key>.json（JSON 而非 YAML：零新依赖——七模块纯
   标准库红线不破；声明制性质与调研参照[KG-Hub download.yaml]一致）；
2. 参数解析顺序：**显式命令行 > 疾病档案 > 模块原默认**（三档都不落才报错）；
3. fail-closed：档案不存在/缺字段/外证源为空 → 立即退出并说明可用档案；
4. 档案入 git 不带本机绝对路径——ext102 等文件位置一律仓内相对路径
   （configs 路径策略，拍板 v3 ④）。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DISEASE_DIR = ROOT / "configs" / "diseases"

# 档案必填六字段（缺一即 fail-closed）——与 stage2_spec §一"归属=disease"行一致
REQUIRED_FIELDS = ("key", "mesh_ids", "name_root", "doid",
                   "disease_query", "ext102")

# argparse dest → 档案字段名（唯一映射登记处，三模块共用）
DEST_TO_FIELD = {"mesh": "mesh_ids", "name_root": "name_root",
                 "doid": "doid", "key": "key",
                 "disease_query": "disease_query", "ext102": "ext102"}


def load(key: str) -> dict:
    """载入疾病档案；任何不合法状态直接退出（fail-closed）。"""
    p = DISEASE_DIR / f"{key}.json"
    if not p.exists():
        avail = sorted(x.stem for x in DISEASE_DIR.glob("*.json")) or ["（无）"]
        sys.exit(f"[disease_profile] 无档案 {p.name}；configs/diseases/ 可用: "
                 f"{', '.join(avail)}")
    d = json.loads(p.read_text(encoding="utf-8"))
<<<<<<< HEAD
    # 值非空校验（评审 W2.1 审 P1：显式 null/空串会静默绕过"键存在"检查）；
=======
    # 值非空校验（监工 W2.1 审 P1：显式 null/空串会静默绕过"键存在"检查）；
>>>>>>> origin/main
    # ext102 除外——空外证源走下方专门的提示（空票逻辑说明），不用泛化报错
    missing = [f for f in REQUIRED_FIELDS
               if f != "ext102" and not d.get(f)]
    if missing:
        sys.exit(f"[disease_profile] 档案 {p.name} 缺字段: {missing}")
    if not d.get("ext102"):
        # 空票（无外部佐证源的病）的逻辑随第二个疾病实例落地——当前单病
        # 实例阶段先挡住，防止静默落到 HTN 的外证文件上（张冠李戴零容忍）
        sys.exit(f"[disease_profile] 档案 {p.name} 的 ext102 为空——"
                 f"当前版本要求提供外部佐证源（空票逻辑见 spec §五注）")
    return d


def resolve(args: argparse.Namespace, orig_defaults: dict) -> argparse.Namespace:
    """按"显式 > 档案 > 原默认"填 None 位（None=argparse 哨兵，仅显式传参非 None）。

    orig_defaults：dest → 改造前默认值（改造时从 argparse default= 挪进来，
    值一字不改——这是 golden 等价成立的前提：无 --disease 时行为逐字节不变）。
    """
    profile = load(args.disease) if getattr(args, "disease", None) else None
    for dest, field in DEST_TO_FIELD.items():
        if not hasattr(args, dest):
            continue  # 该子命令没有这个参数（如 tier1 无 mesh）
        cur = getattr(args, dest)
        if cur is not None:
            continue  # 显式传参最高优先
        if profile is not None:
            val = profile[field]
            # ext102 等路径字段：档案存仓内相对路径，解析为 ROOT 锚定绝对路径
            if dest == "ext102":
                val = ROOT / val
            setattr(args, dest, val)
        else:
            setattr(args, dest, orig_defaults[dest])
    return args
