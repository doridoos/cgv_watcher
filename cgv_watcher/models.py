"""감시기에서 다루는 데이터 모델."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Showtime:
    """하나의 상영 회차."""

    date: str  # YYYYMMDD
    start_time: str  # HH:MM
    movie: str = ""
    hall: str = ""  # 상영관 이름 (예: "IMAX관", "IMAX LASER")
    theater: str = ""  # 극장 이름 (예: "대구")
    screening_id: str = ""  # 회차 식별자 (좌석 조회에 필요)
    remaining: Optional[int] = None  # 예매 가능 좌석 수
    total: Optional[int] = None  # 전체 좌석 수
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        """상태 파일에서 회차를 구분하는 키.

        시작시간을 항상 포함한다 — CGV의 scnsNo는 '상영관' 번호라서
        같은 관의 다른 회차끼리 충돌할 수 있기 때문.
        """
        tail = self.screening_id or f"{self.hall}:{self.movie}"
        return f"{self.date}:{self.start_time}:{tail}"


_SEAT_RE = re.compile(r"^([A-Za-z]+)\s*-?\s*(\d+)$")


@dataclass
class Seat:
    """좌석 하나. seat_id는 'E6' 같은 형태."""

    seat_id: str
    available: bool = True

    @property
    def row(self) -> str:
        m = _SEAT_RE.match(self.seat_id.strip())
        return m.group(1).upper() if m else ""

    @property
    def number(self) -> int:
        m = _SEAT_RE.match(self.seat_id.strip())
        return int(m.group(2)) if m else 0


@dataclass
class Change:
    """직전 관측 대비 변화. 알림 여부 판단의 입력."""

    showtime: Showtime
    prev_remaining: Optional[int]
    cur_remaining: Optional[int]
    new_seats: list[str] = field(default_factory=list)  # 새로 풀린 좌석 전체
    notable_seats: list[str] = field(default_factory=list)  # 필터를 통과한(사용자가 원하는) 좌석
    first_seen: bool = False

    @property
    def increased(self) -> bool:
        if self.prev_remaining is None or self.cur_remaining is None:
            return False
        return self.cur_remaining > self.prev_remaining
