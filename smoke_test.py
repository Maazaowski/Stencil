"""Smoke test -- verify installation works without any AI calls or database."""

from decimal import Decimal


def main():
    print("Stencil Smoke Test")
    print("=" * 50)

    # 1. Config
    from stencil.config import settings
    settings.ensure_directories()
    print(f"[OK] Config loaded. Data dir: {settings.data_dir}")
    for d in [settings.inbound_dir, settings.processing_dir, settings.completed_dir,
              settings.exceptions_dir, settings.archive_dir]:
        assert d.exists(), f"Directory not created: {d}"
    print("[OK] All data directories created")

    # 2. Canonical schema
    from stencil.validation.schema import (
        CanonicalInvoice,
        ExtractionMetadata,
        ExtractionPath,
        InvoiceHeader,
        LineItem,
        OutputType,
    )
    invoice = CanonicalInvoice(
        intake_id="smoke-test-001",
        output_type=OutputType.STANDARD,
        header=InvoiceHeader(
            supplier_name="Test Supplier Co",
            invoice_number="INV-2026-0042",
            invoice_date="2026-01-15",
            due_date="2026-02-15",
            account_number="ACCT-12345",
            currency="USD",
        ),
        line_items=[
            LineItem(line_number=1, service_id="SVC-001",
                     description="Monthly Ethernet Service", charge_type="recurring",
                     amount=Decimal("1500.00")),
            LineItem(line_number=2, service_id="SVC-002",
                     description="Installation Fee", charge_type="one_time",
                     amount=Decimal("250.00")),
            LineItem(line_number=3, description="Sales Tax", charge_type="tax",
                     amount=Decimal("131.25")),
        ],
        subtotal=Decimal("1750.00"),
        tax=Decimal("131.25"),
        total_due=Decimal("1881.25"),
        metadata=ExtractionMetadata(extraction_path=ExtractionPath.AI, total_pages=3),
    )
    print(f"[OK] Canonical schema: {len(invoice.line_items)} line items, total=${invoice.total_due}")

    # 3. Reconciliation
    from stencil.validation.reconciler import reconcile
    recon = reconcile(invoice)
    print(f"[OK] Reconciliation: reconciled={recon.is_reconciled}, "
          f"variance={recon.variance}, warnings={len(recon.warnings)}")

    # 4. XLSX output
    test_dir = settings.data_dir / "_smoke_test"
    test_dir.mkdir(parents=True, exist_ok=True)

    from stencil.output.xlsx_writer import write_xlsx
    xlsx_path = write_xlsx(invoice, test_dir / "test_output.xlsx")
    assert xlsx_path.exists()
    print(f"[OK] XLSX written: {xlsx_path} ({xlsx_path.stat().st_size:,} bytes)")

    # 5. JSON output
    from stencil.output.json_writer import write_canonical_json, write_extraction_log
    json_path = write_canonical_json(invoice, test_dir / "test_canonical.json")
    log_path = write_extraction_log(invoice, test_dir / "test_log.json")
    assert json_path.exists()
    assert log_path.exists()
    print(f"[OK] Canonical JSON: {json_path}")
    print(f"[OK] Extraction log: {log_path}")

    # 6. Supplier profiles
    from stencil.profiles.loader import load_all_profiles
    profiles = load_all_profiles()
    print(f"[OK] Supplier profiles: {len(profiles)} loaded")
    for pid, p in profiles.items():
        print(f"     - {pid} ({p.identity.canonical_name}, {p.status})")

    # 7. Extraction model schema
    from stencil.models.schema import ColumnRule, ExtractionModel, HeaderFieldRule, TableRule
    model = ExtractionModel(
        model_id="test-model-v1",
        supplier="Test Supplier",
        layout_fingerprint="sha256:abc123",
        header_rules={
            "invoice_number": HeaderFieldRule(pattern=r"Invoice #:\s*(\S+)"),
        },
        table_rules=TableRule(
            start_marker="Service Detail",
            columns=[ColumnRule(name="description", index=0, data_type="string")],
        ),
    )
    print(f"[OK] Extraction model schema: {model.model_id}")

    # Cleanup
    import shutil
    shutil.rmtree(test_dir, ignore_errors=True)

    print("\n" + "=" * 50)
    print("ALL SMOKE TESTS PASSED")
    print("=" * 50)


if __name__ == "__main__":
    main()
