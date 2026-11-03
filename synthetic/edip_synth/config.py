"""Generator parameters. Values marked (c) in the research spec are design choices;
the rest are anchored to public sources cited in docs/synthetic-data.md.

Every number a reviewer might want to tune lives here, so a health system can
match its own volumes by editing one file.
"""
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Config:
    seed: int = 20261102
    # Monday 00:00 to Sunday 23:59 local. US daylight saving time ends on Sunday
    # 2026-11-01 at 02:00, so the 01:00 hour happens twice inside the window.
    window_start: datetime = datetime(2026, 10, 26, 0, 0, tzinfo=TZ)
    days: int = 7

    # Volumes (about 100 ED visits a day across two EDs; 17 percent admitted,
    # close to the national 11.5 to 19 percent).
    encounters: int = 700
    facility_share: dict = field(default_factory=lambda: {"MGH": 0.6, "LKS": 0.4})
    admitted_inpatient: int = 90
    admitted_observation: int = 30
    obs_to_inpatient: int = 3
    lwbs: int = 13
    ama: int = 8
    left_before_complete: int = 10
    transfer_out: int = 6
    returns_72h: int = 25          # second CSN for an existing patient inside the window
    returns_admitted: int = 4

    # ESI mix for all visits (percent), NHAMCS 2022 renormalized and blended (c).
    esi_mix: dict = field(default_factory=lambda: {1: 2, 2: 22, 3: 52, 4: 21, 5: 3})

    # Hourly arrival weights, percent of daily arrivals by start hour (c, anchored
    # to the NHAMCS 2006 trough and peak) and day of week weights (c).
    hourly: tuple = (2.5, 2.0, 1.6, 1.3, 1.2, 1.3, 1.9, 3.1, 4.4, 5.5, 5.8, 6.0,
                     6.0, 5.9, 5.8, 5.8, 5.9, 6.0, 6.1, 5.8, 5.2, 4.6, 3.6, 2.7)
    daily: tuple = (16.2, 14.8, 14.3, 14.0, 14.0, 13.3, 13.4)   # Mon..Sun

    # Timing, minutes: (median, p90) for lognormal draws.
    door_to_triage: tuple = (7, 25)
    door_to_provider: tuple = (20, 75)
    door_to_provider_high_acuity: tuple = (8, 20)
    arrival_to_decision: tuple = (200, 360)
    decision_floor_min: int = 45
    boarding: tuple = (150, 480)
    bed_request_to_assigned: tuple = (35, 190)
    bed_assigned_to_departure: tuple = (50, 150)
    discharged_los: tuple = (160, 330)
    crowding_threshold: float = 0.90
    crowding_multiplier: tuple = (2.0, 3.0)
    isolation_request_multiplier: float = 1.5

    # How long after transfer to keep simulating inpatient data (hours).
    inpatient_horizon_h: int = 48
    # Starting inpatient census as a share of beds (background patients).
    initial_occupancy: tuple = (0.74, 0.88)
    target_occupancy: float = 0.78

    # Shares among admitted patients (c).
    isolation_share: float = 0.15
    missing_code_status: int = 4
    allergy_any: float = 0.38

    # Site profile knobs the renderers read (section 13 of the spec).
    site_profile: dict = field(default_factory=lambda: {
        "MGH": {"hl7_version": "2.5.1", "class_change": "A06", "a14": True,
                "z_segments": True, "msh7_offset": True, "flowsheet_oru": True},
        "LKS": {"hl7_version": "2.3", "class_change": "A08", "a14": False,
                "z_segments": False, "msh7_offset": False, "flowsheet_oru": False},
    })

    @property
    def admitted(self) -> int:
        return self.admitted_inpatient + self.admitted_observation
