"""Module 5: Deal Pipeline / CRM.

State machine for deal stages, activity logging, and tasks.
"""
from __future__ import annotations

from app.models.deal import DEAL_STAGES

# Legal transitions per stage. `lost` is reachable from any active stage.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "lead": {"contacted", "lost"},
    "contacted": {"qualified", "lead", "lost"},
    "qualified": {"pricing", "contacted", "lost"},
    "pricing": {"buyer_matched", "qualified", "lost"},
    "buyer_matched": {"spa", "pricing", "lost"},
    "spa": {"lc", "buyer_matched", "lost"},
    "lc": {"shipment", "spa", "lost"},
    "shipment": {"closed", "lc", "lost"},
    "closed": set(),
    "lost": set(),
}

# Default win probability per stage — used for pipeline weighting.
STAGE_PROBABILITY: dict[str, int] = {
    "lead": 5,
    "contacted": 10,
    "qualified": 25,
    "pricing": 40,
    "buyer_matched": 55,
    "spa": 70,
    "lc": 85,
    "shipment": 95,
    "closed": 100,
    "lost": 0,
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: str, target: str) -> bool:
    if current not in ALLOWED_TRANSITIONS:
        return False
    return target in ALLOWED_TRANSITIONS[current]


def transition(current: str, target: str) -> str:
    if target not in DEAL_STAGES:
        raise InvalidTransition(f"Unknown stage: {target}")
    if current == target:
        return current
    if not can_transition(current, target):
        raise InvalidTransition(
            f"Cannot transition from '{current}' to '{target}'. "
            f"Allowed: {sorted(ALLOWED_TRANSITIONS.get(current, set()))}"
        )
    return target


def default_probability(stage: str) -> int:
    return STAGE_PROBABILITY.get(stage, 10)
