#!/usr/bin/env python3
"""PubTator 实体对关系分类 v2（五层自动质控）。

L0 学名映射与归一交叉校验（names.dmp）→ L1 句子级候选生成 →
L2 few-shot 强约束 Prompt（学名+原文提及）→ L3a 确定性校验（逐字 span、
主语绑定、predicate×类别兼容矩阵）→ L3b 正向边 k=3@T=0.7 自一致性投票 →
L3c 跨架构模型 judge（deepseek）。

密钥只从环境变量读取。输入 data/pubtator/ibd_articles.jsonl，
输出 data/staging/llm_relations.jsonl（每阶段整体快照落盘，可断点续跑）。
"""
import argparse
import json
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data" / "pubtator" / "articles.jsonl"
OUTPUT = ROOT / "data" / "staging" / "llm_relations.jsonl"
NAMES_CACHE = ROOT / "data" / "raw" / "taxon_names_cache.json"
NAMES_DMP = ROOT / "data" / "raw" / "names.dmp"
RANKS_CACHE = ROOT / "data" / "raw" / "taxon_ranks_cache.json"
NODES_DMP = ROOT / "data" / "raw" / "nodes.dmp"
MICROBE_CACHE = ROOT / "data" / "raw" / "taxon_microbe_cache.json"
# 仅允许物种/属级作为 Microbe 主体，界/门/纲/目/科等高阶分类单元不参与关系抽取。
ALLOWED_RANKS = {"species", "genus"}
# Microbe 主体限定为细菌/古菌 + 微型真菌允许清单；植物/大型真菌/动物为 Food 或其他域，
# 不属于 Microbe 节点（语料含食疗/草药文献，PubTator 会把植物也标为 Species）。
MICROBIAL_SUPERKINGDOMS = {"2", "2157"}  # Bacteria, Archaea
MICRO_FUNGI_GENERA = {"Saccharomyces", "Candida", "Aspergillus", "Penicillium",
                      "Malassezia", "Kluyveromyces", "Pichia", "Debaryomyces"}

PREDICATES = {
    "increases_abundance_in", "decreases_abundance_in", "alleviates", "aggravates",
    "produces", "consumes", "affects", "regulates_host_gene", "modulates_host_gene",
    "promotes_growth", "inhibits_growth", "participates_in", "sensitive_to",
    "biotransforms", "contained_in", "no_relation",
}
# predicate × (主体类别, 客体类别) 兼容矩阵：非法组合确定性拒绝。
COMPAT = {
    ("Microbe", "Disease"): {"increases_abundance_in", "decreases_abundance_in",
                             "alleviates", "aggravates", "affects"},
    ("Microbe", "Metabolite"): {"produces", "consumes", "affects", "biotransforms"},
    ("Microbe", "Gene"): {"regulates_host_gene", "modulates_host_gene", "affects"},
    # schema 既定：Food→Microbe = promotes_growth|inhibits_growth（affects 为关联表述安全阀）
    ("Food", "Microbe"): {"promotes_growth", "inhibits_growth", "affects"},
}

# 食物组词典（agent 侧需求单 W4，P3 食物组×菌群）：canonical -> 别名列表。
# 节点 id 归一为 LFS:FOOD:<slug>；词表可替换（项目X 标准膳食分类表到位后映射即可）。
FOOD_LEXICON = {
    "whole grain": ["whole grain", "whole grains", "whole-grain", "oat", "oats", "oatmeal", "barley", "brown rice", "rye", "quinoa"],
    "dietary fiber": ["dietary fiber", "dietary fibre", "soluble fiber", "insoluble fiber", "cellulose", "hemicellulose", "psyllium"],
    "prebiotic": ["prebiotic", "prebiotics", "inulin", "fructooligosaccharide", "fructo-oligosaccharide", "FOS", "galactooligosaccharide", "GOS", "resistant starch", "beta-glucan", "beta-glucans"],
    "polyphenol": ["polyphenol", "polyphenols", "flavonoid", "flavonoids", "anthocyanin", "resveratrol", "quercetin", "catechin", "curcumin", "ellagic acid"],
    "fermented food": ["fermented food", "fermented foods", "kimchi", "sauerkraut", "kefir", "kombucha", "miso", "tempeh", "sourdough"],
    "dairy product": ["dairy", "yogurt", "yoghurt", "milk", "cheese", "buttermilk", "whey protein"],
    "red meat": ["red meat", "beef", "pork", "lamb", "mutton"],
    "processed meat": ["processed meat", "sausage", "bacon", "ham", "deli meat"],
    "poultry": ["poultry", "chicken", "turkey"],
    "fish and seafood": ["fish", "seafood", "salmon", "sardine", "mackerel", "omega-3 fatty acid", "omega-3"],
    "fruit": ["fruit", "fruits", "berry", "berries", "apple", "citrus", "pomegranate", "grape"],
    "vegetable": ["vegetable", "vegetables", "cruciferous vegetable", "broccoli", "leafy green", "carrot", "tomato"],
    "legume": ["legume", "legumes", "bean", "beans", "lentil", "soybean", "tofu", "pea protein"],
    "nut and seed": ["nut", "nuts", "almond", "walnut", "flaxseed", "chia seed"],
    "coffee and tea": ["coffee", "caffeine", "tea", "green tea", "black tea"],
    "alcohol": ["alcohol", "ethanol", "wine", "beer", "chronic alcohol"],
    "high-fat diet": ["high-fat diet", "high fat diet", "western diet", "western-style diet"],
    "mediterranean diet": ["mediterranean diet", "plant-based diet", "vegetarian diet", "vegan diet"],
    "food emulsifier": ["emulsifier", "emulsifiers", "polysorbate", "carboxymethylcellulose", "maltodextrin"],
    "artificial sweetener": ["artificial sweetener", "non-nutritive sweetener", "sucralose", "aspartame", "saccharin"],
}
_FOOD_ALIASES = [(a, canon) for canon, aliases in FOOD_LEXICON.items() for a in aliases]
_FOOD_PATTERN = re.compile("|".join(
    sorted((re.escape(a) for a, _ in _FOOD_ALIASES), key=len, reverse=True)), re.I)

HOST_TAXIDS = {"9606", "10090", "10116", "9913", "9823", "10114"}
GENERIC_MICROBE_TAXIDS = {"749906"}
# 衍生产物简称识别：属首字母 + 产物关键词（如 AmEVs 之于 Akkermansia）。
PRODUCT_HINTS = ("ev", "vesicle", "metabolite", "exopolysaccharide", "eps", "lps",
                 "exosome", "supernatant", "lysate", "extract", "cell-free",
                 "outer membrane", "peptidoglycan", "cell wall")
PRED_CN = {"increases_abundance_in": "在该疾病中丰度升高", "decreases_abundance_in": "在该疾病中丰度降低",
           "alleviates": "缓解/改善", "aggravates": "加重/恶化", "produces": "产生",
           "consumes": "消耗/利用", "affects": "影响", "biotransforms": "生物转化",
           "regulates_host_gene": "调控宿主基因", "modulates_host_gene": "调节宿主基因"}

