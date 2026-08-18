"""Load and cache supplier profiles from MySQL.

The DB is the source of truth (seeded from ``supplier_profiles/*.json`` on boot,
see ``db/seed.py``). Public function signatures are unchanged so consumers don't
care that storage moved from disk to the database.
"""

from pathlib import Path

import structlog

from stencil.profiles.schema import SupplierProfile

logger = structlog.get_logger()

_profiles_cache: dict[str, SupplierProfile] = {}


def _row_to_profile(row) -> SupplierProfile | None:
    try:
        return SupplierProfile.model_validate(row.data)
    except Exception as e:  # pragma: no cover - defensive
        logger.error("profiles.parse_failed", profile_id=getattr(row, "profile_id", "?"), error=str(e))
        return None


def load_all_profiles(profiles_dir: Path | None = None) -> dict[str, SupplierProfile]:
    """Load all supplier profiles from the DB into the cache. Returns dict keyed
    by profile_id. ``profiles_dir`` is accepted for signature compatibility and
    ignored (storage is the database)."""
    global _profiles_cache
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = registry.list_profiles(db)
    finally:
        db.close()

    profiles: dict[str, SupplierProfile] = {}
    for row in rows:
        profile = _row_to_profile(row)
        if profile is not None:
            profiles[profile.profile_id] = profile

    _profiles_cache = profiles
    logger.info("profiles.loaded_all", count=len(profiles))
    return profiles


def get_profile(profile_id: str) -> SupplierProfile | None:
    """Return a profile, reading its DB row fresh so cross-process edits are visible."""
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = registry.get_profile(db, profile_id)
    finally:
        db.close()

    if row is not None:
        profile = _row_to_profile(row)
        if profile is not None:
            _profiles_cache[profile_id] = profile
            return profile

    if not _profiles_cache:
        load_all_profiles()
    return _profiles_cache.get(profile_id)


def save_profile(profile: SupplierProfile, *, actor=None) -> None:
    """Persist (insert or update) a profile in the DB and refresh the cache."""
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        registry.upsert_profile(
            db,
            profile_id=profile.profile_id,
            status=profile.status,
            canonical_name=profile.identity.canonical_name,
            data=profile.model_dump(mode="json"),
            actor=actor,
        )
    finally:
        db.close()
    _profiles_cache[profile.profile_id] = profile


def delete_profile(profile_id: str, *, actor=None) -> bool:
    from stencil.db import registry
    from stencil.db.session import SessionLocal

    db = SessionLocal()
    try:
        deleted = registry.delete_profile(db, profile_id, actor=actor)
    finally:
        db.close()
    _profiles_cache.pop(profile_id, None)
    return deleted


def _is_active(profile: SupplierProfile) -> bool:
    return profile.status == "active"


def _sort_profiles_for_selection(profiles: list[SupplierProfile]) -> list[SupplierProfile]:
    return sorted(profiles, key=lambda profile: profile.version, reverse=True)


def find_profile_by_supplier(supplier_name: str, *, active_only: bool = True) -> SupplierProfile | None:
    """Find the highest-version profile matching a supplier name or alias."""
    if not _profiles_cache:
        load_all_profiles()

    name_lower = supplier_name.lower()
    matches = []
    for profile in _profiles_cache.values():
        if active_only and not _is_active(profile):
            continue
        if profile.identity.canonical_name.lower() == name_lower:
            matches.append(profile)
            continue
        if any(alias.lower() == name_lower for alias in profile.identity.aliases):
            matches.append(profile)

    if not matches:
        return None
    return _sort_profiles_for_selection(matches)[0]


def get_active_profiles() -> list[SupplierProfile]:
    # Always re-read so watcher/worker pick up activations without a stale cache.
    load_all_profiles()
    return _sort_profiles_for_selection([p for p in _profiles_cache.values() if _is_active(p)])


def resolve_profile_path(profile: SupplierProfile, path_field: str) -> Path | None:
    """Resolve a profile's configured inbound/output directory (literal path)."""
    raw = getattr(profile, path_field, None)
    if not raw:
        return None
    return Path(raw).expanduser()


def normalize_account_path(raw: str | None) -> str | None:
    """A canonical key for comparing account inbound folders across profiles.

    Case-folds and normalizes separators (via ``os.path.normcase`` — lowercases
    on Windows, no-op on case-sensitive POSIX) so ``/data/X/pdf``, ``\\data\\X\\pdf``
    and a trailing slash all compare equal. Returns ``None`` for empty values or
    unresolved ``{...}`` templates. The folder need not exist yet.
    """
    import os

    if not raw or not raw.strip():
        return None
    if "{" in raw or "}" in raw:  # unresolved template, not a real folder
        return None
    expanded = os.path.expanduser(raw.strip())
    return os.path.normcase(os.path.normpath(os.path.abspath(expanded)))


def account_owner_index(
    *,
    statuses: set[str] | None = None,
    exclude_profile_id: str | None = None,
) -> dict[str, list[str]]:
    """Map every claimed account inbound folder -> the profile_id(s) that claim it.

    A value with more than one profile_id is a mapping conflict. ``statuses``
    filters which profiles count (e.g. only ``active``/``training`` are watched);
    ``exclude_profile_id`` drops one profile (used when validating that same
    profile so it doesn't conflict with itself).
    """
    load_all_profiles()
    index: dict[str, list[str]] = {}
    for profile in _profiles_cache.values():
        if exclude_profile_id and profile.profile_id == exclude_profile_id:
            continue
        if statuses is not None and profile.status not in statuses:
            continue
        for account in profile.effective_accounts:
            key = normalize_account_path(account.inbound_path)
            if key is None:
                continue
            index.setdefault(key, []).append(profile.profile_id)
    return index
