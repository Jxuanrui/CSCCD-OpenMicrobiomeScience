#!/usr/bin/env python3
"""合并 curated seed + LLM staging，执行证据分级与幂等去重。"""
import argparse, json
from collections import defaultdict
from datetime import date
from pathlib import Path
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
SEED=ROOT/'data/seed'; STAGING=ROOT/'data/staging'; MERGED=ROOT/'data/merged'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--include-c',action='store_true'); args=ap.parse_args()
    MERGED.mkdir(parents=True,exist_ok=True)
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
        for key,rs in groups.items():
            pmids=sorted({r.get('pmid','') for r in rs if r.get('pmid')})
            years=sorted({str(r.get('year','')) for r in rs if r.get('year')})
            row={'subject':key[0],'predicate':key[1],'object':key[2],
                 'source_type':'llm_extracted','pmids':';'.join(pmids),
                 'years':';'.join(years),'support_count':len(pmids),
                 'confidence':max(float(r.get('confidence',0)) for r in rs),
                 'polarity':rs[0].get('polarity','neutral'),'last_updated':date.today().isoformat()}
            review.append(row)
            if len(pmids)>=2:
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
    edges.to_csv(MERGED/'merged_edges.tsv',sep='\t',index=False)
    nodes.to_csv(MERGED/'merged_nodes.tsv',sep='\t',index=False)
    print(f'[out] 主图 nodes={len(nodes)} edges={len(edges)}; pending_review={len(review)}')
    print(f'[qc] 主图重复边={edges.duplicated(["subject","predicate","object"]).sum()}')

if __name__=='__main__': main()
