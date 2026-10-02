#!/usr/bin/env python3
"""Contextual Divergence Review v0.2 + RelationAssertion 生成（裁决 2026-09-25 之十项）。

核心原则：context 不是关系的附加 metadata，context 是 assertion 成立条件的
一部分。知识单位 = Entity + Predicate + Target + Context + Evidence + Provenance。

产出（data/merged/）：
- conflicts_review.tsv     情境分歧审阅表（v0.4：evidence-aware context + gate 派生可比性）
- contextual_divergence_summary.json  五类 context 指标 + 分类/解决完备度 + backlog（规范化计数）
- relation_assertions.tsv  RelationAssertion 一等知识对象（canonical relation 之外的事实层）
- pending_review_sample / high_degree_report（既有抽检口径）

Evidence-aware context（裁决 3/4）：每个维度 {value, status: explicit|inferred|unknown,
source}——证据不足禁止补全，缺失显式 unknown；只有 evidence-backed 维度参与
可比性判断。Comparability Gate（裁决 5）：关键维度全匹配才 comparable；任何
关键维度 unknown → partially_comparable（不得"看起来一样"判 comparable）；
关键维度已知但不匹配 → incomparable；comparable 且方向相反才可能
true_biological_conflict（且不进入 canonical summary，裁决 8）。
"""
from __future__ import annotations

import json
import random
import re
from datetime import date
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "data/staging/llm_relations.jsonl"
MERGED = Path(os.environ.get("KG_MERGED_DIR", str(ROOT / "data/merged")))  # C2d：可指 candidate_v2（重算目标）
random.seed(20260925)

#: 核心情境维度（裁决 3）
CONTEXT_DIMS = ("strain", "host_species", "host_population", "geography",
                "disease", "disease_subtype", "disease_stage", "diet",
                "intervention", "dose", "experimental_model", "study_type",
                "endpoint", "timepoint", "anatomical_site")
#: 可比性判定的关键维度（裁决 5 + E：anatomical_site）
KEY_DIMS = ("strain", "host_species", "disease", "anatomical_site",
            "experimental_model", "study_type", "endpoint", "intervention")

#: 实体粒度不适用于所有 subject（C：applicable 判定）
_IN_VITRO_MARKERS = ("in vitro", "organoid", "cell line")

#: 泛化/欠具体触发 token（1A 收口）：允许作 evidence signal，
#: 但不得单独确认 context match（"60 mg/kg" vs "100 mg/kg" 不能因都是 mg 而 match）
_GENERIC_VALUES = frozenset({"strain", "treated", "intake", "supplementation",
                             "probiotic", "mg", "dose", "cfu", "g/kg", "after",
                             "trial", "randomized", "derived from", "isolate", "administration"})

#: 1B 收口（2026-09-29 监工令 P0-2）：疾病 object 的同义/缩写形式族——
#: target≠background 过滤此前只排除与 object 名同形的词，同义词族全漏
#: （batch1 实证：object=Neoplasms 时 tumorigenesis/cancer/carcinoma 泄入
#: disease context；object=IBD 时 inflammatory bowel disease 泄入）。
_DISEASE_SYNONYM_FAMILY = {
    "neoplasm": {"cancer", "cancers", "tumor", "tumors", "tumour", "tumours",
                 "tumorigenesis", "tumorogenesis", "carcinogenesis", "carcinoma",
                 "carcinomas", "neoplasia", "neoplasias", "neoplastic",
                 "oncogenesis", "malignancy", "malignancies", "malignant",
                 "metastasis", "metastases", "metastasize"},
    "inflammatory bowel disease": {"ibd", "inflammatory bowel disease"},
    "colitis": {"colitis", "colitides"},
    "obesity": {"obesity", "obese"},
    "diabetes": {"diabetes", "diabetic", "diabetics"},
}


def _object_disease_forms(object_name: str) -> set:
    """object 疾病概念的全部表面形式（自身词 + 同义族 + 多词首字母缩写）。"""
    if not object_name:
        return set()
    low = object_name.lower()
    forms = {w for w in low.replace(",", " ").split() if len(w) > 3}
    for concept, family in _DISEASE_SYNONYM_FAMILY.items():
        concept_words = set(concept.split())
        if (forms & concept_words) or any(f in low for f in family) \
                or any(w in concept or concept in w for w in forms):
            forms |= family
    words = [w for w in low.replace(",", " ").split() if len(w) > 2 and w.isalpha()]
    if len(words) >= 2:
        forms.add("".join(w[0] for w in words))
    return forms


#: P0-2 收口（2026-09-29）：描述菌来源/栖息地的定语不是研究宿主——
#: "human commensal/intestinal/gut/symbiotic/oral/stomach/nasal microbiome"、
#: "murine-specific pathogen" 等短语中的物种词描述菌株生态位，不得据此确认
#: host_species（batch1 一审实证 #13/#23/#25/#27/#29/#31）。
_HOST_EPITHET_RE = re.compile(
    r"\b(human|murine|mouse)\s*[-\s]?\s*(?:[a-z\-]+\s+){0,3}"
    r"(commensal|intestinal|gut|symbiotic|associated|derived|specific|"
    r"oral|stomach|nasal|skin|microbiome|microbiota|colonizes|cells?|"
    r"tissues?|cell lines?)\b")

#: 终审缺陷类 4（2026-09-29）："isolated from … patients" 类来源归属——
#: 菌株来源宿主不是研究宿主（终审实证 #36）。
_ISOLATED_FROM_RE = re.compile(
    r"isolated\s+from[^.\n]{0,60}?\b(patients|humans|human|mice|rats|children)\b")

#: 终审缺陷类 2：结局动词语境中的共享词表 token（disease∩endpoint）不是背景病
#: （"leading to increased inflammation" / "inflammation elicited by"，终审实证 #21/#27）。
_OUTCOME_PRE_RE = re.compile(
    r"\b(?:leading to|leads to|resulting in|increased|decreased|reduced|elevated|"
    r"promotes?|promoted|induces?|induced|triggers?|triggered|causes?|caused|"
    r"drives?|counteract|dampens?|dampened|alleviates?|ameliorates?|attenuates?|"
    r"improves?|reduces?|suppresses?|prevents?)\s+(?:an?\s+)?(?:increased\s+|"
    r"decreased\s+|reduced\s+|elevated\s+)?([a-z\-]+)")
