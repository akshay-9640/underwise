"""Reading files: CSV counting, PDFs, attachment inventory and upload validation. No model involved."""

import io

import pytest
from pypdf import PdfWriter

from underwise.documents import PacketError, check_upload, count_schedule, load_packet, safe_name

HEADER = "location_id,address,city,state,zip,construction\n"


def csv(tmp_path, text, raw=None):
    p = tmp_path / "locations.csv"
    p.write_bytes(raw if raw is not None else text.encode())
    return count_schedule(p)


def test_counts_nonblank_rows_after_header(tmp_path):
    f = csv(tmp_path, HEADER + "L1,1 Main St,Town,OH,43004,Frame\n\n , , , , , \nL2,2 Main St,Town,OH,43004,Frame\n")
    assert (f.value, f.state) == (2, "KNOWN")
    assert f.evidence[0].locator == "CSV data rows 1-2 (header excluded)"


def test_header_only_is_zero_locations(tmp_path):
    f = csv(tmp_path, HEADER)
    assert (f.value, f.state) == (0, "KNOWN") and "none" in f.evidence[0].locator


def test_crlf_and_bom_are_fine(tmp_path):
    assert csv(tmp_path, None, raw=("﻿" + HEADER + "L1,1 Main St,Town,OH,43004,Frame\r\n").replace("\n", "\r\n").encode()).value == 1


@pytest.mark.parametrize("text,why", [
    ("id,address\nL1,1 Main St\n", "header"),
    (HEADER + "L1,1 Main St,Town\n", "data row 1 has 3 columns"),
    ("", "empty"),
])
def test_unparsable_csv_is_invalid_with_null_count(tmp_path, text, why):
    f = csv(tmp_path, text)
    assert f.state == "INVALID" and f.value is None and why in f.note


def test_non_utf8_csv_is_invalid(tmp_path):
    assert csv(tmp_path, None, raw=b"\xff\xfe\x00bad").state == "INVALID"


def test_cell_values_are_not_validated(tmp_path):
    assert csv(tmp_path, HEADER + "L1,somewhere,Town,ZZ,not-a-zip,unknown\n").value == 1


def test_inventory_and_unreadable_pdfs(make_packet):
    blank = io.BytesIO()
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.write(blank)
    folder = make_packet("P2-missing-info", files={"broken.pdf": b"%PDF-1.4 not really", "scan.pdf": blank.getvalue()})
    p = load_packet(folder)
    by = {a.file: a for a in p.attachments}
    assert by["application.pdf"].readable and by["application.pdf"].pages == 2
    assert not by["broken.pdf"].readable and "could not be read" in by["broken.pdf"].error
    assert not by["scan.pdf"].readable and "no selectable text" in by["scan.pdf"].error
    assert {s.file for s in p.sources} == {"application.pdf", "broker_email.txt"}


def test_missing_csv_gives_missing_schedule(make_packet):
    p = load_packet(make_packet("P1-clean", drop=("locations.csv",)))
    assert p.schedule.state == "MISSING" and p.schedule.evidence[0].locator == "uploaded attachment inventory"


def test_upload_validation():
    check_upload(["broker_email.txt", "application.pdf", "locations.csv"], [10, 10, 10], False)
    check_upload(["application.pdf"], [10], True)
    for names, pasted, msg in [([], False, "Add the packet"), (["a.docx"], False, "only"), (["a.txt"], True, "one broker email"),
                               (["a.csv", "b.csv"], False, "one locations schedule"), (["a.pdf", "A.PDF"], False, "same name")]:
        with pytest.raises(PacketError, match=msg):
            check_upload(names, [10] * len(names), pasted)
    with pytest.raises(PacketError, match="10 MB"):
        check_upload(["big.pdf"], [11 * 1024 * 1024], False)


def test_safe_name_blocks_path_traversal():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("a/b\\c<>.pdf") == "b_c__.pdf"
    assert safe_name("..") == "file"
