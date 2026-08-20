"""A deliverable must never silently replace a different invoice's file.

The supplier-folder copy was a bare ``shutil.copy2``. Combined with naming the
file after the account rather than the source PDF, every invoice for an account
wrote the same path and only the last one survived.  Naming is fixed in
``_generate_output``; this covers the copy itself, so a future naming regression
cannot quietly destroy data again.
"""

from stencil.pipeline.processor import _deliver_without_clobbering


def _write(path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_first_delivery_lands_under_its_own_name(tmp_path):
    src = _write(tmp_path / "src" / "invoice.xlsx", b"alpha")
    dest = tmp_path / "out"
    dest.mkdir()

    delivered = _deliver_without_clobbering(src, dest)

    assert delivered.name == "invoice.xlsx"
    assert delivered.read_bytes() == b"alpha"


def test_redelivering_identical_content_is_idempotent(tmp_path):
    src = _write(tmp_path / "src" / "invoice.xlsx", b"alpha")
    dest = tmp_path / "out"
    dest.mkdir()

    first = _deliver_without_clobbering(src, dest)
    second = _deliver_without_clobbering(src, dest)

    assert first == second
    assert sorted(p.name for p in dest.iterdir()) == ["invoice.xlsx"]


def test_different_content_under_the_same_name_is_never_clobbered(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    first = _write(tmp_path / "a" / "invoice.xlsx", b"january")
    second = _write(tmp_path / "b" / "invoice.xlsx", b"february")

    _deliver_without_clobbering(first, dest)
    landed = _deliver_without_clobbering(second, dest)

    assert landed.name == "invoice (2).xlsx"
    assert (dest / "invoice.xlsx").read_bytes() == b"january"
    assert landed.read_bytes() == b"february"


def test_a_third_distinct_invoice_gets_its_own_slot(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    for i, body in enumerate([b"one", b"two", b"three"]):
        _deliver_without_clobbering(_write(tmp_path / str(i) / "invoice.xlsx", body), dest)

    assert sorted(p.name for p in dest.iterdir()) == [
        "invoice (2).xlsx", "invoice (3).xlsx", "invoice.xlsx",
    ]
    assert {p.read_bytes() for p in dest.iterdir()} == {b"one", b"two", b"three"}


def test_a_repeat_of_an_already_suffixed_file_reuses_its_slot(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    first = _write(tmp_path / "a" / "invoice.xlsx", b"january")
    second = _write(tmp_path / "b" / "invoice.xlsx", b"february")

    _deliver_without_clobbering(first, dest)
    _deliver_without_clobbering(second, dest)
    again = _deliver_without_clobbering(second, dest)

    assert again.name == "invoice (2).xlsx"
    assert sorted(p.name for p in dest.iterdir()) == ["invoice (2).xlsx", "invoice.xlsx"]


def test_no_partial_staging_file_survives(tmp_path):
    src = _write(tmp_path / "src" / "invoice.xlsx", b"alpha")
    dest = tmp_path / "out"
    dest.mkdir()

    _deliver_without_clobbering(src, dest)

    assert not any(p.name.startswith(".") for p in dest.iterdir())
