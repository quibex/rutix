"""Parse and edit sections of daily/*.md files.

A daily file has these sections (top-to-bottom): Сон, Время (ч), Привычки,
Питание (table), Что сделано, Заметки. We touch Питание / Привычки /
Что сделано / Заметки. The rest stays as the user wrote it.
"""

import re
from dataclasses import dataclass


@dataclass
class MealItem:
    slot: str  # "Завтрак" | "Обед" | "Ужин" | "Перекус" | etc.
    name: str
    kcal: int
    protein: float
    fat: float
    carbs: float
    source: str = ""  # "reference" | "estimate" | "" — set by Claude parser


# --- Section helpers --------------------------------------------------------

_SECTION_RE = re.compile(
    r"^## (?P<title>[^\n]+)\n(?P<body>.*?)(?=\n## |\Z)",
    re.MULTILINE | re.DOTALL,
)


def _replace_section_body(md: str, title: str, new_body: str) -> str:
    """Replace the body of `## <title>` with new_body. Body excludes the
    horizontal-rule terminator if one is present below.

    Raises ValueError if the section is not found.
    """
    for match in _SECTION_RE.finditer(md):
        if match.group("title").strip() == title:
            old_body = match.group("body")
            return md[: match.start("body")] + new_body + md[match.start("body") + len(old_body) :]
    raise ValueError(f"{title} section not found")


def _section_body(md: str, title: str) -> str:
    """Return the raw body of `## <title>` (text between header and next '## ')."""
    for match in _SECTION_RE.finditer(md):
        if match.group("title").strip() == title:
            return match.group("body")
    raise ValueError(f"{title} section not found")


# --- Notes / Done -----------------------------------------------------------


def _append_bullet(body: str, text: str) -> str:
    """Append `- text` as a new bullet to a section body.

    Replaces a single empty `- ` placeholder if present; otherwise appends.
    Trailing whitespace and the section's terminating `---` line (if any)
    are preserved as-is.
    """
    lines = body.splitlines(keepends=False)
    placeholder_idx = None
    for i, line in enumerate(lines):
        if line.strip() == "-":
            placeholder_idx = i
            break

    new_line = f"- {text}"
    if placeholder_idx is not None:
        lines[placeholder_idx] = new_line
    else:
        # Find last bullet line; insert after it. If no bullets, insert before
        # any '---' or trailing blanks.
        last_bullet = -1
        for i, line in enumerate(lines):
            if line.lstrip().startswith("- "):
                last_bullet = i
        if last_bullet >= 0:
            lines.insert(last_bullet + 1, new_line)
        else:
            # No bullets — append after first blank
            lines.insert(0, new_line)

    return "\n".join(lines)


def append_note(md: str, text: str) -> str:
    if text in parse_notes(md):
        return md
    body = _section_body(md, "Заметки")
    return _replace_section_body(md, "Заметки", _append_bullet(body, text))


def append_done(md: str, text: str) -> str:
    # Idempotent: skip if the bullet is already present. update_habits is
    # re-run by the 06:00/08:00 catch-up crons, and without this gate every
    # retry would duplicate every bullet under `## Что сделано`.
    if text in parse_done(md):
        return md
    body = _section_body(md, "Что сделано")
    return _replace_section_body(md, "Что сделано", _append_bullet(body, text))


_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$")


def _parse_bullets(md: str, section: str) -> list[str]:
    """Return non-empty bullet texts from a section. Empty placeholder
    `-` lines and lines containing only `- ` are skipped. Missing section → []."""
    try:
        body = _section_body(md, section)
    except ValueError:
        return []
    bullets: list[str] = []
    for line in body.splitlines():
        if not line.strip() or line.strip() == "-":
            continue
        m = _BULLET_RE.match(line)
        if m:
            bullets.append(m.group(1).strip())
    return bullets


def parse_notes(md: str) -> list[str]:
    """Return non-empty bullets from the ## Заметки section."""
    return _parse_bullets(md, "Заметки")


def parse_done(md: str) -> list[str]:
    """Return non-empty bullets from the ## Что сделано section."""
    return _parse_bullets(md, "Что сделано")


# --- Habits -----------------------------------------------------------------

_HABIT_LINE_RE = re.compile(r"^(\s*-\s*\[)([ x])(\]\s*)(.+?)\s*$")


def parse_habit_labels(md: str) -> list[str]:
    """Return habit labels from the ## Привычки section, in source order.

    Includes both checked ([x]) and unchecked ([ ]) habits — the config is the
    same regardless of today's state.
    """
    body = _section_body(md, "Привычки")
    labels: list[str] = []
    for line in body.splitlines():
        m = _HABIT_LINE_RE.match(line)
        if m:
            labels.append(m.group(4).strip())
    return labels


