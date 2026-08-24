from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ModelProvider(Protocol):
    """In-process test/embedding boundary; the repository ships no CLI provider.

    Production creative work is prompt-native and follows ``WORKFLOW.md`` task
    envelopes.  The protocol remains only so deterministic pipeline logic can be
    exercised with in-memory fakes or embedded by a host that supplies results
    without spawning an Agent command.
    """

    def generate(self, prompt: str, output_schema: Path | None = None) -> str: ...
