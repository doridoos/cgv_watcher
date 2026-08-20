"""예매 오픈 알림(용아맥 오픈 알리미 스타일) 시나리오.

- 콜드스타트: 기존 회차는 '오픈'으로 알리지 않고 요약 1건만
- 이후 새 회차가 나타나면 🆕 오픈 알림
- 오픈 전(회차 0개) 시작 → "오픈되면 알려드립니다" 요약
"""

from pathlib import Path

from cgv_watcher.config import (
    AlertConfig,
    Config,
    EndpointConfig,
    PollConfig,
    TargetConfig,
    TelegramConfig,
)
from cgv_watcher.models import Showtime
from cgv_watcher.watcher import run_once


class FakeClient:
    def __init__(self, shows_by_date):
        self.shows_by_date = shows_by_date

    def fetch_showtimes(self, date):
        return self.shows_by_date.get(date, [])


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


def make_config(tmp_path: Path) -> Config:
    return Config(
        target=TargetConfig(
            theater_name="용산",
            movie_keyword="오디세이",
            hall_keyword="IMAX",
            date_from="20260815",
            date_to="20260816",
        ),
        alert=AlertConfig(),
        telegram=TelegramConfig(),
        poll=PollConfig(),
        endpoints={"showtimes": EndpointConfig(url="http://fake")},
        state_dir=tmp_path,
    )


def imax_show(date="20260815", time="18:00", movie="오디세이"):
    return Showtime(
        date=date, start_time=time, movie=movie, hall="IMAX LASER",
        screening_id="1", remaining=100,
    )


def test_cold_start_summarizes_instead_of_open_alerts(tmp_path):
    cfg = make_config(tmp_path)
    client = FakeClient({"20260815": [imax_show()]})
    n = RecordingNotifier()

    run_once(cfg, client=client, notifier=n)

    assert len(n.sent) == 1
    assert "감시 시작" in n.sent[0]
    assert "회차 1개" in n.sent[0]
    assert "새 회차 발견" not in n.sent[0]  # 기존 회차를 오픈으로 오인하지 않음


def test_cold_start_before_open_says_waiting(tmp_path):
    cfg = make_config(tmp_path)
    n = RecordingNotifier()

    run_once(cfg, client=FakeClient({}), notifier=n)  # 아직 아무 회차도 없음

    assert len(n.sent) == 1
    assert "아직 없습니다" in n.sent[0]
    assert "오픈되면" in n.sent[0]


def test_new_showtime_after_baseline_triggers_open_alert(tmp_path):
    cfg = make_config(tmp_path)
    n = RecordingNotifier()

    # 1일차 회차만 있는 상태로 기준값 저장
    run_once(cfg, client=FakeClient({"20260815": [imax_show()]}), notifier=n)
    n.sent.clear()

    # 다음 사이클: 변화 없음 → 침묵
    run_once(cfg, client=FakeClient({"20260815": [imax_show()]}), notifier=n)
    assert n.sent == []

    # 2일차 회차가 새로 열림 → 오픈 알림
    run_once(
        cfg,
        client=FakeClient(
            {"20260815": [imax_show()], "20260816": [imax_show(date="20260816")]}
        ),
        notifier=n,
    )
    assert len(n.sent) == 1
    assert "새 회차 발견" in n.sent[0]
    assert "8월 16일" in n.sent[0]
    assert "8월 15일" not in n.sent[0]  # 기존 회차는 다시 알리지 않음


def test_startup_summary_can_be_disabled(tmp_path):
    cfg = make_config(tmp_path)
    cfg.alert.startup_summary = False
    n = RecordingNotifier()

    run_once(cfg, client=FakeClient({"20260815": [imax_show()]}), notifier=n)

    assert n.sent == []
