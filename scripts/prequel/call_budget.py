from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .errors import ArtifactValidationError, CallBudgetExceeded
from .model_router import ResolvedModelSettings
from .run_manifest import RunManifest, utc_now


SPENT_STATUSES = {"COMPLETED", "FAILED"}


@dataclass(frozen=True)
class CallReservation:
    call_id: str
    stage: str


class CallBudget:
    def __init__(self, manifest: RunManifest):
        self.manifest = manifest
        if "budget" not in manifest.data:
            raise ArtifactValidationError("旧运行清单没有新版调用预算")

    @staticmethod
    def _recompute(budget: dict[str, Any]) -> None:
        spent = sum(
            1
            for record in budget["calls"].values()
            if record.get("status") in SPENT_STATUSES
        )
        budget["spent"] = spent
        budget["remaining"] = max(
            0, budget["limit"] - spent - len(budget["active"])
        )

    @property
    def remaining(self) -> int:
        with self.manifest._lock:
            return int(self.manifest.data["budget"]["remaining"])

    def reserve(
        self,
        stage: str,
        settings: ResolvedModelSettings,
        reason_code: str,
    ) -> CallReservation:
        return self.reserve_many([(stage, settings, reason_code)])[0]

    def reserve_many(
        self,
        requests: Iterable[tuple[str, ResolvedModelSettings, str]],
    ) -> list[CallReservation]:
        requested = list(requests)
        if not requested:
            return []

        def update(data: dict[str, Any]) -> list[CallReservation]:
            budget = data["budget"]
            self._recompute(budget)
            if budget["remaining"] < len(requested):
                raise CallBudgetExceeded(
                    f"调用预算不足：剩余{budget['remaining']}，需要{len(requested)}"
                )
            reservations: list[CallReservation] = []
            for stage, settings, reason_code in requested:
                number = budget["next_call_id"]
                budget["next_call_id"] += 1
                call_id = f"call_{number:03d}"
                budget["calls"][call_id] = {
                    "call_id": call_id,
                    "stage": stage,
                    "reason_code": reason_code,
                    "profile": settings.profile,
                    "model": settings.model,
                    "reasoning_effort": settings.reasoning_effort,
                    "reserved_at": utc_now(),
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": None,
                    "status": "RESERVED",
                    "error_code": None,
                    "error_summary": None,
                    "usage": None,
                }
                budget["active"].append(call_id)
                reservations.append(CallReservation(call_id, stage))
            self._recompute(budget)
            return reservations

        return self.manifest.mutate(update)

    def mark_running(self, reservation: CallReservation) -> None:
        def update(data: dict[str, Any]) -> None:
            record = self._record(data, reservation)
            if record["status"] != "RESERVED":
                raise ArtifactValidationError("只有RESERVED调用可以启动")
            record["status"] = "RUNNING"
            record["started_at"] = utc_now()

        self.manifest.mutate(update)

    def complete(
        self,
        reservation: CallReservation,
        duration_ms: int,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._settle(reservation, "COMPLETED", duration_ms, usage=usage)

    def fail(
        self,
        reservation: CallReservation,
        duration_ms: int,
        error: BaseException,
    ) -> None:
        self._settle(
            reservation,
            "FAILED",
            duration_ms,
            error_code=type(error).__name__,
            error_summary=str(error)[-1000:],
        )

    def cancel_before_provider(self, reservation: CallReservation) -> None:
        def update(data: dict[str, Any]) -> None:
            budget = data["budget"]
            record = self._record(data, reservation)
            if record["status"] != "RESERVED":
                raise ArtifactValidationError("只能释放尚未启动的调用预留")
            record["status"] = "CANCELLED"
            record["finished_at"] = utc_now()
            budget["active"].remove(reservation.call_id)
            self._recompute(budget)

        self.manifest.mutate(update)

    def recover_interrupted(self) -> None:
        def update(data: dict[str, Any]) -> None:
            budget = data["budget"]
            for call_id in list(budget["active"]):
                record = budget["calls"][call_id]
                record.update(
                    {
                        "status": "FAILED",
                        "finished_at": utc_now(),
                        "error_code": "INTERRUPTED",
                        "error_summary": "上次进程结束时调用仍处于活动状态",
                    }
                )
            budget["active"] = []
            self._recompute(budget)

        self.manifest.mutate(update)

    def _settle(
        self,
        reservation: CallReservation,
        status: str,
        duration_ms: int,
        *,
        usage: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> None:
        def update(data: dict[str, Any]) -> None:
            budget = data["budget"]
            record = self._record(data, reservation)
            if record["status"] not in {"RESERVED", "RUNNING"}:
                raise ArtifactValidationError("调用已经结算")
            record.update(
                {
                    "status": status,
                    "finished_at": utc_now(),
                    "duration_ms": max(0, int(duration_ms)),
                    "usage": usage,
                    "error_code": error_code,
                    "error_summary": error_summary,
                }
            )
            if reservation.call_id in budget["active"]:
                budget["active"].remove(reservation.call_id)
            self._recompute(budget)

        self.manifest.mutate(update)

    @staticmethod
    def _record(
        data: dict[str, Any], reservation: CallReservation
    ) -> dict[str, Any]:
        try:
            record = data["budget"]["calls"][reservation.call_id]
        except KeyError as exc:
            raise ArtifactValidationError("调用预留不存在") from exc
        if record.get("stage") != reservation.stage:
            raise ArtifactValidationError("调用预留阶段不匹配")
        return record
