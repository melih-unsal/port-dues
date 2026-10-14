from portdues.ingestion.pages import LogicalPage
from portdues.ingestion.sections import build_sections, chunk_text


def page(i: int, text: str) -> LogicalPage:
    return LogicalPage(index=i, source_page=i, clip=(0, 0, 1, 1), text=text, needs_vision=False, markdown=text)


FOOTER = "Tariff Book 2024 - all fees in EUR excluding VAT"


def test_numbering_drives_hierarchy_and_footers_are_dropped():
    pages = [
        page(1, f"Definitions\n\"vessel\" means a ship.\n{FOOTER}"),
        page(2, f"SECTION 1\nMARINE SERVICES\n1.1 PILOTAGE\nPer service 100.00\n{FOOTER}"),
        page(3, f"1.1.1 SURCHARGES\n50% outside hours.\n## 1.2 TOWAGE\nPer service 900.00\n{FOOTER}"),
        page(4, f"SECTION 2\nPORT DUES\n2.1 BASIC FEE\nPer 100 GT 12.00\n{FOOTER}"),
    ]
    sections, notes = build_sections(pages, "Test tariff")
    by_key = {s.key: s for s in sections}
    assert by_key["1"].title == "MARINE SERVICES"
    assert by_key["1.1.1"].parent == "1.1"
    assert by_key["1.2"].parent == "1"  # markdown heading level is irrelevant, numbering wins
    assert by_key["2.1"].parent == "2"
    assert by_key["0"].title.startswith("Preamble")
    assert all(FOOTER not in s.text for s in sections)
    assert FOOTER in notes


def test_contents_page_is_skipped():
    toc = "CONTENTS\n" + "\n".join(f"{i}.1 SOME CLAUSE {i} {i + 10}" for i in range(1, 8))
    pages = [page(1, toc), page(2, "1.1 PILOTAGE\nreal text")]
    sections, _ = build_sections(pages, "t")
    clause = next(s for s in sections if s.key == "1.1")
    assert clause.text == "real text"
    assert clause.pages == {2}


def test_mixed_case_numbered_lines_are_list_items_not_headings():
    pages = [page(1, "3.1 FEES\n1. Vessel does not proceed beyond the coast.\n2.1 Port Rule Licences per port")]
    sections, _ = build_sections(pages, "t")
    assert [s.key for s in sections] == ["", "3.1"]


def test_chunking_keeps_blocks_together():
    text = "\n\n".join(f"para {i} " + "x" * 300 for i in range(12))
    chunks = chunk_text(text, size=1000)
    assert len(chunks) > 1
    assert all(len(c) <= 1300 for c in chunks)
    assert "".join(chunks).count("para") == 12
