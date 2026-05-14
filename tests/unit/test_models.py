from datetime import date

from rutix.db.models import MedActive, MedicationLog, MoodEntry


async def test_mood_entry_persists_and_loads(session):
    entry = MoodEntry(
        day=date(2026, 5, 14),
        mood=2,
        anxiety=1,
        irritability=0,
        sleep_hours=7.5,
    )
    session.add(entry)
    await session.commit()

    loaded = await session.get(MoodEntry, date(2026, 5, 14))
    assert loaded is not None
    assert loaded.mood == 2
    assert loaded.sleep_hours == 7.5
    assert loaded.weight is None


async def test_medication_log_composite_key(session):
    session.add(MedicationLog(day=date(2026, 5, 14), med_key="seizar", taken=True))
    session.add(MedicationLog(day=date(2026, 5, 14), med_key="gidr_kanon", taken=False))
    await session.commit()

    loaded = await session.get(MedicationLog, (date(2026, 5, 14), "seizar"))
    assert loaded.taken is True


async def test_med_active_archived_at_nullable(session):
    session.add(
        MedActive(
            key="seizar",
            name="Сейзар",
            column_label="Сейзар",
            current_dose="25",
            started_at=date(2026, 4, 26),
        )
    )
    await session.commit()

    loaded = await session.get(MedActive, "seizar")
    assert loaded.archived_at is None
