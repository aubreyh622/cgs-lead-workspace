"""Outreach agent: weighted mean of the CGS email standard's lines (1-5, api/email_rubric.py) from the reviewing model;
no draft counts as 0, as in the quality check."""


def evaluate(instance):
    o = instance.get("observed") or {}
    if o.get("error"):
        return {"score": 0.0, "explanation": f"No draft or review: {o['error']}"}
    lines = instance.get("rubric") or []
    return {"score": float(o.get("mean") or 0.0),
            "explanation": "  ".join(f"{k} {o.get(k)}" for k in lines) + f"; improve: {o.get('improve', '')}"}
