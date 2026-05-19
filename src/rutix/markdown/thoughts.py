"""Generate thoughts/2026-Wxx.md — Заметки section bullets from daily files
collected into a single weekly archive.

Pure code, no Claude — agent_instructions.md says "переносишь мысли из
daily-файлов недели". This is exactly that: read ## Заметки from each daily,
group by date, write a flat archive file.
"""

from dataclasses import dataclass
from datetime import date

from rutix.markdown.weekly import RU_MONTH_SHORT, russian_date_range


@dataclass
class ThoughtsDay:
    date: date
    notes: list[str]


def _day_label(d: date) -> str:
    return f"{d.day} {RU_MONTH_SHORT[d.month]}"


def render_thoughts_weekly(year: int, week_num: int, days: list[ThoughtsDay]) -> str:
    if days:
        date_range = russian_date_range(days[0].date, days[-1].date)
        title = f"# Мысли — Неделя {week_num} ({date_range})"
    else:
        title = f"# Мысли — Неделя {week_num}"

    days_with_notes = [d for d in days if d.notes]
    if not days_with_notes:
        return f"{title}\n\n_За неделю не было заметок._\n"

    blocks = []
    for d in days_with_notes:
        bullets = "\n".join(f"- {n}" for n in d.notes)
        blocks.append(f"## {_day_label(d.date)}\n\n{bullets}")

    return f"{title}\n\n" + "\n\n".join(blocks) + "\n"
