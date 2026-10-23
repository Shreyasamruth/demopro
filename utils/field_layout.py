"""Locate existing PHI words and their field labels without rewriting clinical text."""
import re


FIELD_LABELS = (
    "Patient Name", "Patient ID", "Patient Identifier", "Patient Number", "Patient",
    "Full Name", "Name", "MRN", "PID", "Medical Record Number", "Record Number",
    "Report Date", "Date of Birth", "Birth Date", "DOB", "Date", "Referring Doctor",
    "Referring Dr", "Referred By", "Referring Physician", "Consultant", "Doctor",
    "Physician", "Phone Number", "Contact Number", "Telephone", "Mobile", "Phone",
    "Email Address", "E-mail", "Email", "Patient Address", "Address", "Signature",
    "Hospital ID", "Hospital Name", "Hospital", "Accession Number", "Registration Number",
)


def canonical(text):
    return "".join(character.casefold() for character in text if character.isalnum())


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def word_groups(words, value, whole_words=False):
    """Match punctuation/spacing variants while retaining exact word geometry."""
    dense, offsets = "", []
    for index, (text, _) in enumerate(words):
        normalized = canonical(text)
        if normalized:
            offsets.append((len(dense), len(dense) + len(normalized), index))
            dense += normalized
    target = canonical(value)
    if not target:
        return []
    groups = []
    for match in re.finditer(re.escape(target), dense):
        touched = [(start, end, index) for start, end, index in offsets
                   if start < match.end() and end > match.start()]
        if whole_words and (touched[0][0] != match.start() or touched[-1][1] != match.end()):
            continue
        groups.append([index for _, _, index in touched])
    return groups


def field_boxes(words, value):
    """Remove each value plus a nearby identifying label, leaving other fields intact."""
    labels, claimed = [], set()
    for label in sorted(FIELD_LABELS, key=len, reverse=True):
        for group in word_groups(words, label, whole_words=True):
            if set(group) & claimed:
                continue
            labels.append((group, union([words[i][1] for i in group])))
            claimed.update(group)
    boxes = []
    for group in word_groups(words, value):
        value_box = union([words[i][1] for i in group])
        y0, x0, y1, x1 = value_box
        height = y1 - y0
        candidates = []
        for label_group, label_box in labels:
            if set(group) & set(label_group):
                continue
            ly0, lx0, ly1, lx1 = label_box
            same_line = abs((ly0 + ly1) / 2 - (y0 + y1) / 2) <= max(height, ly1 - ly0) * .55
            if same_line and 0 <= x0 - lx1 <= 250:
                between = [i for i, (_, b) in enumerate(words)
                           if b[1] >= lx1 and b[3] <= x0
                           and abs((b[0] + b[2]) / 2 - (y0 + y1) / 2) <= height * .55
                           and i not in group and i not in label_group]
                # A title such as "Dr." may sit between the label and a name.
                if any(canonical(words[i][0]) not in {"dr", "mr", "mrs", "ms", "prof"} for i in between):
                    continue
                candidates.append((x0 - lx1, union([value_box, label_box])))
        if candidates:
            boxes.append(min(candidates, key=lambda pair: pair[0])[1])
        else:
            # Separate rectangles preserve clinical text between wrapped PHI words.
            boxes.extend(words[i][1] for i in group)
            # Also handle templates with the label immediately above its value.
            above = [b for _, b in labels if 0 <= y0 - b[2] <= height * 1.25
                     and abs(b[1] - x0) <= max(12, height)]
            if above:
                boxes.append(max(above, key=lambda b: b[2]))
    return boxes


def expand_label_span(text, start, end):
    """Include an adjacent identifying label in a text deletion, never findings."""
    prefix = text[:start]
    choices = []
    for label in FIELD_LABELS:
        pattern = r"(?<!\w)" + r"[\s_-]*".join(re.escape(p) for p in label.split())
        pattern += r"\s*(?:[:#=]\s*)?(?:(?:Dr|Mr|Mrs|Ms|Prof)\.?\s+)?$"
        match = re.search(pattern, prefix, re.I)
        if match:
            choices.append(match.start())
    return (min(choices) if choices else start), end


def remove_text_fields(text, spans):
    ranges = sorted(expand_label_span(text, ent["start"], ent["end"]) for ent in spans)
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    for start, end in reversed(merged):
        text = text[:start] + text[end:]
    # Empty identifying fields vanish from the readable text; clinical lines stay.
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