def update_habits_checked(md: str, done: set[str]) -> str:
    """Mark each habit whose label is in `done` as checked. Already-checked
    habits stay checked. Unmatched habits are untouched.
    """
    if not done:
        return md
    body = _section_body(md, "Привычки")
    new_lines = []
    for line in body.splitlines():
        m = _HABIT_LINE_RE.match(line)
        if m:
            label = m.group(4).strip()
            if label in done:
                new_lines.append(f"{m.group(1)}x{m.group(3)}{m.group(4)}")
                continue
        new_lines.append(line)
    return _replace_section_body(md, "Привычки", "\n".join(new_lines))


# --- Meals ------------------------------------------------------------------

_TOTAL_RE = re.compile(r"^\|\s*\*\*Итого\*\*\s*\|", re.MULTILINE)
_DATA_ROW_RE = re.compile(
    r"^\|\s*([^|]*?)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*$"
)
_HEADER_OR_SEP_RE = re.compile(r"^\|.*Приём.*\||^\|[\s\-:|]+\|$")


def parse_meals(md: str) -> list[MealItem]:
    """Read all data rows in the Питание table.

    The 'Приём' column carries through empty rows: an empty slot cell inherits
    the slot from the previous data row. Returns items in row order.
    """
    body = _section_body(md, "Питание")
    items: list[MealItem] = []
    current_slot = ""
    for line in body.splitlines():
        if not line.strip().startswith("|"):
            continue
        if _HEADER_OR_SEP_RE.match(line):
            continue
        if _TOTAL_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        slot, name, kcal, p, f, c = m.groups()
        slot = slot.strip()
        if slot:
            current_slot = slot
        if not name.strip() or name.strip() == "":
            continue
        items.append(
            MealItem(
                slot=current_slot,
                name=name.strip(),
                kcal=int(kcal),
                protein=float(p),
                fat=float(f),
                carbs=float(c),
            )
        )
    return items


def _format_num(v: float) -> str:
    """For totals — strip trailing zero on whole numbers."""
    if v == int(v):
        return str(int(v))
    return f"{v:g}"


def append_meal(md: str, item: MealItem) -> str:
    """Append a meal row to the Питание table and recompute the Итого row.

    If the previous data row has the same slot label, the new row's slot
    cell is left empty (matches the existing repo convention).
    """
    body = _section_body(md, "Питание")
    lines = body.splitlines()

    # Locate Итого line index; it must exist
    itogo_idx = None
    for i, line in enumerate(lines):
        if _TOTAL_RE.match(line):
            itogo_idx = i
            break
    if itogo_idx is None:
        raise ValueError("Питание Итого row not found")

    # Determine the previous slot label (last non-placeholder data row)
    prev_slot = ""
    for line in reversed(lines[:itogo_idx]):
        if not line.startswith("|"):
            continue
        if _HEADER_OR_SEP_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        s = m.group(1).strip()
        if s:
            prev_slot = s
            break

    rendered_slot = "" if item.slot == prev_slot else item.slot
    new_row = (
        f"| {rendered_slot} | {item.name} | {item.kcal} | "
        f"{_format_num(item.protein)} | {_format_num(item.fat)} | {_format_num(item.carbs)} |"
    )

    # Drop the empty placeholder row if it's right above Итого
    place_idx = itogo_idx - 1
    if place_idx >= 0 and lines[place_idx].strip().replace("|", "").replace(" ", "") == "":
        del lines[place_idx]
        itogo_idx -= 1

    lines.insert(itogo_idx, new_row)
    itogo_idx += 1

    # Recompute totals from all data rows
    items_now = []
    for line in lines[:itogo_idx]:
        if not line.startswith("|"):
            continue
        if _HEADER_OR_SEP_RE.match(line):
            continue
        m = _DATA_ROW_RE.match(line)
        if not m:
            continue
        items_now.append((int(m.group(3)), float(m.group(4)), float(m.group(5)), float(m.group(6))))
    total_kcal = sum(i[0] for i in items_now)
    total_p = sum(i[1] for i in items_now)
    total_f = sum(i[2] for i in items_now)
    total_c = sum(i[3] for i in items_now)
    lines[itogo_idx] = (
        f"| **Итого** |  | **{total_kcal}** | **{_format_num(total_p)}** | "
        f"**{_format_num(total_f)}** | **{_format_num(total_c)}** |"
    )

    return _replace_section_body(md, "Питание", "\n".join(lines))
