from __future__ import annotations

from pathlib import Path

import yaml

from mra.knowledge.cli import main


def _write_entry(path: Path, **overrides: object) -> Path:
    values: dict[str, object] = {
        "id": "methods-cli",
        "package": "methods",
        "title": "队列A批次审计",
        "version": 1,
        "evidence_level": "confirmed_rule",
        "applicability": "队列A",
        "content": "Goldberg 方法用于批次混杂审计。",
        "source": [{"kind": "literature", "ref": "PMID:456", "date": "2026-09-12"}],
        "approval": {"status": "approved", "by": "pi", "revision": "r1"},
    }
    values.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(values, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_cli_ingest_search_and_list_continue_after_failure(
    tmp_path: Path, capsys: object
) -> None:
    yaml_dir = tmp_path / "packages"
    _write_entry(yaml_dir / "nested" / "methods.yaml")
    _write_entry(
        yaml_dir / "context.yaml",
        id="context-cli",
        package="context",
        title="Cohort context",
        applicability="项目X",
        content="Cohort context",
    )
    (yaml_dir / "bad.yaml").write_text("id: invalid\n", encoding="utf-8")
    db_path = tmp_path / "var" / "knowledge.db"

    assert main(["ingest", str(yaml_dir), "--db", str(db_path)]) == 1
    ingest_output = capsys.readouterr().out
    assert "methods-cli/1/methods" in ingest_output
    assert "context-cli/1/context" in ingest_output
    assert "FAILED" in ingest_output
    assert "success=2 failure=1" in ingest_output

    assert main(["search", "队列A", "--db", str(db_path)]) == 0
    search_output = capsys.readouterr().out
    assert "队列A批次审计|confirmed_rule|队列A|PMID:456" in search_output

    assert main(["list", "--package", "methods", "--db", str(db_path)]) == 0
    list_output = capsys.readouterr().out
    assert "methods-cli/1/methods" in list_output
    assert "context-cli/1/context" not in list_output