SYSTEM = """你是医学知识图谱关系判定器。输入是一个句子和其中两个已标准化的实体（给出拉丁学名与原文提及）。
只判断该句是否明确支持二者的有向关系，不要跨句推测，不要用背景知识补充。必须返回严格 JSON：
{"predicate":"关系名或no_relation","confidence":0到1,"polarity":"positive|negative|neutral","subject_mention":"句中指代主体实体的原文片段","evidence":"句内的逐字原文依据(≤60字)"}
主语绑定规则（最重要）：
- subject_mention 必须是句中明确指代"该特定微生物本身或其直接产物（如菌体囊泡/代谢物）"的原文片段；
- 整体菌群/微生态（microbiota、microbiome、gut communities 等）、宿主、其他菌的产物，禁止绑定到该菌，此时返回 no_relation；
- 仅有宿主或菌群整体的效应句（如 patient microbiotas preserved colitogenic capacity）必须返回 no_relation。
- 仅同句共现但无因果表述时返回 no_relation。
方向约定：subject 是实体1，object 是实体2；方向不明确返回 no_relation。
关系提示：Microbe-Disease 关注 abundance/associated with/alleviates/aggravates；Microbe-Metabolite 关注 produces/consumes/biotransforms。

示例1（产物提及允许正向）：
句：Akkermansia muciniphila-derived extracellular vesicles alleviate colitis.
实体1：Akkermansia muciniphila [Microbe] 实体2：colitis [Disease]
返回：{"predicate":"alleviates","confidence":0.9,"polarity":"positive","subject_mention":"Akkermansia muciniphila-derived extracellular vesicles","evidence":"Akkermansia muciniphila-derived extracellular vesicles alleviate colitis"}

示例2（整体菌群禁止绑定）：
句：Crohn's disease patient microbiotas preserved colitogenic capacity in mice.
实体1：Clostridioides difficile [Microbe]（该菌只是文中别处提及） 实体2：Crohn Disease [Disease]
返回：{"predicate":"no_relation","confidence":0.9,"polarity":"neutral","subject_mention":"","evidence":""}

produces 与 biotransforms 的严格区分（最重要）：
- produces 仅限该微生物自身从头合成/发酵分泌的代谢物（如产丁酸盐/乙酸盐）；
- 底物→产物的转化反应一律用 biotransforms(底物)，禁止对同一反应再输出 produces(产物)；
- 宿主体内/粪便/血清代谢物丰度升高（可能为 cross-feeding 或群落效应）不构成 produces，证据不足时返回 no_relation；
- 层级规则：句中明确到种（如 C. scindens）时主体即该种，禁止将种级表型泛化到整个属。

机制归因规则（间接效应禁止映射为直接代谢谓词）：
- produces/biotransforms/consumes 仅限该菌活菌自身的酶促反应或从头合成；
- "X-mediated production/stimulation"（菌介导宿主或微环境产生）不是该菌自身合成，可用 affects 或 no_relation；
- 巴氏灭菌/热灭活菌体（pasteurized/heat-killed/postbiotic）经宿主信号轴（如 FXR/FGF19）产生的间接调控不是菌体酶促反应；
- 脱硫酸基/去结合等修饰反应（desulfation/deconjugation/sulfatase）是对大分子（如 mucin）的修饰，不是摄取游离底物（如游离 Sulfates），禁止映射为 consumes(游离物)；
- "remodels/alters 谱图"类表述默认为间接调控而非直接转化；
- 客体粒度：Death/存活率等终末事件应建模为具体疾病或 Mortality 风险概念，禁止把 Death 当可加重的疾病实体；
- Food→Microbe 谓词：膳食暴露促进该菌生长/富集用 promotes_growth，抑制用 inhibits_growth，仅相关表述用 affects 或 no_relation；
- Food 蕴含判定铁律（第六轮校准）：唯一标准是"句子是否直接陈述该三元组"，禁止任何生物学合理化补全（Evidence first, plausibility second）：
  ① X 降解/发酵/利用某食物成分（degrades/ferments/utilizes/metabolizes）≠ 该成分促进 X 生长——此类句子对 promotes_growth 一律 no_relation；
  ② 菌是食物效应的介导者/关键菌（mediates/mediator/key player/responsible for the effect）≠ food affects 该菌；
  ③ 饮食改变的是微生物代谢物水平（metabolite/SCFA levels changed）≠ 改变微生物本身（丰度/生长/组成）；
  ④ signature/associated/biomarker 菌至多支持 affects 且需句子明说丰度或组成变化，禁止升级 promotes_growth；
  ⑤ promotes_growth 证据标准：句子必须直接陈述"摄入/添加该食物后该菌丰度/数量/生长显著增加"（increased abundance/enriched/stimulated the growth of X upon consumption），否则降级 affects 或 no_relation；
  ⑥ 食物组归一必须与原文措辞一致，禁止上位/近义映射（如 western diet ≠ high-fat diet）。
- Food 领域校准（第五轮人工抽检确立，严格执行）：
  ① 食品微生态≠宿主调控：句子若描述食品自身的微生物组成（发酵剂/starter culture、优势菌种/dominant species、食品基质演替/food matrix、contains/harbors/isolated from food），Food→Microbe 一律 no_relation——本图谱只记录"膳食摄入对宿主肠道菌群的影响"；
  ② 实体歧义：milk 出现在 breast/human/maternal 语境=母乳（垂直传递）≠ dairy product，判 no_relation；肉类作为病原来源/载体（source of/carrier/foodborne/contamination）=食品安全语义，判 no_relation；
  ③ 证据强度：仅"检出/存在"（were found/detected/present）或"罕见利用"（uncommon/rare）不足以支撑 promotes_growth，至多 affects；方法学选取语境（were selected/to investigate/for testing）一律 no_relation；
  ④ 植物提取物/纯化合物的体外抗菌活性不归入全食物组关系；
- 强因果谓词（aggravates/alleviates）仅限实验性因果证据（干预/定植/清除实验），纯相关表述（risk factor、elevated in disease）只可产 no_relation 或关联型弱谓词。"""

CUES = ("microbiota", "microbiome", "bacter", "species", "strain", "abundance",
        "increase", "decrease", "produce", "metabol", "associate", "correlat",
        "regulat", "inhibit", "promot", "alleviat", "aggravat", "affect",
        "vesicle", "ferment")

_local = threading.local()


def get_session():
    if not hasattr(_local, "session"):
        _local.session = requests.Session()
        _local.session.headers["Connection"] = "close"
    return _local.session


# ---------------------------------------------------------------- L0 学名
def load_taxon_names(wanted):
    """names.dmp 单遍过滤出 wanted taxid 的学名，缓存复用（参照 rxnorm_cache）。"""
    if NAMES_CACHE.exists():
        cached = json.loads(NAMES_CACHE.read_text(encoding="utf-8"))
        if wanted <= set(cached):
            return {t: cached[t] for t in wanted}
    else:
        cached = {}
    found = dict(cached)
    with NAMES_DMP.open(encoding="utf-8") as f:
        for line in f:
            parts = [x.strip() for x in line.split("|")]
            if len(parts) > 3 and parts[0] in wanted and parts[3] == "scientific name":
                found[parts[0]] = parts[1]
    NAMES_CACHE.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return {t: found.get(t, "") for t in wanted}


