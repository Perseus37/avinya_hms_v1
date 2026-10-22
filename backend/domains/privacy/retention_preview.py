"""Retention category registry and counts-only preview helpers.

This module intentionally accepts counts from an approved inventory adapter;
it does not introspect or modify tenant tables.
"""

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class RetentionCategory:
    code: str
    count_candidates: Callable[[], int]
    count_on_hold: Callable[[], int]


class RetentionCategoryRegistry:
    def __init__(self) -> None:
        self._categories: dict[str, RetentionCategory] = {}

    def register(self, category: RetentionCategory) -> None:
        if category.code in self._categories:
            raise ValueError(f"Retention category already registered: {category.code}")
        self._categories[category.code] = category

    def get(self, code: str) -> RetentionCategory | None:
        return self._categories.get(code)

    def codes(self) -> tuple[str, ...]:
        return tuple(sorted(self._categories))


retention_category_registry = RetentionCategoryRegistry()


def preview_retention_counts(
    registry: RetentionCategoryRegistry, category_codes: Iterable[str]
) -> list[dict[str, int | str]]:
    """Return aggregate candidate/held counts only; never expose record identifiers."""
    report = []
    for code in sorted(set(category_codes)):
        category = registry.get(code)
        if category is None:
            raise ValueError(f"Retention category is not in the reviewed inventory: {code}")
        candidates = category.count_candidates()
        held = category.count_on_hold()
        if candidates < 0 or held < 0 or held > candidates:
            raise ValueError(f"Invalid count result for retention category: {code}")
        report.append(
            {
                "category_code": code,
                "candidate_count": candidates,
                "held_count": held,
                "eligible_count": candidates - held,
            }
        )
    return report


STANDARD_RETENTION_CATEGORIES: set[str] = {
    "patients",
    "inactive-patients",
    "privacy-decision-audits",
    "synthetic-attachments",
    "encounters",
    "clinical-notes",
    "orders",
    "billing-invoices",
}


def get_allowed_retention_categories() -> set[str]:
    """Return set of recognized standard and custom registered retention categories."""
    return STANDARD_RETENTION_CATEGORIES | set(retention_category_registry.codes())


CATEGORY_ALIASES: dict[str, str] = {
    "patients": "patients",
    "inactive-patients": "patients",
    "inactive_patients": "patients",
    "patient": "patients",
    "privacy-decision-audits": "privacy-decision-audits",
    "privacy_decision_audits": "privacy-decision-audits",
    "privacy-audits": "privacy-decision-audits",
    "privacy_audits": "privacy-decision-audits",
    "synthetic-attachments": "synthetic-attachments",
    "synthetic_attachments": "synthetic-attachments",
    "encounters": "encounters",
    "clinical-encounters": "encounters",
    "clinical_encounters": "encounters",
    "clinical-notes": "clinical-notes",
    "clinical_notes": "clinical-notes",
    "orders": "orders",
    "diagnostic-orders": "orders",
    "diagnostic_orders": "orders",
    "billing-invoices": "billing-invoices",
    "billing_invoices": "billing-invoices",
    "invoices": "billing-invoices",
}


def canonicalize_category(category: str | None) -> str | None:
    """Return the canonical category code for a given category or alias (N-02)."""
    if category is None:
        return None
    cleaned = category.strip().lower()
    return CATEGORY_ALIASES.get(cleaned, cleaned)
