"""Evidence agent: 1 when it proposed nothing above what the notes support (no over-claim, no wrong condition). The serious error."""


def evaluate(instance):
    o = instance.get("observed") or {}
    bad = list(o.get("over") or []) + list(o.get("wrong") or [])
    return {"score": 0.0 if bad else 1.0, "explanation": ("over-claims: " + ", ".join(bad)) if bad else "no over-claim"}
