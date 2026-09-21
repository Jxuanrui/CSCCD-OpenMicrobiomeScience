#!/usr/bin/env python3
"""将已复制的 gutMGene 三张公开表转换为本项目统一节点/边 TSV。
复用 MicrobiomePlatform_Portable 的字段语义，不修改原始目录。
"""
from datetime import date
from pathlib import Path
import re
import pandas as pd
ROOT=Path(__file__).resolve().parents[2]; SRC=ROOT/'data/sources/gutMGene'; OUT=ROOT/'data/seed'; TODAY=date.today().isoformat()
NODE_COLS=['id','name','category','aliases','xrefs','tax_rank']; EDGE_COLS=['subject','predicate','object','source_type','evidence_tier','pmids','years','support_count','confidence','polarity','last_updated']

def text(v): return '' if pd.isna(v) else str(v).strip()
def clean_id(v): return text(v).replace('.0','')
def add_node(nodes, id_, name, cat, xrefs='', rank=''):
 if id_ and id_ not in nodes: nodes[id_]={'id':id_,'name':text(name) or id_,'category':cat,'aliases':'','xrefs':xrefs,'tax_rank':rank}
def edge(edges, s,p,o,pmid,rank='A'):
 if not s or not o:return
 k=(s,p,o); e=edges.setdefault(k,{'subject':s,'predicate':p,'object':o,'source_type':'curated','evidence_tier':rank,'pmids':set(),'years':set(),'support_count':0,'confidence':1.0,'polarity':'','last_updated':TODAY})
 if pmid: e['pmids'].add(pmid)
 e['support_count'] += 1
 if pmid.isdigit(): e['years'].add('')

def load(name):
 p=SRC/name
 for enc in ['utf-8','gb18030','latin1']:
  try:return pd.read_csv(p,encoding=enc,low_memory=False)
  except UnicodeDecodeError:pass
 raise RuntimeError('无法读取 '+str(p))
def main():
 nodes={}; edges={}
 mm=load('Gut Microbe-Microbial metabolite.csv')
 mg=load('Gut Microbe-Host Gene.csv')
 metg=load('Microbial metabolite-Host Gene.csv')
 # Microbe -> metabolite
 for _,r in mm.iterrows():
  tid=clean_id(r.get('Gut Microbiota NCBI ID')); cid=clean_id(r.get('Metabolite ChEBI'))
  if not cid: cid='PUBCHEM:'+clean_id(r.get('Metabolite PubChem CID'))
  if tid.isdigit(): add_node(nodes,'NCBITaxon:'+tid,r.get('Gut Microbiota'), 'Microbe',rank=text(r.get('Rank')))
  if cid and cid!='PUBCHEM:': add_node(nodes,cid,r.get('Metabolite'),'Metabolite',xrefs='|'.join(x for x in [text(r.get('Metabolite PubChem CID')),text(r.get('Metabolite KEGG')),text(r.get('Metabolite HMDB'))] if x))
  if tid.isdigit() and cid and cid!='PUBCHEM:': edge(edges,'NCBITaxon:'+tid,'produces',cid,clean_id(r.get('PMID')))
 # Microbe -> gene
 for _,r in mg.iterrows():
  tid=clean_id(r.get('Gut Microbiota NCBI ID')); gid=clean_id(r.get('Gene ID'))
  if tid.isdigit(): add_node(nodes,'NCBITaxon:'+tid,r.get('Gut Microbiota'),'Microbe',rank=text(r.get('Rank')))
  if gid.isdigit(): add_node(nodes,'NCBIGene:'+gid,r.get('Gene'),'Gene')
  if tid.isdigit() and gid.isdigit(): edge(edges,'NCBITaxon:'+tid,'regulates_host_gene','NCBIGene:'+gid,clean_id(r.get('PMID')))
 # Metabolite -> gene
 for _,r in metg.iterrows():
  cid=clean_id(r.get('Metabolite ChEBI'))
  if not cid: cid='PUBCHEM:'+clean_id(r.get('Metabolite PubChem CID'))
  gid=clean_id(r.get('Gene ID'))
  if cid and cid!='PUBCHEM:': add_node(nodes,cid,r.get('Metabolite'),'Metabolite',xrefs='|'.join(x for x in [text(r.get('Metabolite PubChem CID')),text(r.get('Metabolite KEGG')),text(r.get('Metabolite HMDB'))] if x))
  if gid.isdigit(): add_node(nodes,'NCBIGene:'+gid,r.get('Gene'),'Gene')
  if cid and cid!='PUBCHEM:' and gid.isdigit(): edge(edges,cid,'modulates_host_gene','NCBIGene:'+gid,clean_id(r.get('PMID')))
 # serialize sets
 for e in edges.values():
  e['pmids']='|'.join(sorted(x for x in e['pmids'] if x)); e['years']=''; e['support_count']=int(e['support_count'])
 pd.DataFrame(list(nodes.values()),columns=NODE_COLS).sort_values('id').to_csv(OUT/'gutmgene_nodes.tsv',sep='\t',index=False)
 pd.DataFrame(list(edges.values()),columns=EDGE_COLS).sort_values(['subject','predicate','object']).to_csv(OUT/'gutmgene_edges.tsv',sep='\t',index=False)
 print(f'gutMGene tables: MM={len(mm)} MG={len(mg)} MetG={len(metg)}; nodes={len(nodes)} edges={len(edges)}')
 print('node categories:',pd.DataFrame(nodes.values())['category'].value_counts().to_dict())
 print('predicates:',pd.DataFrame(edges.values())['predicate'].value_counts().to_dict())
if __name__=='__main__': main()
