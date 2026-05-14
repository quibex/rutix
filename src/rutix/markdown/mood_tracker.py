"""Surgical edits to health/mood_tracker.md.

Schema (matches the existing format in quibex/life:health/mood_tracker.md):
| День | Настр. | Сон (ч) | Вес | Тревога | Раздр. | <med1> | ... | Алк/Нарк | Заметки |

`Алк/Нарк` column is always rendered empty — kept for back-compat with existing rows.
"""
import re
from dataclasses import dataclass, field

RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


@dataclass
class MedColumn:
    column_label: str    # "Сейзар" — used by header generator (Phase 2+); not by render_row
    taken: bool
    dose: str             # "25" or "12.5"


@dataclass
class DayRow:
    day: int
    mood: int | None = None
    sleep_hours: float | None = None
    weight: float | None = None
    anxiety: int | None = None
    irritability: int | None = None
    meds: list[MedColumn] = field(default_factory=list)
    notes: str = ""


def render_row(row: DayRow) -> str:
    """Render a single table row in the canonical format."""
    cells: list[str] = [
        str(row.day),
        _signed(row.mood),
        _float_cell(row.sleep_hours),
        _float_cell(row.weight),
        _int_cell(row.anxiety),
        _int_cell(row.irritability),
    ]
    cells.extend(_med_cell(m) for m in row.meds)
    cells.append("")            # Алк/Нарк always empty
    cells.append(row.notes)
    return "| " + " | ".join(cells) + " |"


def _int_cell(v: int | None) -> str:
    return "" if v is None else str(v)


def _signed(v: int | None) -> str:
    if v is None:
        return ""
    if v > 0:
        return f"+{v}"
    return str(v)


def _float_cell(v: float | None) -> str:
    if v is None:
        return ""
    if v == int(v):
        return str(int(v))
    return str(v)


def _med_cell(m: MedColumn) -> str:
    return f"✓ {m.dose}" if m.taken else ""


def update_day_row(markdown: str, year: int, month: int, day: int, new_row: str) -> str:
    """Replace the row for given day in the month section.

    Raises ValueError if the section header or the day row is not found.
    """
    section_header = f"## {RU_MONTHS[month]} {year}"
    section_idx = markdown.find(section_header)
    if section_idx == -1:
        raise ValueError(f"Section not found: {section_header}")

    after_header = markdown[section_idx + len(section_header):]
    next_section = re.search(r"\n## ", after_header)
    section_end = (
        section_idx + len(section_header) + next_section.start()
        if next_section
        else len(markdown)
    )
    section_text = markdown[section_idx:section_end]

    pattern = re.compile(rf"^\|\s*{day}\s*\|.*$", re.MULTILINE)
    if not pattern.search(section_text):
        raise ValueError(f"Day row {day} not found in section {section_header}")
    new_section = pattern.sub(new_row, section_text, count=1)
    return markdown[:section_idx] + new_section + markdown[section_end:]
