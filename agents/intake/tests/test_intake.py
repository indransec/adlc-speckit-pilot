"""test:intake (REQ-F-001): claims are registered and classified."""


def test_claims_are_classified_and_registered(platform, make_claim):
    for scenario, ctype in [("delay_stp", "delay"), ("bagdelay_stp", "baggage_delay"), ("bagloss_excess", "baggage_loss")]:
        s = platform.process(make_claim(scenario))
        assert s["claim"]["claim_type"] == ctype
        assert s["claim"]["category"] == ("delay" if ctype == "delay" else "baggage")
        assert any(n["subject"].startswith("We have received") for n in s["notifications"])
