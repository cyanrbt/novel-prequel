from __future__ import annotations


def due_audits(
    last_chapter: int, health_interval: int = 10, arc_interval: int = 20
) -> dict[str, bool]:
    """Return deterministic audit due flags without executing an Agent."""
    return {
        "health": last_chapter > 0 and last_chapter % health_interval == 0,
        "arc": last_chapter > 0 and last_chapter % arc_interval == 0,
    }
