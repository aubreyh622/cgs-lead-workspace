"""Outreach agent: 1 unless the reviewing model found a statement about the company the facts do not support."""


def evaluate(instance):
    o = instance.get("observed") or {}
    bad = bool(o.get("unsupported_claim"))
    return {"score": 0.0 if bad else 1.0, "explanation": "unsupported claim" if bad else "every claim is supported by the facts"}
