#!/usr/bin/env python3
"""合并 curated seed + LLM staging，执行证据分级与幂等去重。"""
import argparse, json
from collections import defaultdict
from datetime import date
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
SEED=ROOT/'data/seed'; STAGING=ROOT/'data/staging'; MERGED=ROOT/'data/merged'


# ---- Source Registry 闸门（P0-1）：未登记来源的知识不得进入 KG ----
REGISTRY = ROOT/'data/registry/source_registry.tsv'
SOURCE_MAP = {  # 输入文件前缀 -> registry source_key（新增数据源必须先登记）
    'seed_': 'maier2018_st3', 'bugsigdb_': 'bugsigdb_export',
    'gutmgene_': 'gutmgene_v3', 'gutmdisorder_': 'gutmdisorder_v3',
    'kegg_pathway_': 'kegg_rest', 'llm_relations': 'glm_extract_v2',
}
def registry_gate():
    import pandas as pd
    reg = pd.read_csv(REGISTRY, sep='\t').fillna('')
    active = set(reg[reg.status=='active'].source_key)
    missing = [k for k in SOURCE_MAP.values() if k not in active]
    if missing:
        raise SystemExit(f'[registry] 以下来源未登记/未激活，拒绝合并: {missing}')
    return {k: v for k, v in SOURCE_MAP.items()}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--include-c',action='store_true'); args=ap.parse_args()
    MERGED.mkdir(parents=True,exist_ok=True)
    smap = registry_gate(); print(f'[registry] {len(smap)} 个输入来源全部登记在案')
    nodes=pd.read_csv(SEED/'seed_nodes.tsv',sep='\t')
    edges=pd.read_csv(SEED/'seed_edges.tsv',sep='\t').fillna('')
    # 合并成熟的 BugSigDB gut 子集（结构与主图相同，按三元组幂等去重）。
    bnodes, bedges = SEED/'bugsigdb_nodes.tsv', SEED/'bugsigdb_edges.tsv'
    if bnodes.exists():
        nodes = pd.concat([nodes, pd.read_csv(bnodes, sep='\t').fillna('')], ignore_index=True)
    if bedges.exists():
        edges = pd.concat([edges, pd.read_csv(bedges, sep='\t').fillna('')], ignore_index=True)
    # 合并 gutMGene 三表转换结果：Microbe–Metabolite、Microbe/Metabolite–Gene。
    gnodes, gedges = SEED/'gutmgene_nodes.tsv', SEED/'gutmgene_edges.tsv'
    if gnodes.exists():
        nodes = pd.concat([nodes, pd.read_csv(gnodes, sep='\t').fillna('')], ignore_index=True)
    if gedges.exists():
        edges = pd.concat([edges, pd.read_csv(gedges, sep='\t').fillna('')], ignore_index=True)
    # 合并 gutMDisorder Literature-based Disorder vs Health 关联。
    dnodes, dedges = SEED/'gutmdisorder_nodes.tsv', SEED/'gutmdisorder_edges.tsv'
    if dnodes.exists():
        nodes = pd.concat([nodes, pd.read_csv(dnodes, sep='\t').fillna('')], ignore_index=True)
    if dedges.exists():
        edges = pd.concat([edges, pd.read_csv(dedges, sep='\t').fillna('')], ignore_index=True)
    # 合并 KEGG REST Gene→Pathway 映射（宿主基因通路，Tier A 策展）。
    knodes, kedges = SEED/'kegg_pathway_nodes.tsv', SEED/'kegg_pathway_edges.tsv'
    if knodes.exists():
        nodes = pd.concat([nodes, pd.read_csv(knodes, sep='\t').fillna('')], ignore_index=True)
    if kedges.exists():
        edges = pd.concat([edges, pd.read_csv(kedges, sep='\t').fillna('')], ignore_index=True)
    nodes = nodes.drop_duplicates(subset=['id'], keep='first')
    edges = edges.drop_duplicates(subset=['subject','predicate','object'], keep='first')
    # 仅合并明确成功的 LLM 结果；按三元组跨文献聚合：
    # ≥2 篇支持 → Tier B 自动并入主图；单篇 → Tier C 进审查文件不进主图。
    review=[]; agg={}
    stage=STAGING/'llm_relations.jsonl'
    if stage.exists():
        groups={}
        for line in stage.open(encoding='utf-8'):
            r=json.loads(line)
            if r.get('status')!='ok' or r.get('predicate')=='no_relation': continue
            subject, obj=r['subject'],r['object']
            key=(subject['id'],r['predicate'],obj['id'])
            groups.setdefault(key,[]).append(r)
        # P2 冲突契约（裁决 2026-09-25）：同一 (subject, object) 上存在对立谓词
        # → 双方 evidence 保留、进 conflict_state、禁止自动选择（不自动并入主图）。
        OPPOSITES={'alleviates':'aggravates','aggravates':'alleviates',
                   'promotes_growth':'inhibits_growth','inhibits_growth':'promotes_growth'}
        by_pair={}
        for (sid,pred,oid) in groups:
            by_pair.setdefault((sid,oid),[]).append(pred)
        conflicted=set(); conflicts=[]
        for (sid,oid),preds in by_pair.items():
            for pred in set(preds):
                opp=OPPOSITES.get(pred)
                if opp and opp in preds and pred < opp:  # 规范序去重（每冲突组一行）
                    conflicted.add((sid,pred,oid)); conflicted.add((sid,opp,oid))
                    pm=lambda p: ';'.join(sorted({r.get('pmid','') for r in groups[(sid,p,oid)] if r.get('pmid')}))
                    conflicts.append({'subject':sid,'object':oid,
                                      'predicate_a':pred,'pmids_a':pm(pred),
                                      'predicate_b':opp,'pmids_b':pm(opp),
                                      'state':'conflict_state',
                                      'resolution':'manual_review_required'})
        if conflicts:
            pd.DataFrame(conflicts).to_csv(MERGED/'conflicts.tsv',sep='\t',index=False)
            print(f'[conflict] 对立谓词冲突 {len(conflicts)} 组——双方保留、待人工裁决（不自动入图）')
        for key,rs in groups.items():
            pmids=sorted({r.get('pmid','') for r in rs if r.get('pmid')})
            years=sorted({str(r.get('year','')) for r in rs if r.get('year')})
            row={'subject':key[0],'predicate':key[1],'object':key[2],
                 'source_type':'llm_extracted','pmids':';'.join(pmids),
                 'years':';'.join(years),'support_count':len(pmids),
                 'confidence':max(float(r.get('confidence',0)) for r in rs),
                 'polarity':rs[0].get('polarity','neutral'),'last_updated':date.today().isoformat()}
            review.append(row)
            if key in conflicted:
                row['evidence_tier']='C'; row['conflict_state']=True
            elif len(pmids)>=2:
                row['evidence_tier']='B'; agg[key]=row
            else:
                row['evidence_tier']='C'
    pd.DataFrame(review).to_csv(MERGED/'pending_review_edges.tsv',sep='\t',index=False)
    if agg:
        # Tier-B 实体节点并入主图（仅缺失时追加，保持幂等）。
        stage_nodes=[]
        seen_ids=set(nodes['id'])
        if stage.exists():
            for line in stage.open(encoding='utf-8'):
                r=json.loads(line)
                if r.get('status')!='ok': continue
                for ent in (r['subject'],r['object']):
                    key=(r['subject']['id'],r['predicate'],r['object']['id'])
                    if key in agg and ent['id'] not in seen_ids:
                        stage_nodes.append({'id':ent['id'],'name':ent.get('name') or ent['id'],
                                            'category':ent['category'],'aliases':'','xrefs':'','tax_rank':''})
                        seen_ids.add(ent['id'])
        if stage_nodes:
            nodes=pd.concat([nodes,pd.DataFrame(stage_nodes)],ignore_index=True)
            nodes=nodes.drop_duplicates(subset=['id'],keep='first')
        add=pd.DataFrame(agg.values())
        add['evidence_tier']='B'
        edges=pd.concat([edges,add],ignore_index=True)
    edges=edges.drop_duplicates(subset=['subject','predicate','object'],keep='first')
    # G（裁决 2026-09-25）：canonical = 派生视图，非事实层——
    # 冲突 pair 的 canonical 边标 context_dependent 并挂 supporting assertions，
    # 禁止跨 context majority vote 恢复唯一方向。
    if 'relation_status' not in edges.columns: edges['relation_status']='context_supported'
    if 'canonical_view' not in edges.columns: edges['canonical_view']='derived_summary'
    confl_pairs=set()
    cf=MERGED/'conflicts.tsv'
    if cf.exists():
        cdf=pd.read_csv(cf,sep='\t')
        confl_pairs={(r['subject'],r['object']) for _,r in cdf.iterrows()}
    if confl_pairs:
        mask=edges.apply(lambda r:(r['subject'],r['object']) in confl_pairs,axis=1)
        edges.loc[mask,'relation_status']='context_dependent'
        print(f'[canonical] context_dependent canonical 边 {int(mask.sum())} 条（派生视图，方向不唯一）')
    # Phase 2 预备：source_ref/curator 元数据列（存量边按来源段回填，事实字段零改动）
    if 'source_ref' not in edges.columns: edges['source_ref']=''
    if 'curator' not in edges.columns: edges['curator']='automated_pipeline'
    edges['source_ref']=edges['source_ref'].replace('', 'unattributed')
    edges.to_csv(MERGED/'merged_edges.tsv',sep='\t',index=False)
    nodes.to_csv(MERGED/'merged_nodes.tsv',sep='\t',index=False)
    print(f'[out] 主图 nodes={len(nodes)} edges={len(edges)}; pending_review={len(review)}')
    print(f'[qc] 主图重复边={edges.duplicated(["subject","predicate","object"]).sum()}')
    # Capability Adapter v0.1：snapshot manifest（裁决——主图物化必须经 QC 与 manifest）
    import sys as _sys
    _sys.path.insert(0, str(ROOT / 'src/07_capability'))
    import adapter as _adapter
    import json as _json
    ctx_metrics={}
    csum=MERGED/'contextual_divergence_summary.json'
    if csum.exists():
        try:
            ctx_metrics=_json.loads(csum.read_text(encoding='utf-8')).get('context_metrics',{})
        except _json.JSONDecodeError:
            pass
    ahash=''
    art=MERGED/'relation_assertions.tsv'
    if art.exists():
        ahash=_adapter.file_sha256(art)
    annhash=''
    annf=MERGED/'divergence_annotations.tsv'
    if annf.exists():
        annhash=_adapter.file_sha256(annf)
    manifest=_adapter.write_snapshot_manifest(
        n_nodes=len(nodes), n_edges=len(edges), pending_review=len(review),
        conflicts=len(conflicts) if conflicts else 0,
        context_metrics=ctx_metrics, assertion_set_hash=ahash,
        annotation_set_hash=annhash)
    print(f"[snapshot] {manifest['snapshot_id']} registry_sha={manifest['source_registry_version'][:19]}… "
          f"materialized_to_neo4j={manifest['materialized_to_neo4j']}")

if __name__=='__main__': main()
