"""직전 관측값 저장과 변화 계산.

상태 파일이 있어야 '취소표(빈자리 증가)'를 판단할 수 있다.
최초 관측은 기준값으로만 저장한다 — 기존 빈자리를 취소표로 오해하지 않기 위해.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Change, Seat, Showtime

STATE_VERSION = 1


class StateStore:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "state.json"
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # 손상된 상태 파일은 백업해 두고 새로 시작
                self.path.rename(self.path.with_suffix(".corrupt.json"))
        return {"version": STATE_VERSION, "showtimes": {}}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)  # 원자적 교체: 저장 중 죽어도 상태 파일이 깨지지 않게

    # ------------------------------------------------------------------

    def observe(
        self, showtime: Showtime, available_seats: Optional[list[Seat]]
    ) -> Change:
        """새 관측을 기록하고 직전 대비 변화를 돌려준다."""
        entry = self._data["showtimes"].get(showtime.key)
        cur_seat_ids = (
            sorted(s.seat_id for s in available_seats) if available_seats is not None else None
        )
        cur_remaining = (
            len(cur_seat_ids) if cur_seat_ids is not None else showtime.remaining
        )

        if entry is None:
            change = Change(
                showtime=showtime,
                prev_remaining=None,
                cur_remaining=cur_remaining,
                first_seen=True,
            )
        else:
            prev_seats = entry.get("available_seats")
            new_seats: list[str] = []
            if cur_seat_ids is not None and prev_seats is not None:
                new_seats = sorted(set(cur_seat_ids) - set(prev_seats))
            change = Change(
                showtime=showtime,
                prev_remaining=entry.get("remaining"),
                cur_remaining=cur_remaining,
                new_seats=new_seats,
            )

        self._data["showtimes"][showtime.key] = {
            "date": showtime.date,
            "start_time": showtime.start_time,
            "movie": showtime.movie,
            "hall": showtime.hall,
            "remaining": cur_remaining,
            "available_seats": cur_seat_ids,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return change

    def prune(self, keep_keys: set[str]) -> None:
        """이번 조회에서 사라진(지난 날짜 등) 회차 기록 정리."""
        gone = [k for k in self._data["showtimes"] if k not in keep_keys]
        for k in gone:
            del self._data["showtimes"][k]
