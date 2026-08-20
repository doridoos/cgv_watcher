"""알림 메시지 구성과 전송 (Telegram / 콘솔)."""

from __future__ import annotations

import logging
from datetime import date

import requests

from .config import TelegramConfig
from .models import Change

log = logging.getLogger("cgv_watcher.notify")

_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _fmt_date(yyyymmdd: str) -> str:
    try:
        d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
        return f"{d.month}월 {d.day}일({_WEEKDAYS[d.weekday()]})"
    except (ValueError, IndexError):
        return yyyymmdd


def build_alert_message(changes: list[Change], title_prefix: str) -> str:
    """블로그의 최종 메시지 포맷을 따른다.

    CGV 대구 IMAX '오디세이' 빈자리 증가

    - 8월 15일(토) 18:00
      - 2석 → 3석 (+1석)
      - 새 좌석: E6
    """
    opened = [c for c in changes if c.first_seen]
    increased = [c for c in changes if not c.first_seen]

    lines: list[str] = []
    if increased:
        lines.append(f"🎬 {title_prefix} 빈자리 증가")
        lines.append("")
        for c in increased:
            lines.append(f"- {_fmt_date(c.showtime.date)} {c.showtime.start_time}")
            if c.prev_remaining is not None and c.cur_remaining is not None:
                diff = c.cur_remaining - c.prev_remaining
                lines.append(
                    f"  - {c.prev_remaining}석 → {c.cur_remaining}석 (+{diff}석)"
                )
            elif c.cur_remaining is not None:
                lines.append(f"  - 현재 {c.cur_remaining}석")
            # 원하는 좌석만 표시하되, 증가량 숫자는 위에서 실제 값 그대로
            seats = c.notable_seats or c.new_seats
            if seats:
                lines.append(f"  - 새 좌석: {', '.join(seats)}")
    if opened:
        if lines:
            lines.append("")
        lines.append(f"🆕 {title_prefix} 새 회차 발견 (예매 오픈?)")
        lines.append("")
        for c in opened:
            seat_info = (
                f" — 예매 가능 {c.cur_remaining}석" if c.cur_remaining is not None else ""
            )
            lines.append(
                f"- {_fmt_date(c.showtime.date)} {c.showtime.start_time}"
                f" [{c.showtime.hall}]{seat_info}"
            )
    return "\n".join(lines)


class Notifier:
    """Telegram이 설정돼 있으면 전송하고, 항상 콘솔에도 출력한다."""

    def __init__(self, tg: TelegramConfig, timeout_sec: int = 15):
        self.tg = tg
        self.timeout = timeout_sec

    def send(self, text: str) -> bool:
        print(text, flush=True)
        if not self.tg.enabled:
            log.info("텔레그램 미설정 — 콘솔 출력만 했습니다")
            return False
        resp = requests.post(
            f"https://api.telegram.org/bot{self.tg.bot_token}/sendMessage",
            json={"chat_id": self.tg.chat_id, "text": text},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            log.error("텔레그램 전송 실패 %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
