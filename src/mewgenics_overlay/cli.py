"""Command-line interface: validate and demo the overlay core without a GUI.

Usage (from the repo root, with `src` on the path):

    PYTHONPATH=src python -m mewgenics_overlay.cli list [--save path]
    PYTHONPATH=src python -m mewgenics_overlay.cli pick NAME [--save path]
    PYTHONPATH=src python -m mewgenics_overlay.cli partners NAME [--save path] [--room]

If --save is omitted the most recently modified discovered save is used.
"""

from __future__ import annotations

import argparse
import os
import sys

from mewgenics_overlay.core.discovery import newest_save
from mewgenics_overlay.core.session import Session, display_location

ALIVE = ("In House", "Adventure")


def _resolve_save(args) -> str:
    if args.save:
        return args.save
    found = newest_save()
    if not found:
        sys.exit("No save found. Pass --save <path.sav> or set MEWGENICS_SAVES_ROOT.")
    print(f"[auto] {found['path']}", file=sys.stderr)
    return found["path"]


def _sum_colored(stats: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in stats.items())


def cmd_list(sess: Session, args) -> int:
    cats = sess.alive
    print(f"{len(sess.cats)} total cats, {len(cats)} alive in {sess.save_path}")
    cats.sort(key=lambda c: c.name.lower())
    for c in cats[: args.limit]:
        print(f"  {c.name:<24} {c.gender:<7} {display_location(c):<14} "
              f"gen={c.generation} sum={sum(c.base_stats.values())}")
    return 0


def cmd_pick(sess: Session, args) -> int:
    hits = sess.search(args.name)
    if not hits:
        print(f"no cat matching {args.name!r}")
        return 1
    for c in hits[:10]:
        s = sess.summary(c)
        lovers = ", ".join(s.lover_names) or "-"
        print(f"\n{s.name}  [{s.gender}]  {s.room or s.status}  gen={s.generation}  age={s.age}")
        print(f"  base: {_sum_colored(s.base_stats)}  (sum {s.stat_sum})")
        print(f"  total (incl. gear/mods): {_sum_colored(s.total_stats)}")
        print(f"  lovers: {lovers}   inbredness: {s.inbredness:.3f}")
    return 0


def cmd_partners(sess: Session, args) -> int:
    hits = sess.search(args.name)
    if not hits:
        print(f"no cat matching {args.name!r}")
        return 1
    cat = hits[0]
    print(f"Best breeding partners for {cat.name} [{cat.gender}, {display_location(cat)}]")
    rows = sess.rank_partners(
        cat,
        max_partners=args.limit,
        include_adventure=not args.room,
        show_blocked=3,
        order=args.order,
        stimulation=args.stim,
    )
    for r in rows:
        if not r.compatible:
            why = r.reason or "blocked"
            print(f"  ✗ {r.partner.name:<24} {why}  [rel={r.relation.label}]")
            continue
        tags = []
        if r.mutual_lover:
            tags.append("♥♥")
        elif r.is_lover:
            tags.append("♥")
        if r.is_hater:
            tags.append("hates")
        if r.partner.generation >= 1:
            tags.append(f"gen{r.generation}")
        tag = (" " + " ".join(tags)) if tags else ""
        rel = r.relation
        chance = max(0.0, min(100.0, (min(1.0, r.game_compat)) ** 2 * 100.0))
        print(
            f"  ✓ {r.partner.name:<24} risk={r.risk_pct:4.1f}%  "
            f"night={chance:.0f}%  exp={r.expected_avg:.2f}/stat "
            f"(sum {r.stat_sum_range[0]}–{r.stat_sum_range[1]})  "
            f"7s≈{r.seven_plus_total:.1f}  q={r.quality:.0f}  "
            f"[rel={rel.label}, Δgen {rel.gen_gap:+d}, coi={r.coi * 100:.1f}%]{tag}"
        )
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--save", help="path to a .sav file")
    common.add_argument("--limit", type=int, default=25, help="row limit")

    p = argparse.ArgumentParser(description=__doc__, parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="list alive cats", parents=[common])
    sp_pick = sub.add_parser("pick", help="show details for a cat", parents=[common])
    sp_pick.add_argument("name")
    sp_part = sub.add_parser("partners", help="rank breeding partners for a cat", parents=[common])
    sp_part.add_argument("name")
    sp_part.add_argument("--room", action="store_true",
                        help="only consider cats currently in the house")
    sp_part.add_argument("--order", choices=("risk", "quality"), default="risk",
                        help="partner sort: risk-first (default) or MBM quality")
    sp_part.add_argument("--stim", type=float, default=50.0,
                        help="breeding-room Stimulation used for inheritance "
                             "math (default 50)")

    args = p.parse_args(argv)
    save_path = _resolve_save(args)
    sess = Session(save_path)
    cmd = {"list": cmd_list, "pick": cmd_pick, "partners": cmd_partners}[args.cmd]
    return cmd(sess, args)


if __name__ == "__main__":
    raise SystemExit(main())
