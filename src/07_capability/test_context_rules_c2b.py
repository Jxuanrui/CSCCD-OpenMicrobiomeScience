"""C2b 三条规则回归（监工 G3：用例取 4 个 mixed ID 的靶行；先红后绿——本文件在修复前应 FAIL）。

规则 1 anatomical_adjective：部位词作疾病/过程名词的修饰（"intestinal inflammation"）不产独立 anatomical_site
规则 2 endpoint 值域：endpoint 值必须为过程指标词；疾病词（colitis/tumor）归 disease 维度不进 endpoint
规则 3 geography 来源：title 行的地理词不产 geography 属性
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import review_prep as rp

# 靶行（4 个 mixed ID，证据窗原文）
EV_INTESTINAL_INFLAM = "worsening of intestinal inflammation by sutterella species inhibition"
EV_PRISTIMERIN = "parabacteroides distasonis mediates the protective effects of pristimerin against ulcerative colitis"


def test_rule1_adjective_not_site():
    # "intestinal inflammation" 中 intestinal 是 amod 修饰——不应命中 anatomical_site
    hits = rp._anatomical_site_hit(EV_INTESTINAL_INFLAM)
    assert hits is None or "intestinal" not in [h.lower() for h in hits], \
        f"规则1未生效：intestinal 仍作为独立部位命中 {hits}"


def test_rule2_endpoint_excludes_disease_words():
    # colitis 是疾病词——不得作为 endpoint 值输出；endpoint 值域=过程指标
    ev = EV_PRISTIMERIN
    found = rp._hit(ev, "endpoint") if hasattr(rp, "_hit") else None
    if found:
        assert "colitis" not in [f.lower() for f in found], \
            f"规则2未生效：疾病词 colitis 仍在 endpoint 词表命中 {found}"
    # 词表本身也不应含疾病词
    assert not set(w.lower() for w in rp._EXPLICIT["endpoint"]) & {"colitis", "tumor"}, \
        "规则2未生效：endpoint 词表含疾病词 colitis/tumor"


def test_rule3_geography_title_filtered():
    title = "Gut microbiome changes in Chinese patients with colitis"
    assert rp._geography_hit(title, source="title") is None, "规则3未生效：title 地理词未过滤"
    body = "we enrolled 30 chinese participants in a cohort study"
    assert rp._geography_hit(body, source="abstract_sentence") is not None, "正文地理词应保留"
