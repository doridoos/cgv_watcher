"""회차 매칭과 좌석 오탐 필터.

블로그의 교훈 그대로:
- "빈자리 증가"가 기술적으로 맞아도 A~C열만 풀린 거라면 사용자에겐 오탐이다.
- 필터는 알림 여부만 바꾸고, 전체 증가량 숫자는 그대로 보여준다.
"""

from __future__ import annotations

import re

from .config import AlertConfig, TargetConfig
from .models import Change, Seat, Showtime


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def match_showtime(st: Showtime, target: TargetConfig) -> bool:
    """감시 대상 조건(영화/상영관/시작시간 창)에 맞는 회차인가."""
    if target.movie_keyword and _norm(target.movie_keyword) not in _norm(st.movie):
        return False
    if target.hall_keyword and _norm(target.hall_keyword) not in _norm(st.hall):
        return False
    # 등급코드가 설정돼 있으면 정확 일치 요구 (이름 매칭보다 견고. IMAX='03')
    if target.grade_code and st.grade != target.grade_code:
        return False
    if st.start_time:
        if not (target.time_from <= st.start_time <= target.time_to):
            return False
    return True


def seat_row(seat_id: str) -> str:
    m = re.match(r"^([A-Za-z]+)", seat_id.strip())
    return m.group(1).upper() if m else ""


def notable_seats(new_seat_ids: list[str], alert: AlertConfig) -> list[str]:
    """새로 풀린 좌석 중 사용자가 실제로 원하는(무시 열이 아닌) 좌석만."""
    return [s for s in new_seat_ids if seat_row(s) not in alert.ignore_rows]


def should_alert(change: Change, alert: AlertConfig) -> bool:
    """이 변화를 알릴 것인가.

    - 최초 관측: 기준값 저장만. 단 alert_on_new_showtime이면 '회차 발견' 알림.
    - 좌석 상세가 있으면: 무시 열을 제외하고도 새 좌석이 남아야 알림.
    - 좌석 상세가 없으면: alert_on_count_only 설정에 따름.
    """
    if change.first_seen:
        return alert.alert_on_new_showtime
    if not change.increased:
        return False
    if change.new_seats:
        return bool(change.notable_seats)
    return alert.alert_on_count_only
