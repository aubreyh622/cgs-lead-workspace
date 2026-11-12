"""Discovery agent: 1 when no nomination is clearly not a fit (a bank, a retailer, a consulting firm ...)."""


def evaluate(instance):
    o = instance.get("observed") or {}
    bad = [r["company"] for r in o.get("rows") or [] if r.get("kind") == "not a fit"]
    return {"score": 0.0 if bad else 1.0, "explanation": ("not a fit: " + ", ".join(bad)) if bad else "no misfit"}
