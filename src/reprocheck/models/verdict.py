"""Typed reproducibility verdict.

The verdict is a single label derived from objective rules, never a score:

``PASS``
    A reproduction ran and every executed step succeeded, with no open warning.
``PARTIAL``
    The reproduction succeeded as far as it went, but something important was
    left unverified or a warning is still open.
``FAIL``
    A failure of the reproduction itself was observed.
``NOT_ATTEMPTED``
    No reproduction was requested (``reprocheck scan`` only).

The model also carries what is *unknown* (``not_verified``), which is kept
separate from what *failed* on purpose: a step that was never executed is not a
result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VerdictStatus(StrEnum):
    """The four objective verdicts. There is no numeric score."""

    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"

    def to_dict(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class UnverifiedItem:
    """Something ReproCheck did not check, with the reason why."""

    item: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"item": self.item, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class ReproducibilityVerdict:
    """Overall verdict plus the reasons and the unknowns behind it."""

    status: VerdictStatus = VerdictStatus.NOT_ATTEMPTED
    reasons: tuple[str, ...] = ()
    not_verified: tuple[UnverifiedItem, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status is VerdictStatus.PASS

    @property
    def failed(self) -> bool:
        return self.status is VerdictStatus.FAIL

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reasons": list(self.reasons),
            "not_verified": [item.to_dict() for item in self.not_verified],
        }
