"""상태 비교·알림 판단·메시지 포맷 — 블로그 시나리오 그대로 검증."""

from pathlib import Path

from cgv_watcher.config import AlertConfig, TargetConfig
from cgv_watcher.filters import match_showtime, notable_seats, should_alert
from cgv_watcher.models import Change, Seat, Showtime
from cgv_watcher.notify import build_alert_message
from cgv_watcher.state import StateStore


def make_showtime(**kw) -> Showtime:
    base = dict(
        date="20260815",
        start_time="18:00",
        movie="오디세이",
        hall="IMAX LASER",
        screening_id="3",
    )
    base.update(kw)
    return Showtime(**base)


# ---------------------------------------------------------------- 회차 매칭


def test_match_by_movie_hall_and_time_window():
    target = TargetConfig(
        movie_keyword="오디세이", hall_keyword="IMAX", time_from="10:30", time_to="21:00"
    )
    assert match_showtime(make_showtime(), target)
    assert not match_showtime(make_showtime(movie="다른영화"), target)
    assert not match_showtime(make_showtime(hall="2관"), target)
    assert not match_showtime(make_showtime(start_time="09:00"), target)  # 창 이전
    assert not match_showtime(make_showtime(start_time="21:30"), target)  # 창 이후


def test_match_ignores_spaces_and_case():
    target = TargetConfig(hall_keyword="imax")
    assert match_showtime(make_showtime(hall="IMAX LASER 관"), target)


# ------------------------------------------------------- 최초 관측 = 기준값


def test_first_observation_is_baseline_not_alert(tmp_path: Path):
    store = StateStore(tmp_path)
    change = store.observe(make_showtime(), [Seat("E6"), Seat("E7")])
    assert change.first_seen
    assert change.cur_remaining == 2
    # 기본 정책: 새 회차 발견은 알리되, alert_on_new_showtime=False면 침묵
    assert not should_alert(change, AlertConfig(alert_on_new_showtime=False))
    assert should_alert(change, AlertConfig(alert_on_new_showtime=True))


# ----------------------------------------------------- 증가했을 때만 알림


def test_alert_only_on_increase(tmp_path: Path):
    store = StateStore(tmp_path)
    alert = AlertConfig(alert_on_new_showtime=False)
    st = make_showtime()

    store.observe(st, [Seat("E6"), Seat("E7")])  # 기준값 2석

    # 같음 → 침묵
    same = store.observe(st, [Seat("E6"), Seat("E7")])
    assert not should_alert(same, alert)

    # 감소 → 침묵
    fewer = store.observe(st, [Seat("E6")])
    assert not should_alert(fewer, alert)

    # 증가 → 알림, 새 좌석 식별
    more = store.observe(st, [Seat("E6"), Seat("E7"), Seat("H9")])
    more.notable_seats = notable_seats(more.new_seats, alert)
    assert should_alert(more, alert)
    # 직전(1석: E6) 대비 새로 풀린 좌석
    assert set(more.new_seats) == {"E7", "H9"}


# --------------------------------------------- A~C열 오탐 필터 (블로그 교훈)


def test_front_row_only_increase_is_silent(tmp_path: Path):
    store = StateStore(tmp_path)
    alert = AlertConfig(ignore_rows=["A", "B", "C"], alert_on_new_showtime=False)
    st = make_showtime()

    store.observe(st, [Seat("E6")])
    change = store.observe(st, [Seat("E6"), Seat("A3"), Seat("B7")])  # 앞열만 풀림
    change.notable_seats = notable_seats(change.new_seats, alert)
    assert change.increased
    assert change.notable_seats == []
    assert not should_alert(change, alert)  # 기술적으론 증가지만 사용자에겐 오탐


def test_mixed_rows_alert_shows_only_wanted_seats(tmp_path: Path):
    store = StateStore(tmp_path)
    alert = AlertConfig(ignore_rows=["A", "B", "C"], alert_on_new_showtime=False)
    st = make_showtime()

    store.observe(st, [Seat("E6")])
    change = store.observe(st, [Seat("E6"), Seat("A3"), Seat("D12")])
    change.notable_seats = notable_seats(change.new_seats, alert)
    assert should_alert(change, alert)
    assert change.notable_seats == ["D12"]  # 원하는 좌석만 표시
    # 전체 증가량은 실제 값 그대로 (1 → 3, +2)
    msg = build_alert_message([change], "CGV 대구 IMAX ‘오디세이’")
    assert "1석 → 3석 (+2석)" in msg
    assert "D12" in msg and "A3" not in msg


# ------------------------------------------- 좌석 상세 없이 좌석 수만 있을 때


def test_count_only_mode(tmp_path: Path):
    store = StateStore(tmp_path)
    st = make_showtime(remaining=2)

    store.observe(st, None)
    change = store.observe(make_showtime(remaining=3), None)
    assert change.increased
    assert should_alert(change, AlertConfig(alert_on_count_only=True))
    assert not should_alert(change, AlertConfig(alert_on_count_only=False))


# ----------------------------------------------------------- 메시지 포맷


def test_message_format_matches_blog():
    change = Change(
        showtime=make_showtime(),
        prev_remaining=2,
        cur_remaining=3,
        new_seats=["E6"],
        notable_seats=["E6"],
    )
    msg = build_alert_message([change], "CGV 대구 IMAX ‘오디세이’")
    assert "CGV 대구 IMAX ‘오디세이’ 빈자리 증가" in msg
    assert "- 8월 15일(토) 18:00" in msg
    assert "  - 2석 → 3석 (+1석)" in msg
    assert "  - 새 좌석: E6" in msg


def test_state_persists_between_runs(tmp_path: Path):
    st = make_showtime()
    store1 = StateStore(tmp_path)
    store1.observe(st, [Seat("E6")])
    store1.save()

    store2 = StateStore(tmp_path)  # 새 프로세스 흉내
    change = store2.observe(st, [Seat("E6"), Seat("E7")])
    assert not change.first_seen
    assert change.prev_remaining == 1
    assert change.new_seats == ["E7"]


def test_prune_removes_stale_showtimes(tmp_path: Path):
    store = StateStore(tmp_path)
    old = make_showtime(date="20260814")
    cur = make_showtime(date="20260815")
    store.observe(old, None)
    store.observe(cur, None)
    store.prune({cur.key})
    store.save()

    store2 = StateStore(tmp_path)
    assert store2.observe(cur, None).first_seen is False
    assert store2.observe(old, None).first_seen is True  # 정리돼서 다시 최초 관측
