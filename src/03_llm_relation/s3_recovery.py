#!/usr/bin/env python3
"""S3 恢复脚本：仅对 dropped_judge 记录用 GLM 中转站重跑 judge.

不修改 staging 主文件——结果写到 s3_recovery_results.jsonl，之后合并。
"""
import json, os, re, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "data/staging"
CANDIDATES = STAGING / "s3_recovery_candidates.jsonl"
RESULTS = STAGING / "s3_recovery_results.jsonl"

BASE = os.getenv("JUDGE_BASE_URL", "https://ai.shimiaocheng.top/v1")
KEY = os.getenv("JUDGE_KEY", "")
MODEL = os.getenv("JUDGE_MODEL", "GLM-5.3")

JUDGE_SYSTEM = """你是一个科学证据审查员。给定一个断言（主体、谓词、客体）和证据句子，
判断该断言是否被证据明确支持。

输出格式（严格）：
- 如果支持：SUPPORTED
- 如果不支持：NEI (Not Enough Information)
- 如果矛盾：CONTRADICTED

只输出上述三个词之一，不要其他文字。"""


def call_judge(subject_name, predicate, object_name, evidence, pmid):
    prompt = f"""断言：{subject_name} --[{predicate}]--> {object_name}
证据句子：{evidence}
PMID: {pmid}

请判断该断言是否被上述证据明确支持。"""

    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 256,
        "temperature": 0,
    }
    req = urllib.request.Request(
        BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = json.load(resp)["choices"][0]["message"]["content"]
                # 解析
                c = content.strip().upper()
                if "SUPPORTED" in c:
                    return "SUPPORTED", content
                elif "NEI" in c or "NOT ENOUGH" in c:
                    return "NEI", content
                elif "CONTRADICT" in c:
                    return "CONTRADICTED", content
                else:
                    return "NEI", content  # 无法解析时保守处理
        except Exception as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
            else:
                return "ERROR", str(e)[:100]


def main():
    if not KEY:
        print("错误：请设置 JUDGE_KEY 环境变量")
        sys.exit(1)

    records = [json.loads(l) for l in open(CANDIDATES)]
    print(f"[S3-recovery] 候选: {len(records)} | 模型: {MODEL} @ {BASE}")

    results_f = open(RESULTS, "w")
    stats = {"SUPPORTED": 0, "NEI": 0, "CONTRADICTED": 0, "ERROR": 0}

    for i, r in enumerate(records):
        subj = r.get("subject", {})
        obj = r.get("object", {})
        subj_name = subj.get("name", subj.get("id", ""))
        obj_name = obj.get("name", obj.get("id", ""))
        predicate = r.get("predicate", "")
        evidence = r.get("sentence", "") or r.get("evidence", "")
        pmid = r.get("pmid", "")

        verdict, raw = call_judge(subj_name, predicate, obj_name, evidence, pmid)
        stats[verdict] += 1

        result = {
            "idx": i + 1,
            "subject_id": subj.get("id", ""),
            "subject_name": subj_name,
            "predicate": predicate,
            "object_id": obj.get("id", ""),
            "object_name": obj_name,
            "pmid": pmid,
            "evidence": evidence[:200],
            "judge_verdict": verdict,
            "judge_raw": raw[:100],
            "original_status": r.get("status", ""),
            "model": MODEL,
        }
        results_f.write(json.dumps(result, ensure_ascii=False) + "\n")
        results_f.flush()

        if (i + 1) % 50 == 0:
            print(f"  [{i+1}/{len(records)}] {stats}", flush=True)
        time.sleep(0.2)  # 限速

    results_f.close()
    print(f"\n[S3-recovery] 完成: {stats}")
    print(f"结果: {RESULTS}")

    # 汇总
    summary = {
        "total": len(records),
        "model": MODEL,
        "endpoint": BASE,
        **stats,
        "survival_rate": round(stats["SUPPORTED"] / len(records), 4) if records else 0,
    }
    (STAGING / "s3_recovery_summary.json").write_text(json.dumps(summary, indent=1))
    print(f"汇总: {STAGING / 's3_recovery_summary.json'}")


if __name__ == "__main__":
    main()
