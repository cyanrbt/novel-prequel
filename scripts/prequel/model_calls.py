from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

from .call_budget import CallBudget, CallReservation
from .model_router import ResolvedModelSettings, StageModelRouter
from .progress import ProgressReporter, ProgressSink
from .run_manifest import RunManifest


class ModelCallExecutor:
    def __init__(
        self,
        router: StageModelRouter,
        manifest: RunManifest,
        progress: ProgressSink | None = None,
    ):
        self.router = router
        self.budget = CallBudget(manifest)
        self.progress = ProgressReporter(progress)

    def reserve_many(
        self, requests: Iterable[tuple[str, str]]
    ) -> list[CallReservation]:
        resolved = [
            (stage, self._settings_for(stage), reason)
            for stage, reason in requests
        ]
        return self.budget.reserve_many(resolved)

    def _settings_for(self, stage: str) -> ResolvedModelSettings:
        resolver = getattr(self.router, "settings_for", None)
        if callable(resolver):
            return resolver(stage)
        provider = self.router.provider_for(stage)
        profile_for = getattr(self.router, "profile_for", None)
        profile = profile_for(stage) if callable(profile_for) else "injected-test-provider"
        return ResolvedModelSettings(
            profile,
            getattr(provider, "model", None) or "injected-test-provider",
            getattr(provider, "reasoning_effort", None) or "none",
        )

    def call(
        self,
        stage: str,
        prompt: str,
        output_schema: Path | None,
        reason_code: str,
    ) -> str:
        reservation = self.budget.reserve(
            stage, self._settings_for(stage), reason_code
        )
        return self.call_reserved(reservation, prompt, output_schema)

    def call_reserved(
        self,
        reservation: CallReservation,
        prompt: str,
        output_schema: Path | None,
    ) -> str:
        started = time.monotonic()
        self.budget.mark_running(reservation)
        with self.budget.manifest._lock:
            record = dict(
                self.budget.manifest.data["budget"]["calls"][reservation.call_id]
            )
        event_fields = {
            "call_id": reservation.call_id,
            "stage": reservation.stage,
            "model": record["model"],
            "reasoning_effort": record["reasoning_effort"],
        }
        self.progress.emit("CALL_STARTED", **event_fields)
        try:
            output = self.router.provider_for(reservation.stage).generate(
                prompt, output_schema
            )
        except BaseException as exc:
            duration_ms = self._elapsed_ms(started)
            self.budget.fail(reservation, duration_ms, exc)
            self.progress.emit(
                "CALL_FAILED",
                **event_fields,
                duration_ms=duration_ms,
                error_code=type(exc).__name__,
            )
            raise
        duration_ms = self._elapsed_ms(started)
        self.budget.complete(reservation, duration_ms, usage=None)
        self.progress.emit(
            "CALL_COMPLETED", **event_fields, duration_ms=duration_ms
        )
        return output

    def artifact_invalid(
        self,
        *,
        stage: str,
        failure_kind: str,
        diagnostic_artifact: str | None,
    ) -> None:
        self.progress.emit(
            "ARTIFACT_INVALID",
            stage=stage,
            failure_kind=failure_kind,
            diagnostic_artifact=diagnostic_artifact,
        )

    def stage_reused(self, stage: str) -> None:
        self.progress.emit("STAGE_REUSED", stage=stage)

    def cancel_before_provider(self, reservation: CallReservation) -> None:
        self.budget.cancel_before_provider(reservation)

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return round((time.monotonic() - started) * 1000)
