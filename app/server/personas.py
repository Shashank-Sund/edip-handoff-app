"""Who is using the app and what they may do.

Mock mode offers demo personas (invented people). Live mode maps the signed in Databricks user to a role through
EDIP_ROLE_MAP (see config.py); without a mapping a user gets the ED nurse role.

Roles:
  ed_nurse, ed_physician     see every patient; edit, attest and sign the handoffs for their audience
  unit_nurse                 sees patients bound for their unit; acknowledges nurse handoffs with a read back
  admitting_physician        sees every admitted patient; acknowledges physician handoffs with a read back
"""
from __future__ import annotations

SIGNS = {"ed_nurse": ("sbar_nurse", "custom_sea58"), "ed_physician": ("ipass_physician", "custom_sea58")}
ACKS = {"unit_nurse": ("sbar_nurse", "custom_sea58"), "admitting_physician": ("ipass_physician", "custom_sea58")}
DEFAULT_TEMPLATE = {"ed_nurse": "sbar_nurse", "ed_physician": "ipass_physician", "unit_nurse": "sbar_nurse",
                    "admitting_physician": "ipass_physician"}
ROLE_LABEL = {"ed_nurse": "ED nurse", "ed_physician": "ED physician", "unit_nurse": "Receiving nurse",
              "admitting_physician": "Admitting physician"}


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
