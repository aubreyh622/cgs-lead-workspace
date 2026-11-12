"""Discovery agent: share of nominations that are companies matching the direction."""


def evaluate(instance):
    o = instance.get("observed") or {}
    n = o.get("nominations") or 0
    return {"score": (o.get("fits", 0) / n) if n else 0.0, "explanation": f"{o.get('fits', 0)} of {n} nominations fit"}
