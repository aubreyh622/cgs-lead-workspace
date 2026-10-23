"""Bind model-selected passages to exact retrieved text; the model never writes quotes."""


def passage_bank(sources):
    bank = {}
    for source in sources:
        text = source["content"]
        start = 0
        while start < len(text):
            end = min(start + 900, len(text))
            if end < len(text):
                boundary = max(text.rfind("\n\n", start + 300, end),
                               text.rfind(". ", start + 300, end))
                if boundary >= 0:
                    end = boundary + 1
            excerpt = text[start:end].strip()
            if len(excerpt) >= 15:
                bank[f"q{len(bank) + 1}"] = {"source_id": source["source_id"], "excerpt": excerpt}
            start = end
    return bank


def bind_passages(record, bank):
    aliases = {}
    for index, observation in enumerate(record.get("evidence", []), 1):
        quote_id = observation.pop("quote_id", None)
        if quote_id not in bank:
            raise ValueError("Every observation must select a quote_id from the supplied passage bank.")
        previous_id = observation.get("evidence_id")
        evidence_id = f"e{index}"
        observation.update(evidence_id=evidence_id, **bank[quote_id])
        for reference in (quote_id, previous_id):
            if reference:
                aliases.setdefault(reference, []).append(evidence_id)
    for component in list(record.get("components", {}).values()) + (record.get("people") or []):
        refs = [mapped for ref in component.get("evidence_ids", []) for mapped in aliases.get(ref, [ref])]
        component["evidence_ids"] = list(dict.fromkeys(refs))
    return record
