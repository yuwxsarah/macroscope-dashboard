from src.extended_providers import ChinaLiquidityProvider


def test_dr_text_does_not_capture_007():
    text = "DR007 加权利率(%) 1.4321 最新利率 1.44"
    assert ChinaLiquidityProvider._extract_rate(text, "DR007") == 1.4321
