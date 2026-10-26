from .schema import Decision

def decide(p: float, tau_low: float, tau_high: float) -> Decision:
    if p >= tau_high:
        return Decision.DELIVER
    if p <= tau_low:
        return Decision.REJECT
    return Decision.ABSTAIN
