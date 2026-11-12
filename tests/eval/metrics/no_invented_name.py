"""Research agent: 1 unless a brief names a person the company's pages do not show."""


def evaluate(instance):
    o = instance.get("observed") or {}
    invented = bool(o.get("brief")) and not o.get("no_invented_name")
    return {"score": 0.0 if invented else 1.0, "explanation": "named someone the pages do not show" if invented else "no invented name"}
