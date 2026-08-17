"""
Commission maths for Planned Real Estate.

Kept apart from the routes so it can be reasoned about and tested on its own.
Money rules are the thing you least want buried inside a request handler.

Vocabulary
----------
value           For a rental, the agreed MONTHLY rent. For a sale, the agreed
                price. This is what was actually agreed, which may be below the
                advertised listing price.
term_months     Length of the lease. Ignored for sales.
free_months     Rent-free months given to the tenant. May be a half month.
commission_basis
                monthly_rent  - a percentage of one month's rent (the usual
                                rental deal; 50% is the house default)
                annual_rent   - a percentage of the whole contract
                sale_price    - a percentage of the sale price
                fixed         - a flat amount, percentage ignored
commission_on   contract   - free months don't reduce the commission base
                effective  - free months do reduce it
"""

BASES = ("monthly_rent", "annual_rent", "sale_price", "fixed")
ON_CHOICES = ("contract", "effective")
AGENT_ROLES = ("lead", "support", "referrer")


def effective_monthly(value, term_months, free_months):
    """Rent per month once free months are spread across the term."""
    value = float(value or 0)
    term = float(term_months or 0)
    free = float(free_months or 0)
    if term <= 0:
        return value
    paid = max(term - free, 0)
    return value * paid / term


def commission_base(value, basis, term_months=12, free_months=0,
                    commission_on="contract"):
    """The figure the percentage is applied to."""
    value = float(value or 0)
    term = float(term_months or 0)
    free = float(free_months or 0)

    if basis == "fixed":
        return 0.0
    if basis == "sale_price":
        return value
    if basis == "monthly_rent":
        return (effective_monthly(value, term, free)
                if commission_on == "effective" else value)
    if basis == "annual_rent":
        months = max(term - free, 0) if commission_on == "effective" else term
        return value * months
    raise ValueError(f"Unknown commission basis: {basis}")


def commission_amount(value, basis, pct, term_months=12, free_months=0,
                      commission_on="contract", fixed_amount=None):
    """What the agency earns on this deal."""
    if basis == "fixed":
        return round(float(fixed_amount or 0), 2)
    base = commission_base(value, basis, term_months, free_months,
                           commission_on)
    return round(base * float(pct or 0) / 100.0, 2)


def contract_total(value, term_months, free_months):
    """What the tenant actually pays across the lease."""
    paid = max(float(term_months or 0) - float(free_months or 0), 0)
    return round(float(value or 0) * paid, 2)


# ---------------------------------------------------------------------------
# Splitting between agents
# ---------------------------------------------------------------------------


def check_shares(shares):
    """shares: list of percentages. Returns (ok, message)."""
    cleaned = [float(s or 0) for s in shares]
    if not cleaned:
        return False, "Add at least one agent to the deal."
    if any(s < 0 for s in cleaned):
        return False, "A share can't be negative."
    total = round(sum(cleaned), 2)
    if abs(total - 100.0) > 0.01:
        return False, f"Shares add up to {total:g}%. They need to total 100%."
    return True, ""


def split_amounts(total, shares):
    """Divide a commission by share percentages, without losing a fil.

    The rounding remainder goes to the largest share, so the parts always add
    back up to the total.
    """
    total = round(float(total or 0), 2)
    cleaned = [float(s or 0) for s in shares]
    if not cleaned:
        return []
    parts = [round(total * s / 100.0, 2) for s in cleaned]
    drift = round(total - sum(parts), 2)
    if drift:
        biggest = max(range(len(parts)), key=lambda i: cleaned[i])
        parts[biggest] = round(parts[biggest] + drift, 2)
    return parts
