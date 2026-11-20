from dataclasses import dataclass


@dataclass(frozen=True)
class UpsertCounts:
    """What an upsert did. inserted + updated + unchanged = rows we tried to write."""

    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
