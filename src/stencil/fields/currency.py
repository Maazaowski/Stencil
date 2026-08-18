"""Currency normalization helpers for extraction fields."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_BUILTIN_ALIASES = {
    "$": "USD",
    "dollar": "USD",
    "dollars": "USD",
    "us dollar": "USD",
    "us dollars": "USD",
    "usd": "USD",
    "c$": "CAD",
    "cad": "CAD",
    "canadian dollar": "CAD",
    "canadian dollars": "CAD",
    "€": "EUR",
    "eur": "EUR",
    "euro": "EUR",
    "euros": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "pound sterling": "GBP",
    "pounds sterling": "GBP",
    "₹": "INR",
    "rs": "INR",
    "inr": "INR",
    "rupee": "INR",
    "rupees": "INR",
    "¥": "JPY",
    "jpy": "JPY",
    "yen": "JPY",
    "aud": "AUD",
    "australian dollar": "AUD",
    "australian dollars": "AUD",
    "nzd": "NZD",
    "new zealand dollar": "NZD",
    "new zealand dollars": "NZD",
    "sgd": "SGD",
    "singapore dollar": "SGD",
    "singapore dollars": "SGD",
    "hkd": "HKD",
    "hong kong dollar": "HKD",
    "hong kong dollars": "HKD",
    "cny": "CNY",
    "yuan": "CNY",
    "renminbi": "CNY",
    "chf": "CHF",
    "swiss franc": "CHF",
    "swiss francs": "CHF",
    "sek": "SEK",
    "swedish krona": "SEK",
    "nok": "NOK",
    "norwegian krone": "NOK",
    "dkk": "DKK",
    "danish krone": "DKK",
    "zar": "ZAR",
    "rand": "ZAR",
    "aed": "AED",
    "dirham": "AED",
    "dirhams": "AED",
}


@dataclass(frozen=True)
class CurrencyNormalization:
    code: str | None
    warning: str | None = None


def _clean_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else None


def _rules_value(rules: Any, name: str, default: Any) -> Any:
    if rules is None:
        return default
    if isinstance(rules, dict):
        return rules.get(name, default)
    return getattr(rules, name, default)


def _allowed_codes(rules: Any) -> list[str]:
    raw = _rules_value(rules, "allowed_codes", []) or []
    if not isinstance(raw, list):
        return []
    return [code for value in raw if (code := _clean_code(value))]


def _default_code(rules: Any) -> str | None:
    return _clean_code(_rules_value(rules, "default_code", None))


def _aliases(rules: Any) -> dict[str, str]:
    raw = _rules_value(rules, "aliases", {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for alias, code in raw.items():
        cleaned_code = _clean_code(code)
        if cleaned_code:
            out[str(alias).strip().lower()] = cleaned_code
    return out


def currency_fallback(rules: Any = None, fallback: str | None = None) -> str | None:
    allowed = _allowed_codes(rules)
    default = _default_code(rules)
    if default:
        return default
    if len(allowed) == 1:
        return allowed[0]
    return _clean_code(fallback)


def normalize_currency_code(
    value: Any,
    *,
    rules: Any = None,
    fallback: str | None = None,
) -> CurrencyNormalization:
    allowed = set(_allowed_codes(rules))
    fallback_code = currency_fallback(rules, fallback)

    def accept(code: str | None) -> str | None:
        if code is None:
            return None
        if allowed and code not in allowed:
            return None
        return code

    if value is None or (isinstance(value, str) and not value.strip()):
        return CurrencyNormalization(accept(fallback_code))

    text = str(value).strip()
    code = _clean_code(text)
    if accepted := accept(code):
        return CurrencyNormalization(accepted)

    lowered = text.lower()
    profile_aliases = _aliases(rules)
    if accepted := accept(profile_aliases.get(lowered)):
        return CurrencyNormalization(accepted)

    if accepted := accept(_BUILTIN_ALIASES.get(lowered)):
        return CurrencyNormalization(accepted)

    for alias, alias_code in profile_aliases.items():
        if alias and alias in lowered and (accepted := accept(alias_code)):
            return CurrencyNormalization(accepted)
    for alias, alias_code in _BUILTIN_ALIASES.items():
        if alias and alias in lowered and (accepted := accept(alias_code)):
            return CurrencyNormalization(accepted)

    if fallback_code:
        return CurrencyNormalization(
            accept(fallback_code),
            f"currency value {text!r} was not recognized; using fallback {fallback_code}",
        )
    return CurrencyNormalization(
        None,
        f"currency value {text!r} was not recognized and was dropped",
    )
