"""Shared instrument-aware fundamental vocabulary (READ-ONLY).

This package is a leaf: it imports nothing from the economic-intelligence or
fundamental-intelligence layers, so both can depend on one vocabulary without an
import cycle. See domains.py (the factor vocabulary) and profiles.py (which
factors reach which instrument, and how).
"""
from app.services.instrument_intelligence.domains import (
    DOMAIN_LABELS,
    DOMAIN_ORDER,
    FundamentalDomain,
    domain_labels,
    match_domains,
    matched_domain_terms,
    normalize_text,
    order_domains,
)
from app.services.instrument_intelligence.profiles import (
    FOCUS_INSTRUMENT_NAMES,
    PROFILES,
    DomainMatch,
    InstrumentProfile,
    RelevanceKind,
    factor_reason,
    focus_symbol_for_token,
    match_profile_domains,
    profile_for,
)

__all__ = [
    "DOMAIN_LABELS",
    "DOMAIN_ORDER",
    "FOCUS_INSTRUMENT_NAMES",
    "PROFILES",
    "DomainMatch",
    "FundamentalDomain",
    "InstrumentProfile",
    "RelevanceKind",
    "domain_labels",
    "factor_reason",
    "focus_symbol_for_token",
    "match_domains",
    "match_profile_domains",
    "matched_domain_terms",
    "normalize_text",
    "order_domains",
    "profile_for",
]
