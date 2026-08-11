from pathlib import Path

from app.auth import hash_password, verify_password
from app.documents import ParsedSection, chunk_sections, lexical_score, parse_file, safe_filename
from app.lemonade import extract_json_object


def test_password_roundtrip():
    encoded = hash_password("VeryStrong123!")
    assert verify_password("VeryStrong123!", encoded)
    assert not verify_password("wrong", encoded)


def test_extract_json_wrapped():
    value = extract_json_object('```json\n{"decision":"ALLOWED","answer":"ok"}\n```')
    assert value["decision"] == "ALLOWED"


def test_safe_filename():
    assert ".." not in safe_filename("../../policy?.txt")
    assert safe_filename("../../policy?.txt").endswith(".txt")


def test_chunking_overlap():
    chunks = chunk_sections([ParsedSection("A", "Монгол дүрэм. " * 300)], chunk_size=300, overlap=40)
    assert len(chunks) > 2
    assert all(chunk.text for chunk in chunks)


def test_lexical_score_relevant():
    good = lexical_score("8 хувь хөнгөлөлт", "8 хувь хөнгөлөлтөд менежерийн зөвшөөрөл авна")
    bad = lexical_score("8 хувь хөнгөлөлт", "компанийн автомашины журам")
    assert good > bad


def test_parse_txt(tmp_path: Path):
    p = tmp_path / "rule.txt"
    p.write_text("Компанийн дүрэм журмын туршилт", encoding="utf-8")
    sections = parse_file(p)
    assert sections and "дүрэм" in sections[0].text


def test_metric_extraction_and_boundaries():
    from app.assistant_engine import _metric_values, _in_range
    from app.retrieval import RetrievedRule

    assert _metric_values("8% хөнгөлөлт")["percent"] == 8
    assert _metric_values("5 сая төгрөг")["mnt"] == 5_000_000
    rule = RetrievedRule(
        id="r", title="r", text="r", category="x", decision_hint="ALLOWED", approver="",
        source_section="", source_document_id="", metric="percent", min_value=5, max_value=10,
        min_inclusive=False, max_inclusive=True, priority=100, score=1.0,
    )
    assert not _in_range(5, rule)
    assert _in_range(10, rule)
