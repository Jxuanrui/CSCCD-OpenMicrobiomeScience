#!/usr/bin/env python3
"""Export Phase 5 confirmed artifacts to JSONL format.

Usage:
    env/bin/python3 export_confirmed_artifacts.py > phase5_confirmed_artifacts.jsonl
"""

import sqlite3
import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

def export_confirmed_artifacts():
    """Export all confirmed artifacts across 10 batches."""
    total_count = 0

    for i in range(1, 11):
        db_path = DATA_DIR / f"phase5_batch_{i:03d}.db"
        if not db_path.exists():
            print(f"⚠ Warning: {db_path} not found", file=sys.stderr)
            continue

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("""
            SELECT
                p.paper_id,
                p.title,
                p.doi,
                p.pmid,
                p.journal,
                p.year,
                a.url,
                a.confidence,
                a.evidence
            FROM Artifact a
            JOIN Paper p ON a.paper_id = p.paper_id
            WHERE a.status = 'confirmed'
            ORDER BY p.paper_id, a.url
        """)

        for row in cursor:
            paper_id, title, doi, pmid, journal, year, url, confidence, evidence_json = row

            record = {
                "paper_id": paper_id,
                "title": title,
                "doi": doi,
                "pmid": pmid,
                "journal": journal,
                "year": year,
                "artifact_url": url,
                "confidence": round(confidence, 3),
                "evidence": json.loads(evidence_json),
                "batch": i
            }

            print(json.dumps(record, ensure_ascii=False))
            total_count += 1

        conn.close()

    print(f"✓ Exported {total_count} confirmed artifacts", file=sys.stderr)

if __name__ == "__main__":
    import sys
    export_confirmed_artifacts()
