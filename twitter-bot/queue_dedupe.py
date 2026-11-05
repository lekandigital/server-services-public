"""
Shared queue rules for pending_follow:

0. Manual list order: if list_priority.json exists, source lists named there are worked through
   in exactly that order (dashboard drag-and-drop writes it). A list that is *not* named there
   — anything imported since the last drag — is placed by resolve_list_order rather than dumped
   at the end; see that function for where new arrivals land.
1. Duplicate usernames (same handle): keep one row — prefer the row from the higher-priority
   source_list (manual rank, else smallest pending count), then lowest followers_count.
2. Follow order: same tuple — manual rank, then smallest lists, then lowest follower count.

Non-pending rows are unchanged by dedupe_best_pending; merge_identity_duplicates handles
identity-key collisions across the full queue for audit --fix.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any

from twitter_csv import identity_key_set

STATUS_PRIORITY = {"unfollowed": 4, "followed": 3, "skipped": 2, "pending_follow": 1}

LIST_PRIORITY_FILE = "list_priority.json"

UNRANKED = float("inf")

# "Following now" and "Up next" are promises the dashboard has already made on screen.
# A list imported afterwards slots in behind both rather than displacing either.
HEAD_SLOTS = 2


class ListOrder(dict):
    """{source_list: declared rank}, plus the anchors that were saved alongside it.

    A dict subclass, so every `priority.get(name, UNRANKED)` call site keeps working
    untouched. The extra attribute is what lets order resolution tell a list the user
    deliberately dragged from one that merely happened to be on screen when some other
    row was dragged — before anchors existed, a single drag saved the whole visible
    order, so every list looked equally deliberate and new imports had nowhere to go
    but the bottom.

    anchors maps a source list to "up" or "down": the direction the user dragged it.
    """

    def __init__(self, ranks=None, anchors=None):
        super().__init__(ranks or {})
        self.anchors: dict[str, str] = dict(anchors or {})


def load_list_priority(data_dir: str = ".") -> ListOrder:
    """Read the manual list order into {source_list: rank}. Missing/corrupt file = no manual order."""
    path = os.path.join(data_dir, LIST_PRIORITY_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ListOrder()
    order = data.get("order") if isinstance(data, dict) else data
    if not isinstance(order, list):
        return ListOrder()
    ranks: dict[str, float] = {}
    for name in order:
        if isinstance(name, str) and name and name not in ranks:
            ranks[name] = float(len(ranks))
    raw_anchors = data.get("anchors") if isinstance(data, dict) else None
    anchors = {}
    if isinstance(raw_anchors, dict):
        for name, direction in raw_anchors.items():
            # A file written before anchors existed has none, which is the point: its
            # order is honoured, but none of it counts as deliberate, so new lists slot
            # in near the top instead of behind every list that was on screen that day.
            if isinstance(name, str) and name in ranks and direction in ("up", "down"):
                anchors[name] = direction
    return ListOrder(ranks, anchors)


def auto_sort_key(name: str, sizes: Counter[str] | dict[str, int]):
    """Automatic order for a list nobody has placed: unfinished first, then smallest."""
    pending = sizes.get(name, 0)
    return (pending == 0, pending, name)


def resolve_list_order(
    priority: dict[str, float] | None,
    sizes: Counter[str] | dict[str, int],
    names,
) -> list[str]:
    """The order these source lists are actually worked in, best first.

    The saved arrangement only has an opinion about the lists that were in it. A list
    imported since then is "fresh", and gets placed rather than appended:

      * fresh lists go in as one block, smallest first, behind the list being followed
        now and the one labelled "up next" — those two are already on screen as what
        happens next, and a new import must not silently take either slot;
      * ...and behind any list the user dragged *up*, since that drag said "sooner".
        A list dragged *down* said "later", so fresh work goes above it, not below;
      * a list with nothing pending sinks to the bottom whatever its saved rank. The bot
        skips it either way, and burying new lists under finished ones is the thing this
        ordering exists to stop.
    """
    names = list(dict.fromkeys(names))
    anchors = getattr(priority, "anchors", None) or {}
    ranked = sorted((n for n in names if priority and n in priority),
                    key=lambda n: priority[n])
    placed = set(ranked)
    fresh = sorted((n for n in names if n not in placed),
                   key=lambda n: auto_sort_key(n, sizes))

    live_ranked = [n for n in ranked if sizes.get(n, 0) > 0]
    done_ranked = [n for n in ranked if sizes.get(n, 0) <= 0]
    live_fresh = [n for n in fresh if sizes.get(n, 0) > 0]
    done_fresh = [n for n in fresh if sizes.get(n, 0) <= 0]

    at = HEAD_SLOTS
    for i, name in enumerate(live_ranked):
        if anchors.get(name) == "up":
            at = max(at, i + 1)
    at = min(at, len(live_ranked))

    return live_ranked[:at] + live_fresh + live_ranked[at:] + done_ranked + done_fresh


def effective_ranks(
    priority: dict[str, float] | None,
    sizes: Counter[str] | dict[str, int],
    names=None,
) -> dict[str, float]:
    """resolve_list_order as the {name: rank} map entry_rank_tuple wants.

    Empty when there is no manual order at all, so the automatic rules below stay in
    charge and nothing about auto mode changes.
    """
    if not priority:
        return {}
    if names is None:
        names = list(priority) + list(sizes)
    return {n: float(i) for i, n in enumerate(resolve_list_order(priority, sizes, names))}


def followers_sort_key(entry: dict[str, Any]) -> float:
    fc = entry.get("followers_count")
    if fc is None or fc == "":
        return float("inf")
    try:
        return float(int(fc))
    except (TypeError, ValueError):
        return float("inf")


def pending_source_sizes(pending_entries: list[dict[str, Any]]) -> Counter[str]:
    return Counter((e.get("source_list") or "unknown") for e in pending_entries)


def entry_rank_tuple(
    entry: dict[str, Any],
    sizes: Counter[str],
    priority: dict[str, float] | None = None,
) -> tuple[float, float, float, str]:
    """Lower tuple = higher priority: manual list rank, then smallest list, then low followers."""
    src = entry.get("source_list") or "unknown"
    prio = (priority or {}).get(src, UNRANKED)
    sz = sizes[src]
    fc = followers_sort_key(entry)
    ul = (entry.get("username") or "").strip().lower()
    return (prio, sz, fc, ul)


def dedupe_best_pending(
    pending_entries: list[dict[str, Any]],
    priority: dict[str, float] | None = None,
):
    """
    Collapse duplicate usernames; sort for follow order.
    Returns (ordered_entries, source_sizes).
    """
    sizes = pending_source_sizes(pending_entries)
    priority = effective_ranks(priority, sizes, sizes.keys())
    best_by_username: dict[str, dict[str, Any]] = {}
    for e in pending_entries:
        ul = (e.get("username") or "").strip().lower()
        if not ul:
            continue
        cur = best_by_username.get(ul)
        if cur is None or entry_rank_tuple(e, sizes, priority) < entry_rank_tuple(cur, sizes, priority):
            best_by_username[ul] = e
    ordered = sorted(best_by_username.values(), key=lambda e: entry_rank_tuple(e, sizes, priority))
    return ordered, sizes


def pick_winner_entries(
    ents: list[dict[str, Any]],
    sizes: Counter[str],
    priority: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Pick one queue row from a group sharing identity keys."""
    best = ents[0]
    for e in ents[1:]:
        sb = STATUS_PRIORITY.get(best.get("status"), 0)
        se = STATUS_PRIORITY.get(e.get("status"), 0)
        if se > sb:
            best = e
        elif se == sb:
            if best.get("status") == "pending_follow" and e.get("status") == "pending_follow":
                if entry_rank_tuple(e, sizes, priority) < entry_rank_tuple(best, sizes, priority):
                    best = e
            # same non-pending status: keep first (deterministic)
    return best


def merge_identity_duplicates(
    queue: list[dict[str, Any]],
    priority: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """
    Merge rows that share any identity key (username / user id).
    Winner: higher STATUS_PRIORITY; if both pending_follow, manual list rank then smallest
    source list then followers.
    """
    if not queue:
        return []

    pending_entries = [e for e in queue if e.get("status") == "pending_follow"]
    sizes = pending_source_sizes(pending_entries)
    priority = effective_ranks(priority, sizes, sizes.keys())

    n = len(queue)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    key_to_first: dict[str, int] = {}
    for i, e in enumerate(queue):
        for k in identity_key_set(e.get("username"), e.get("user_id")):
            if k in key_to_first:
                union(i, key_to_first[k])
            else:
                key_to_first[k] = i

    groups: defaultdict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    resolved: list[dict[str, Any]] = []
    for root in sorted(groups.keys(), key=lambda r: min(groups[r])):
        idxs = sorted(groups[root])
        ents = [queue[i] for i in idxs]
        resolved.append(pick_winner_entries(ents, sizes, priority))

    return resolved
