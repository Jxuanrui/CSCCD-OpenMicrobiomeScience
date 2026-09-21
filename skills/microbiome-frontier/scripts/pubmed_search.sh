#!/usr/bin/env bash
# pubmed_search.sh — Query PubMed for recent gut microbiome literature
#
# Usage:
#   bash pubmed_search.sh "<query>" [top_n] [from_year] [to_year]
#
# Examples:
#   bash pubmed_search.sh "IBD gut microbiome metagenomics" 10 2023
#   bash pubmed_search.sh "gut phageome ulcerative colitis" 8 2024
#   bash pubmed_search.sh "gut-brain axis Alzheimer microbiome" 5 2023 2025
#
# Output: ranked list with PMID / year / journal / title
# No API key required for basic use (rate limit: 3 req/sec).

set -euo pipefail

QUERY="${1:-gut microbiome}"
TOP_N="${2:-10}"
FROM_YEAR="${3:-2023}"
TO_YEAR="${4:-2026}"
BASE_URL="https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# URL-encode query
ENCODED=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$QUERY")

echo "[PubMed Search] Query: $QUERY | Years: $FROM_YEAR-$TO_YEAR | Top: $TOP_N"

# Step 1: Search for PMIDs
SEARCH_URL="${BASE_URL}/esearch.fcgi?db=pubmed&term=${ENCODED}&datetype=pdat&mindate=${FROM_YEAR}&maxdate=${TO_YEAR}&retmax=${TOP_N}&sort=relevance&retmode=json"
SEARCH_TMP=$(mktemp /tmp/pubmed_search_XXXXXX.json)
trap 'rm -f "$SEARCH_TMP" "$SUMMARY_TMP"' EXIT

curl -s --max-time 20 "$SEARCH_URL" -o "$SEARCH_TMP"

if [ ! -s "$SEARCH_TMP" ]; then
  echo "[pubmed_search] ERROR: No response from PubMed search" >&2
  exit 1
fi

# Extract PMIDs and total count
PMIDS=$(python3 -c "
import json, sys
with open('$SEARCH_TMP') as f:
    d = json.load(f)
result = d.get('esearchresult', {})
ids = result.get('idlist', [])
total = result.get('count', '0')
print(total + '|' + ','.join(ids))
")

TOTAL=$(echo "$PMIDS" | cut -d'|' -f1)
ID_LIST=$(echo "$PMIDS" | cut -d'|' -f2)

if [ -z "$ID_LIST" ]; then
  echo "[pubmed_search] No results found for this query."
  exit 0
fi

echo "[PubMed Search] Total matching: $TOTAL | Fetching details for top $TOP_N..."

# Step 2: Fetch summaries for all PMIDs in one call
SUMMARY_URL="${BASE_URL}/esummary.fcgi?db=pubmed&id=${ID_LIST}&retmode=json"
SUMMARY_TMP=$(mktemp /tmp/pubmed_summary_XXXXXX.json)

curl -s --max-time 25 "$SUMMARY_URL" -o "$SUMMARY_TMP"

# Step 3: Parse and render
python3 - "$QUERY" "$TOTAL" "$ID_LIST" "$SUMMARY_TMP" <<'PYEOF'
import json, sys

query_label = sys.argv[1]
total       = sys.argv[2]
id_list     = sys.argv[3].split(',')
summary_file = sys.argv[4]

with open(summary_file, 'r', encoding='utf-8') as f:
    try:
        data = json.load(f)
    except Exception as e:
        print(f"[pubmed_search] JSON parse error: {e}")
        sys.exit(1)

results = data.get('result', {})

print("=" * 88)
for i, pmid in enumerate(id_list, 1):
    if pmid not in results:
        continue
    info = results[pmid]
    title   = (info.get('title') or 'N/A').rstrip('.')
    journal = (info.get('fulljournalname') or info.get('source') or 'Unknown')
    pubdate = (info.get('pubdate') or '?')[:7]
    doi     = ''
    for art_id in info.get('articleids', []):
        if art_id.get('idtype') == 'doi':
            doi = art_id.get('value', '')
            break

    print(f"\n{i:>2}. [PMID {pmid}] [{pubdate}] {title[:75]}")
    print(f"    Journal : {journal[:65]}")
    if doi:
        print(f"    DOI     : https://doi.org/{doi}")
    else:
        print(f"    PubMed  : https://pubmed.ncbi.nlm.nih.gov/{pmid}/")

print("\n" + "=" * 88)
print(f"Source: PubMed E-utilities | Total in database: {total} | Shown: {len(id_list)}")
PYEOF
