"""Breeding-room Stimulation, from the furniture actually in each room.

The game's Stimulation stat is provided by room furniture (definitions in
resources.gpak). The vendored parser's ``summarize_furniture_room`` aggregates
furniture effects per room; here we expose the resulting Stimulation value so
pair math (stat inheritance weighting, single-carrier defect odds) can use the
real number instead of the default 50.
"""

from __future__ import annotations

from typing import Dict

from mewgenics_overlay.vendor.save_parser import summarize_furniture_room

STIMULATION_DEFAULT = 50.0


def room_stimulation_map(
    furniture_by_room: dict,
    definitions: dict,
) -> Dict[str, float]:
    """room key -> furniture Stimulation value (may be negative)."""
    out: Dict[str, float] = {}
    for room, items in (furniture_by_room or {}).items():
        if not room:
            continue
        summary = summarize_furniture_room(
            items, definitions=definitions, room=room)
        out[room] = float(summary.raw_effects.get("Stimulation", 0.0)
                          or 0.0)
    return out