def load_taxon_ranks(wanted):
    """nodes.dmp 单遍过滤出 wanted taxid 的分类等级与父节点，缓存复用。"""
    if RANKS_CACHE.exists():
        cached = json.loads(RANKS_CACHE.read_text(encoding="utf-8"))
        if wanted <= set(cached):
            return {t: cached[t] for t in wanted}
    else:
        cached = {}
    found = dict(cached)
    with NODES_DMP.open(encoding="utf-8") as f:
        for line in f:
            parts = [x.strip() for x in line.split("|")]
            if len(parts) > 2 and parts[0] in wanted:
                found[parts[0]] = {"rank": parts[2], "parent": parts[1]}
    RANKS_CACHE.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return {t: found.get(t, {"rank": "", "parent": ""}) for t in wanted}


_PARENT_MAP = None
_RANK_MAP = None


def _load_lineage_maps():
    """全量单遍加载 taxid→parent 与 taxid→rank（界级判定需沿父链上溯）。"""
    global _PARENT_MAP, _RANK_MAP
    if _PARENT_MAP is not None:
        return
    _PARENT_MAP, _RANK_MAP = {}, {}
    with NODES_DMP.open(encoding="utf-8") as f:
        for line in f:
            parts = line.split("|", 3)
            if len(parts) < 3:
                continue
            _PARENT_MAP[parts[0].strip()] = parts[1].strip()
            _RANK_MAP[parts[0].strip()] = parts[2].strip()


def resolve_microbial(wanted, sci_names):
    """判定各 taxid 是否属于 Microbe 域（细菌/古菌/微型真菌），结果缓存。"""
    if MICROBE_CACHE.exists():
        cached = json.loads(MICROBE_CACHE.read_text(encoding="utf-8"))
        if wanted <= set(cached):
            return {t: cached[t] for t in wanted}
    else:
        cached = {}
    todo = {t for t in wanted if t not in cached}
    if todo:
        _load_lineage_maps()
        for t in todo:
            seen, cur, is_fungus, kingdom = set(), str(t), False, ""
            while cur and cur not in seen:
                seen.add(cur)
                if cur == "4751":  # Fungi
                    is_fungus = True
                # 新版 NCBI dump 将 superkingdom 更名为 domain，两者都识别。
                if _RANK_MAP.get(cur) in ("superkingdom", "domain"):
                    kingdom = cur
                    break
                cur = _PARENT_MAP.get(cur, "")
            if kingdom in MICROBIAL_SUPERKINGDOMS:
                cached[t] = True
            elif is_fungus:
                # 真菌域：仅微型真菌属放行，大型真菌/蘑菇按非 Microbe 处理。
                genus = str(sci_names.get(t, "")).split()[0] if sci_names.get(t) else ""
                cached[t] = genus in MICRO_FUNGI_GENERA
            else:
                cached[t] = False
    MICROBE_CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return {t: cached.get(t, False) for t in wanted}


RELINK_CACHE = ROOT / "data" / "raw" / "taxon_relink_cache.json"


def build_mention_relink(mentions):
    """names.dmp 精确学名匹配：纠正 PubTator 种级提及归一到属级 ID（或反之）的错配。

    人工抽检发现（PMID:41957291）促炎主体明确为种 C. scindens，却被归一到
    Clostridium 属级 ID，导致种级表型泛化到整属。凡原文提及与 names.dmp 某
    学名完全一致且与 PubTator 归一结果不同，一律改链到该学名对应 taxid。
    """
    todo = {m for m in mentions if m and m not in ("", "-")}
    if RELINK_CACHE.exists():
        cached = json.loads(RELINK_CACHE.read_text(encoding="utf-8"))
        todo = {m for m in todo if m.lower() not in cached}
    else:
        cached = {}
    if todo:
        wanted_lower = {m.lower() for m in todo}
        # 缩写提及（"C. scindens" 型）：扫描时同步收集 "G* epithet" 学名候选。
        abbr = {}
        for m in todo:
            mm = re.match(r"^([A-Za-z])\.\s+([a-z][a-z-]+)$", m)
            if mm:
                abbr.setdefault((mm.group(1).upper(), mm.group(2).lower()), set()).add(m.lower())
        found, abbr_hits = {}, {}
        with NAMES_DMP.open(encoding="utf-8") as f:
            for line in f:
                parts = [x.strip() for x in line.split("|")]
                if len(parts) > 3 and parts[3] != "scientific name":
                    continue
                low = parts[1].lower()
                if low in wanted_lower:
                    found.setdefault(low, []).append((parts[0], parts[1]))
                toks = parts[1].split()
                if len(toks) == 2:
                    key = (toks[0][0].upper(), toks[1].lower())
                    if key in abbr:
                        abbr_hits.setdefault(key, set()).add((parts[0], parts[1]))
        for m in wanted_lower:
            hits = found.get(m, [])
            # 学名在 NCBI 唯一（少数异名冲突时放弃改链，保守保持原归一）。
            cached[m] = {"taxid": hits[0][0], "name": hits[0][1]} if len(hits) == 1 else {}
        # 缩写展开：唯一 "G* epithet" 学名命中时映射到该学名。
        for key, ms in abbr.items():
            hits = abbr_hits.get(key, set())
            if len({t for t, _ in hits}) == 1:
                taxid, name = next(iter(hits))
                for m in ms:
                    cached[m] = {"taxid": taxid, "name": name}
        RELINK_CACHE.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
    return cached



def food_entities(sent):
    """句内食物组提及（词典匹配）→ Food 实体列表（schema 既定 LFS:FOOD 前缀）。"""
    out, seen = [], set()
    for m in _FOOD_PATTERN.finditer(sent):
        matched = m.group(0)
        canon = dict((a.lower(), c) for a, c in _FOOD_ALIASES).get(matched.lower())
        if not canon or canon in seen:
            continue
        seen.add(canon)
        out.append({"id": f"LFS:FOOD:{canon.replace(' ', '_')}", "name": canon,
                    "mention": matched, "category": "Food"})
    return out

def tokens(s):
    return set(re.findall(r"[a-z0-9]{3,}", s.lower()))


def normalization_ok(mention, sci_name):
    """L0 交叉校验：PubTator 归一的 taxid 学名与原文提及是否相符，不符则丢弃。"""
    if not sci_name:
        return True  # 本地库缺失时保守放行
    if mention.lower().rstrip(".") == sci_name.lower():
        return True
    if tokens(mention) & tokens(sci_name):
        return True
    # 属级简称："C. difficile" 型：首字母匹配属名首字母
    m = re.match(r"^([A-Z])\.\s*(\S+)", mention)
    if m and sci_name[0].lower() == m.group(1).lower() and m.group(2).lower() in tokens(sci_name):
        return True
    return False


# ---------------------------------------------------------------- L1 句子
ABBREV = {"i.e", "e.g", "et al", "vs", "cf", "approx", "ca", "fig", "figs", "no",
          "sp", "spp", "subsp", "var", "dr", "prof", "st", "jr", "unpub"}


def split_sentences(text):
    """缩写保护的正则切句，返回 (句子, 起点, 终点) 列表（passage 相对偏移）。"""
    spans, start = [], 0
    for m in re.finditer(r"([.!?])\s+(?=[A-Z0-9(\"'])", text):
        end = m.start() + 1
        toks = re.findall(r"[A-Za-z][A-Za-z.]*", text[max(0, end - 14):end])
        tok = toks[-1] if toks else ""
        base = tok.rstrip(".").lower()
        if (len(tok.rstrip(".")) <= 1 and tok.endswith(".")) or base in ABBREV:
            continue
        spans.append((start, end))
        start = m.end()
    spans.append((start, len(text)))
    return [(text[s:e].strip(), s, e) for s, e in spans if text[s:e].strip()]


