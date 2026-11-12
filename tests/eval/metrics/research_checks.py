"""Research agent: share of the six quality-check tests passed (brief, signal, dated, contact, no invented name, recommendation)."""
CHECKS = ("brief", "signal", "dated", "contact", "no_invented_name", "recommendation")


def evaluate(instance):
    o = instance.get("observed") or {}
    if o.get("error"):
        return {"score": 0.0, "explanation": f"No brief: {o['error']}"}
    passed = [k for k in CHECKS if o.get(k)]
    return {"score": len(passed) / len(CHECKS),
            "explanation": f"passed {len(passed)}/6; missed: {', '.join(k for k in CHECKS if k not in passed) or 'none'}; contact {o.get('contact_found')}"}
