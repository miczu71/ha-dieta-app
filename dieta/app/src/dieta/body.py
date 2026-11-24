"""Import pomiarów ciała z Zepp do bazy (bez sieci): ``body_measurement`` + dzienna waga w ``weight``."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from dieta.store import BodyRow, Store
from dieta.zepp import BodyRecord


@dataclass(frozen=True)
class ImportResult:
    new: int
    skipped: int
    days: int  # dni, dla których zapisano wagę do ``weight``


def _to_row(record: BodyRecord) -> BodyRow:
    return BodyRow(
        ts=record.ts,
        source="zepp",
        weight_kg=record.weight_kg,
        fat_pct=record.fat_pct,
        muscle_pct=record.muscle_rate,
        water_pct=record.water_pct,
        bone_kg=record.bone_kg,
        visceral=record.visceral,
        bmr_kcal=record.bmr_kcal,
        bmi=record.bmi,
        impedance=record.impedance,
        body_score=record.body_score,
        metabolic_age=record.metabolic_age,
        raw_json=json.dumps(record.raw, sort_keys=True),
    )


def import_records(store: Store, person_id: int, records: Sequence[BodyRecord]) -> ImportResult:
    new = store.add_body_measurements(person_id, [_to_row(r) for r in records])
    last_of_day: dict[date, float] = {}
    for record in sorted(records, key=lambda r: r.ts):
        if record.weight_kg is not None:
            last_of_day[datetime.fromtimestamp(record.ts).date()] = record.weight_kg
    days = 0
    for day, kg in last_of_day.items():
        if store.add_weight_if_not_manual(person_id, day, kg):
            days += 1
    return ImportResult(new=new, skipped=len(records) - new, days=days)
