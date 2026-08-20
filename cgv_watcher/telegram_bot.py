"""텔레그램 대화형 설정/제어 봇 (버튼 메뉴 UX).

config.yaml을 손으로 고치지 않고, 텔레그램에서 버튼으로 감시 대상(영화/극장/
날짜/시간대/앞열 필터)을 바꾸고 감시를 켜고 끌 수 있다.

- 봇이 바꾼 값은 state/overrides.yaml에 저장되어 config.yaml 위에 병합된다
  (원본 config.yaml의 주석은 그대로 보존).
- 감시 루프는 백그라운드 스레드로 같은 프로세스에서 돌고, 매 사이클 설정을
  다시 읽으므로 버튼으로 바꾼 값이 다음 조회부터 바로 적용된다.
- 설정된 chat_id 외의 사용자는 무시한다 (봇 주소가 알려져도 조작 불가).

실행:  python -m cgv_watcher bot
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

from .browser_fetch import BlockedError
from .config import Config, ConfigError, load_config
from .notify import Notifier
from .scheduler import current_interval, set_burst
from .watcher import run_once

log = logging.getLogger("cgv_watcher.bot")

_API = "https://api.telegram.org/bot{token}/{method}"


# ---------------------------------------------------------------- 입력 파서
# (버튼으로 고를 수 없는 값은 텍스트로 받는다. 테스트 가능하게 순수 함수로.)

_DATE_RE = re.compile(r"^(\d{8})(?:\s*[-~]\s*(\d{8}))?$")
_TIME_RE = re.compile(r"^(\d{1,2}:\d{2})\s*[-~]\s*(\d{1,2}:\d{2})$")
_THEATER_RE = re.compile(r"^(\d{4})(?:\s+(.+))?$")


def parse_date_range(text: str) -> Optional[dict]:
    """'20260815-20260817' 또는 '20260815' → target 오버라이드."""
    m = _DATE_RE.match(text.strip())
    if not m:
        return None
    return {"date_from": m.group(1), "date_to": m.group(2) or m.group(1)}


def parse_time_range(text: str) -> Optional[dict]:
    """'10:30-21:00' → target 오버라이드."""
    m = _TIME_RE.match(text.strip())
    if not m:
        return None

    def norm(t: str) -> str:
        h, mi = t.split(":")
        return f"{int(h):02d}:{mi}"

    return {"time_from": norm(m.group(1)), "time_to": norm(m.group(2))}


def parse_theater(text: str) -> Optional[dict]:
    """'0013' 또는 '0013 용산' → target 오버라이드."""
    m = _THEATER_RE.match(text.strip())
    if not m:
        return None
    out = {"theater_code": m.group(1)}
    if m.group(2):
        out["theater_name"] = m.group(2).strip()
    return out


# ------------------------------------------------------------ 오버라이드 저장


def load_overrides(path: Path) -> dict:
    if path.exists():
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            pass
    return {}


def save_override(path: Path, section: str, values: dict) -> None:
    data = load_overrides(path)
    data.setdefault(section, {}).update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


# ---------------------------------------------------------------- 메뉴 정의

MAIN_MENU = [
    [{"text": "📊 상태", "callback_data": "status"},
     {"text": "🔍 지금 조회", "callback_data": "check_now"}],
    [{"text": "▶️ 감시 시작", "callback_data": "watch_on"},
     {"text": "⏸ 감시 중지", "callback_data": "watch_off"}],
    [{"text": "🎬 영화", "callback_data": "movie"},
     {"text": "🏢 극장", "callback_data": "theater"}],
    [{"text": "📅 날짜", "callback_data": "dates"},
     {"text": "⏰ 시간대", "callback_data": "times"}],
    [{"text": "🪑 앞열 필터", "callback_data": "rows"},
     {"text": "⚡ 버스트 20분", "callback_data": "burst"}],
]

DATES_MENU = [
    [{"text": "오늘부터 7일", "callback_data": "dates_roll_7"},
     {"text": "오늘부터 14일", "callback_data": "dates_roll_14"}],
    [{"text": "직접 입력 (YYYYMMDD-YYYYMMDD)", "callback_data": "dates_custom"}],
    [{"text": "« 메뉴", "callback_data": "menu"}],
]

TIMES_MENU = [
    [{"text": "전체 시간", "callback_data": "times_all"},
     {"text": "10:30~21:00", "callback_data": "times_day"}],
    [{"text": "직접 입력 (HH:MM-HH:MM)", "callback_data": "times_custom"}],
    [{"text": "« 메뉴", "callback_data": "menu"}],
]

THEATER_MENU = [
    [{"text": "용산아이파크몰 (0013)", "callback_data": "theater_set:0013:용산"}],
    [{"text": "직접 입력 (siteNo [이름])", "callback_data": "theater_custom"}],
    [{"text": "« 메뉴", "callback_data": "menu"}],
]

MOVIE_MENU = [
    [{"text": "전체 영화 감시", "callback_data": "movie_all"}],
    [{"text": "직접 입력 (영화 제목)", "callback_data": "movie_custom"}],
    [{"text": "« 메뉴", "callback_data": "menu"}],
]

ROWS_MENU = [
    [{"text": "A~C열 무시 (권장)", "callback_data": "rows_abc"},
     {"text": "필터 끄기", "callback_data": "rows_none"}],
    [{"text": "« 메뉴", "callback_data": "menu"}],
]


def build_status(cfg: Config, enabled: bool) -> str:
    t = cfg.target
    dates = t.dates()
    lines = [
        f"{'🟢 감시 중' if enabled else '⚪ 중지됨'}",
        f"극장: {t.theater_name or '-'} (siteNo={t.theater_code or '-'})",
        f"영화: {t.movie_keyword or '(전체)'}",
        f"상영관: {t.hall_keyword or '(전체)'}"
        + (f" / 등급코드 {t.grade_code}" if t.grade_code else ""),
        f"날짜: {dates[0]} ~ {dates[-1]}"
        + ("" if t.date_from else f" (롤링 {len(dates)}일)"),
        f"시간대: {t.time_from} ~ {t.time_to}",
        f"앞열 필터: {', '.join(cfg.alert.ignore_rows) or '없음'}",
        f"주기: {current_interval(cfg)}초",
    ]
    state_file = cfg.state_dir / "state.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            shows = data.get("showtimes", {})
            lines.append(f"추적 중인 회차: {len(shows)}개")
            if shows:
                latest = max(v.get("updated_at", "") for v in shows.values())
                lines.append(f"마지막 갱신: {latest[:19].replace('T', ' ')} UTC")
        except (json.JSONDecodeError, ValueError):
            pass
    return "\n".join(lines)


# ---------------------------------------------------------------- 감시 스레드


class WatchWorker(threading.Thread):
    """백그라운드 감시 루프. 매 사이클 설정을 다시 읽는다."""

    def __init__(self, config_path: str):
        super().__init__(daemon=True)
        self.config_path = config_path
        self.next_run = 0.0
        self.consecutive_errors = 0

    def poke(self) -> None:
        """다음 틱에 바로 조회하게 만든다 ('지금 조회' 버튼)."""
        self.next_run = 0.0

    def _enabled(self, cfg: Config) -> bool:
        ov = load_overrides(cfg.state_dir / "overrides.yaml")
        return bool((ov.get("watch") or {}).get("enabled", False))

    def run(self) -> None:
        while True:
            try:
                cfg = load_config(self.config_path)
                if self._enabled(cfg) and time.time() >= self.next_run:
                    try:
                        run_once(cfg)
                        self.consecutive_errors = 0
                    except BlockedError as e:
                        # IP 차단 — 감시를 자동으로 끄고 알린다 (재시도 금지)
                        log.error("IP 차단 감지 — 감시 중지: %s", e)
                        save_override(
                            cfg.state_dir / "overrides.yaml", "watch", {"enabled": False}
                        )
                        Notifier(cfg.telegram, cfg.poll.timeout_sec).send(
                            "⛔ CGV가 이 IP의 접속을 제한해 감시를 자동 중지했습니다.\n"
                            "재시도는 차단을 길게 만들 수 있습니다. 시간을 두고 다른 "
                            "네트워크(국내 가정용 IP)에서 ▶️ 감시 시작을 눌러주세요."
                        )
                    except Exception as e:
                        self.consecutive_errors += 1
                        log.error("조회 실패 (%d연속): %s", self.consecutive_errors, e)
                        if self.consecutive_errors == cfg.poll.error_notify_after:
                            Notifier(cfg.telegram, cfg.poll.timeout_sec).send(
                                f"⚠️ CGV 감시기: {self.consecutive_errors}회 연속 조회 실패.\n"
                                f"마지막 오류: {e}"
                            )
                    interval = current_interval(cfg)
                    self.next_run = time.time() + interval + random.uniform(
                        0, cfg.poll.jitter_sec
                    )
            except Exception as e:  # 설정 파일이 깨져도 스레드는 살아있어야 한다
                log.error("감시 스레드 오류: %s", e)
            time.sleep(3)


# ------------------------------------------------------------------- 봇 본체


class Bot:
    def __init__(self, config_path: str):
        self.config_path = config_path
        cfg = load_config(config_path)
        if not cfg.telegram.enabled:
            raise ConfigError(
                "bot 모드에는 telegram.bot_token과 chat_id가 필요합니다.\n"
                "README의 '텔레그램 봇 만들기'를 먼저 진행해주세요."
            )
        self.token = cfg.telegram.bot_token
        self.chat_id = str(cfg.telegram.chat_id)
        self.pending: Optional[str] = None  # 다음 텍스트 입력의 의미
        self.worker = WatchWorker(config_path)

    # ---- 텔레그램 API 래퍼

    def api(self, method: str, **params: Any) -> Optional[dict]:
        try:
            resp = requests.post(
                _API.format(token=self.token, method=method), json=params, timeout=35
            )
            data = resp.json()
            if not data.get("ok"):
                log.error("%s 실패: %s", method, data)
                return None
            return data.get("result")
        except (requests.RequestException, json.JSONDecodeError) as e:
            log.error("%s 오류: %s", method, e)
            return None

    def send(self, text: str, keyboard: Optional[list] = None) -> None:
        params: dict[str, Any] = {"chat_id": self.chat_id, "text": text}
        if keyboard is not None:
            params["reply_markup"] = {"inline_keyboard": keyboard}
        self.api("sendMessage", **params)

    def send_menu(self, header: str = "무엇을 할까요?") -> None:
        self.send(header, MAIN_MENU)

    # ---- 설정 변경

    def _cfg(self) -> Config:
        return load_config(self.config_path)

    def _ov_path(self) -> Path:
        return self._cfg().state_dir / "overrides.yaml"

    def set_target(self, values: dict, done_msg: str) -> None:
        save_override(self._ov_path(), "target", values)
        self.send_menu(f"✅ {done_msg}\n\n{build_status(self._cfg(), self._watch_enabled())}")

    def _watch_enabled(self) -> bool:
        ov = load_overrides(self._ov_path())
        return bool((ov.get("watch") or {}).get("enabled", False))

    def set_watch(self, enabled: bool) -> None:
        save_override(self._ov_path(), "watch", {"enabled": enabled})
        if enabled:
            self.worker.poke()
            self.send_menu("▶️ 감시를 시작했습니다. 곧 첫 조회를 합니다.")
        else:
            self.send_menu("⏸ 감시를 중지했습니다. 설정은 유지됩니다.")

    # ---- 업데이트 처리

    def handle_callback(self, cb: dict) -> None:
        self.api("answerCallbackQuery", callback_query_id=cb["id"])
        data = cb.get("data", "")
        self.pending = None

        if data == "menu":
            self.send_menu()
        elif data == "status":
            self.send(build_status(self._cfg(), self._watch_enabled()), MAIN_MENU)
        elif data == "watch_on":
            self.set_watch(True)
        elif data == "watch_off":
            self.set_watch(False)
        elif data == "check_now":
            self.worker.poke()
            if not self._watch_enabled():
                save_override(self._ov_path(), "watch", {"enabled": True})
                self.send("▶️ 감시가 꺼져 있어 켜고 바로 조회합니다…")
            else:
                self.send("🔍 바로 조회합니다…")
        elif data == "burst":
            cfg = self._cfg()
            until = set_burst(cfg, 20)
            self.worker.poke()
            self.send_menu(
                f"⚡ 20분 동안 {cfg.poll.burst_interval_sec}초 간격 "
                f"(~{until.astimezone().strftime('%H:%M')}), 이후 자동 복귀."
            )
        elif data == "movie":
            self.send("영화 필터:", MOVIE_MENU)
        elif data == "movie_all":
            self.set_target({"movie_keyword": ""}, "전체 영화를 감시합니다")
        elif data == "movie_custom":
            self.pending = "movie"
            self.send("영화 제목(일부)을 입력해주세요. 예: 오디세이")
        elif data == "theater":
            self.send("극장 선택:", THEATER_MENU)
        elif data.startswith("theater_set:"):
            _, code, name = data.split(":", 2)
            self.set_target(
                {"theater_code": code, "theater_name": name}, f"극장: {name} ({code})"
            )
        elif data == "theater_custom":
            self.pending = "theater"
            self.send(
                "극장 siteNo(4자리)와 이름을 입력해주세요. 예: 0013 용산\n"
                "(siteNo는 cgv.co.kr 예매 페이지 주소창의 siteNo= 값)"
            )
        elif data == "dates":
            self.send("감시 날짜:", DATES_MENU)
        elif data in ("dates_roll_7", "dates_roll_14"):
            days = 7 if data.endswith("_7") else 14
            self.set_target(
                {"date_from": "", "date_to": "", "days": days},
                f"오늘부터 {days}일 롤링 감시 (오픈 감시에 적합)",
            )
        elif data == "dates_custom":
            self.pending = "dates"
            self.send("날짜를 입력해주세요. 예: 20260815-20260817 (하루면 20260815)")
        elif data == "times":
            self.send("회차 시작시간 창:", TIMES_MENU)
        elif data == "times_all":
            self.set_target({"time_from": "00:00", "time_to": "23:59"}, "전체 시간대")
        elif data == "times_day":
            self.set_target({"time_from": "10:30", "time_to": "21:00"}, "10:30~21:00")
        elif data == "times_custom":
            self.pending = "times"
            self.send("시간 창을 입력해주세요. 예: 10:30-21:00")
        elif data == "rows":
            self.send("앞열(스크린 바로 앞) 좌석만 풀렸을 때:", ROWS_MENU)
        elif data == "rows_abc":
            save_override(self._ov_path(), "alert", {"ignore_rows": ["A", "B", "C"]})
            self.send_menu("✅ A~C열만 풀린 경우는 알리지 않습니다")
        elif data == "rows_none":
            save_override(self._ov_path(), "alert", {"ignore_rows": []})
            self.send_menu("✅ 모든 열의 빈자리 증가를 알립니다")
        else:
            self.send_menu()

    def handle_text(self, text: str) -> None:
        text = text.strip()
        if text.startswith("/"):
            self.pending = None
            if text.split("@")[0] in ("/start", "/menu", "/help"):
                self.send_menu(
                    "🎬 CGV 표 감시기입니다.\n버튼으로 설정하고 감시를 시작하세요."
                )
            elif text.split("@")[0] == "/status":
                self.send(build_status(self._cfg(), self._watch_enabled()), MAIN_MENU)
            else:
                self.send_menu("모르는 명령이에요. 메뉴에서 골라주세요.")
            return

        if self.pending == "movie":
            self.pending = None
            self.set_target({"movie_keyword": text}, f"영화: {text}")
        elif self.pending == "theater":
            parsed = parse_theater(text)
            if parsed:
                self.pending = None
                self.set_target(parsed, f"극장 설정: {text}")
            else:
                self.send("형식이 맞지 않아요. 예: 0013 용산")
        elif self.pending == "dates":
            parsed = parse_date_range(text)
            if parsed:
                self.pending = None
                self.set_target(parsed, f"날짜: {parsed['date_from']}~{parsed['date_to']}")
            else:
                self.send("형식이 맞지 않아요. 예: 20260815-20260817")
        elif self.pending == "times":
            parsed = parse_time_range(text)
            if parsed:
                self.pending = None
                self.set_target(
                    parsed, f"시간대: {parsed['time_from']}~{parsed['time_to']}"
                )
            else:
                self.send("형식이 맞지 않아요. 예: 10:30-21:00")
        else:
            self.send_menu()

    def handle_update(self, update: dict) -> None:
        msg = update.get("message")
        if msg is not None:
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                log.warning("허용되지 않은 chat_id 무시: %s", msg.get("chat", {}).get("id"))
                return
            if "text" in msg:
                self.handle_text(msg["text"])
            return
        cb = update.get("callback_query")
        if cb is not None:
            if str(cb.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                log.warning("허용되지 않은 callback 무시")
                return
            self.handle_callback(cb)

    # ---- 메인 루프

    def run(self) -> None:
        self.worker.start()
        self.send_menu(
            "🎬 CGV 표 감시기 봇이 켜졌습니다.\n"
            f"({datetime.now():%m/%d %H:%M} 기준, 버튼으로 설정하세요)"
        )
        offset = None
        log.info("봇 시작 (chat_id=%s)", self.chat_id)
        while True:
            result = self.api(
                "getUpdates",
                timeout=25,
                offset=offset,
                allowed_updates=["message", "callback_query"],
            )
            if result is None:
                time.sleep(5)
                continue
            for update in result:
                offset = update["update_id"] + 1
                try:
                    self.handle_update(update)
                except Exception as e:
                    log.error("업데이트 처리 오류: %s", e)


def run_bot(config_path: str) -> None:
    Bot(config_path).run()
