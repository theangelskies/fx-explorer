from dataclasses import dataclass


@dataclass(frozen=True)
class UpsertCounts:
    """What an upsert did. inserted + updated + unchanged = rows we tried to write."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0


@dataclass(frozen=True)
class KeysetPage[T]:
    """One page of a keyset-paginated query (see docs/decisions/0003-cursor-pagination.md).

    The repository asks the database for limit + 1 rows. If that extra row comes back there
    is at least one more page (has_more=True), and the extra row is dropped, so `items` never
    holds more than `limit` rows. The caller builds the next cursor from items[-1].
    """

    items: list[T]
    has_more: bool

    @classmethod
    def from_rows(cls, rows: list[T], limit: int) -> "KeysetPage[T]":
        return cls(items=rows[:limit], has_more=len(rows) > limit)