_OUTCOME_POST_RE = re.compile(
    r"\b([a-z\-]+)\s+(?:elicited|induced|triggered|observed)\s+by\b")

#: 终审缺陷类 3：给药途径不是解剖部位（"oral administration"，终审实证 #9/#34）。
#: 终审 v3 缺陷类（2026-09-29）：他实体名称碎片与结局修饰词不得作 disease_stage——
#: "acute-phase proteins"、"severe acute respiratory syndrome"、"more severe inflammation"。
_STAGE_NAME_FRAGMENT_RE = re.compile(
    r"\b(severe|acute|mild|early|late|chronic)[\s-]*(phase|acute respiratory syndrome|"
    r"phase proteins?|care|onset)")
_STAGE_OUTCOME_MODIFIER_RE = re.compile(
    r"\b(more|less|increased|decreased|significantly)\s+(severe|mild|acute|chronic)\b")


def _disease_stage_hit(evidence_text: str):
    """stage 维度专用命中：剔除名称碎片与结局修饰词用法。"""
    found = _hit(evidence_text, "disease_stage")
    if not found:
        return None
    low = evidence_text.lower()
    bad = set()
    _stage_words = ("severe", "acute", "mild", "early", "late", "chronic", "advanced")
    for m in _STAGE_NAME_FRAGMENT_RE.finditer(low):
        bad.add(m.group(1))
        span_txt = m.group(0)
        bad |= {w for w in _stage_words if w in span_txt}  # 短语内部的分期词一并剔除
    for m in _STAGE_OUTCOME_MODIFIER_RE.finditer(low):
        bad.add(m.group(2))
    if bad:
        found = [f for f in found if f.lower() not in bad] or None
        if not found:
            return None
    # 二次终审缺陷类 B：分期词邻接非病理中心词（microbiota recovery /
    # chronic nitric oxide blockade / chronic cocaine use）→ 不作 disease_stage
    _non_patho = ("microbiota", "recovery", "blockade", "cocaine", "nitric",
                  "oxide", "use", "consumption", "intake", "exposure",
                  "treatment", "administration", "care", "onset", "phase",
                  "stationary", "proteins", "protein", "syndrome")
    kept = []
    for f in (found or []):
        occ = list(re.finditer(rf"\b{re.escape(f.lower())}\b", low))
        bad_occ = 0
        for m in occ:
            window = low[max(0, m.start()-25):m.end()+25].split()
            if any(h in window for h in _non_patho):
                bad_occ += 1
        if bad_occ < len(occ):
            kept.append(f)
    # G5 P0-3（监工）：中心词约束——形容词型分期词右侧 0~2 token 的中心词必须属
    # disease 词表；endpoint 独有词（inflammation 等病理过程）与 stage of 非疾病宾语均剔除。
    _D = {w.lower() for w in _EXPLICIT.get("disease", [])}
    _EP = {w.lower() for w in _EXPLICIT.get("endpoint", [])}  # 含双表词（inflammation）：作分期中心词时一律视为病理过程（监工 G5 E5）
    def _center_ok(sw):
        m2 = re.search(rf"\b{re.escape(sw)}\s+stage\s+of\s+([a-z\-]+)", low)
        if m2:
            return m2.group(1) in _D
        mm = re.search(rf"\b{re.escape(sw)}\s+((?:[a-z\-]+\s+){{0,2}}[a-z\-]+)", low)
        if mm:
            words = mm.group(1).split()
            if words[0] in _EP:           # 紧邻病理过程词（chronic inflammation）→ 剔除
                return False
            if any(w in _D or w in ("disease", "diseases") for w in words):
                return True               # 窗口内含疾病中心词（advanced colorectal cancer）
            return False
        return True
    _ADJ = {"severe", "acute", "mild", "early", "late", "chronic", "advanced"}
    kept = [f for f in kept if f.lower() not in _ADJ or _center_ok(f.lower())]
    return kept or None


_ROUTE_PHRASE_RE = re.compile(
    r"\b(oral|intragastric|intravenous|subcutaneous|topical|nasal)\s+"
    r"(administration|gavage|dosing|delivery|supplementation)\b")


def _host_species_hit(evidence_text: str):
    """host_species 专用命中：按 token 剔除定语/来源归属用法（该 token 的全部
    出现均为定语/归属 → 从命中中移除）。"""
    found = _hit(evidence_text, "host_species")
    if not found:
        return None
    low = evidence_text.lower()
    attributed = {}
    for m in _HOST_EPITHET_RE.finditer(low):
        attributed[m.group(1)] = attributed.get(m.group(1), 0) + 1
    for m in _ISOLATED_FROM_RE.finditer(low):
        attributed[m.group(1)] = attributed.get(m.group(1), 0) + 1
    if not attributed:
        return found
    kept = []
    for f in found:
        fl = f.lower()
        n_total = len(re.findall(rf"\b{re.escape(fl)}\b", low))
        if n_total > attributed.get(fl, 0):
            kept.append(f)
    return kept or None


