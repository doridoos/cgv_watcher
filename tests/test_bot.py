"""텔레그램 봇: 입력 파서, 오버라이드 저장/병합, 권한 검사."""

from pathlib import Path

import yaml

from cgv_watcher.config import load_config
from cgv_watcher.telegram_bot import (
    Bot,
    load_overrides,
    parse_date_range,
    parse_theater,
    parse_time_range,
    save_override,
)


# ---------------------------------------------------------------- 파서


def test_parse_date_range():
    assert parse_date_range("20260815-20260817") == {
        "date_from": "20260815", "date_to": "20260817",
    }
    assert parse_date_range("20260815 ~ 20260817")["date_to"] == "20260817"
    assert parse_date_range("20260815") == {
        "date_from": "20260815", "date_to": "20260815",
    }
    assert parse_date_range("내일") is None
    assert parse_date_range("2026-08-15") is None


def test_parse_time_range():
    assert parse_time_range("10:30-21:00") == {"time_from": "10:30", "time_to": "21:00"}
    assert parse_time_range("9:00 ~ 23:30") == {"time_from": "09:00", "time_to": "23:30"}
    assert parse_time_range("아무때나") is None


def test_parse_theater():
    assert parse_theater("0013") == {"theater_code": "0013"}
    assert parse_theater("0013 용산") == {
        "theater_code": "0013", "theater_name": "용산",
    }
    assert parse_theater("용산") is None


# ------------------------------------------------- 오버라이드 저장 + 병합


def test_save_and_load_overrides(tmp_path: Path):
    ov = tmp_path / "overrides.yaml"
    save_override(ov, "target", {"movie_keyword": "오디세이"})
    save_override(ov, "target", {"days": 14})
    save_override(ov, "watch", {"enabled": True})

    data = load_overrides(ov)
    assert data["target"] == {"movie_keyword": "오디세이", "days": 14}
    assert data["watch"] == {"enabled": True}


def test_overrides_merge_into_config(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "target": {"movie_keyword": "원래영화", "hall_keyword": "IMAX"},
                "endpoints": {"showtimes": {"url": "http://x"}},
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()
    save_override(state / "overrides.yaml", "target", {"movie_keyword": "오디세이"})

    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.target.movie_keyword == "오디세이"  # 봇이 바꾼 값이 우선
    assert cfg.target.hall_keyword == "IMAX"  # 안 바꾼 값은 원본 유지


# ---------------------------------------------------------------- 권한


def make_bot(tmp_path: Path) -> Bot:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "telegram": {"bot_token": "TESTTOKEN", "chat_id": "111"},
                "endpoints": {"showtimes": {"url": "http://x"}},
                "state_dir": str(tmp_path / "state"),
            }
        ),
        encoding="utf-8",
    )
    return Bot(str(tmp_path / "config.yaml"))


def test_bot_ignores_other_chats(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    calls = []
    monkeypatch.setattr(bot, "api", lambda method, **p: calls.append((method, p)))

    bot.handle_update({"message": {"chat": {"id": 999}, "text": "/start"}})
    assert calls == []  # 남의 채팅은 완전 무시

    bot.handle_update({"message": {"chat": {"id": 111}, "text": "/start"}})
    assert calls and calls[0][0] == "sendMessage"


def test_bot_menu_flow_sets_override(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    monkeypatch.setattr(bot, "api", lambda method, **p: {"ok": True})

    # '영화 → 직접 입력' 버튼 후 텍스트 입력
    bot.handle_update(
        {"callback_query": {"id": "1", "data": "movie_custom",
                            "message": {"chat": {"id": 111}}}}
    )
    assert bot.pending == "movie"
    bot.handle_update({"message": {"chat": {"id": 111}, "text": "오디세이"}})
    assert bot.pending is None

    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.target.movie_keyword == "오디세이"


def test_watch_toggle(tmp_path, monkeypatch):
    bot = make_bot(tmp_path)
    monkeypatch.setattr(bot, "api", lambda method, **p: {"ok": True})

    assert not bot._watch_enabled()
    bot.handle_update(
        {"callback_query": {"id": "1", "data": "watch_on",
                            "message": {"chat": {"id": 111}}}}
    )
    assert bot._watch_enabled()
    bot.handle_update(
        {"callback_query": {"id": "2", "data": "watch_off",
                            "message": {"chat": {"id": 111}}}}
    )
    assert not bot._watch_enabled()
