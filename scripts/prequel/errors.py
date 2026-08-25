class PrequelError(RuntimeError):
    """Base error rendered as a concise CLI message."""


class StateValidationError(PrequelError):
    """The single source of truth is missing or invalid."""


class ArtifactValidationError(PrequelError):
    """An agent artifact is missing, malformed, or unsafe."""


class QualityGateError(PrequelError):
    """A plan, draft, or semantic review failed a mandatory gate."""


class AtomicWriteError(PrequelError):
    """A formal project artifact could not be committed atomically."""
