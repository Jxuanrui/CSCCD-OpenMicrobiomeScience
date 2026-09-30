#!/usr/bin/env python3
"""MeSH 查表 role 评估（规则已冻结 sha256:b100c670…，监工 P0-1/P0-2）。"""
import json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/08_route_eval'))
from mesh_normalize import normalize_mesh

# ===== 冻结的过程词列表（不得修改——sha256 已锁定）=====
ENDPOINT_WORDS = (
    'inflammation', 'carcinogenesis', 'dysbiosis', 'neoplasia', 'injury',
    'damage', 'necrosis', 'fibrosis', 'steatosis', 'progression',
    'proliferation', 'oxidative stress', 'severity', 'tumorigenesis',
    'oncogenesis', 'metastasis'
)

def predict(disease_name_or_id: str) -> str:
    nm = normalize_mesh(disease_name_or_id.replace('MESH:', ''))
    if not nm.get('resolved'):
        nm = normalize_mesh(disease_name_or_id)
    if not nm.get('resolved'):
        return '9'
    n = nm['preferred_name'].lower()
    for w in ENDPOINT_WORDS:
        if w in n:
            return '3'
    t = nm.get('tree_numbers', [])
    return '2' if any(x.startswith('C') or x.startswith('F03') for x in t) else '9'

def wilson(p, n, z=1.96):
    if n == 0: return (0, 1)
    d = 1 + z*z/n; c = p + z*z/(2*n)
    h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return ((c-h)/d, (c+h)/d)

def evaluate(items, label):
    """items: list of (name_or_id, gold_role_str)"""
    tp = {'2': 0, '3': 0, '9': 0}
    fp = {k: 0 for k in tp}
    fn = {k: 0 for k in tp}
    detail = []
    for name, gold in items:
        pred = predict(name)
        if pred == gold: tp[gold] += 1
        else:
            fp[pred] += 1
            fn[gold] += 1
        detail.append({'name': name, 'gold': gold, 'pred': pred,
                       'correct': pred == gold})

    n = len(items)
    correct = sum(tp.values())
    acc = correct / n if n else 0
    lb, ub = wilson(acc, n)

    print(f"\n===== {label} (n={n}) =====")
    print(f"  Accuracy: {correct}/{n} = {acc:.3f}  Wilson 95% CI: [{lb:.3f}, {ub:.3f}]")
    for cls in ('2', '3', '9'):
        p = tp[cls] / (tp[cls] + fp[cls]) if tp[cls] + fp[cls] > 0 else 0
        r = tp[cls] / (tp[cls] + fn[cls]) if tp[cls] + fn[cls] > 0 else 0
        f1 = 2*p*r/(p+r) if p+r > 0 else 0
        print(f"  class {cls}: P={p:.3f} R={r:.3f} F1={f1:.3f} (tp={tp[cls]} fp={fp[cls]} fn={fn[cls]})")
    return {'n': n, 'correct': correct, 'acc': round(acc, 4),
            'ci': [round(lb, 4), round(ub, 4)],
            'per_class': {c: {'P': round(tp[c]/(tp[c]+fp[c]), 3) if tp[c]+fp[c]>0 else 0,
                              'R': round(tp[c]/(tp[c]+fn[c]), 3) if tp[c]+fn[c]>0 else 0}
                          for c in ('2','3','9')},
            'detail': detail}

if __name__ == '__main__':
    # dev 集（用户盲标 50 条）
    import csv
    dev = []
    for u in csv.DictReader(open('data/merged/route_eval/user_blind_labels_50_ORIGINAL.tsv'), delimiter='\t'):
        dev.append((u['object'], u['role'].strip()))
    dev_r = evaluate(dev, 'DEV (user blind 50)')

    # test 集（剩余 100 条——需用户标注 gold 后启用）
    # 当前仅有预测（无 gold），等用户标注后填入
    print("\n===== TEST (100 条，等用户标注 gold) =====")
    print("  预测已就绪（mesh_lookup_150_preds.jsonl 后 100 条），等 gold 标注")

    json.dump(dev_r, open('data/merged/route_eval/eval_dev_results.json', 'w'), ensure_ascii=False, indent=1)
