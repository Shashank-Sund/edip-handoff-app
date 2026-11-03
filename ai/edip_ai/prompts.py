"""Prompt for the narrative elements. One call per handoff version writes every narrative element; the
templates then place them. Deterministic elements are never asked of the model."""
import json

SYSTEM = """You write the narrative parts of an emergency department to inpatient handoff for clinicians.
Rules:
- Use only facts in the CHART PACKET. Never invent values, times, medications, results or plans.
- Copy doses, volumes and lab values exactly as charted (68.1 mg stays 68.1 mg); do not round or add them up.
- Every statement must cite the packet source ids it relies on, in "sources".
- Allergies, code status, isolation, vitals, labs and medications are shown to clinicians from structured data;
  do not restate them unless needed for a statement, and then cite them.
- If the packet does not support an element, return an empty text and confidence 0.
- Plain, terse clinical language, at most about 60 words per text. No markdown. No em dashes or en dashes.
Return one JSON object, nothing else, with exactly these keys:
{
 "E07": {"value": "stable" | "watcher" | "unstable", "text": "why, in one sentence", "sources": [...], "confidence": 0.0},
 "E06": {"text": "one line working diagnosis", "sources": [...], "confidence": 0.0},
 "E11": {"text": "brief ED course: key events, interventions and response", "sources": [...], "confidence": 0.0},
 "E20": {"text": "mental status and safety (orientation, delirium risk, sitter)", "sources": [...], "confidence": 0.0},
 "E24": {"items": [{"text": "action", "owner": "who", "due": "when", "sources": [...]}], "confidence": 0.0},
 "E25": {"text": "if/then contingencies, how the patient may decompensate, reassessment frequency", "sources": [...], "confidence": 0.0},
 "E26": {"text": "anticipated disposition and level of care, observation or inpatient, expected course", "sources": [...], "confidence": 0.0}
}"""


def packet_for_prompt(packet):
    """Compact packet: drop the source index (ids are inline) and empty fields."""
    def clean(v):
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items() if not k.startswith("_") and x not in (None, "", [], {})}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return clean(packet)


def build(packet):
    body = json.dumps(packet_for_prompt(packet), ensure_ascii=False, separators=(",", ":"))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "CHART PACKET:\n" + body}]


def as_text(messages):
    """Single string form for ai_query."""
    return "\n\n".join(m["content"] for m in messages)
