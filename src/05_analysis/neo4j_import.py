#!/usr/bin/env python3
"""将 merged_nodes.tsv / merged_edges.tsv 幂等导入 Neo4j，并执行单菌扇出查询。"""
import argparse, csv, json, os, time
from pathlib import Path
from neo4j import GraphDatabase
ROOT=Path(__file__).resolve().parents[2]

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--uri',default='bolt://127.0.0.1:17687'); ap.add_argument('--password',default=os.getenv('NEO4J_PASSWORD')); ap.add_argument('--microbe',default='NCBITaxon:1304'); args=ap.parse_args()
 driver=GraphDatabase.driver(args.uri,auth=('neo4j',args.password)); driver.verify_connectivity()
 with driver.session(database='neo4j') as s:
  s.run('CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE').consume()
  nodes=list(csv.DictReader((ROOT/'data/merged/merged_nodes.tsv').open(),delimiter='\t'))
  edges=list(csv.DictReader((ROOT/'data/merged/merged_edges.tsv').open(),delimiter='\t'))
  # 当前脚本负责重建项目实例，因此先清空该实例中的旧图，避免历史关系残留。
  s.run('MATCH (n:Entity) DETACH DELETE n').consume()
  s.run('UNWIND $rows AS r MERGE (n:Entity {id:r.id}) SET n.name=r.name,n.category=r.category,n.aliases=r.aliases,n.xrefs=r.xrefs,n.tax_rank=r.tax_rank',rows=nodes).consume()
  s.run('UNWIND $rows AS r MATCH (a:Entity {id:r.subject}),(b:Entity {id:r.object}) MERGE (a)-[e:RELATED {predicate:r.predicate}]->(b) SET e.source_type=r.source_type,e.evidence_tier=r.evidence_tier,e.pmids=r.pmids,e.years=r.years,e.support_count=toInteger(r.support_count),e.confidence=toFloat(r.confidence),e.polarity=r.polarity,e.last_updated=r.last_updated',rows=edges).consume()
  counts=s.run('MATCH (n:Entity) RETURN count(n) AS nodes').single()['nodes']; rels=s.run('MATCH ()-[r:RELATED]->() RETURN count(r) AS edges').single()['edges']
  t=time.perf_counter(); result=s.run('MATCH (m:Entity {id:$id})-[r:RELATED*1..3]-(n:Entity) WHERE n.category IN ["Disease","Metabolite","Drug","Food"] RETURN DISTINCT n.id AS id,n.name AS name,n.category AS category LIMIT 1000',id=args.microbe).data(); elapsed=time.perf_counter()-t
 print(json.dumps({'nodes':counts,'edges':rels,'fanout_microbe':args.microbe,'fanout_results':len(result),'query_seconds':round(elapsed,4)},ensure_ascii=False,indent=2)); driver.close()
if __name__=='__main__': main()
