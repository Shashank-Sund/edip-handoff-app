"""Who is using the app and what they may do.

Mock mode offers demo personas (invented people). Live mode maps the signed in Databricks user to a role through
EDIP_ROLE_MAP (see config.py); without a mapping a user gets the read only viewer role.

Roles:
  ed_nurse, ed_physician     see every patient; edit, attest and sign the handoffs for their audience
  unit_nurse                 sees patients bound for their unit; acknowledges nurse handoffs with a read back
  admitting_physician        sees every admitted patient; acknowledges physician handoffs with a read back
  viewer                     sees every patient, changes nothing (signed in users without a role)
"""
from __future__ import annotations

SIGNS = {"ed_nurse": ("sbar_nurse", "custom_sea58"), "ed_physician": ("ipass_physician", "custom_sea58")}
ACKS = {"unit_nurse": ("sbar_nurse", "custom_sea58"), "admitting_physician": ("ipass_physician", "custom_sea58")}
DEFAULT_TEMPLATE = {"ed_nurse": "sbar_nurse", "ed_physician": "ipass_physician", "unit_nurse": "sbar_nurse",
                    "admitting_physician": "ipass_physician", "viewer": "sbar_nurse"}
ROLE_LABEL = {"ed_nurse": "ED nurse", "ed_physician": "ED physician", "unit_nurse": "Receiving nurse",
              "admitting_physician": "Admitting physician", "viewer": "Viewer (read only)"}


def demo_personas(units: list[dict]) -> list[dict]:
    people = [
        {"id": "ed-rn", "name": "Jordan Reyes, RN", "role": "ed_nurse", "unit": None, "facility": None},
        {"id": "ed-md", "name": "Alex Chen, MD", "role": "ed_physician", "unit": None, "facility": None},
        {"id": "hosp-md", "name": "Morgan Patel, MD", "role": "admitting_physician", "unit": None, "facility": None},
    ]
    names = ["Sam Okafor, RN", "Riley Novak, RN", "Casey Lindqvist, RN", "Taylor Brooks, RN", "Jamie Alvarez, RN",
             "Drew Kowalski, RN", "Avery Nguyen, RN", "Quinn Mahoney, RN"]
    for i, u in enumerate(sorted(units, key=lambda u: (u["facility"], u["code"]))):
        people.append({"id": f"unit-{u['facility']}-{u['code']}".lower(), "name": names[i % len(names)],
                       "role": "unit_nurse", "unit": u["code"], "facility": u["facility"], "unit_name": u["name"]})
    people.append({"id": "viewer", "name": "Viewer", "role": "viewer", "unit": None, "facility": None})
    for p in people:
        p["role_label"] = ROLE_LABEL[p["role"]]
        p["default_template"] = DEFAULT_TEMPLATE[p["role"]]
        p["can_sign"] = list(SIGNS.get(p["role"], ()))
        p["can_ack"] = list(ACKS.get(p["role"], ()))
    return people


def sees(person: dict, facility: str | None, dest_unit: str | None) -> bool:
    if person["role"] != "unit_nurse":
        return True
    return dest_unit == person["unit"] and (person.get("facility") in (None, facility))


# ------------------------------------------------------------------------------------------------ role map
FIXED_IDS = ("ed-rn", "ed-md", "hosp-md", "viewer")


def unit_id(facility: str, code: str) -> str:
    return f"unit-{facility}-{code}".lower()


def parse_role_map(raw: str | None) -> tuple[dict[str, str], list[str]]:
    """EDIP_ROLE_MAP as {lowercased email: persona id}, and the problems found in it (logged at start up).
    Emails match without regard to case. A broken map gives everyone the read only viewer role."""
    import json

    problems: list[str] = []
    try:
        data = json.loads(raw or "{}")
    except ValueError as exc:
        return {}, [f"EDIP_ROLE_MAP is not valid JSON ({exc}); everyone gets the read only viewer role"]
    if not isinstance(data, dict):
        return {}, ["EDIP_ROLE_MAP must be a JSON object of email to persona id; everyone gets the read only viewer role"]
    out: dict[str, str] = {}
    for email, pid in data.items():
        if not isinstance(pid, str) or not pid.strip():
            problems.append(f"EDIP_ROLE_MAP: the persona for {email} is not a persona id; they get the viewer role")
            continue
        key, pid = email.strip().lower(), pid.strip()
        if key in out and out[key] != pid:
            problems.append(f"EDIP_ROLE_MAP: {email} is listed twice with different personas; using {pid}")
        if pid not in FIXED_IDS and not (pid.startswith("unit-") and pid.count("-") >= 2):
            problems.append(f"EDIP_ROLE_MAP: {email} maps to {pid}, which is not a persona id (see app/README.md); "
                            f"they get the viewer role")
        out[key] = pid
    return out, problems


def mapped_units(role_map: dict[str, str], known: set[str]) -> list[dict]:
    """Receiving units named in the role map that the data does not list (no patients bound there yet), so their
    nurses keep their role. The persona id is unit-<facility>-<unit code>, lowercased."""
    out, seen = [], set(known)
    for pid in role_map.values():
        if pid.startswith("unit-") and pid not in seen:
            fac, _, code = pid[len("unit-"):].partition("-")
            if fac and code:
                seen.add(pid)
                out.append({"dept_id": pid, "facility": fac.upper(), "code": code.upper(), "name": f"{fac.upper()} {code.upper()}",
                            "level": "", "beds": 0})
    return out