def passage_offset(passage):
    """由首个可定位标注推算该 passage 的文档级起始偏移。"""
    text = passage.get("text", "")
    for a in passage.get("annotations", []):
        t = a.get("text", "")
        if not t:
            continue
        pos = text.find(t)
        loc = (a.get("locations") or [{}])[0].get("offset")
        if pos >= 0 and loc is not None:
            return loc - pos
    return 0


def build_pairs(records, limit, taxon_names, taxon_ranks, is_microbe, relink):
    """句子级候选：两实体标注必须落在同一句内；Microbe→Disease/Metabolite。"""
    pairs, seen = [], set()
    stats = Counter()
    for rec in records:
        pmid = str(rec.get("id", rec.get("pmid", "")))
        for passage in rec.get("passages", []):
            text = passage.get("text", "")
            if not text:
                continue
            base = passage_offset(passage)
            sents = split_sentences(text)
            sent_ents = [[] for _ in sents]
            for ann in passage.get("annotations", []):
                info = ann.get("infons", {})
                ident = info.get("identifier")
                if not ident or ident == "-":
                    continue
                db, typ = info.get("database", ""), info.get("type", "")
                loc = (ann.get("locations") or [{}])[0].get("offset")
                if loc is None:
                    continue
                rel = loc - base
                idx = next((i for i, (s, a, b) in enumerate(sents) if a <= rel < b), None)
                if idx is None:
                    continue
                if db == "ncbi_taxonomy":
                    taxid = str(info.get("normalized_id", ident))
                    mention = ann.get("text", "")
                    # 种/属级错配重链接：原文提及与 NCBI 学名精确一致而与 PubTator
                    # 归一不同时，改链到学名对应 taxid，避免种级表型泛化到整属。
                    rl = relink.get(mention.lower())
                    if rl and rl.get("taxid") and rl["taxid"] != taxid:
                        stats["relinked"] += 1
                        taxid = rl["taxid"]
                    if taxid in HOST_TAXIDS or taxid in GENERIC_MICROBE_TAXIDS:
                        continue
                    if not is_microbe.get(taxid, False):
                        stats["non_microbial_dropped"] += 1
                        continue
                    rank = (taxon_ranks.get(taxid) or {}).get("rank", "")
                    if rank and rank not in ALLOWED_RANKS:
                        stats["rank_dropped"] += 1
                        continue
                    sci = taxon_names.get(taxid, "")
                    if not normalization_ok(mention, sci):
                        stats["norm_mismatch_dropped"] += 1
                        continue
                    ent = {"id": f"NCBITaxon:{taxid}", "name": sci or mention,
                           "mention": mention, "category": "Microbe"}
                elif typ == "Disease":
                    nid = ident if str(ident).startswith("MESH:") else f"MESH:{info.get('normalized_id', ident)}"
                    ent = {"id": str(nid), "name": info.get("name", ann.get("text", "")),
                           "mention": ann.get("text", ""), "category": "Disease"}
                elif typ == "Chemical":
                    nid = ident if str(ident).startswith("MESH:") else f"MESH:{info.get('normalized_id', ident)}"
                    ent = {"id": str(nid), "name": info.get("name", ann.get("text", "")),
                           "mention": ann.get("text", ""), "category": "Metabolite"}
                else:
                    continue
                sent_ents[idx].append(ent)
                stats[f"ent_{ent['category']}"] += 1
            # 同句内配对
            for idx, ents in enumerate(sent_ents):
                sent_ents[idx] = ents = ents + food_entities(sents[idx][0])
                sent_text = sents[idx][0]
                # PubMed 评论性标题（"Comment on: ..."）无抽取价值。
                if sent_text.startswith("Comment on"):
                    continue
                if not any(c in sent_text.lower() for c in CUES):
                    continue
                for sub in ents:
                    if sub["category"] == "Food":
                        # schema 既定方向：Food→Microbe（膳食暴露对菌生长/富集的影响）
                        for obj in ents:
                            if obj["category"] != "Microbe" or obj["id"] == sub["id"]:
                                continue
                            key = (pmid, sub["id"], obj["id"])
                            if key in seen:
                                continue
                            seen.add(key)
                            pairs.append({"pmid": pmid, "sentence": sent_text,
                                          "subject": sub, "object": obj})
                            if len(pairs) >= limit:
                                return pairs, stats
                        continue
                    if sub["category"] != "Microbe":
                        continue
                    for obj in ents:
                        if obj["category"] not in ("Disease", "Metabolite") or obj["id"] == sub["id"]:
                            continue
                        key = (pmid, sub["id"], obj["id"])
                        if key in seen:
                            continue
                        seen.add(key)
                        pairs.append({"pmid": pmid, "sentence": sent_text,
                                      "subject": sub, "object": obj})
                        if len(pairs) >= limit:
                            return pairs, stats
    return pairs, stats


# ---------------------------------------------------------------- L2/L3 API
def parse_json(text):
    """从模型输出提取首个 JSON 对象并做通用修复，不假设字段结构。"""
    text = (text or "").strip().replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("模型未返回JSON")
    raw = re.sub(r",\s*([}\]])", r"\1", m.group(0))
    return json.loads(raw)


ILLEGAL_PREDICATE = "__illegal__"


def validate_extraction(result):
    """抽取结果的 schema 校验（judge 等其他调用不走此校验）。"""
    if not isinstance(result, dict) or result.get("predicate") not in PREDICATES:
        # 模型偶尔返回 associated_with（有关联但无方向），按 schema 语义保守降级为 no_relation；
        # 其余自创 predicate（causes/treats 等）语义映射风险高，标记后确定性丢弃，不做 API 重试。
        if isinstance(result, dict) and result.get("predicate") == "associated_with":
            result["predicate"] = "no_relation"
        elif isinstance(result, dict) and result.get("predicate"):
            result["predicate"] = ILLEGAL_PREDICATE
            result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0))))
            result["polarity"] = result.get("polarity", "neutral")
            result["evidence"] = str(result.get("evidence", ""))[:200]
            result["subject_mention"] = str(result.get("subject_mention", ""))[:120]
            return result
        else:
            raise ValueError(f"非法predicate: {result.get('predicate') if isinstance(result, dict) else type(result)}")
    result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0))))
    result["polarity"] = result.get("polarity", "neutral")
    result["evidence"] = str(result.get("evidence", ""))[:200]
    result["subject_mention"] = str(result.get("subject_mention", ""))[:120]
    return result


def call_api(base_url, api_key, model, messages, temperature, max_tokens=3072):
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
    headers = {"Authorization": f"Bearer {api_key}"}
    import time
    for attempt in range(4):
        try:
            r = get_session().post(base_url.rstrip("/") + "/chat/completions",
                                   json=payload, headers=headers, timeout=(15, 180))  # 中转排队+长推理下单次可达60s+，35s读超时是此前高error率的根因
        except requests.RequestException:
            time.sleep(2 ** attempt); continue
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt); continue
        r.raise_for_status()
        data = r.json()
        message = (data.get("choices") or [{}])[0].get("message", {})
        return parse_json(message.get("content") or message.get("reasoning_content") or "")
    raise RuntimeError("API 重试后仍失败")


