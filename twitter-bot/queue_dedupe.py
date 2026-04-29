"""
Shared queue rules for pending_follow:

1. Duplicate usernames (same handle): keep one row — prefer the row whose source_list has the
   smallest pending count (smallest list wins), then lowest followers_count.
2. Follow order: same tuple — smallest lists first, then lowest follower count within ties.

Non-pending rows are unchanged by dedupe_best_pending; merge_identity_duplicates handles
identity-key collisions across the full queue for audit --fix.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from twitter_csv import identity_key_set

STATUS_PRIORITY = {"unfollowed": 4, "followed": 3, "skipped": 2, "pending_follow": 1}


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


def entry_rank_tuple(entry: dict[str, Any], sizes: Counter[str]) -> tuple[float, float, str]:
    """Lower tuple = higher priority for smallest-list-first + low-follower ordering."""
    src = entry.get("source_list") or "unknown"
    sz = sizes[src]
    fc = followers_sort_key(entry)
    ul = (entry.get("username") or "").strip().lower()
    return (sz, fc, ul)


def dedupe_best_pending(pending_entries: list[dict[str, Any]]):
    """
    Collapse duplicate usernames; sort for follow order.
    Returns (ordered_entries, source_sizes).
    """
    sizes = pending_source_sizes(pending_entries)
    best_by_username: dict[str, dict[str, Any]] = {}
    for e in pending_entries:
        ul = (e.get("username") or "").strip().lower()
        if not ul:
            continue
        cur = best_by_username.get(ul)
        if cur is None or entry_rank_tuple(e, sizes) < entry_rank_tuple(cur, sizes):
            best_by_username[ul] = e
    ordered = sorted(best_by_username.values(), key=lambda e: entry_rank_tuple(e, sizes))
    return ordered, sizes


def pick_winner_entries(ents: list[dict[str, Any]], sizes: Counter[str]) -> dict[str, Any]:
    """Pick one queue row from a group sharing identity keys."""
    best = ents[0]
    for e in ents[1:]:
        sb = STATUS_PRIORITY.get(best.get("status"), 0)
        se = STATUS_PRIORITY.get(e.get("status"), 0)
        if se > sb:
            best = e
        elif se == sb:
            if best.get("status") == "pending_follow" and e.get("status") == "pending_follow":
                if entry_rank_tuple(e, sizes) < entry_rank_tuple(best, sizes):
                    best = e
            # same non-pending status: keep first (deterministic)
    return best


def merge_identity_duplicates(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Merge rows that share any identity key (username / user id).
    Winner: higher STATUS_PRIORITY; if both pending_follow, smallest source list then followers.
    """
    if not queue:
        return []

    pending_entries = [e for e in queue if e.get("status") == "pending_follow"]
    sizes = pending_source_sizes(pending_entries)

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
        resolved.append(pick_winner_entries(ents, sizes))

    return resolved
