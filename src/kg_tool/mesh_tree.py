"""MeSH 树解析（官方 desc/supp 压缩包 → 树号祖先表）。

为什么要自己解析而不装第三方库：只需要"某个 MeSH UID 的祖先有哪些"这一件事，
官方 XML 里 TreeNumber 字段就是答案；为此引入 pymedtermino 之类的大依赖不值
（轮子三栏表口径）。解析用 iterparse 流式，313MB 解压后也不吃内存。

口径要点（与 L2 规格一致）：
- 只认 `^[DC]\\d{6,}$` 的 UID（与 crosswalk 规范化同一条正则，不另立标准）；
- 树号形如 `C14.907.489`，其祖先 = 逐级截断（`C14.907`、`C14`）；**不含自身**；
- 同一 UID 可有多个树号（多归属），祖先取并集；
- 距离 = 树号层级差（`C14.907.489`→`C14.907` 记 1，→`C14` 记 2）。
"""
import gzip
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

MESH_UID_RE = re.compile(r"^[DC]\d{6,}$", re.I)


def parse_dump(path: Path) -> tuple[dict, dict]:
    """解析 desc/supp XML → (uid → 树号列表, uid → 名称)。

    desc 的记录标签是 DescriptorRecord，supp 是 SupplementalRecord；两者字段同名
    （DescriptorUI/SupplementalRecordUI、TreeNumber、String），这里按两种都试。
    """
    tree_by_uid = defaultdict(list)
    name_by_uid = {}
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as f:
        for _, el in ET.iterparse(f, events=("end",)):
            tag = el.tag.split("}")[-1]
            if tag not in ("DescriptorRecord", "SupplementalRecord"):
                continue
            uid = None
            for k in ("DescriptorUI", "SupplementalRecordUI"):
                node = el.find(f".//{k}")
                if node is not None and node.text:
                    uid = node.text.strip()
                    break
            if not uid or not MESH_UID_RE.match(uid):
                el.clear()
                continue
            names = [n.text.strip() for n in el.findall(".//String")
                     if n.text and n.text.strip()]
            if names:
                name_by_uid[uid] = names[0]
            for tn in el.findall(".//TreeNumber"):
                if tn.text:
                    tree_by_uid[uid].append(tn.text.strip())
            el.clear()
    return dict(tree_by_uid), name_by_uid


def build_ancestor_map(tree_by_uid: dict) -> dict:
    """uid → {祖先uid: 最小距离}。

    树号路径即层级：把每个树号的所有前缀当成"更泛化的位置"，再映射回 UID。
    这样 "高血压 C14.907.489" 与 "心血管疾病 C14.907" 之间就有 1 步关系。
    """
    tn2uid = defaultdict(list)
    for uid, tns in tree_by_uid.items():
        for tn in tns:
            tn2uid[tn].append(uid)
    out = {}
    for uid, tns in tree_by_uid.items():
        anc = {}
        for tn in tns:
            parts = tn.split(".")
            for k in range(1, len(parts)):      # 只取真前缀（不含自身）
                prefix = ".".join(parts[:k])
                dist = len(parts) - k
                for other in tn2uid.get(prefix, ()):
                    if other == uid:
                        continue
                    if other not in anc or dist < anc[other]:
                        anc[other] = dist
        out[uid] = anc
    return out


def load_tree(desc_gz: Path, supp_gz: Path) -> dict:
    """一次性装载 → {uid: {祖先uid: 距离}}（含名称表旁挂）。"""
    t1, n1 = parse_dump(desc_gz)
    t2, n2 = parse_dump(supp_gz)
    t1.update(t2)
    n1.update(n2)
    anc = build_ancestor_map(t1)
    return {"tree_by_uid": t1, "name_by_uid": n1, "ancestors": anc}