def _anatomical_site_hit(evidence_text: str):
    """anatomical_site 专用命中：给药途径短语中的部位词不计（全部出现均为途径 → 剔除）。"""
    found = _hit(evidence_text, "anatomical_site")
    if not found:
        return None
    low = evidence_text.lower()
    route_words = [m.group(1) for m in _ROUTE_PHRASE_RE.finditer(low)]
    _site_epithet_re = re.compile(
        r"\b(gut|skin|oral|intestinal|colonic|nasal|periodontal|fecal)\s+"
        r"(?:[a-z\-]+\s+){0,2}(commensal|bacterium|bacteria|bacterial|species|taxa|microbiota|"
        r"microbiome|pathobiont|symbiont|inhabitant)\b")
    ep_sites = [m.group(1) for m in _site_epithet_re.finditer(low)]
    # C2b 规则1（2026-10-01 监工G3）：部位词后紧邻疾病/过程名词（amod 修饰）时视为
    # 修饰成分不产独立部位——"intestinal inflammation / colonic damage / hepatic injury"
    _site_disease_mod_re = re.compile(
        r"\b(gut|intestinal|colonic|colon|hepatic|renal|gastric|pulmonary|"
        r"liver|lung|brain|joint|oral|nasal|periodontal)\s+"
        r"(?:[a-z\-]+\s+){0,2}(inflammation|inflammatory|damage|injury|disease|"
        r"diseases|colitis|cancer|carcinoma|tumor|tumorigenesis|fibrosis|necrosis|"
        r"steatosis|dysbiosis|barrier|severity|progression|pain|failure)\b")
    dis_mod_sites = {m.group(1) for m in _site_disease_mod_re.finditer(low)}
    kept = []
    for f in found:
        fl = f.lower()
        n_att = (sum(1 for w in route_words if w == fl) + sum(1 for w in ep_sites if w == fl)
                 + (1 if fl in {d.lower() for d in dis_mod_sites} else 0))  # C2b 规则1：疾病修饰词计数
        n_total = len(re.findall(rf"\b{re.escape(fl)}\b", low))
        if n_total > n_att:
            kept.append(f)
    return kept or None


def _disease_background_hit(evidence_text: str, object_name: str):
    """disease 维度专用命中：1B 同义族 + object 名碎片 + 结局语境三重过滤。"""
    found = _hit(evidence_text, "disease")
    if not found:
        return None
    low = evidence_text.lower()
    if object_name:
        obj_forms = _object_disease_forms(object_name)
        found = [f for f in found
                 if not any(of in f.lower() or f.lower() in of for of in obj_forms)] or None
        if not found:
            return None
    outcome_tokens = {m.group(1) for m in _OUTCOME_PRE_RE.finditer(low)}
    outcome_tokens |= {m.group(1) for m in _OUTCOME_POST_RE.finditer(low)}
    if outcome_tokens:
        found = [f for f in found if f.lower() not in outcome_tokens] or None
    return _hyphen_compound_hit(evidence_text, found) if found else found

_EXPLICIT = {
    "strain": ["MMX", "MRE 600", "ETBF", "NTBF", "pks+", "K-12", "Nissle",
               "engineered", "strain", "isolate", "clone", "derived from",
               "genotypes", "CD-SpA"],
    "host_species": ["mice", "mouse", "murine", "human", "patients", "rats",
                     "children", "adults"],
    "experimental_model": ["DSS", "AOM", "CAC", "EAE", "in vitro", "organoid",
                           "cell line", "HCT-116", "HT-29", "gnotobiotic",
                           "germ-free"],
    "study_type": ["cohort", "RCT", "randomized", "trial", "cross-sectional",
                   "case-control"],
    "disease_stage": ["early", "late", "advanced", "mild", "severe", "recovery",
                      "chronic", "acute"],
    "diet": ["diet", "dietary", "fiber", "inulin", "FOS", "GOS", "high-fat",
             "western diet"],
    "intervention": ["supplementation", "administration", "treated",
                     "supplemented", "gavage", "intake", "probiotic"],
    "geography": ["chinese", "china", "european", "japanese", "korean",
                  "african", "indian"],   # population≠geography（precision 一审移除）
    # C2b 规则2（2026-10-01 监工G3）：endpoint=过程指标值域，疾病词 colitis/tumor 移除（归 disease 维度）
    "endpoint": ["inflammation", "tumorigenesis", "barrier", "proliferation",
                 "survival", "dysbiosis", "severity", "damage", "injury",
                 "carcinogenesis", "oncogenesis", "metastasis", "steatosis",
                 "fibrosis", "necrosis", "progression", "permeability"],
    "timepoint": ["weeks", "days", "months", "hours", "after"],
    "dose": ["mg", "g/kg", "dose", "cfu"],
    "host_population": [],   # 无显式词表——默认 unknown（禁止模型补全）
    "anatomical_site": ["gut", "intestinal", "colonic", "colon", "liver",
                        "hepatic", "skin", "airway", "lung", "periodontal",
                        "oral", "systemic", "blood", "brain", "joint"],
    "disease": ["colitis", "cancer", "carcinoma", "tumor", "tumorigenesis",
                "inflammation", "IBD", "dermatitis", "obesity", "diabetes",
                "NASH", "NAFLD", "arthritis", "encephalomyelitis", "neoplasia"],
    "disease_subtype": ["ulcerative", "crohn", "collagenous", "CAC", "NASH",
                        "NAFLD", "atopic", "collitis-associated",
                        "colitis-associated", "autoimmune", "hepatocellular"],
}

#: object 侧粗粒度本体 gap（F：不假设粒度问题只在 microbe/food 侧）
OBJECT_SIDE_GAPS = {
    "MESH:D007249": {"entity_granularity": "inflammation_to_anatomical"},
    "MESH:D009369": {"entity_granularity": "cancer_to_specific_cancer"},
    "MESH:D015179": {"entity_granularity": "cancer_to_specific_cancer"},
}


def _hit(text: str, dim: str):
    t = text.lower()
    import re
    out = []
    for k in _EXPLICIT.get(dim, []):
        kl = k.lower()
        # 词边界匹配（precision 一审：子串匹配致 translated→late 类假阳性）
        if re.search(rf"(?<![a-z0-9]){re.escape(kl)}(?![a-z0-9])", t):
            out.append(k)
    return out or None