def classify_pair(base_url, api_key, model, fallback_model, pair):
    """L2 抽取（T=0.7 保证投票有随机性），失败换备用模型。"""
    sent = pair["sentence"][:2200]
    user = (f"句：{sent}\n实体1：{pair['subject']['name']} [Microbe]（原文提及：{pair['subject']['mention']}）\n"
            f"实体2：{pair['object']['name']} [{pair['object']['category']}]（原文提及：{pair['object']['mention']}）\n"
            f"请按规则只返回JSON。")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    try:
        return validate_extraction(call_api(base_url, api_key, model, messages, 0.7)), "primary"
    except Exception:
        return validate_extraction(call_api(base_url, api_key, fallback_model, messages, 0.7)), "fallback"


def judge_triple(base_url, api_key, judge_model, pair, predicate):
    """L3c 跨架构 judge：NLI 式 SUPPORTED/REFUTED/NEI + 主语绑定检查（T=0）。"""
    sent = pair["sentence"][:2200]
    claim = f"{pair['subject']['name']}（或其直接产物）{PRED_CN.get(predicate, predicate)} {pair['object']['name']}"
    sys_j = ("你是事实核查器。给定前提句和待核查论断，判断前提是否支持论断，且论断主语是否绑定到该特定微生物"
             "（而非菌群整体/宿主/其他菌）。只返回严格JSON："
             '{"verdict":"SUPPORTED|REFUTED|NEI","subject_binding_ok":true或false,"reason":"≤30字"}。'
             "若论断谓词为 produces：仅当前提明确该菌自身合成/分泌该代谢物时才 SUPPORTED；"
             "底物转化反应应为 biotransforms 而非 produces；宿主体内/粪便代谢物丰度升高（cross-feeding/群落效应）不支持 produces。"
             "Food 蕴含铁律（第六轮校准）：你检验的是句子是否蕴含三元组，不是寻找生物学解释让它说得通（Evidence first, plausibility second）。"
             "谓词降级（第七轮校准）：原句含 may/might/marginal/tended to/trend/suggest/appear/possible 时，promotes_growth 和 inhibits_growth 一律降为 affects。"
             "主语归属三禁（第七轮校准 2026-09-30 监工裁决）：合生元/复合制剂的效应禁止拆归给单一成分；背景因素不是作用主语；营养素不能上归到食物组。"
             "以下推理链一律 REFUTED：degrades/ferments/utilizes 底物→promotes_growth；mediator/key player→food affects 该菌；"
             "代谢物水平变化→微生物变化；signature/associated 菌→promotes_growth；western diet 等近义饮食模式互换。"
"若论断谓词为 produces/biotransforms/consumes：核查是否为该菌活菌自身的酶促反应——"
             "介导性产生（X-mediated production）、灭活制剂的宿主轴调控（pasteurized/postbiotic）、"
             "修饰反应（desulfation/sulfatase 切大分子基团）均不支持这三个谓词。"
             "若论断主语层级与前提不符（前提明确到种而论断用属，或相反）则 REFUTED。"
             "校准准则（第四轮人工复核确立，严格执行）："
             "①功能定语/同位语/背景从句中的事实性陈述（如 SCFA-producing consortia (e.g., Lactobacillus)、"
             "X Present in Y supernatant）属受支持，不得因句法位置拒判；"
             "②affects 为弱谓词：enhances/reduces/lowers biosynthesis 或 levels 等直接调控逻辑真包含于 affects；"
             "③受控词表同义映射有效：SCFA↔Fatty Acids, Volatile、glycans↔Polysaccharides 等规范化上位词不算实体未对齐；"
             "④客体粒度问题（如 Death 应为 Pneumonia/Mortality Risk）在 reason 标注'实体抽取不规范'，"
             "并先判断是否粒度问题再定 subject_binding_ok；"
             "⑤仅相关/危险因素表述（may be a risk factor、elevated in disease）不支持强因果谓词"
             " aggravates/alleviates → NEI，同类句式标准保持一致。")
    user = f"前提句：{sent}\n论断：{claim}"
    try:
        return call_api(base_url, api_key, judge_model,
                        [{"role": "system", "content": sys_j}, {"role": "user", "content": user}],
                        0, max_tokens=2048)
    except Exception as exc:
        return {"verdict": "ERROR", "subject_binding_ok": False, "reason": str(exc)[:120]}


