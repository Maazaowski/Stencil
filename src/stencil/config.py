from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ST_", case_sensitive=False, extra="ignore")

    app_name: str = "Stencil"
    debug: bool = False

    # Folder paths
    # data_dir = the SUPPLIER data root. It contains ONLY per-supplier folders
    # (each profile's inbound PDFs and delivered Excel output, e.g.
    # /data/Lumen/{pdf,xls}). Nothing operational is written here.
    data_dir: Path = Path("stencil_data")
    # accounts_scan_dir = the root the Accounts view scans for <customer>/<account>/pdf
    # folders. Defaults to data_dir/Invoices; override per deployment (prod maps
    # the Invoices share to /data/Invoices, but some layouts use /data/Astera/Invoices).
    accounts_scan_dir: Path | None = Field(default=None)
    # On startup, kick a background accounts sync only if the snapshot is missing
    # or older than this many hours (the page always serves the last snapshot).
    accounts_sync_ttl_hours: int = 12
    # work_dir = the OPERATIONAL state root. It holds the processing lifecycle
    # artifacts — what was ingested, in-flight, completed, excepted, archived.
    # Kept separate from data_dir so the supplier folders stay clean.
    work_dir: Path = Path("stencil_work")
    inbound_dir: Path | None = Field(default=None)
    processing_dir: Path | None = Field(default=None)
    completed_dir: Path | None = Field(default=None)
    exceptions_dir: Path | None = Field(default=None)
    archive_dir: Path | None = Field(default=None)

    # Database (MySQL via PyMySQL)
    database_url: str = "mysql+pymysql://stencil:stencil@localhost:3306/stencil"

    # Master key (Fernet) used to encrypt provider API keys entered in the app and
    # stored in MySQL. Generate with: `python -c "from cryptography.fernet import
    # Fernet; print(Fernet.generate_key().decode())"`. When unset, in-app key
    # storage is disabled and only the ST_*_API_KEY env vars below are used.
    secret_key: str = ""

    # Login system. The first admin is bootstrapped from these two env vars at
    # startup ONLY while the users table is empty; remove them from .env after
    # first boot. Sessions are HTTP-only cookies backed by the auth_sessions
    # table (token hash only).
    admin_email: str = ""
    admin_password: str = ""
    session_ttl_days: int = 7
    # Mark the session cookie Secure (HTTPS-only). Leave unset for the safe
    # default: ON in production, OFF when `debug` is on — a Secure cookie is
    # silently dropped over plain http://localhost, which would break dev login.
    # Set ST_SESSION_COOKIE_SECURE explicitly to override either way.
    session_cookie_secure: bool | None = None

    # CORS / Frontend
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    frontend_url: str = "http://localhost:3000"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    # Number of invoice/job tasks the Celery worker process may run at once.
    # Applied when the worker starts; change in Settings, then restart worker.
    worker_concurrency: int = 2

    # LLM provider: "openai" or "anthropic". The active provider's key is resolved
    # in-app (encrypted, via Settings) or from the env fallback for that provider.
    # See src/stencil/llm/ for the provider abstraction.
    llm_provider: str = "openai"

    # OpenAI
    openai_api_key: str = ""
    # Anthropic (Claude) — env fallback used when no in-app key is stored.
    anthropic_api_key: str = ""
    openai_model_classification: str = "gpt-5.5"
    openai_model_extraction: str = "gpt-5.5"
    openai_model_model_generation: str = "gpt-5.5"
    openai_max_retries: int = 3
    openai_timeout: int = 300
    # Max completion tokens. Must cover BOTH the model's reasoning AND the JSON
    # output — a large multi-page invoice can exceed the old 16k cap, which
    # returns 200 OK with empty content. Keep generous.
    openai_max_output_tokens: int = 32768
    ai_extraction_mode: str = "compact_chunked"  # compact_chunked or legacy
    ai_chunk_max_layout_chars: int = 24000
    ai_chunk_overlap_rows: int = 3
    ai_chunk_max_retries: int = 3
    ai_chunk_concurrency: int = 1
    # Evidence-driven profile discovery. Shadow mode records a plan/validation
    # report while legacy extraction remains authoritative during rollout.
    profile_discovery_engine_enabled: bool = True
    profile_discovery_shadow_mode: bool = True
    profile_discovery_max_ai_chunks: int = 40
    profile_discovery_max_prompt_tokens: int = 120_000
    profile_discovery_invoice_concurrency: int = 4
    profile_discovery_max_refinements: int = 2
    profile_discovery_historical_id_coverage: float = 0.90
    # Dev-only: how many captured AI-call prompt dumps to keep under work_dir/ai_debug
    # (oldest pruned). Only written when the `debug` runtime setting is on.
    ai_debug_max_files: int = 300
    # Dev-only prompt-evaluation harness: labeled-case corpus dir + run retention.
    # Default is the repo-root `eval_cases/` dir, mounted into the containers at
    # /app/eval_cases (WORKDIR=/app), so cases added on the host appear live.
    eval_corpus_dir: str = "eval_cases"
    eval_max_runs: int = 50
    # Default worker count for an eval run (overridable per-run from the Evals UI).
    # Each case opens its own short-lived DB sessions, so cases run in a thread pool.
    eval_run_concurrency: int = 4

    # Extraction is text-first: we send the PDF's embedded text layer (accurate
    # for numbers) and only attach a page image when a page has too little text
    # to be a real text layer (i.e. a scanned/image-only page that needs vision).
    extraction_send_page_images: bool = True  # set False to force pure text-only
    extraction_use_layout_text: bool = True
    extraction_min_page_text_chars: int = 80  # pages with fewer chars get an image
    extraction_image_dpi: int = 150
    extraction_image_detail: str = "high"

    # Model pricing — USD per 1,000,000 tokens, keyed by model name (globally
    # unique across providers). Override via ST_OPENAI_PRICING (JSON) when rates
    # change. OpenAI source: developers.openai.com/api/docs/models (verified 2026-06);
    # Anthropic source: platform.claude.com/docs (verified 2026-06).
    openai_pricing: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "gpt-5.6-sol": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
            "gpt-5.6-terra": {"input": 2.5, "cached_input": 0.25, "output": 15.0},
            "gpt-5.6-luna": {"input": 1.0, "cached_input": 0.1, "output": 6.0},
            "gpt-5.5": {"input": 5.0, "cached_input": 0.5, "output": 30.0},
            "gpt-4o": {"input": 2.5, "cached_input": 1.25, "output": 10.0},
            "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.6},
        }
    )
    # Anthropic pricing — USD per 1,000,000 tokens, override via ST_ANTHROPIC_PRICING (JSON).
    anthropic_pricing: dict[str, dict[str, float]] = Field(
        default_factory=lambda: {
            "claude-opus-4-8": {"input": 5.0, "cached_input": 0.5, "output": 25.0},
            "claude-sonnet-4-6": {"input": 3.0, "cached_input": 0.3, "output": 15.0},
            "claude-haiku-4-5": {"input": 1.0, "cached_input": 0.1, "output": 5.0},
        }
    )
    # Fallback rate used when a model is not found in openai_pricing.
    openai_pricing_default: dict[str, float] = Field(
        default_factory=lambda: {"input": 5.0, "cached_input": 0.5, "output": 30.0}
    )

    # Extraction
    confidence_threshold: float = 0.75
    reconciliation_variance_threshold: float = 0.01

    # Above this page count a document that fails its arithmetic check is not
    # delivered to the supplier folder. A short invoice can be eyeballed; nobody
    # verifies 3,400 rows by hand, so shipping an unchecked large document is
    # worse than shipping nothing. The full package is still written to
    # completed/ and an exception is raised for review. 0 disables the gate.
    # Measured: reconciliation passes 68% under 100 pages and 25% above 200.
    blocking_check_page_threshold: int = 100

    # --- sample authoring (P2) -------------------------------------------------
    # For a long document with no usable model, read a dozen representative pages
    # with AI, author extraction rules from them, then replay those rules across
    # every page deterministically. Measured on a 656-page invoice: full AI
    # extraction sends 2.87M characters over 123 sequential calls and returned
    # between 0 and 384 rows across four runs; a rule replay of the same document
    # takes 0.09s, costs nothing, and is identical every time.
    #
    # OFF by default: this path has not yet been validated against live AI, and an
    # unvalidated route must not change production behaviour on its own.
    sample_authoring_enabled: bool = False
    # Below this page count, ordinary extraction is cheap and reliable enough.
    sample_authoring_min_pages: int = 100
    # How many pages the authoring pass is allowed to read.
    sample_authoring_max_sample_pages: int = 12
    # Replayed output must reconcile to within this fraction of the stated total
    # before it is trusted in place of full extraction. Deliberately tighter than
    # reconciliation_variance_threshold: this decides whether to skip reading the
    # document at all, so it must be harder to pass than an ordinary check.
    sample_authoring_max_variance: float = 0.005
    model_validation_required_successes: int = 3
    model_validation_max_failures: int = 3
    # Grounded authoring refine loop: author -> execute -> diff against the whole
    # training set, then re-author with the structured diff as feedback until the
    # model reproduces every invoice or this attempt budget is exhausted.
    model_authoring_max_attempts: int = 3
    model_generation_max_image_pages: int = 30
    # Max same-fingerprint invoices used to author one model. Larger sets cover
    # more row shapes but cost more authoring tokens; over the cap we keep the
    # invoices that previously failed plus the most line-item-rich ones.
    model_training_max_set_size: int = 5
    # During a training run the per-invoice AI ground-truth extraction (train +
    # holdout) runs in a bounded thread pool — these are I/O-bound OpenAI calls.
    # Each worker uses its own DB session. 1 disables parallelism (sequential).
    model_training_extract_concurrency: int = 5
    # Authoring context budget. The authoring prompt carries a representative
    # SAMPLE of line items per invoice (one exemplar per distinct row shape) so
    # large many-line-item bills don't blow the model's input limit; the model is
    # still validated against the full invoice. 0 = no sampling (send all rows).
    model_authoring_sample_rows: int = 25
    # Hard ceiling on authoring prompt input tokens (kept under the model's real
    # input limit with margin for estimate error). Normal multi-invoice corpus
    # sets sit well below this and pass UNCHANGED — only oversized sets (large
    # multi-page wireless bills) get trimmed, in order of least value, before the
    # call. Phase-2 decomposition lowers cost for everything.
    model_authoring_max_input_tokens: int = 700_000

    # Supplier profiles
    supplier_profiles_dir: Path = Path("supplier_profiles")

    # Output specifications (customer deliverable column definitions)
    output_specs_dir: Path = Path("output_specs")

    # Field schemas (extraction contracts)
    field_schemas_dir: Path = Path("field_schemas")

    # Extraction models
    extraction_models_dir: Path = Path("extraction_models")

    # File watcher
    watcher_poll_interval: float = 1.0
    watcher_stable_seconds: float = 3.0
    # Profiles live in MySQL, so the watcher re-syncs its watched directories by
    # polling the DB on this interval (active profiles + their account folders).
    watcher_registry_rescan_seconds: float = 15.0

    # Largest accepted upload, in bytes (0 disables the cap). Uploads are
    # streamed to disk in chunks and rejected with 413 past this size, so an
    # oversized PDF cannot exhaust API memory.
    max_upload_bytes: int = 100 * 1024 * 1024

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"

    def model_post_init(self, __context: object) -> None:
        # Operational directories live under work_dir (separate from the
        # supplier data root) so the supplier folders contain only PDFs/Excel.
        if self.inbound_dir is None:
            self.inbound_dir = self.work_dir / "inbound"
        if self.processing_dir is None:
            self.processing_dir = self.work_dir / "processing"
        if self.completed_dir is None:
            self.completed_dir = self.work_dir / "completed"
        if self.exceptions_dir is None:
            self.exceptions_dir = self.work_dir / "exceptions"
        if self.archive_dir is None:
            self.archive_dir = self.work_dir / "archive"
        if self.accounts_scan_dir is None:
            self.accounts_scan_dir = self.data_dir / "Invoices"

    def ensure_directories(self) -> None:
        for d in [
            self.inbound_dir,
            self.processing_dir,
            self.completed_dir,
            self.exceptions_dir,
            self.archive_dir,
            self.extraction_models_dir,
        ]:
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
