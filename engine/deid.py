"""Occurrence-safe PHI tokenization; tokens are unique within each document."""
import re
import secrets


class DeidEngine:
    def __init__(self, model_name=None):
        # Visual NER is supplied by the agent. These rules supplement it.
        self.patterns = {
            "EMAIL": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
            "PHONE": r"(?<!\w)(?:\+?\d{1,3}[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\w)",
            "DATE": r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b",
        }

    def detect_phi(self, text):
        entities = []
        for label, pattern in self.patterns.items():
            for match in re.finditer(pattern, text):
                entities.append(dict(text=match.group(), start=match.start(), end=match.end(), label=label))
        for match in re.finditer(r"\b(?:Patient ID|MRN|PID)\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9-]*)", text, re.I):
            entities.append(dict(text=match.group(1), start=match.start(1), end=match.end(1), label="ID"))
        return entities

    def _merge_entities(self, text, entities):
        valid = []
        for entity in entities:
            start, end = entity.get("start"), entity.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
                raise ValueError("Invalid PHI text offset; document was not shared.")
            if text[start:end] != entity["text"]:
                raise ValueError("PHI text does not match its offset.")
            valid.append(dict(entity))
        merged = []
        for ent in sorted(valid, key=lambda e: (e["start"], -e["end"])):
            if merged and ent["start"] < merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], ent["end"])
                merged[-1]["text"] = text[merged[-1]["start"]:merged[-1]["end"]]
            else:
                merged.append(ent)
        return merged

    def mask_text(self, text, entities, reversible=False):
        merged = self._merge_entities(text, entities)
        namespace = secrets.token_hex(6)
        while f"[PHI_{namespace}_" in text:
            namespace = secrets.token_hex(6)
        mapping, memory, parts, cursor = {}, {}, [], 0
        for ent in merged:
            original = text[ent["start"]:ent["end"]]
            identity = (ent["label"], original)
            if identity not in memory:
                label = re.sub(r"[^A-Z0-9]", "_", ent["label"].upper())[:32]
                token = f"[PHI_{namespace}_{label}_{len(memory) + 1:04d}]"
                memory[identity] = token
                if reversible:
                    mapping[token] = original
            parts.extend([text[cursor:ent["start"]], memory[identity]])
            cursor = ent["end"]
        parts.append(text[cursor:])
        return "".join(parts), mapping
