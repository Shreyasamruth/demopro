from typing import List, Dict, Any
import re

class ContextPreserver:
    def __init__(self):
        # Patterns for medical values that should NOT be redacted
        self.medical_value_patterns = [
            r'\d+\s?(?:mg|g|ml|kg|cm|mm|units)', # Dosages/Measurements
            r'\d+/\d+\s?mmHg',                 # Blood Pressure
            r'(?:pH|Hb|WBC|RBC|HCT)\s?\d+(?:\.\d+)?', # Lab values
            r'\d+(?:\.\d+)?\s?%',               # Percentages
            r'(?:positive|negative|reactive|non-reactive)', # Test results
        ]

        # Common medical terminology whitelist (simplified)
        self.whitelist = {
            "hypertension", "diabetes", "asthma", "ct scan", "mri", "blood pressure",
            "findings", "impression", "history", "patient", "clinical", "evaluation"
        }

    def is_medical_context(self, text: str, label: str) -> bool:
        # Check if the text matches any medical value pattern
        for pattern in self.medical_value_patterns:
            if re.fullmatch(pattern, text, re.IGNORECASE):
                return True

        # Check if the text is in the whitelist
        if text.lower() in self.whitelist:
            return True

        # Special logic for dates: preserve if it looks like a relative time (e.g., "3 days ago")
        if label == "DATE":
            relative_date_patterns = [r'\d+\s?(?:days?|weeks?|months?|years?)\s?ago', r'today', r'yesterday']
            for pattern in relative_date_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    return True

        return False

    def filter_phi(self, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filters out entities that are actually medical context."""
        filtered = []
        for ent in entities:
            if not self.is_medical_context(ent['text'], ent['label']):
                filtered.append(ent)
        return filtered
