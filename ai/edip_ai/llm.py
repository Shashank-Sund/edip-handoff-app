"""Model clients. The model is configuration (ai/config.yaml or the job parameter), so a site can switch from
the open model default to any hosted endpoint, for example databricks-claude-sonnet-5-5, without code changes.

  MockLLM     deterministic narrative from the packet; CI and the app's mock mode
  ServingLLM  one request per handoff through the Databricks SDK (chat endpoint), returns token usage
  ai_query    batch path used by the job (see jobs/handoff_job.py), same prompt
"""
import json
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config.yaml"


def config(path=CONFIG):
    return yaml.safe_load(Path(path).read_text())


class MockLLM:
    """Writes a short, fully cited narrative from the packet without a model."""
    name = "mock"

    def complete(self, messages, packet, retry=False):
        p = packet
        v = (p.get("vitals") or {}).get("latest") or {}
        labs = [x for x in p.get("labs", []) if x.get("flag") in ("HH", "LL")] or p.get("labs", [])[-2:]
        dest = p.get("destination") or {}
        sev = "unstable" if any(x.get("flag") in ("HH", "LL") for x in labs) else ("watcher" if (p.get("visit") or {}).get("esi") in (1, 2) else "stable")
        lab_txt = "; ".join(f"{x['name']} {x['value']}" for x in labs)
        meds = p.get("ed_meds", [])[-3:]
        out = {
            "E07": {"value": sev, "text": f"ESI {(p.get('visit') or {}).get('esi')}" + (f", critical {lab_txt}" if sev == "unstable" else ""),
                    "sources": [p["visit"]["src"]] + [x["src"] for x in labs if sev == "unstable"], "confidence": 0.6},
            "E06": {"text": (p.get("visit") or {}).get("chief_complaint") or "", "sources": [p["visit"]["src"]], "confidence": 0.5},
            "E11": {"text": "; ".join(f"{m['name']} {m['dose']} {m['unit']}" for m in meds) or "No ED medications recorded",
                    "sources": [m["src"] for m in meds], "confidence": 0.5},
            "E20": {"text": "", "sources": [], "confidence": 0.0},
            "E24": {"items": [{"text": f"Follow up {x['name']}", "owner": x["owner"], "due": "on result", "sources": [x["src"]]}
                              for x in p.get("pending", [])[:4]], "confidence": 0.6},
            "E25": {"text": f"If HR over 120 or SBP under 90, call the admitting team. Last HR {v.get('hr')}" if v else "",
                    "sources": [v["src"]] if v else [], "confidence": 0.4},
            "E26": {"text": f"{dest.get('level_of_care') or dest.get('pending_unit') or 'Inpatient'} admission to {dest.get('service') or 'medicine'}",
                    "sources": [dest["src"]], "confidence": 0.5},
        }
        return json.dumps(out), {"prompt_tokens": 0, "completion_tokens": 0}


class ServingLLM:
    def __init__(self, endpoint, max_tokens=1500, temperature=0.1, client=None):
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole
        self.w = client or WorkspaceClient()
        self.endpoint, self.max_tokens, self.temperature = endpoint, max_tokens, temperature
        self._msg = lambda m: ChatMessage(role=ChatMessageRole(m["role"]), content=m["content"])
        self.name = endpoint

    def complete(self, messages, packet, retry=False):
        r = self.w.serving_endpoints.query(name=self.endpoint, messages=[self._msg(m) for m in messages],
                                           max_tokens=self.max_tokens * (2 if retry else 1), temperature=self.temperature)
        text = r.choices[0].message.content
        if isinstance(text, list):      # some endpoints return content parts (reasoning plus text)
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict) and part.get("type") == "text")
        usage = {"prompt_tokens": getattr(r.usage, "prompt_tokens", None), "completion_tokens": getattr(r.usage, "completion_tokens", None)}
        return text, usage


def client(cfg=None):
    cfg = cfg or config()
    if cfg.get("client") == "mock":
        return MockLLM()
    return ServingLLM(cfg["model"], cfg.get("max_tokens", 1500), cfg.get("temperature", 0.1))
