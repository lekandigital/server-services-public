#!/usr/bin/env python3
"""Regression test for where a newly imported list lands in the follow order.

The bug this pins down: a drag saved the whole visible order, so every list looked
deliberately placed, and a list imported afterwards was the only unranked one — it sank
below every finished list. These checks cover the placement rule in
queue_dedupe.resolve_list_order and the anchor bookkeeping in dashboard.py.

    python3 test_list_order.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard  # noqa: E402
from queue_dedupe import (  # noqa: E402
    ListOrder,
    dedupe_best_pending,
    load_list_priority,
    resolve_list_order,
)

PASSED = []


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
    PASSED.append(label)
    print(f"  ok  {label}")


def order(saved, anchors, sizes, names=None):
    ranks = ListOrder({n: float(i) for i, n in enumerate(saved)}, anchors)
    return resolve_list_order(ranks, sizes, names if names is not None else list(sizes))


def run():
    print("\n— placement —")

    # The reported bug: A is being followed, B is up next, C and D are finished, and the
    # freshly imported NEW used to land below all four.
    sizes = {"A": 500, "B": 300, "C": 0, "D": 0, "NEW": 900}
    got = order(["A", "B", "C", "D"], {}, sizes)
    check("a new list lands behind current and up-next, not at the bottom",
          got == ["A", "B", "NEW", "C", "D"], str(got))

    check("finished lists sink even when they were dragged to the top",
          order(["C", "A", "B"], {}, sizes)[:1] == ["A"],
          str(order(["C", "A", "B"], {}, sizes)))

    sizes2 = {"A": 500, "B": 300, "E": 100, "NEW": 900}
    check("a list pinned upward keeps new lists behind it",
          order(["A", "B", "E"], {"E": "up"}, sizes2) == ["A", "B", "E", "NEW"],
          str(order(["A", "B", "E"], {"E": "up"}, sizes2)))
    check("a list pinned downward does not",
          order(["A", "B", "E"], {"E": "down"}, sizes2) == ["A", "B", "NEW", "E"],
          str(order(["A", "B", "E"], {"E": "down"}, sizes2)))

    many = {"A": 500, "B": 300, "N1": 40, "N2": 900, "N3": 7}
    check("several new lists go in together, smallest first",
          order(["A", "B"], {}, many) == ["A", "B", "N3", "N1", "N2"],
          str(order(["A", "B"], {}, many)))

    check("a new list never displaces the list being followed now",
          order(["A", "B"], {}, {"A": 500, "B": 300, "NEW": 1})[0] == "A",
          str(order(["A", "B"], {}, {"A": 500, "B": 300, "NEW": 1})))

    check("with only one arranged list left, a new list still goes after it",
          order(["A"], {}, {"A": 500, "NEW": 10}) == ["A", "NEW"],
          str(order(["A"], {}, {"A": 500, "NEW": 10})))

    check("no saved order at all stays purely automatic",
          order([], {}, {"A": 500, "NEW": 10}) == ["NEW", "A"],
          str(order([], {}, {"A": 500, "NEW": 10})))

    print("\n— the bot follows that order —")
    q = ([{"username": f"a{i}", "source_list": "A", "status": "pending_follow"} for i in range(5)]
         + [{"username": f"b{i}", "source_list": "B", "status": "pending_follow"} for i in range(3)]
         + [{"username": f"n{i}", "source_list": "NEW", "status": "pending_follow"} for i in range(9)]
         + [{"username": f"z{i}", "source_list": "Z", "status": "pending_follow"} for i in range(4)])
    ranks = ListOrder({"A": 0.0, "B": 1.0, "Z": 2.0}, {})
    ordered, _ = dedupe_best_pending(q, ranks)
    seen = list(dict.fromkeys(e["source_list"] for e in ordered))
    check("dedupe_best_pending queues the new list third, ahead of the unpinned Z",
          seen == ["A", "B", "NEW", "Z"], str(seen))

    ranks_pinned = ListOrder({"A": 0.0, "B": 1.0, "Z": 2.0}, {"Z": "up"})
    ordered, _ = dedupe_best_pending(q, ranks_pinned)
    seen = list(dict.fromkeys(e["source_list"] for e in ordered))
    check("a pinned Z stays ahead of the new list",
          seen == ["A", "B", "Z", "NEW"], str(seen))

    print("\n— what a drag saves —")
    tmp = tempfile.mkdtemp(prefix="xb-order-")
    prev_dir = dashboard.DATA_DIR
    dashboard.DATA_DIR = tmp
    dashboard.app.config["TESTING"] = True
    try:
        with dashboard.app.test_client() as c:
            c.post("/api/queue/sources/order", json={"order": ["A", "B", "C"]})
            saved = json.load(open(os.path.join(tmp, "list_priority.json")))
            check("an order with no named row anchors nothing", saved["anchors"] == {},
                  str(saved))

            c.post("/api/queue/sources/order",
                   json={"order": ["C", "A", "B"], "moved": "C", "direction": "up"})
            check("dragging a row up anchors it up",
                  load_list_priority(tmp).anchors == {"C": "up"},
                  str(load_list_priority(tmp).anchors))

            c.post("/api/queue/sources/order",
                   json={"order": ["A", "B", "C"], "moved": "C", "direction": "down"})
            check("dragging it back down flips the anchor",
                  load_list_priority(tmp).anchors == {"C": "down"},
                  str(load_list_priority(tmp).anchors))

            c.post("/api/queue/sources/order", json={"order": ["A", "C", "B"], "moved": "A"})
            anchors = load_list_priority(tmp).anchors
            check("a drag with no direction still anchors, falling back to the saved order",
                  anchors.get("A") == "up" and anchors.get("C") == "down", str(anchors))

            c.post("/api/queue/sources/order", json={"unpin": "C"})
            after = load_list_priority(tmp)
            check("unpinning drops the anchor and keeps the position",
                  "C" not in after.anchors and sorted(after, key=after.get) == ["A", "C", "B"],
                  str((after.anchors, sorted(after, key=after.get))))

            c.post("/api/queue/sources/order", json={"reset": True})
            check("reset removes the file entirely",
                  not os.path.exists(os.path.join(tmp, "list_priority.json")))

            legacy = {"order": ["A", "B", "C"], "updated": "2025-01-01T00:00:00"}
            with open(os.path.join(tmp, "list_priority.json"), "w") as f:
                json.dump(legacy, f)
            loaded = load_list_priority(tmp)
            check("a file written before anchors existed keeps its order and anchors nothing",
                  sorted(loaded, key=loaded.get) == ["A", "B", "C"] and loaded.anchors == {},
                  str((sorted(loaded, key=loaded.get), loaded.anchors)))
            check("...so a new list slots straight in behind current and up-next",
                  resolve_list_order(loaded, {"A": 5, "B": 5, "C": 5, "NEW": 5},
                                     ["A", "B", "C", "NEW"]) == ["A", "B", "NEW", "C"])
    finally:
        dashboard.DATA_DIR = prev_dir
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        run()
    except AssertionError as e:
        print(f"\nFAILED after {len(PASSED)} checks: {e}")
        sys.exit(1)
    print(f"\nAll {len(PASSED)} checks passed")
