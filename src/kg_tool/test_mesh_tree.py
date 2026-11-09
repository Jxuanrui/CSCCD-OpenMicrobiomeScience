"""MeSH 树解析单测（祖先映射的方向与距离）。"""
import gzip

from kg_tool.mesh_tree import MESH_UID_RE, build_ancestor_map, parse_dump

DESC = """<?xml version="1.0"?>
<DescriptorRecordSet>
 <DescriptorRecord>
  <DescriptorUI>D006973</DescriptorUI>
  <DescriptorName><String>Hypertension</String></DescriptorName>
  <TreeNumberList><TreeNumber>C14.907.489</TreeNumber></TreeNumberList>
 </DescriptorRecord>
 <DescriptorRecord>
  <DescriptorUI>D002318</DescriptorUI>
  <DescriptorName><String>Cardiovascular Diseases</String></DescriptorName>
  <TreeNumberList><TreeNumber>C14.907</TreeNumber></TreeNumberList>
 </DescriptorRecord>
</DescriptorRecordSet>"""

SUPP = """<?xml version="1.0"?>
<SupplementalRecordSet>
 <SupplementalRecord>
  <SupplementalRecordUI>C536657</SupplementalRecordUI>
  <SupplementalRecordName><String>Some symptom</String></SupplementalRecordName>
  <TreeNumberList><TreeNumber>C14.907.489.999</TreeNumber></TreeNumberList>
 </SupplementalRecord>
</SupplementalRecordSet>"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    with gzip.open(p, "wt", encoding="utf-8") as f:
        f.write(text)
    return p


def test_ancestor_direction_and_distance(tmp_path):
    d = _write(tmp_path, "desc.gz", DESC)
    s = _write(tmp_path, "supp.gz", SUPP)
    tree_by_uid, names = parse_dump(d)
    tree_by_uid2, names2 = parse_dump(s)
    tree_by_uid.update(tree_by_uid2)
    names.update(names2)
    anc = build_ancestor_map(tree_by_uid)
    # 高血压 → 心血管疾病：1 步（C14.907.489 的真前缀 C14.907）
    assert anc["D006973"]["D002318"] == 1
    # 更细的 C 码 → 高血压 1 步、→ 心血管 2 步
    assert anc["C536657"]["D006973"] == 1
    assert anc["C536657"]["D002318"] == 2
    # 方向不可逆：粗的不能"祖先"细的
    assert "D006973" not in anc["D002318"]
    assert names["D006973"] == "Hypertension"


def test_uid_regex_matches_spec():
    assert MESH_UID_RE.match("D012817") and MESH_UID_RE.match("C536657")
    assert not MESH_UID_RE.match("OMIM:603663")
