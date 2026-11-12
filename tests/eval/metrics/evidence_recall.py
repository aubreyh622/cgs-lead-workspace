"""Evidence agent: share of the expected evidence it proposed within the expected range (the quality check's "found")."""


def evaluate(instance):
    o = instance.get("observed") or {}
    if o.get("error"):
        return {"score": 0.0, "explanation": f"No analysis: {o['error']}"}
    expected = o.get("expected") or 0
    hit = len(o.get("hit") or [])
    return {"score": (hit / expected) if expected else 1.0,
            "explanation": f"found {hit}/{expected}; missed: {', '.join(o.get('missed') or []) or 'none'}"}
