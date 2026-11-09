"""runner 测试（W2.3）：步序与 argv 同参性、区间过滤、断点续跑跳过。

不真跑模块（重数据）——真链验证由 golden_check W2.3 档承担；
单测锁机制：顺序=依赖序、argv 与 golden 链逐字同构、resume 只跳
"已完且指纹相符"的步。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import runner


# 每步假产物文件名（FakeMain 造出来，喂 runner 的产物护栏/指纹计算）
PRODUCT_FILE = {"tier0": ("out", "merged_edges.tsv"),
                "merge": ("out", "merged_edges.tsv"),
                "interact": ("sg", "interaction_report.tsv"),
                "deepchain": ("sg", "chains.tsv"),
                "inference": ("sg", "chain_annotations.tsv"),
                "hypothesize": ("sg", "hypothesis_cards.tsv")}


class FakeMain:
    def __init__(self, cmd):
        self.cmd = cmd
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        where, fname = PRODUCT_FILE[self.cmd]
        if where == "out":
            base = Path(argv[argv.index("--out") + 1])
        else:
            base = Path(argv[argv.index("--subgraph-dir") + 1])
        base.mkdir(parents=True, exist_ok=True)
        (base / fname).write_text(f"dummy-{self.cmd}", encoding="utf-8")
        return 0


def test_step_order_and_argv(tmp_path, monkeypatch):
    """全链：顺序=STEPS，argv 含正确子命令与目录衔接。"""
    fakes = {}
    for k in runner.MODULES:
        fakes[k] = FakeMain(k)
        monkeypatch.setitem(runner.MODULES, k,
                            type("M", (), {"main": fakes[k]}))
    rc = runner.main(["--disease", "htn",
                      "--tier2-batch", "/tmp/batch",
                      "--out-dir", str(tmp_path)])
    assert rc == 0
    assert [len(f.calls) == 1 for f in fakes.values()]
    assert [fakes[k].calls[0][0] for k in runner.STEPS] == \
        ["tier0", "merge", "--disease", "--subgraph-dir", "--subgraph-dir",
         "--disease"]  # subgraph 两步吃子命令名，其余首参=选项
    # 关键衔接断言：merge 吃 tier0 目录、interact 起吃 subgraph 目录
    merge_call = fakes["merge"].calls[0]
    assert merge_call[merge_call.index("--subgraph-dir") + 1] \
        == str(tmp_path / "tier0")
    assert merge_call[merge_call.index("--out") + 1] == str(tmp_path / "subgraph")
    hyp_call = fakes["hypothesize"].calls[0]
    assert hyp_call[hyp_call.index("--subgraph-dir") + 1] \
        == str(tmp_path / "subgraph")
    assert "--disease" in hyp_call and "htn" in hyp_call


def test_from_to_range(tmp_path, monkeypatch):
    fakes = {}
    for k in runner.MODULES:
        fakes[k] = FakeMain(k)
        monkeypatch.setitem(runner.MODULES, k,
                            type("M", (), {"main": fakes[k]}))
    runner.main(["--disease", "htn", "--tier2-batch", "/tmp/b",
                 "--out-dir", str(tmp_path),
                 "--from-step", "interact", "--to-step", "deepchain"])
    assert [k for k in runner.STEPS if fakes[k].calls] == ["interact",
                                                           "deepchain"]


def test_resume_skips_done_with_matching_sha(tmp_path, monkeypatch):
    """续跑只跳'已完且指纹相符'的步——指纹不符/未记录的照跑。"""
    # 预置 tier0 的代表产物与已完状态（指纹相符）
    (tmp_path / "tier0").mkdir()
    prod = tmp_path / "tier0" / "merged_edges.tsv"
    prod.write_text("x", encoding="utf-8")
    sha = runner._sha16(prod)
    (tmp_path / "run_state.json").write_text(json.dumps(
        {"steps": {"tier0": {"status": "done", "outputs":
                             {"tier0/merged_edges.tsv": sha}}}}),
        encoding="utf-8")
    fakes = {}
    for k in runner.MODULES:
        fakes[k] = FakeMain(k)
        monkeypatch.setitem(runner.MODULES, k,
                            type("M", (), {"main": fakes[k]}))
    rc = runner.main(["--disease", "htn", "--tier2-batch", "/tmp/b",
                      "--out-dir", str(tmp_path), "--resume",
                      "--from-step", "tier0", "--to-step", "merge"])
    assert rc == 0
    # tier0 跳过、merge 照跑
    assert [k for k in runner.STEPS if fakes[k].calls] == ["merge"]


def test_resume_reruns_when_sha_mismatch(tmp_path, monkeypatch):
    """指纹不符=视为未完，照跑（防'状态说完了但产物被动过'的假跳过）。"""
    (tmp_path / "tier0").mkdir()
    (tmp_path / "tier0" / "merged_edges.tsv").write_text("内容变了",
                                                         encoding="utf-8")
    (tmp_path / "run_state.json").write_text(json.dumps(
        {"steps": {"tier0": {"status": "done", "outputs":
                             {"tier0/merged_edges.tsv": "deadbeef"}}}}),
        encoding="utf-8")
    fakes = {}
    for k in runner.MODULES:
        fakes[k] = FakeMain(k)
        monkeypatch.setitem(runner.MODULES, k,
                            type("M", (), {"main": fakes[k]}))
    runner.main(["--disease", "htn", "--tier2-batch", "/tmp/b",
                 "--out-dir", str(tmp_path), "--resume",
                 "--from-step", "tier0", "--to-step", "tier0"])
    assert [k for k in runner.STEPS if fakes[k].calls] == ["tier0"]