def _hyphen_compound_hit(evidence_text: str, found):
    """二次终审缺陷类 C：连字符复合词拆碎片（colitis-associated→colitis、
    blood-brain→blood）。token 的全部出现均在连字符复合词内 → 剔除。"""
    if not found:
        return found
    low = evidence_text.lower()
    kept = []
    for f in found:
        fl = re.escape(f.lower())
        n_total = len(re.findall(rf"(?<![a-z0-9]){fl}(?![a-z0-9])", low))
        n_compound = len(re.findall(rf"(?<=[a-z]-){fl}(?![a-z0-9])|(?<![a-z0-9-]){fl}(?=-[a-z])", low))
        if n_total > n_compound:
            kept.append(f)
        return kept or None


_TITLE_IDX: dict[str, str] = {}

def _pmid_title(pmid) -> str:
    """pmid→PubMed title（articles.jsonl passages[type=title]懒加载索引，监工 G4b 拍板#1）。"""
    if not _TITLE_IDX:
        try:
            with (ROOT / "data/pubtator/articles.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    try: a = json.loads(line)
                    except json.JSONDecodeError: continue
                    for p in a.get("passages", []):
                        if p.get("infons", {}).get("type") == "title":
                            _TITLE_IDX[str(a.get("pmid", a.get("id", "")))] = p.get("text", "")
                            break
        except FileNotFoundError:
            pass
    return _TITLE_IDX.get(str(pmid), "")

def build_context(evidence_text: str, object_id: str, subject_id: str = "", object_name: str = "", pmid: str = "") -> dict:
    """Evidence-aware context v0.5：每维 {value,status,source,applicable,unknown_reason}。

    C：unknown 细分 applicable/unknown_reason（in vitro 的 geography 与
    human cohort 未采样不是同一种 unknown）；D：object 实体身份 =
    structured_metadata（可确认 match），非 inferred。
    """
    ctx = {}
    t = evidence_text.lower()
    for dim in CONTEXT_DIMS:
        if dim == "geography":
            # C2b 规则3 v2（监工 G4b：先适用性→真实 title 字段判定，弃字符位置启发）
            ok, reason = _applicability(dim, subject_id, evidence_text)
            if not ok:
                ctx[dim] = {"value": "", "status": "not_applicable", "source": "",
                            "applicable": False, "unknown_reason": reason or "in_vitro"}
                continue
            _body_hits = _hit(evidence_text, "geography")
            if _body_hits:
                ctx[dim] = {"value": ",".join(_body_hits[:2]), "status": "explicit",
                            "source": "abstract_sentence", "applicable": True,
                            "unknown_reason": ""}
            else:
                _title = _pmid_title(pmid) if pmid else ""
                _title_only = bool(_title and _hit(_title, "geography"))
                ctx[dim] = {"value": "", "status": "unknown", "source": "",
                            "applicable": True,
                            "unknown_reason": ("geography_title_only" if _title_only
                                                else "not_present_in_available_evidence")}
            continue
        if dim == "disease":
            # 契约修正（裁决第 1 项）+ 终审缺陷类 1/2（2026-09-29）：disease background
            # 三重过滤——1B 同义族 + object 名碎片 + 结局动词语境（共享词表 token
            # 在 "leading to increased X"/"X elicited by" 中是结局不是背景病）。
            found = _disease_background_hit(evidence_text, object_name)
            if found:
                ctx[dim] = {"value": ",".join(found[:2]), "status": "explicit",
                            "source": "abstract_sentence", "applicable": True,
                            "unknown_reason": ""}
            else:
                ctx[dim] = _unknown(True, "not_present_in_available_evidence")
            continue
        found = _hit(evidence_text, dim)
        if dim == "host_species":
            found = _host_species_hit(evidence_text)
        elif dim == "anatomical_site":
            # 终审缺陷类 3 + 二次终审#16：给药途径与栖息地定语（gut commensal X）
            found = _anatomical_site_hit(evidence_text)
        elif dim == "disease_stage":
            found = _disease_stage_hit(evidence_text)
        if found:
            # 二次终审缺陷类 C：连字符复合词碎片（全出现均在复合词内 → 剔除）
            found = _hyphen_compound_hit(evidence_text, found)
        if dim == "disease_subtype" and found and \
                ctx.get("disease", {}).get("status") not in ("explicit", None):
            # 悬挂 subtype：父疾病维度未确认（unknown）时降 inferred，避免无父孤悬
            ctx[dim] = {"value": ",".join(found[:2]), "status": "inferred",
                        "source": "dangling_without_parent_disease", "applicable": True,
                        "unknown_reason": "parent_disease_unknown"}
            continue
        elif dim in ("disease_subtype", "disease_stage") and object_name:
            # 终审缺陷类 1：object 名碎片从 subtype/stage 维度漏入
            # （object="Crohn Disease"→subtype 不得取 crohn；"Liver Failure, Acute"→stage 不得取 acute）
            own = {w for w in object_name.lower().replace(",", " ").split() if len(w) > 3}
            if found:
                found = [f for f in found
                         if not any(ow in f.lower() or f.lower() in ow for ow in own)] or None
        if found:
            # 1A 收口（P0-2）：命中全部为泛化 token → 存为 inferred signal，
            # 不得标 explicit（batch1 实证 strain="strain" 以 explicit 入库）
            if {f.lower() for f in found} <= _GENERIC_VALUES:
                ctx[dim] = {"value": ",".join(found[:2]), "status": "inferred",
                            "source": "abstract_signal", "applicable": True,
                            "unknown_reason": "generic_token_only"}
            else:
                ctx[dim] = {"value": ",".join(found[:2]), "status": "explicit",
                            "source": "abstract_sentence", "applicable": True,
                            "unknown_reason": ""}
        else:
            applicable, reason = _applicability(dim, subject_id, t)
            ctx[dim] = _unknown(applicable, reason)
    return ctx


def _unknown(applicable: bool, reason: str) -> dict:
    return {"value": "", "status": "unknown",
            "source": "",
            "applicable": applicable,
            "unknown_reason": reason if applicable else "not_applicable"}


def _applicability(dim: str, subject_id: str, text: str) -> tuple[bool, str]:
    """维度适用性（确定性规则；in vitro 的 population = not_applicable）。"""
    if dim == "strain":
        if subject_id.startswith("LFS:FOOD"):
            return False, ""
        return True, "not_present_in_available_evidence"
    if dim in ("host_population", "geography"):
        if any(m in text for m in _IN_VITRO_MARKERS):
            return False, ""
        return True, "not_present_in_available_evidence"
    if dim == "dose":
        if _hit(text, "intervention"):
            return True, "not_present_in_available_evidence"
        return False, ""
    return True, "not_present_in_available_evidence"


def context_completeness(ctx: dict) -> float:
    """覆盖率分母 = applicable 维度（C：固定 13/15 维会人为放大 missingness）。"""
    applicable = [d for d in CONTEXT_DIMS if ctx[d].get("applicable", True)]
    known = sum(1 for d in applicable if ctx[d]["status"] != "unknown")
    return round(known / max(len(applicable), 1), 4)


def comparability_gate(ctx_a: dict, ctx_b: dict) -> tuple[str, str]:
    """关键维度门 v0.5（D）：inference 可以增加怀疑，不能制造确定性。

    explicit / structured_metadata 双侧在场 → 可确认 match/mismatch；
    inferred 只作 soft——可维持 partially 或提示检查，不得单独升级 comparable。
    """
    basis, incomparable, blocked = [], False, False
    for d in KEY_DIMS:
        a, b = ctx_a[d], ctx_b[d]
        if a["status"] == "unknown" or b["status"] == "unknown":
            if not (a.get("applicable", True) and b.get("applicable", True)):
                basis.append(f"{d}:not_applicable")
                continue
            blocked = True
            basis.append(f"{d}:unknown")
            continue
        confirmable = all(x["status"] in ("explicit",)
                          or x.get("source") == "structured_metadata"
                          for x in (a, b))
        sa = {x.strip().lower() for x in a["value"].split(",") if x.strip()}
        sb = {x.strip().lower() for x in b["value"].split(",") if x.strip()}
        matched = sa & sb
        generic_only = bool(matched) and matched <= _GENERIC_VALUES
        if matched and generic_only:
            # 1A：交集仅含泛化 token → evidence signal，不确认 match
            basis.append(f"{d}:generic_signal({','.join(sorted(matched))})")
            blocked = True
        elif matched:
            basis.append(f"{d}:match" if confirmable else f"{d}:soft_match(inferred)")
            if not confirmable:
                blocked = True   # soft 不能升级 comparable
        else:
            incomparable = True
            basis.append(f"{d}:mismatch" if confirmable else f"{d}:soft_mismatch(inferred)")
    status = ("incomparable" if incomparable
              else "partially_comparable" if blocked else "comparable")
    return status, ";".join(basis)


def load_evidence_index():
    """证据索引（v0.6 修复：key 必须含 object——缺维会把同 PMID 其他对象的
    证据错配显示，曾导致抽检表 [94] 的假性实体错配）。"""
    idx = {}
    with STAGING.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "ok" or r.get("predicate") == "no_relation":
                continue
            key = (r["subject"]["id"], r["predicate"], r["object"]["id"],
                   str(r.get("pmid", "")))
            if key not in idx or float(r.get("confidence", 0)) > float(idx[key].get("confidence", 0)):
                idx[key] = r
    return idx


def side_ev(idx, sid, pred, oid, pmids: str):
    out = []
    for pmid in [p for p in pmids.split(";") if p]:
        r = idx.get((sid, pred, oid, pmid))
        if r:
            out.append(f"[{pmid}] {r.get('evidence') or r.get('sentence', '')[:160]}")
    return " || ".join(out) if out else ""


#: span 规范化契约（裁决第 4 项，冻结）：小写折叠 + 全部 Unicode 空白（含
#: 换行/制表）折叠为单空格 + 首尾去除；不做 NFKC、不去标点、不剥引用标记、
#: 不做句子边界切分；基底 = evidence + sentence 拼接（0.1→0.2 基底变更）。
#: 变更此算法必须 bump 版本并迁移 assertion_id。
SPAN_NORMALIZATION_VERSION = "norm/0.8-c2b-round2"  # C2b 规则升级：amod修饰过滤/endpoint值域去疾病词/title地理过滤（0.6 基线之上，监工G3）


def _norm_span(text: str) -> str:
    return " ".join((text or "").lower().split())


def _content_id(subject: str, predicate: str, object_: str,
                pmid: str, span: str) -> str:
    """内容寻址稳定 ID（B）：sha256(s·p·o·pmid·normalized_span)——
    resume/replay/rerun 后同一证据单元同 identity；execution_id 只进 provenance。"""
    import hashlib
    canon = json.dumps([subject, predicate, object_, pmid, _norm_span(span)],
                       ensure_ascii=False, separators=(",", ":"))
    return "RA-" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:20]


