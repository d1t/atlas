from app.models.supplier import Supplier
from app.services.counterparty import CounterpartyService


def _mk(**kw) -> Supplier:
    defaults = dict(
        name="ACME Sugar Mill",
        type="mill",
        country="Brazil",
        commodity="sugar",
        website="https://acmesugarmill.com",
        email="export@acmesugarmill.com",
        description="Industrial sugar mill in Brazil.",
        credibility_score=50,
        risk_score=50,
        red_flags=[],
        classification_confidence=0.0,
        extra_data={},
    )
    defaults.update(kw)
    return Supplier(**defaults)


def test_legit_mill_has_boosted_credibility():
    cp = CounterpartyService()
    scored = cp.score(_mk())
    assert scored["credibility_score"] > 50
    assert scored["red_flags"] == []


def test_gmail_flagged_and_risk_up():
    cp = CounterpartyService()
    scored = cp.score(_mk(email="ceo@gmail.com", website=None))
    assert "generic_email_domain" in scored["red_flags"]
    assert "no_website" in scored["red_flags"]
    assert scored["risk_score"] > 50
    assert scored["credibility_score"] < 50


def test_email_website_mismatch():
    cp = CounterpartyService()
    scored = cp.score(_mk(email="export@somethingelse.com", website="https://acmesugarmill.com"))
    assert "email_website_mismatch" in scored["red_flags"]


def test_suspicious_language_flagged():
    cp = CounterpartyService()
    scored = cp.score(
        _mk(description="We offer guaranteed best price with no LC needed — too good to miss.")
    )
    assert "suspicious_language" in scored["red_flags"]
    assert scored["risk_score"] >= 65
