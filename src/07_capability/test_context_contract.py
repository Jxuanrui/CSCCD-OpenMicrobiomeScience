#!/usr/bin/env python3
"""P0-2 构造性回归测试（2026-09-29 监工令）：v1 契约 1A/1B 在 context 存储侧生效。

1A：泛化 token 命中不得存为 explicit（inferred signal）。
1B：disease background 过滤扩展到 object 同义/缩写形式族（batch1 实证泄漏路径）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from review_prep import build_context, _object_disease_forms  # noqa: E402


def test_1b_neoplasm_family_filtered():
    ctx = build_context("f. nucleatum potentiates intestinal tumorigenesis in mice",
                        "MESH:D009369", "NCBITaxon:514", object_name="Neoplasms")
    assert ctx["disease"]["status"] == "unknown", "tumorigenesis 是 Neoplasms 的同义族，不得作 background"


def test_1b_ibd_family_and_abbreviation():
    ctx = build_context("bifidobacterium aids treatment of inflammatory bowel disease (ibd)",
                        "MESH:D015212", "NCBITaxon:1685",
                        object_name="Inflammatory Bowel Disease")
    assert ctx["disease"]["status"] == "unknown"


def test_1b_colitis_object_keeps_unrelated_cancer():
    ctx = build_context("bacteria x studied in colitis and cancer models in mice",
                        "MESH:D003092", "NCBITaxon:1", object_name="Colitis")
    # colitis（target 同形）被滤除；cancer 与 object 无关 → 保留为 background
    assert ctx["disease"]["status"] == "explicit"
    assert "cancer" in ctx["disease"]["value"] and "colitis" not in ctx["disease"]["value"]


def test_1a_generic_only_not_explicit():
    ctx = build_context("the extract from streptomyces sp. mum265- a strain isolated from soil",
                        "MESH:D000", "NCBITaxon:1", object_name="X")
    assert ctx["strain"]["status"] == "inferred"
    assert ctx["strain"]["source"] == "abstract_signal"


def test_1a_mixed_with_specific_token_stays_explicit():
    ctx = build_context("enterotoxigenic b. fragilis (etbf) strain causes tumors",
                        "MESH:D009369", "NCBITaxon:818", object_name="Neoplasms")
    assert ctx["strain"]["status"] == "explicit"
    assert "ETBF" in ctx["strain"]["value"]


def test_object_forms_abbreviation():
    forms = _object_disease_forms("Inflammatory Bowel Disease")
    assert "ibd" in forms and "inflammatory" in forms


def test_v1_regression_disease_target_context_separated():
    # release_gate_check 既有构造性回归必须继续成立
    ctx = build_context("microbe aggravates colitis in this study", "MESH:D003092",
                        "NCBITaxon:1", "Colitis")
    assert ctx["disease"]["status"] == "unknown"


def test_1a_habitat_epithets_not_host():
    for text, why in [
        ("helicobacter pylori colonizes the human stomach and may affect the inflammatory response", "human stomach=栖息地定语"),
        ("probiotics prevent disease induced by citrobacter rodentium, a murine-specific enteric pathogen", "murine-specific=病原特异性的定语"),
        ("mutanobactins isolated from streptococcus mutans, a member of the human oral microbiome", "human oral microbiome=栖息地定语"),
    ]:
        ctx = build_context(text, "MESH:D000", "NCBITaxon:1", object_name="X")
        assert ctx["host_species"]["status"] != "explicit", why


def test_1a_derived_from_is_generic():
    ctx = build_context("probiotic bacteria derived from the genera lactobacillus are effective",
                        "MESH:D000", "NCBITaxon:1", object_name="X")
    assert ctx["strain"]["status"] == "inferred", "derived from 是泛化描述符不得 explicit"


def test_final_subclass_target_fragment_filtered():
    ctx = build_context("helicobacter hepaticus triggers crohn's-like symptoms in mice",
                        "MESH:D003429", "NCBITaxon:1", object_name="Crohn Disease")
    assert ctx["disease_subtype"]["status"] != "explicit", "crohn 是 object 名碎片，终审#2"

def test_final_stage_fragment_filtered():
    ctx = build_context("f. nucleatum promotes the development of acute liver failure",
                        "MESH:X", "NCBITaxon:1", object_name="Liver Failure, Acute")
    assert ctx["disease_stage"]["status"] != "explicit", "acute 是 object 名碎片，终审#8"

def test_final_outcome_not_background_disease():
    ctx = build_context("children with cld, leading to increased inflammation",
                        "MESH:X", "NCBITaxon:1", object_name="Bacteroides")
    assert ctx["disease"]["status"] != "explicit", "leading to increased inflammation 是结局，终审#21"

def test_final_route_not_site():
    ctx = build_context("oral administration of lactobacillus alleviates colitis in mice",
                        "MESH:D003092", "NCBITaxon:1", object_name="Colitis")
    assert ctx["anatomical_site"]["status"] != "explicit" or \
        "oral" not in ctx["anatomical_site"]["value"], "oral administration 是途径，终审#9"

def test_final_isolated_from_not_host():
    ctx = build_context("s. aureus strain isolated from atopic dermatitis patients tested in vitro",
                        "MESH:X", "NCBITaxon:1", object_name="X")
    assert "patients" not in ctx["host_species"]["value"], "isolated from patients 是来源归属，终审#36"


def test_v3_human_cells_substrate_not_host():
    ctx = build_context("psoralen upregulates genes in human periodontal ligament cells",
                        "MESH:X", "NCBITaxon:1", object_name="X")
    assert "human" not in ctx["host_species"].get("value", "") or \
        ctx["host_species"]["status"] != "explicit", "human X cells 是细胞底物，v3-#24"

def test_v3_microbiota_habitat_not_host():
    ctx = build_context("disappearance of h. pylori from the human microbiota may be linked",
                        "MESH:X", "NCBITaxon:1", object_name="X")
    assert ctx["host_species"]["status"] != "explicit", "from the human microbiota 是栖息地，v3-#36"

def test_v3_stage_name_fragment_filtered():
    ctx = build_context("sars-cov-2 and severe acute respiratory syndrome contribute to gi inflammation",
                        "MESH:X", "NCBITaxon:1", object_name="X")
    assert ctx["disease_stage"]["status"] != "explicit", "severe/acute 是 SARS 名称碎片，v3-#14"

def test_v3_stage_outcome_modifier_filtered():
    ctx = build_context("superantigens increase more severe cutaneous inflammation in ad patients",
                        "MESH:X", "NCBITaxon:1", object_name="X")
    assert "severe" not in (ctx["disease_stage"].get("value") or ""), "more severe 是结局修饰，v3-#31"

def test_v3_dangling_subtype_inferred():
    ctx = build_context("lactobacillus alleviates dili via indole-3-lactic acid in hepatocellular assays",
                        "MESH:X", "NCBITaxon:1", object_name="X")
    if "hepatocellular" in (ctx.get("disease_subtype", {}).get("value") or ""):
        assert ctx["disease_subtype"]["status"] == "inferred", "悬挂 subtype 须降 inferred，v3-#17/18"
