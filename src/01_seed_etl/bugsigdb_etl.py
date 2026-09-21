#!/usr/bin/env python3
"""BugSigDB full_dump.csv → nodes/edges TSV 增量转换。
数据来源：waldronlab/BugSigDBExports（成熟公开导出）。"""
import argparse, re
from datetime import date
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]; RAW=ROOT/'data/raw/BugSigDBExports'; OUT=ROOT/'data/seed'

def split(v): return [x.strip() for x in str(v).split('|') if x.strip() and x.strip()!='nan']
# 人工抽检发现的非疾病概念（干扰因素/人口学变量），不作为 Disease 节点入图。
NON_DISEASE_COND={'Diet','Age','Health','Healthy'}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=0); args=ap.parse_args()
 df=pd.read_csv(RAW/'full_dump.csv',skiprows=1,low_memory=False)
 df=df[df['Body site'].fillna('').str.contains('gut|intestin|colon|rect|stomach|fec',case=False,regex=True)]
 if args.limit: df=df.head(args.limit)
 nodes={}; edges={}; today=date.today().isoformat()
 for _,r in df.iterrows():
  taxids=split(r.get('NCBI Taxonomy IDs','')); names=split(r.get('MetaPhlAn taxon names','')); direction=str(r.get('Abundance in Group 1','')).lower()
  pmid=str(r.get('PMID','')).replace('.0',''); year=str(r.get('Year','')).replace('.0',''); cond=str(r.get('Condition','')).strip()
  if not cond or cond=='nan' or cond in NON_DISEASE_COND: continue
  did='LFS:COND:'+re.sub(r'[^A-Za-z0-9]+','_',cond).strip('_'); nodes.setdefault(did,{'id':did,'name':cond,'category':'Disease','aliases':'','xrefs':'','tax_rank':''})
  for i,t in enumerate(taxids):
   if not t.isdigit(): continue
   name=names[i] if i<len(names) else t
   # BugSigDB 的 MetaPhlAn 列同时包含 kingdom/phylum/class/order/family/genus/species；
   # 物种层才可直接作为 Microbe 节点参与菌名查询，其他层级记录暂不入主图。
   if not name.startswith('s__'): continue
   species_name=name[3:].split(',', 1)[0].strip()
   if not species_name or species_name.lower() in {'unclassified','unknown'}: continue
   mid='NCBITaxon:'+t; nodes.setdefault(mid,{'id':mid,'name':species_name,'category':'Microbe','aliases':'','xrefs':'','tax_rank':'species'})
   pred='increases_abundance_in' if 'increase' in direction else 'decreases_abundance_in' if 'decrease' in direction else None
   if pred:
    key=(mid,pred,did); edges[key]={'subject':mid,'predicate':pred,'object':did,'source_type':'curated','evidence_tier':'A','pmids':pmid if pmid!='nan' else '','years':year if year!='nan' else '','support_count':1,'confidence':1.0,'polarity':'','last_updated':today}
 pd.DataFrame(nodes.values()).to_csv(OUT/'bugsigdb_nodes.tsv',sep='\t',index=False); pd.DataFrame(edges.values()).to_csv(OUT/'bugsigdb_edges.tsv',sep='\t',index=False)
 print(f'BugSigDB gut rows={len(df)} nodes={len(nodes)} edges={len(edges)}')
if __name__=='__main__': main()
