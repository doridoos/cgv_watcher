"""참고 저장소(DongminL) 설계를 반영한 부분들의 검증.

- 세션 URL의 날짜 파라미터 교체 (하이브리드 폴링의 핵심)
- tcscnsGradCd 기반 IMAX 필터
- 알림 메시지의 예매 링크
"""

import time

from cgv_watcher.browser_fetch import CapturedSession
from cgv_watcher.cgv_api import CgvClient
from cgv_watcher.config import TargetConfig
from cgv_watcher.filters import match_showtime
from cgv_watcher.models import Change, Showtime
from cgv_watcher.notify import build_alert_message
from cgv_watcher.parsers import extract_showtimes_auto


def make_session(url: str, date: str = "20260815") -> CapturedSession:
    return CapturedSession(api_url=url, headers={}, date=date)


# ------------------------------------------------- 세션 URL 날짜 교체


def test_session_url_same_date_reused_as_is():
    s = make_session("https://cgv.co.kr/x/searchMovScnInfo?siteNo=0013&scnYmd=20260815")
    assert CgvClient._session_url_for_date(s, "20260815") == s.api_url


def test_session_url_date_param_replaced():
    s = make_session("https://cgv.co.kr/x/searchMovScnInfo?siteNo=0013&scnYmd=20260815")
    url = CgvClient._session_url_for_date(s, "20260816")
    assert "scnYmd=20260816" in url
    assert "siteNo=0013" in url


def test_session_url_without_date_param_is_unusable_for_other_dates():
    s = make_session("https://cgv.co.kr/x/searchMovScnInfo?siteNo=0013")
    assert CgvClient._session_url_for_date(s, "20260816") is None


def test_session_ttl():
    s = make_session("https://x/?scnYmd=20260815")
    assert s.age_sec() < 1
    s.captured_at = time.time() - 21 * 60
    assert s.age_sec() > 20 * 60


# ------------------------------------------------- 등급코드 필터


def test_grade_code_filter():
    imax = Showtime(
        date="20260815", start_time="18:00", movie="오디세이",
        hall="IMAX LASER", grade="03",
    )
    normal = Showtime(
        date="20260815", start_time="18:00", movie="오디세이", hall="2관", grade="00",
    )
    target = TargetConfig(grade_code="03")
    assert match_showtime(imax, target)
    assert not match_showtime(normal, target)


def test_grade_extracted_from_cgv_response():
    data = {
        "data": [
            {
                "scnYmd": "20260815", "scnsNo": "0136", "scnSseq": "3",
                "movNm": "오디세이", "scnsrtTm": "1800", "scnendTm": "2049",
                "frSeatCnt": 2, "cpSeatCnt": 241, "tcscnsGradCd": "03",
                "scnsEnm": "IMAX LASER",
            }
        ]
    }
    shows = extract_showtimes_auto(data, date_hint="20260815")
    assert shows[0].grade == "03"


# ------------------------------------------------- 예매 링크


def test_booking_link_appended_to_message():
    change = Change(
        showtime=Showtime(date="20260815", start_time="18:00"),
        prev_remaining=2,
        cur_remaining=3,
    )
    msg = build_alert_message(
        [change], "CGV 용산 IMAX",
        booking_url="https://cgv.co.kr/cnm/movieBook/cinema?siteNo=0013&scnYmd=20260815",
    )
    assert msg.endswith("예매: https://cgv.co.kr/cnm/movieBook/cinema?siteNo=0013&scnYmd=20260815")