# ---------------------------------------------------------------- L3a 确定性校验
def norm_ws(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def mention_ok(mention, ent):
    """主语绑定确定性校验：提及与学名词元重合，或属首字母衍生产物简称。"""
    m = norm_ws(mention)
    if not m:
        return False
    known = tokens(f"{ent['name']} {ent.get('mention', '')}")
    if tokens(m) & known:
        return True
    if ent["name"] and mention and mention[0].lower() == ent["name"][0].lower() \
            and any(h in m for h in PRODUCT_HINTS):
        return True
    return False


def deterministic_checks(pair, res):
    """返回 (通过, 原因)。evidence 须为句子逐字子串，subject_mention 须真实且绑定正确。"""
    sent_n = norm_ws(pair["sentence"])
    pred = res["predicate"]
    if pred not in COMPAT.get((pair["subject"]["category"], pair["object"]["category"]), set()):
        return False, f"compat:{pred}"
    if pred != "no_relation":
        ev = norm_ws(res["evidence"])
        if len(ev) < 8 or ev not in sent_n:
            return False, "evidence_not_verbatim"
        men = res["subject_mention"]
        if not men or norm_ws(men) not in sent_n:
            return False, "mention_not_in_sentence"
        if not mention_ok(men, pair["subject"]):
            return False, "mention_not_bound_to_microbe"
    return True, ""


def demote_transform_conflicts(rows):
    """同 pmid+主体存在 biotransforms 边且 produces 边的客体名出现在转化句中时，降级 produces。

    人工抽检发现（PMID:42461117）：P. distasonis 将 genistin 转化为 genistein，
    模型同时输出 biotransforms(genistin) 与 produces(genistein)。转化反应的
    产物不构成"从头合成"，避免 produces 语义污染（交叉验证至同文不同句）。
    """
    by_subj = {}
    for r in rows:
        if r.get("status") == "ok" and r["predicate"] in ("biotransforms", "produces"):
            by_subj.setdefault((r.get("pmid"), r["subject"]["id"]),
                               {"bt": [], "pr": []})[ "bt" if r["predicate"] == "biotransforms" else "pr"].append(r)
    n = 0
    for group in by_subj.values():
        if not group["bt"]:
            continue
        bt_text = " ".join(r.get("sentence", "") for r in group["bt"]).lower()
        for r in group["pr"]:
            obj_name = str(r["object"].get("name", ""))
            tok = [t for t in re.findall(r"[a-z0-9]{5,}", obj_name.lower())]
            if tok and any(re.search(rf"\b{re.escape(t)}\b", bt_text) for t in tok):
                r["status"] = "dropped_check"
                r["check_fail"] = "transform_conflict_produces"
                n += 1
    return n


POSTBIOTIC_MARKERS = re.compile(r"pasteurized|heat[- ]killed|postbiotic|paraprobiotic", re.I)


def mediated_by_subject(mention, sent):
    """主体锚定的介导模式："X-mediated …(production)" / "… production mediated by X"。

    句中任意位置的 mediat+produc 共现过于宽泛（"threonine-producing B. fragilis
    facilitates … mediated …" 会被误伤），必须锚定主体提及且 60 字符窗口内含
    产物词才算介导信号；产物的语序在锚点前后均可。
    """
    if not mention:
        return False
    esc = re.escape(mention)
    anchors = list(re.finditer(esc + r"[\s-]*mediated", sent, re.I))
    anchors += list(re.finditer(r"mediated by\s+" + esc, sent, re.I))
    for m in anchors:
        window = sent[max(0, m.start() - 60):m.end() + 60]
        if re.search(r"produc", window, re.I):
            return True
    return False



# 第五轮校准：食品微生态/歧义/载体三类确定性拦截（Food 主体边）
FOOD_ECOLOGY = re.compile(r"starter culture|dominant species|food matrix|fermented by|harbou?rs?|isolated from .{0,20}food|enriched with .{0,25}(bacteri|lactobacill)", re.I)
BREAST_MILK = re.compile(r"breast\s*milk|human\s*milk|maternal\s*milk", re.I)
CARRIER_SEM = re.compile(r"foodborne|source of contamination|carrier of|transmission", re.I)

def demote_indirect_mechanism(rows):
    """间接/介导机制不得映射为直接代谢谓词（人工抽检第二轮 3 案例的确定性编码）。

    ① 灭活制剂（pasteurized/postbiotic）的宿主轴效应不是活菌酶促反应→硬降级；
    ② 修饰反应（desulfation/sulfatase 切 mucin 硫酸基）非摄取游离底物→硬降级；
    ③ 主体锚定的介导产生（"X-mediated production"）→仅当机制感知 judge 未判
      SUPPORTED 时降级，判 SUPPORTED 则保留并加 flag 供人工复核（介导语言对
      "菌介导宿主产生"与"菌自身机制介导产生"存在歧义，交给 judge 仲裁）。
    """
    n = 0
    for r in rows:
        if r.get("status") != "ok":
            continue
        pred, sent = r["predicate"], r.get("sentence", "")
        men = r.get("subject_mention", "")
        why = None
        # Food 主体专用拦截（第五轮校准）
        if r["subject"].get("category") == "Food":
            if FOOD_ECOLOGY.search(sent):
                r["status"] = "dropped_check"; r["check_fail"] = "food_ecology_not_host"; n += 1; continue
            if BREAST_MILK.search(sent):
                r["status"] = "dropped_check"; r["check_fail"] = "breast_milk_not_dairy"; n += 1; continue
            if CARRIER_SEM.search(sent):
                r["status"] = "dropped_check"; r["check_fail"] = "carrier_not_dietary"; n += 1; continue
            # 第六轮铁律的确定性版
            GROWTH_OK = re.search(r"(increas|enrich|stimulat|promot)\w*[^.]{0,40}(abundance|growth|levels?\s+of|population)", sent, re.I)
            if pred == "promotes_growth" and re.search(r"\b(degrad|ferment|utiliz|metaboliz)\w*", sent, re.I) and not GROWTH_OK:
                r["status"] = "dropped_check"; r["check_fail"] = "substrate_utilization_not_promotion"; n += 1; continue
            if pred in ("affects", "promotes_growth") and re.search(r"mediat\w+ by|\bmediator\b|key player|responsible for the", sent, re.I) and not GROWTH_OK:
                r["status"] = "dropped_check"; r["check_fail"] = "mediator_not_target"; n += 1; continue
            if re.search(r"metabolit\w*\s+(levels?|concentration|production)\s+[^.]{0,30}(chang|increas|decreas|alter)", sent, re.I) and not GROWTH_OK:
                r["status"] = "dropped_check"; r["check_fail"] = "metabolite_level_not_microbe"; n += 1; continue
        if pred in ("produces", "biotransforms", "consumes") and POSTBIOTIC_MARKERS.search(f"{men} {sent}"):
            why = "postbiotic_indirect"
        elif pred == "consumes" and re.search(r"desulf\w*|sulfatase", sent, re.I):
            why = "modification_not_consumption"
        elif pred == "produces" and mediated_by_subject(men, sent):
            if (r.get("judge") or {}).get("verdict") == "SUPPORTED":
                r["flag"] = "mediated_production"  # 保留但显式标记，审核表可见
                continue
            why = "mediated_production"
        if why:
            r["status"] = "dropped_check"
            r["check_fail"] = why
            n += 1
    return n


EXEC = {"capability_id": "", "execution_id": "", "started_at": ""}
USAGE_REF = Path(__file__).resolve().parents[2] / "data/registry/resource_usage.jsonl"


def snapshot(rows):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # 裁决 2026-09-25（staging 字段）：created_at / execution_id / capability_id /
    # resource_ref。旧记录显式标注 backfill（不伪造创建时间）；新记录真实时间戳。
    for r in rows:
        if r is None:
            continue
        if r.get("capability_id") != EXEC.get("capability_id"):
            r["created_at"] = f"backfill@{EXEC['started_at']}"
            r["execution_id"] = EXEC["execution_id"]
            r["capability_id"] = EXEC["capability_id"]
            r["resource_ref"] = str(USAGE_REF)
        else:
            r.setdefault("created_at", EXEC["started_at"])
            r.setdefault("execution_id", EXEC["execution_id"])
            r.setdefault("capability_id", EXEC["capability_id"])
            r.setdefault("resource_ref", str(USAGE_REF))
    # S2 修复（2026-09-29 监工定稿 s2_fix_plan）：原子写入——先 tmp 再 os.replace，
    # 进程被杀不再可能留下半行/截断文件（连带保护 S1 成果）
    tmp = OUTPUT.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            if r is None:
                continue
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, OUTPUT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-pairs", type=int, default=30)
    ap.add_argument("--fallback-model", default="glm-5.3-flash")
    ap.add_argument("--judge-model", default="deepseek-v4-flash")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    key, base = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "glm-5.3")
    if not key or not base:
        raise SystemExit("请设置 OPENAI_API_KEY 和 OPENAI_BASE_URL")

    # Capability Adapter v0.1：执行信封 + ResourceUsage 账本引用
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "07_capability"))
    import adapter as _adapter
    global EXEC, USAGE_REF
    EXEC = _adapter.new_execution(_adapter.CAP_CLASSIFY)
    USAGE_REF = _adapter.USAGE_LEDGER
    _t0 = time.time()
    print(f"[adapter] execution={EXEC['execution_id']} workspace={EXEC['workspace_id']}", flush=True)

    records = [json.loads(x) for x in INPUT.open(encoding="utf-8")]
    wanted = set()
    microbe_mentions = set()
    for rec in records:
        for p in rec.get("passages", []):
            for a in p.get("annotations", []):
                inf = a.get("infons", {})
                if inf.get("database") == "ncbi_taxonomy":
                    t = str(inf.get("normalized_id", inf.get("identifier", "")))
                    if t:
                        wanted.add(t)
                        microbe_mentions.add(a.get("text", ""))
    relink = build_mention_relink(microbe_mentions)
    wanted |= {v["taxid"] for v in relink.values() if v.get("taxid")}
    taxon_names = load_taxon_names(wanted)
    taxon_ranks = load_taxon_ranks(wanted)
    is_microbe = resolve_microbial(wanted, taxon_names)
    print(f"[L0] 学名映射 {len(taxon_names)} 个 taxid（缺失 {sum(1 for v in taxon_names.values() if not v)}）；"
          f"微生物域通过 {sum(is_microbe.values())}/{len(is_microbe)}；"
          f"重链接候选 {sum(1 for v in relink.values() if v.get('taxid'))} 个学名", flush=True)

    pairs, l1_stats = build_pairs(records, args.limit_pairs, taxon_names, taxon_ranks, is_microbe, relink)
    print(f"[L1] 句子级候选 {len(pairs)} 对；过滤统计 {dict(l1_stats)}", flush=True)

    # 断点续跑：跳过已完成且非 error 的 v2 行
    rows = {f"{p['pmid']}|{p['subject']['id']}|{p['object']['id']}": None for p in pairs}
    if args.resume and OUTPUT.exists():
        for line in OUTPUT.open(encoding="utf-8"):
            try:
                prev = json.loads(line)
            except (json.JSONDecodeError, AttributeError):
                continue
            if not prev or prev.get("pipeline") != "v2":
                continue
            k = f"{prev['pmid']}|{prev['subject']['id']}|{prev['object']['id']}"
            if k in rows and prev.get("status") != "error":
                rows[k] = prev
        done = sum(v is not None for v in rows.values())
        dup_votes = sum(1 for v in rows.values()
                        if v and v.get("status") == "ok" and v.get("stage") == "1" and v.get("votes"))
        print(f"[resume] 已完成 {done}/{len(pairs)}，仅补齐其余；"
              f"duplicate-vote 风险行（stage=1 仍带 votes）={dup_votes}", flush=True)
    # v6 钉版（监工 P0-2）：运行 commit 写入 manifest，杜绝共用检出被改后版本漂移
    try:
        import subprocess as _sp
        _commit = _sp.check_output(["git", "rev-parse", "--short", "HEAD"],
                                   cwd=str(ROOT), stderr=_sp.DEVNULL).decode().strip()
    except Exception:
        _commit = "unknown"
    (ROOT / "data/logs/v6_run_manifest.json").write_text(
        json.dumps({"started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "commit": _commit, "argv": vars(args)}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    todo = [p for p in pairs if rows[f"{p['pmid']}|{p['subject']['id']}|{p['object']['id']}"] is None]
    api_calls = 0  # S1 计数（主线程，安全）
    api_calls_d = {"n": 0}
    api_lock = threading.Lock()

    # ---- 阶段1：全候选单次抽取 + L3a 确定性校验
    def stage1(pair):
        nonlocal api_calls
        try:
            res, which = classify_pair(base, key, model, args.fallback_model, pair)
            api_calls += 1
        except Exception as exc:
            api_calls += 1
            return {"pipeline": "v2", **pair, "predicate": "no_relation", "confidence": 0,
                    "polarity": "neutral", "subject_mention": "", "evidence": str(exc)[:200],
                    "status": "error", "model": model, "stage": "1"}
        ok, why = deterministic_checks(pair, res)
        status = "ok" if res["predicate"] != "no_relation" else "no_relation"
        if status == "ok" and not ok:
            status = "dropped_check"
        return {"pipeline": "v2", **pair, **res, "status": status,
                "model": model if which == "primary" else args.fallback_model,
                "stage": "1", "check_fail": why}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, (pair, row) in enumerate(zip(todo, ex.map(stage1, todo)), 1):
            rows[f"{pair['pmid']}|{pair['subject']['id']}|{pair['object']['id']}"] = row
            if i % 10 == 0 or i == len(todo):
                snapshot(list(rows.values()))
            print(f"  [S1 {i}/{len(todo)}] {pair['subject']['name']} -> {row['predicate']} -> "
                  f"{pair['object']['name']} [{row['status']}]", flush=True)
    snapshot(list(rows.values()))

    # ---- P0 预筛（第七轮校准 2026-09-30 监工裁决）：确定性拦截合生元/复合制剂 ----
    SYMBIOTIC_PAT = re.compile(
        r"\b(?:co[- ](?:administration|fermentation|supplementation|culture)|"
        r"combined\s+with|together\s+with|synbiotic[s]?|"
        r"probiotic[s]?\s*\+\s*prebiotic|"
        r"probiotic[s]?\s+and\s+prebiotic|"
        r"co[- ]administered|co[- ]supplemented|"
        r"enriched\s+system|complex\s+(?:diet|extract|food))\b", re.I)
    COMPOUND_PAT = re.compile(
        r"\b(?:protein\s+hydrolysate|extract\s+of|isolate[sd]?\s+from|"
        r"bioactive\s+compound|fraction\s+of)\b", re.I)

    def prescreen_food(pair, row):
        """确定性预筛：合生元/复合制剂 → 强制 INSUFFICIENT（不入投票池）。"""
        if row['subject'].get('category') != 'Food':
            return row
        sent = pair.get('sentence', '')
        food_name = row['subject'].get('name', '').lower()
        # 规则1：句中同时出现 prebiotic 类食物 + probiotic/菌株名 → 合生元
        if any(w in food_name for w in ('prebiotic', 'fiber', 'fos', 'gos', 'inulin')):
            if re.search(r'\b(?:probiotic|lactobacill\w+|bifidobacter\w+|streptococc\w+)\b', sent, re.I):
                if re.search(r'\b(?:co[- ]|combined|together|with|and|\+)\b', sent, re.I):
                    row['status'] = 'dropped_prescreen'
                    row['stage'] = '0'
                    row['flag'] = 'synbiotic_detection_deterministic'
                    return row
        # 规则2：合生元句式
        if SYMBIOTIC_PAT.search(sent):
            row['status'] = 'dropped_prescreen'
            row['stage'] = '0'
            row['flag'] = 'synbiotic_detection_deterministic'
            return row
        # 规则3：配料/提取物上归拦截
        if COMPOUND_PAT.search(sent) and any(w in food_name for w in ('vegetable', 'fruit', 'meat', 'fish', 'dairy')):
            row['flag'] = 'compound_ingredient_risk'
        return row

    # ---- 阶段2：正向边 k=3@T=0.7 投票（已有 1 票，补 2 票；仅处理未投过票的 stage1 行）
    vote_targets = [(p, r) for p in pairs
                    for r in [rows[f"{p['pmid']}|{p['subject']['id']}|{p['object']['id']}"]]
                    if r and r["status"] == "ok" and r.get("stage") == "1"]

    def vote(pair, row):
        # S2 修复（s2_fix_plan #2/#3）：工作线程只改副本，主线程单点赋值；
        # api_err>=2 保持 stage="1" 待重投（原实现与注释相反：stage=2+单票会
        # 绕过 k=3 直接进 judge 且重启不重投）；持久化 s2_attempts 计数
        with api_lock:
            api_calls_d["n"] += 1
        new = {**row}
        votes, api_err = [new["predicate"]], 0
        for _ in range(2):
            try:
                res, _ = classify_pair(base, key, model, args.fallback_model, pair)
                votes.append(res["predicate"])
            except Exception:
                api_err += 1
            finally:
                with api_lock:
                    api_calls_d["n"] += 1
        new["s2_attempts"] = new.get("s2_attempts", 0) + 1
        top, n = Counter(votes).most_common(1)[0]
        if n >= 2 and top != "no_relation":
            new["status"], new["stage"], new["votes"] = "ok", "2", votes
        elif n >= 2 and top == "no_relation":
            new["status"], new["stage"], new["predicate"] = "no_relation", "2", "no_relation"
            new["votes"] = votes
        elif api_err >= 2:
            # 两票均因 API 失败缺失：保持 stage="1"（重启自动重投，不进 judge）
            new["stage"] = "1"
            new["vote_api_err"] = api_err
        else:
            new["status"], new["stage"], new["votes"] = "dropped_vote", "2", votes
        return new

    if vote_targets:
        print(f"[L3b] 对 {len(vote_targets)} 条正向边补充投票", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [(p, ex.submit(vote, p, r)) for p, r in vote_targets]
            for i, (p, fut) in enumerate(futures, 1):
                row = fut.result()
                rows[f"{p['pmid']}|{p['subject']['id']}|{p['object']['id']}"] = row
                if i % 10 == 0 or i == len(futures):
                    snapshot(list(rows.values()))  # S2 修复：增量落盘（每 10 条，与 S1 同法）
                print(f"  [S2 {i}/{len(futures)}] {row.get('votes')} -> {row['status']}", flush=True)
        snapshot(list(rows.values()))

    # ---- 阶段3：跨架构 judge（仅处理投过票未判的 stage2 行）
    judge_targets = [(p, r) for p in pairs
                     for r in [rows[f"{p['pmid']}|{p['subject']['id']}|{p['object']['id']}"]]
                     if r and r["status"] == "ok" and r.get("stage") == "2"]

    def judge(pair, row):
        # S3 修复（s2_fix_plan #2）：副本改写 + 线程安全计数
        with api_lock:
            api_calls_d["n"] += 1
        new = {**row}
        verdict = judge_triple(base, key, args.judge_model, pair, new["predicate"])
        new["judge"] = verdict
        if verdict.get("verdict") == "SUPPORTED" and verdict.get("subject_binding_ok"):
            new["status"], new["stage"] = "ok", "3"
        else:
            new["status"], new["stage"] = "dropped_judge", "3"
        return new

    if judge_targets:
        print(f"[L3c] 对 {len(judge_targets)} 条投票存活边执行 {args.judge_model} judge", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = [(p, ex.submit(judge, p, r)) for p, r in judge_targets]
            for i, (p, fut) in enumerate(futures, 1):
                row = fut.result()
                rows[f"{p['pmid']}|{p['subject']['id']}|{p['object']['id']}"] = row
                if i % 10 == 0 or i == len(futures):
                    snapshot(list(rows.values()))  # S3 修复：增量落盘
                print(f"  [S3 {i}/{len(futures)}] {row['judge'].get('verdict')} "
                      f"binding={row['judge'].get('subject_binding_ok')} -> {row['status']}", flush=True)
        snapshot(list(rows.values()))

    # ===== v7 P0 确定性否决（第八轮校准 2026-09-30 监工裁决）=====
    # 否定/非显著限定词：含有这些词的证据句不得保留为 ok 边（彻底丢弃——用户拍板#3）
    NEGATION_MARKERS = re.compile(
        r"\b(?:not\s+significant|failed\s+to|no\s+significant\s+change|"
        r"limited\s+effect|tended\s+to|P\s*[<>=]\s*0\.[01]\b|"
        r"non[- ]significant|marginally\s+significant|"
        r"did\s+not\s+(?:significantly\s+)?(?:alter|change|affect|modify)|"
        r"no\s+(?:significant\s+)?(?:difference|effect|change|impact))\b", re.I)

    # 关联措辞降级：evidence 含这些词时，强谓词一律降为 affects
    ASSOCIATION_MARKERS = re.compile(
        r"\b(?:associated\s+with|correlated\s+with|linked\s+to|"
        r"negative\s+influence|positive\s+influence|inversely\s+associated)\b", re.I)

    # 复合暴露检测（不可归给单一食物组）
    COMPOSITE_MARKERS = re.compile(
        r"\b(?:and\s+beans|fish,\s+beans|multiple\s+diet|"
        r"dietary\s+index|diet\s+index|"
        r"compared\s+to\s+(?:the\s+)?other|"
        r"two\s+carbon\s+sources|versus\s+\w+\s+alone)\b", re.I)

    def v7_deterministic_veto(rows_list):
        """v7 P0 三规则：否定丢弃 / 关联降级 / 复合不产边。"""
        n_neg = n_dem = n_comp = 0
        for r in rows_list:
            if r is None or r.get('status') != 'ok':
                continue
            sent = r.get('sentence', '') or ''
            ev = r.get('evidence', '') or ''
            text = sent + ' ' + ev

            # 规则1：否定/非显著 → 彻底丢弃
            if NEGATION_MARKERS.search(text):
                r['status'] = 'dropped_negation'
                r['stage'] = '8'
                r['flag'] = 'negation_nonsignificant_deterministic'
                n_neg += 1
                continue

            # 规则2：复合暴露 → 不产边
            if r['subject'].get('category') == 'Food' and COMPOSITE_MARKERS.search(sent):
                r['status'] = 'dropped_composite'
                r['stage'] = '8'
                r['flag'] = 'composite_exposure_deterministic'
                n_comp += 1
                continue

            # 规则3：关联措辞 + 强谓词 → 降级为 affects
            if r['subject'].get('category') == 'Food' and ASSOCIATION_MARKERS.search(ev):
                if r.get('predicate') in ('promotes_growth', 'inhibits_growth'):
                    r['predicate'] = 'affects'
                    r['flag'] = 'association_wording_downgraded'
                    n_dem += 1

        return n_neg, n_dem, n_comp

    n_veto = v7_deterministic_veto(list(rows.values()))
    print(f"[v7-veto] 否定丢弃 {n_veto[0]} | 复合不产边 {n_veto[2]} | 关联降级 {n_veto[1]}")

    n_demoted = demote_transform_conflicts(list(rows.values()))
    n_indirect = demote_indirect_mechanism(list(rows.values()))
    if n_demoted or n_indirect:
        snapshot(list(rows.values()))
        print(f"[L3d] 转化冲突降级 produces {n_demoted} 条；间接机制降级 {n_indirect} 条", flush=True)

    final = [r for r in rows.values() if r]
    st = Counter(r["status"] for r in final)
    ok_rows = [r for r in final if r["status"] == "ok"]
    print(f"[out] 候选 {len(final)}；状态 {dict(st)}；正向存活 {len(ok_rows)}；API 调用 {api_calls + api_calls_d['n']}", flush=True)
    print(f"[pred] {dict(Counter(r['predicate'] for r in ok_rows))}", flush=True)
    print(f"[out] -> {OUTPUT}", flush=True)

    # Capability Adapter v0.1：ResourceUsage 记账（token 维度当前中转未回报，
    # 如实记 0 并标注；API 调用数为真实计数）
    _adapter.append_usage(
        EXEC, model_calls=int(api_calls), external_api_calls=0,
        wall_duration_ms=(time.time() - _t0) * 1000,
        note=f"pairs={len(final)}; ok={len(ok_rows)}; "
             f"tokens=0(unreported-by-relay); judge={args.judge_model}")


if __name__ == "__main__":
    main()
