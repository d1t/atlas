import pytest

from app.services.pipeline import (
    InvalidTransition,
    can_transition,
    default_probability,
    transition,
)


def test_linear_forward():
    assert transition("lead", "contacted") == "contacted"
    assert transition("contacted", "qualified") == "qualified"
    assert transition("shipment", "closed") == "closed"


def test_same_stage_is_noop():
    assert transition("pricing", "pricing") == "pricing"


def test_lost_reachable_from_active_stages():
    for stage in ["lead", "contacted", "qualified", "pricing", "buyer_matched", "spa", "lc", "shipment"]:
        assert can_transition(stage, "lost")


def test_closed_is_terminal():
    with pytest.raises(InvalidTransition):
        transition("closed", "shipment")


def test_cannot_skip_stages():
    with pytest.raises(InvalidTransition):
        transition("lead", "spa")


def test_unknown_stage_rejected():
    with pytest.raises(InvalidTransition):
        transition("lead", "nonsense")


def test_probabilities_are_monotonic_in_happy_path():
    happy = ["lead", "contacted", "qualified", "pricing", "buyer_matched", "spa", "lc", "shipment", "closed"]
    probs = [default_probability(s) for s in happy]
    assert probs == sorted(probs)
