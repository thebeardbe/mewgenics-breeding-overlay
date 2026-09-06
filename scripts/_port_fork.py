"""One-off: splice named top-level functions from the maintained MBM fork
(whyayala, v5.9.5) into our vendored copies. Only the listed functions are
touched; everything else in our vendor stays as frankieg33 v5.8.4.

Usage: python3 scripts/_port_fork.py   (prints plan; --apply to write)
"""

import sys

FORK = "/tmp/mbm-whyayala/src"
OURS = "src/mewgenics_overlay/vendor"

# file -> [function names to port (in order of appearance in source)]
PLAN = {
    "save_parser.py": [
        "_stimulation_inheritance_weight",
        "summarize_furniture_room",
        "can_breed",
    ],
    "breeding.py": [
        "estimate_breeding_compatibility",
        "pair_breeding_compatibility",
        "game_compatibility",
    ],
}


def spans(lines, names):
    """Return {name: (start,end)} of top-level def/async def blocks."""
    starts = {}
    for i, line in enumerate(lines):
        for name in names:
            if line.startswith(f"def {name}(") or line.startswith(f"async def {name}("):
                starts[name] = i
    out = {}
    order = [n for n in names if n in starts]
    for j, name in enumerate(order):
        beg = starts[name]
        end = len(lines)
        for other in order:
            if starts[other] > beg and starts[other] < end:
                end = starts[other]
        # stop at next decorator/def of anything else? next 'def ' line >= beg+1
        for i in range(beg + 1, len(lines)):
            if lines[i].startswith("def ") or lines[i].startswith("async def ") or lines[i].startswith("@dataclass"):
                end = min(end, i)
                break
        out[name] = (beg, end)
    return out


def main(apply: bool):
    import difflib
    for fname, names in PLAN.items():
        ours = open(f"{OURS}/{fname}").read().splitlines(keepends=True)
        fork = open(f"{FORK}/{fname}").read().splitlines(keepends=True)
        so = spans(ours, names)
        sf = spans(fork, names)
        missing = [n for n in names if n not in sf]
        if missing:
            print(f"{fname}: NOT FOUND in fork: {missing}")
            continue
        edits = []
        for name in names:
            obeg, oend = so[name]
            fbeg, fend = sf[name]
            old = "".join(ours[obeg:oend])
            new = "".join(fork[fbeg:fend])
            if apply:
                edits.append((obeg, oend, new))
            else:
                d = list(difflib.unified_diff(
                    old.splitlines(), new.splitlines(),
                    f"{fname} ours:{name}", f"{fname} fork:{name}", lineterm=""))
                added = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
                removed = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
                print(f"{fname} :: {name}: will replace {oend-obeg} lines "
                      f"({removed} removed, {added} added)")
        if apply:
            for obeg, oend, new in reversed(edits):
                ours[obeg:oend] = [new]
            with open(f"{OURS}/{fname}", "w") as fh:
                fh.writelines(ours)
            print(f"{fname}: applied {len(edits)} port(s)")


if __name__ == "__main__":
    main("--apply" in sys.argv)
