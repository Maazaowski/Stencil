"""Static checks for the Create Profile with AI blueprint upload copy."""

from pathlib import Path


def test_profile_authoring_ui_uses_blueprint_copy_and_accepts_xls():
    page = Path("frontend/src/app/profiles/new/assistant/page.tsx").read_text(encoding="utf-8")

    assert 'accept=".xlsx,.xls"' in page
    assert "Attach XLS/XLSX blueprint (optional)" in page
    assert "Differs from XLS blueprint" in page
    assert "Matches the XLS blueprint" in page
    assert "Attach expected output" not in page


def test_profile_authoring_chat_shows_optimistic_user_message_and_working_copy():
    page = Path("frontend/src/app/profiles/new/assistant/page.tsx").read_text(encoding="utf-8")

    assert "pendingUserMessage" in page
    assert "setPendingUserMessage({ role: \"user\", content: text })" in page
    assert "authoringProgressWords" in page
    for word in ["Processing", "Thinking", "Sampling", "Extracting", "Re-extracting", "Reconciling", "Previewing"]:
        assert word in page
    assert "Re-extracting changed profiles" in page


def test_profile_authoring_ui_supports_server_sample_removal_and_job_queue():
    page = Path("frontend/src/app/profiles/new/assistant/page.tsx").read_text(encoding="utf-8")
    hook = Path("frontend/src/hooks/use-profile-authoring.ts").read_text(encoding="utf-8")
    types = Path("frontend/src/types/index.ts").read_text(encoding="utf-8")

    assert "useDeleteAuthoringInvoice" in hook
    assert "DELETE" in hook or ".delete<" in hook
    assert "handleDeleteInvoice" in page
    assert 'title="Remove sample"' in page
    assert "samplesLocked" in page
    assert "activeTurnJobs.map" in page
    assert 'session?.status !== "finalized"' in page
    assert 'status === "queued" || j.status === "running"' in hook
    assert 'status: "active" | "running" | "finalized"' in types
    assert "AuthoringJobSummary" in types


def test_profile_authoring_ui_can_cancel_inflight_ai_work():
    page = Path("frontend/src/app/profiles/new/assistant/page.tsx").read_text(encoding="utf-8")
    hook = Path("frontend/src/hooks/use-profile-authoring.ts").read_text(encoding="utf-8")
    types = Path("frontend/src/types/index.ts").read_text(encoding="utf-8")

    assert "useCancelAuthoring" in hook
    assert "/cancel" in hook
    assert "handleCancelAuthoring" in page
    assert "In-flight AI requests were stopped" in page
    assert 'status: "queued" | "running" | "done" | "error" | "cancelled"' in types


def test_profile_authoring_preview_scrolls_without_hiding_evidence_plan():
    page = Path("frontend/src/app/profiles/new/assistant/page.tsx").read_text(encoding="utf-8")

    assert 'className="max-h-[58vh] overflow-y-auto overscroll-contain pr-1"' in page
    assert 'id="evidence-plan"' in page
    assert "scrollIntoView({ behavior: \"smooth\" })" in page
    assert "View plan" in page


def test_profile_editor_supports_explicit_date_formats_without_dropping_labels():
    page = Path("frontend/src/app/profiles/[profileId]/page.tsx").read_text(encoding="utf-8")
    types = Path("frontend/src/types/index.ts").read_text(encoding="utf-8")

    assert "date_format?: string | null" in types
    assert "DATE_FORMAT_SUGGESTIONS" in page
    assert 'value: "%d/%m/%Y"' in page
    assert 'value: "%m/%d/%Y"' in page
    assert 'value: "%Y-%m-%d"' in page
    assert 'field.type === "date"' in page
    assert "Input date format" in page
    assert "setFieldDateFormat" in page
    assert "...existing" in page
    assert "changing one must never erase the other" in page