def iter_atomic_ok_records():
    """A：原子单位 = subject+predicate+object+PMID+evidence span。

    直接遍历 staging ok 行（不经 best-per-key 索引——那会吞掉同 PMID 的
    其他 object/span，重新制造 context collapse）。"""
    seen_spans = set()
    with STAGING.open(encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("status") != "ok" or r.get("predicate") == "no_relation":
                continue
            span = _norm_span(f"{r.get('evidence') or ''} {r.get('sentence') or ''}")
            key = (r["subject"]["id"], r["predicate"], r["object"]["id"],
                   str(r.get("pmid", "")), span)
            if key in seen_spans:   # recovery 重跑产生的重复原子 → 幂等去重
                continue
            seen_spans.add(key)
            yield r, span


def build_relation_assertions(conflicted_pairs: set) -> dict:
    """RelationAssertion v0.5（A/B）：evidence-level atomic unit + 稳定 identity。"""
    import hashlib
    holds = {}
    hold_path = MERGED / "manual_hold.tsv"
    if hold_path.exists():
        import csv
        with hold_path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                obj_id, _, pmid = row["object_pmid"].partition("@")
                holds[(row["subject"], row["predicate"], obj_id, pmid)] = row.get("hold_reason", "")
    rows, ids, full_span_ids = [], set(), []
    for r, span in iter_atomic_ok_records():
        sid, oid = r["subject"]["id"], r["object"]["id"]
        pmid = str(r.get("pmid", ""))
        aid = _content_id(sid, r["predicate"], oid, pmid, span)
        full_span_ids.append(aid)   # 完整 span 派生（列仅展示截断，不入 ID）
        ids.add(aid)
        div = "contextual_divergence_pending" if (sid, oid) in conflicted_pairs else ""
        # 口径统一：context 抽取文本 == ID 的 span 基 == 存储列（evidence+sentence 拼接）
        text = f"{r.get('evidence') or ''} {r.get('sentence') or ''}"
        ctx = build_context(text, oid, sid, str(r.get("object", {}).get("name", "")), str(pmid))
        rows.append({
            "assertion_id": aid,
            "subject": sid, "predicate": r["predicate"], "object": oid,
            "direction": r.get("polarity", "neutral"),
            "confidence": r.get("confidence", ""),
            "evidence_pmid": pmid,
            "evidence_span_norm": span[:1000],  # 完整 span（ID 始终用完整 span；此列供 precision QC 核验）
            "provenance": json.dumps({
                "execution_id": r.get("execution_id", ""),
                "capability_id": r.get("capability_id", ""),
                "source": "pubtator3@2026-09", "pipeline": "llm_extract_v2",
                "created_at": r.get("created_at", "")}, ensure_ascii=False),
            "context": json.dumps(ctx, ensure_ascii=False),
            "context_completeness": context_completeness(ctx),
            "divergence": div,
            "manual_hold": holds.get((sid, r["predicate"], oid, pmid), ""),
            "is_canonical_summary": False})
    pd.DataFrame(rows).to_csv(MERGED / "relation_assertions.tsv", sep="\t", index=False)
    # replay 自检（裁决 4）：完整 span 重派生 ID 逐条一致（展示列截断不入 ID）
    replay_ok = full_span_ids == [r["assertion_id"] for r in rows]
    digest = hashlib.sha256(
        (MERGED / "relation_assertions.tsv").read_bytes()).hexdigest()
    # I：Assertion Atomicity QC（构造性：一行=一原子；ID 无重复=无聚合）
    n_rows = len(rows)
    atomicity = {"n_assertions": n_rows, "duplicate_ids": n_rows - len(ids),
                 "multi_pmid_per_assertion": 0,
                 "replay_identity_stable": bool(replay_ok),
                 "span_normalization_version": SPAN_NORMALIZATION_VERSION,
                 "status": "PASS" if n_rows == len(ids) and replay_ok else "FAIL"}
    return {"n": n_rows, "sha256": "sha256:" + digest, "atomicity_qc": atomicity}


def main():
    idx = load_evidence_index()

    # ---- 情境分歧审阅表 v0.4 ----
    cf = MERGED / "conflicts.tsv"
    ann_path = MERGED / "divergence_annotations.tsv"
    ann = {}
    if ann_path.exists():
        for _, r in pd.read_csv(ann_path, sep="\t").fillna("").iterrows():
            ann[f"{r['subject']}|{r['object']}"] = r.to_dict()
    conflicted_pairs = set()
    rows, ctx_stats = [], {"explicit": 0, "inferred": 0, "unknown": 0, "not_applicable": 0, "total": 0}
    comp_dist = {}
    if cf.exists():
        for _, c in pd.read_csv(cf, sep="\t").fillna("").iterrows():
            ev_a = side_ev(idx, c["subject"], c["predicate_a"], c["object"], c["pmids_a"])
            ev_b = side_ev(idx, c["subject"], c["predicate_b"], c["object"], c["pmids_b"])
            # object 名从 staging 记录解析（target≠background 的 1B 过滤用）
            obj_name = ""
            for (bs, bp, bo, bpm), r in idx.items():
                if bs == c["subject"] and bo == c["object"]:
                    obj_name = str(r["object"].get("name", ""))
                    break
            ctx_a = build_context(ev_a, c["object"], c["subject"], obj_name)
            ctx_b = build_context(ev_b, c["object"], c["subject"], obj_name)
            for ctx in (ctx_a, ctx_b):
                for d in CONTEXT_DIMS:
                    ctx_stats[ctx[d]["status"]] += 1
                    ctx_stats["total"] += 1
            match, basis = comparability_gate(ctx_a, ctx_b)
            comp_dist[match] = comp_dist.get(match, 0) + 1
            conflicted_pairs.add((c["subject"], c["object"]))
            a = ann.get(f"{c['subject']}|{c['object']}", {})
            rows.append({
                "subject": c["subject"], "object": c["object"],
                "side_a": f"{c['predicate_a']} (pmids: {c['pmids_a']})",
                "evidence_a": ev_a,
                "side_b": f"{c['predicate_b']} (pmids: {c['pmids_b']})",
                "evidence_b": ev_b,
                "context_a": json.dumps(ctx_a, ensure_ascii=False),
                "context_b": json.dumps(ctx_b, ensure_ascii=False),
                "context_match_status": match,          # gate 派生（非人工猜测）
                "comparability_basis": basis,
                "primary_divergence_type": a.get("primary_divergence_type", ""),
                "secondary_divergence_type": a.get("secondary_divergence_type", ""),
                "ontology_gap": a.get("ontology_gap", ""),
                "resolution_action": a.get("resolution_action", ""),
                "note": a.get("note", ""),
                "annotated_by": a.get("annotated_by", "")})
        out = pd.DataFrame(rows)
        out.to_csv(MERGED / "conflicts_review.tsv", sep="\t", index=False)
        n_ann = (out["primary_divergence_type"] != "").sum()

        # ---- RelationAssertion（A/B，先于 summary：hash 入 summary）----
        ainfo = build_relation_assertions(conflicted_pairs)
        print(f"[assertions] RelationAssertion {ainfo['n']} 条；sha={ainfo['sha256'][:19]}…")
        print(f"[atomicity-qc] {ainfo['atomicity_qc']}")

        # ---- summary v2（五类 context 指标 + backlog 规范化分组计数）----
        ann_rows = out[out["primary_divergence_type"] != ""]
        types = ann_rows["primary_divergence_type"].value_counts().to_dict()
        sec = ann_rows[ann_rows["secondary_divergence_type"] != ""][
            "secondary_divergence_type"].value_counts().to_dict()
        res = ann_rows["resolution_action"].value_counts().to_dict()
        gap_groups: dict = {}

        def _add_gap(group_key: str, gap_json: str, origin: str, ledger: list):
            try:
                d = json.loads(gap_json)
            except (json.JSONDecodeError, TypeError):
                return
            for k, v in d.items():
                gap_groups.setdefault(k, {})
                gap_groups[k][v] = gap_groups[k].get(v, 0) + 1
                ledger.append({"group": group_key, "gap_type": k, "dimension": v,
                               "origin": origin,
                               "record_id": f"{group_key}|{k}|{v}"})

        backlog_ledger = []
        for _, r0 in ann_rows.iterrows():
            for g in str(r0["ontology_gap"]).split(";"):
                if g.strip():
                    _add_gap(f"{r0['subject']}|{r0['object']}", g.strip(),
                             "manual_annotation", backlog_ledger)
        # object 侧派生 gap 统一入账（与手工注记同台账、同去重键）
        for _, r0 in out.iterrows():
            og = OBJECT_SIDE_GAPS.get(r0["object"])
            if og:
                _add_gap(f"{r0['subject']}|{r0['object']}",
                         json.dumps(og, ensure_ascii=False),
                         "object_side_derived", backlog_ledger)
        uniq = {r["record_id"]: r for r in backlog_ledger}
        pd.DataFrame(sorted(uniq.values(), key=lambda r: r["record_id"])).to_csv(
            MERGED / "ontology_backlog.tsv", sep="\t", index=False)
        backlog = len(uniq)
        backlog_qc = {"declared_count": backlog, "unique_records": len(uniq),
                      "duplicates_merged": len(backlog_ledger) - len(uniq),
                      "status": "PASS" if backlog == len(uniq) else "FAIL"}
        summary = {
            "phase": "contextual_divergence_review_v0.2",
            "total_divergence": len(out),
            "classified": int(n_ann),
            "classification_completeness": round(n_ann / max(len(out), 1), 4),
            "types": {str(k): int(v) for k, v in types.items()},
            "secondary_types": {str(k): int(v) for k, v in sec.items()},
            "multi_label_count": int((ann_rows["secondary_divergence_type"] != "").sum()),
            "resolution": {str(k): int(v) for k, v in res.items()},
            "resolution_path_completeness": round(
                (ann_rows["resolution_action"] != "").sum() / max(n_ann, 1), 4),
            "comparability": comp_dist,   # gate 派生；comparable 且方向相反才可 true_conflict（人工判定）
            "context_metrics": {
                "evidence_availability": round(
                    ((out["evidence_a"] != "") & (out["evidence_b"] != "")).mean(), 4),
                "dimension_coverage": round(
                    1 - ctx_stats["unknown"] / max(ctx_stats["total"], 1), 4),
                "explicit_rate": round(ctx_stats["explicit"] / max(ctx_stats["total"], 1), 4),
                "inferred_rate": round(ctx_stats["inferred"] / max(ctx_stats["total"], 1), 4),
                "unknown_rate": round(ctx_stats["unknown"] / max(ctx_stats["total"], 1), 4)},
            "ontology_gap": gap_groups,
            "ontology_refinement_backlog": backlog,
            "ontology_backlog_qc": backlog_qc,
            "true_biological_conflict": 0,  # 仅 comparable 且反向（gate 当前无 comparable → 0）
            "assertion_set": {"n_assertions": ainfo["n"],
                              "assertion_set_hash": ainfo["sha256"],
                              "atomicity_qc": ainfo["atomicity_qc"]},
            "ai_first_pass": True, "human_review": "pending"}
        (MERGED / "contextual_divergence_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[conflicts] {len(out)} 组；已标注 {n_ann}；comparability={comp_dist}")
        print(f"[context] {summary['context_metrics']}")
        print(f"[backlog] {backlog} 项（分组：{ {k: sum(v.values()) for k, v in gap_groups.items()} }）")

    # ---- F：object 侧 gap 已统一入 ontology_backlog.tsv 台账（口径单一）----

    # ---- I：Context Precision QC v2（precision≠coverage；assertion 级分层）----
    try:
        asserts = pd.read_csv(MERGED / "relation_assertions.tsv", sep="\t").fillna("")
    except FileNotFoundError:
        asserts = None
    if asserts is not None:
        sample_rows = []
        by_dim: dict = {}
        na_rows, np_rows = [], []
        for _, r in asserts.iterrows():
            try:
                ctx = json.loads(r["context"])
            except (json.JSONDecodeError, TypeError):
                continue
            for dim, spec in ctx.items():
                span_text = str(r.get("evidence_span_norm", ""))
                kw_anchor = ""
                for kw in str(spec.get("value", "")).split(","):
                    kw = kw.strip().lower()
                    pos = span_text.find(kw) if kw else -1
                    if pos >= 0:
                        lo, hi = max(0, pos - 70), min(len(span_text), pos + 90)
                        kw_anchor = ("..." if lo > 0 else "") + span_text[lo:hi] + \
                                    ("..." if hi < len(span_text) else "")
                        break
                rec = {"assertion_id": r["assertion_id"], "dimension": dim,
                       "extracted_value": spec.get("value", ""),
                       "status": spec.get("status", ""),
                       "source": spec.get("source", ""),
                       "applicable": spec.get("applicable", ""),
                       "unknown_reason": spec.get("unknown_reason", ""),
                       "evidence_anchor_window": kw_anchor or span_text[:160],
                       "evidence_full_span_head": span_text[:120],
                       "human_supported_yes_no": "", "reviewer_note": ""}
                if spec.get("status") == "explicit":
                    by_dim.setdefault(dim, []).append(rec)
                elif spec.get("status") == "unknown" and not spec.get("applicable", True) \
                        and len(na_rows) < 5:
                    na_rows.append(rec)
                elif spec.get("status") == "unknown" and spec.get("applicable", True) \
                        and spec.get("unknown_reason") == "not_present_in_available_evidence" \
                        and len(np_rows) < 5:
                    np_rows.append(rec)
        # C1a（2026-10-01 监工计划v2）：explicit 每维度带种子随机 ≤4 条（逐字节可复现）；
        # na/np 两分层仅作结构参考，不进精度分母（计分侧只用 status==explicit 行）。
        import random as _rnd
        _rng = _rnd.Random(20261001)
        _explicit_n = 0
        for dim in sorted(by_dim):
            pool = list(by_dim[dim])
            _rng.shuffle(pool)
            take = pool[:4]
            _explicit_n += len(take)
            sample_rows.extend(take)
            by_dim[dim] = pool[4:]  # 剩余=池去掉已抽中（监工 C3-2：原误存全池致去向多算）
        sample_rows.extend(na_rows)
        sample_rows.extend(np_rows)
        pd.DataFrame(sample_rows).to_csv(
            MERGED / "context_precision_sample.tsv", sep="\t", index=False)
        _leftover = sum(len(v) for v in by_dim.values())
        print(f"[precision-qc] v2 分层抽样 {len(sample_rows)} 条"
              f"（explicit×{_explicit_n}（种子 20261001 随机）"
              f" + not_applicable {len(na_rows)} + not_present {len(np_rows)}）")
        print(f"[precision-qc][去向] explicit 池总量 {_explicit_n + _leftover}，抽 {_explicit_n}，"
              f"剩 {_leftover} 条未入样本（每维度 ≤4 上限所致，非丢弃）；"
              f"na/np 各 5 条为结构参考层，不计入精度分母")

    # ---- 既有抽检口径 ----
    pr = MERGED / "pending_review_edges.tsv"
    if pr.exists():
        df = pd.read_csv(pr, sep="\t").fillna("")
        top_b = df[df.evidence_tier == "B"].sort_values(
            ["support_count", "confidence"], ascending=False).head(30)
        conflicted = df[df["conflict_state"] == True] if "conflict_state" in df else df.iloc[0:0]  # noqa: E712
        tier_c = df[df.evidence_tier == "C"]
        rand_c = tier_c.sample(n=min(20, len(tier_c)), random_state=25)
        sample = pd.concat([top_b, conflicted, rand_c]).drop_duplicates(
            subset=["subject", "predicate", "object"])
        sample["evidence"] = [side_ev(idx, r.subject, r.predicate, r.object, r.pmids)
                              for r in sample.itertuples()]
        sample["verdict"] = ""
        sample["sampled_at"] = date.today().isoformat()
        sample.to_csv(MERGED / "pending_review_sample.tsv", sep="\t", index=False)
        print(f"[pending] 抽样 {len(sample)} 条")
    me = MERGED / "merged_edges.tsv"
    if me.exists():
        df = pd.read_csv(me, sep="\t")
        deg = df.groupby("subject").size().sort_values(ascending=False).head(20)
        rep = pd.DataFrame({"subject": deg.index, "out_degree": deg.values})
        rep["predicates"] = [", ".join(sorted(df[df.subject == s].predicate.unique())[:6])
                             for s in deg.index]
        rep.to_csv(MERGED / "high_degree_report.tsv", sep="\t", index=False)
        print(f"[degree] top20 扇出：最高 {deg.iloc[0]}")




# ===== B1': MeSH 解剖词表驱动的 anatomical_site 命中（v3 周期）=====
# 替代手写词表：通过 MeSH A 树（Anatomy）判定是否为独立解剖部位，
# 而非修饰语。比手写 epithet 正则更准确（覆盖全部 MeSH 解剖实体）。
def _mesh_anatomy_hit(evidence_text: str):
    """MeSH A 树驱动的 anatomical_site 命中。

    1. 从文本中抽取候选词（长词优先）
    2. 尝试 MeSH 归一化 → tree_numbers 有 A 前缀 = 解剖部位
    3. 保留"独立部位"语义：排除已被修饰语规则/途径短语剔除的
    """
    import re as _re
    from pathlib import Path as _Path
    import sys as _sys
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "08_route_eval"))
    try:
        from mesh_normalize import normalize_mesh as _nm
    except ImportError:
        return _anatomical_site_hit(evidence_text)  # 兜底：退回手写词表

    low = evidence_text.lower()
    # 候选词：2-4 词短语（MeSH 常见长度），按长度优先
    words = _re.findall(r'[a-z][a-z\-]+(?:\s+[a-z][a-z\-]+){0,3}', low)
    candidates = sorted(set(words), key=len, reverse=True)

    hits = []
    seen = set()
    for c in candidates:
        if any(c in s_ for s_ in seen):  # 跳过已被更长词覆盖的
            continue
        r = _nm(c)
        if r.get('resolved'):
            trees = r.get('tree_numbers', [])
            if any(t.startswith('A') for t in trees):
                # 检查是否被修饰语规则剔除
                if c not in _ROUTE_WORDS and not _is_epithet(c, low):
                    hits.append(c)
                    seen.add(c)

    return hits or None

def _is_epithet(word: str, text: str) -> bool:
    """检查词是否为修饰语（后接菌名/疾病名/过程名词）。"""
    import re as _re
    _patterns = [
        rf"\b{word}\s+(?:[a-z\-]+\s+){{0,2}}(bacterium|bacteria|bacterial|species|microbiota|microbiome)",
        rf"\b{word}\s+(?:[a-z\-]+\s+){{0,2}}(inflammation|inflammatory|damage|injury|disease|colitis|cancer|barrier|severity)",
    ]
    return any(_re.search(p, text) for p in _patterns)

_ROUTE_WORDS = frozensome = frozenset()  # 由 _anatomical_site_hit 的 route_words 填充

if __name__ == "__main__":
    main()
