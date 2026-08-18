# ── Stencil Backend ──────────────────────────────────
FROM python:3.12-slim AS backend

WORKDIR /app

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

# System deps for pymupdf and pymysql/cryptography
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Install third-party dependencies first, from metadata only, so this heavy
# layer is cached and only re-runs when pyproject.toml changes — NOT on every
# code edit. Pre-install pydantic with pinned wheels to avoid flaky index responses.
COPY pyproject.toml ./
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir --timeout 120 --retries 10 \
        "pydantic==2.11.1" \
        "pydantic-core==2.33.0" && \
    mkdir -p src/stencil && touch src/stencil/__init__.py && \
    pip install --no-cache-dir --timeout 120 --retries 10 .

# Copy the real application code (cheap layers that change often).
COPY src/ ./src/
COPY supplier_profiles/ ./supplier_profiles/
COPY field_schemas/ ./field_schemas/
COPY output_specs/ ./output_specs/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Re-install in editable mode (no deps — already installed above) so the
# package resolves to the copied source.
RUN pip install --no-cache-dir --no-deps -e .

# Create operational state directories (overridden by the /work bind mount in
# Docker; these are just a fallback for non-mounted runs). Supplier folders live
# under the separate /data mount and are created on demand.
RUN mkdir -p stencil_work/inbound \
             stencil_work/processing \
             stencil_work/completed \
             stencil_work/exceptions \
             stencil_work/archive \
             extraction_models

EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "stencil.main:app", "--host", "0.0.0.0", "--port", "8000"]
