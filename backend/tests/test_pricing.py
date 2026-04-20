from app.services.deal_structuring import PricingInputs, compute_pricing


def test_basic_margin():
    out = compute_pricing(PricingInputs(buy_price=400, sell_price=500, freight_estimate=40, volume_mt=1000))
    assert out.margin_per_mt == 60
    assert out.total_value == 500_000
    assert out.total_margin == 60_000


def test_negative_margin_recommends_brokerage():
    out = compute_pricing(PricingInputs(buy_price=500, sell_price=480, freight_estimate=30, volume_mt=100))
    assert out.margin_per_mt < 0
    assert out.recommended_structure == "brokerage"


def test_large_thin_margin_back_to_back():
    out = compute_pricing(PricingInputs(buy_price=400, sell_price=405, freight_estimate=0, volume_mt=10_000))
    assert out.recommended_structure == "back_to_back_lc"


def test_healthy_principal_trade():
    out = compute_pricing(PricingInputs(buy_price=400, sell_price=500, freight_estimate=40, volume_mt=1000))
    assert out.recommended_structure == "principal"


def test_scenarios_count_and_structure():
    out = compute_pricing(PricingInputs(buy_price=400, sell_price=500, freight_estimate=40, volume_mt=1000))
    assert len(out.scenarios) == 4
    names = {s["name"] for s in out.scenarios}
    assert names == {"base", "bull", "bear", "stressed"}


def test_zero_volume():
    out = compute_pricing(PricingInputs(buy_price=400, sell_price=500, freight_estimate=40, volume_mt=0))
    assert out.total_value == 0
    assert out.total_margin == 0
    assert out.margin_per_mt == 60
