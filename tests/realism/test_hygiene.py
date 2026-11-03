"""Checklist items 23 to 25: synthetic identifiers, dataset labelling, and the
project's no dash writing rule applied to every table and to the source."""
import re
from dataclasses import fields, is_dataclass
from pathlib import Path

from realism_helpers import ROOT

from edip_synth.ids import luhn_npi
from edip_synth.reference import _FIRST, _LAST, FACILITIES

DASHES = (chr(0x2013), chr(0x2014))      # en dash, em dash (built from code points so this file stays clean)


# ---------------------------------------------------------------- 23. identifiers
def test_npis_are_luhn_valid_and_from_the_synthetic_block(view):
    """Every NPI passes the CMS Luhn check and starts with the generator's reserved 19990 block, so none
    can collide with an issued NPI by construction (NPPES issues from 1 and 2 series but the exact
    block is unassigned in the registry)."""
    npis = [p.npi for p in view.w.providers] + [f.npi for f in view.w.facilities]
    assert len(npis) == len(set(npis))
    for npi in npis:
        assert re.fullmatch(r"19990\d{5}", npi), npi
        assert luhn_npi(npi[:9]) == npi, npi


def test_phones_addresses_and_names_are_fictional(view):
    for p in view.patients.values():
        assert re.fullmatch(r"617-555-01\d\d", p.phone), p.phone          # 555-01xx is the reserved fictional block
        assert re.fullmatch(r"617-555-01\d\d", p.emergency_contact["phone"]), p.emergency_contact
        assert p.state == "MA" and re.fullmatch(r"02\d{3}", p.zip), (p.city, p.zip)
    for f in view.w.facilities:
        assert f.name in ("Metro General Hospital", "Lakeside Community Hospital"), f.name
    for prov in view.w.providers:
        assert prov.first in _FIRST and prov.last in _LAST, (prov.first, prov.last)
        assert re.fullmatch(r"E\d{6}", prov.prov_id) and re.fullmatch(r"[A-Z]{1,4}[A-Z]\d\d", prov.user_id), prov
    assert {f.code for f in FACILITIES} == {e.facility for e in view.encounters}


# ---------------------------------------------------------------- 24. labelling
def test_dataset_is_labelled_synthetic(view):
    readme = (ROOT / "synthetic" / "README.md").read_text()
    assert "No real patient data" in readme and "synthetic" in readme.lower()
    for p in view.patients.values():
        assert p.synthea_id, p.pat_id                 # every identity traces to Synthea or the stand in
        assert p.ssn.startswith("9"), p.ssn            # 900 series is never issued
    assert view.w.config["seed"] and view.w.config["encounters"] == 700
    assert not any(k.lower().startswith(("token", "secret", "password")) for k in view.w.config)


# ---------------------------------------------------------------- 25. no dashes anywhere
def _strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(x, (list, tuple, set)):
        for v in x:
            yield from _strings(v)
    elif is_dataclass(x):
        for f in fields(x):
            yield from _strings(getattr(x, f.name))


def test_no_em_or_en_dashes_in_any_table(view):
    w = view.w
    tables = [w.patients, w.encounters, w.adt, w.ed_events, w.bed_requests, w.orders, w.results, w.med_orders, w.mar,
              w.vitals, w.notes, w.allergies, w.code_status, w.infections, w.departments, w.providers, w.facilities,
              w.faults, w.render_plan, w.config]
    count = 0
    for table in tables:
        for s in _strings(table):
            count += 1
            assert not any(d in s for d in DASHES), s[:120]
    assert count > 100000


def test_no_dashes_in_generator_or_test_source():
    roots = [ROOT / "synthetic" / "edip_synth", ROOT / "synthetic" / "tools", ROOT / "synthetic" / "README.md",
             Path(__file__).parent]
    seen = 0
    for root in roots:
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.suffix in (".py", ".md", ".json")]
        for path in files:
            if "render" in path.parts:
                continue                                 # owned by the renderer work
            text = path.read_text(encoding="utf-8")
            seen += 1
            for i, line in enumerate(text.splitlines(), 1):
                assert not any(d in line for d in DASHES), f"{path}:{i}"
    assert seen >= 15
