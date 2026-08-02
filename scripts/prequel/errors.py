class PrequelError(RuntimeError):
    """Base error rendered as a concise CLI message."""


class StateValidationError(PrequelError):
    """The single source of truth is missing or invalid."""


class ArtifactValidationError(PrequelError):
    """An agent artifact is missing, malformed, or unsafe."""


class ProviderError(PrequelError):
    """The configured model provider could not return usable content."""


class CallBudgetExceeded(PrequelError):
    """A model call was blocked before provider start by the chapter budget."""


class LegacyRunNotResumable(PrequelError):
    """A pre-budget REPLAN workspace can only be inspected."""


class QualityGateError(PrequelError):
    """A plan, draft, or semantic review failed a mandatory gate."""


class AtomicWriteError(PrequelError):
    """A formal project artifact could not be committed atomically."""
