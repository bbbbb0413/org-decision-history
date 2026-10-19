"""상충 심각도 등급.

등급은 여기서 한 번만 정한다. 상충을 기록할 때 등급을 함께 저장하고(`decision_conflicts.severity_level`),
알림·대시보드·API 는 저장된 등급을 그대로 쓴다. 점수를 등급으로 바꾸는 기준이 여러 곳에 있으면 한쪽만
고쳤을 때 같은 상충이 알림에서는 "치명적", 화면에서는 "높음"으로 보인다.

기록 시점 등급을 저장하므로 나중에 구간을 바꿔도 이미 기록된 상충의 등급은 바뀌지 않는다.
"""
from dataclasses import dataclass
from typing import Literal

SeverityLevel = Literal["CRITICAL", "HIGH", "MEDIUM"]

# 등급별 표시. 대시보드(front GovernanceDashboard)의 배지 문구와 같다
_DISPLAY: dict[str, tuple[str, str]] = {
    "CRITICAL": ("🔴", "치명적"),
    "HIGH": ("🟡", "높음"),
    "MEDIUM": ("🟢", "보통"),
}


@dataclass(frozen=True)
class SeverityBands:
    critical_min: float = 0.85
    high_min: float = 0.70

    def __post_init__(self) -> None:
        if not 0.0 <= self.high_min < self.critical_min <= 1.0:
            raise ValueError(
                f"심각도 구간이 잘못됐다: 0 <= high_min({self.high_min}) < critical_min({self.critical_min}) <= 1"
            )

    def level_of(self, score: float) -> SeverityLevel:
        if score >= self.critical_min:
            return "CRITICAL"
        if score >= self.high_min:
            return "HIGH"
        return "MEDIUM"


def display_label(level: str, score: float) -> str:
    emoji, name = _DISPLAY.get(level, ("⚪", level))
    return f"{emoji} {name} ({score:.2f})"


__all__ = ["SeverityBands", "SeverityLevel", "display_label"]
