"""감시 1사이클: 조회 → 필터 → 상태 비교 → 알림 → 저장."""

from __future__ import annotations

import logging

from .cgv_api import CgvClient
from .config import Config
from .filters import match_showtime, notable_seats, should_alert
from .models import Change
from .notify import Notifier, build_alert_message
from .state import StateStore

log = logging.getLogger("cgv_watcher")


def _title_prefix(cfg: Config) -> str:
    t = cfg.target
    parts = ["CGV"]
    if t.theater_name:
        parts.append(t.theater_name)
    if t.hall_keyword:
        parts.append(t.hall_keyword)
    name = " ".join(parts)
    if t.movie_keyword:
        name += f" ‘{t.movie_keyword}’"
    return name


def run_once(
    cfg: Config,
    client: CgvClient | None = None,
    dry_run: bool = False,
    notifier: Notifier | None = None,
) -> list[Change]:
    """한 번 조회하고 필요 시 알림. 발생한 변화 목록을 반환."""
    client = client or CgvClient(cfg)
    store = StateStore(cfg.state_dir)
    notifier = notifier or Notifier(cfg.telegram, cfg.poll.timeout_sec)

    matched_keys: set[str] = set()
    changes: list[Change] = []
    allowed_dates = set(cfg.target.dates())

    for date in cfg.target.dates():
        shows = client.fetch_showtimes(date)
        # 응답에 다른 날짜 회차가 섞여 들어와도(오캡처 등) 감시 범위만 취급
        targets = [
            s for s in shows if s.date in allowed_dates and match_showtime(s, cfg.target)
        ]
        log.info("%s: 조건에 맞는 회차 %d/%d", date, len(targets), len(shows))

        for st in targets:
            matched_keys.add(st.key)
            seats = None
            if "seats" in cfg.endpoints and st.screening_id:
                try:
                    seats = client.fetch_available_seats(st)
                except Exception as e:  # 좌석 조회 실패는 좌석수 기반으로 강등
                    log.warning("좌석 조회 실패(%s %s): %s", st.date, st.start_time, e)
            change = store.observe(st, seats)
            change.notable_seats = notable_seats(change.new_seats, cfg.alert)
            changes.append(change)

    store.prune(matched_keys)
    store.save()

    # 콜드스타트: 기존 회차를 '오픈'으로 알리지 않는다 (참고 프로젝트의 설계).
    # 대신 요약 1건으로 감시가 시작됐음을 확인시켜 준다 — 오픈 전이라 회차가
    # 0개여도 "오픈되면 알려드립니다"가 가서 아이맥스 오픈 감시 시작을 알 수 있다.
    cold = store.was_empty
    if cold and cfg.alert.startup_summary:
        title = _title_prefix(cfg)
        n = len(changes)
        if n:
            total = sum(c.cur_remaining or 0 for c in changes)
            summary = (
                f"👀 {title} 감시 시작 — 조건에 맞는 회차 {n}개, 예매 가능 {total}석.\n"
                "이후 새 회차(예매 오픈)나 빈자리 증가만 알립니다."
            )
        else:
            summary = (
                f"👀 {title} 감시 시작 — 조건에 맞는 회차가 아직 없습니다.\n"
                "예매가 오픈되면 바로 알려드립니다."
            )
        if dry_run:
            log.info("[dry-run] 시작 요약 생략:\n%s", summary)
        else:
            notifier.send(summary)

    to_alert = [
        c
        for c in changes
        if should_alert(c, cfg.alert) and not (cold and c.first_seen)
    ]
    if to_alert:
        # 알림에서 바로 예매 페이지로 갈 수 있게 링크 첨부 (첫 알림 회차의 날짜)
        booking_url = ""
        ep = cfg.endpoints.get("showtimes")
        if ep is not None and ep.page_url:
            d = to_alert[0].showtime.date
            try:
                booking_url = ep.page_url.format(
                    theater_code=cfg.target.theater_code,
                    theater_name=cfg.target.theater_name,
                    date=d,
                    date_dash=f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d,
                )
            except (KeyError, IndexError):  # 알 수 없는 플레이스홀더면 링크 생략
                booking_url = ""
        msg = build_alert_message(to_alert, _title_prefix(cfg), booking_url)
        if dry_run:
            log.info("[dry-run] 알림 생략:\n%s", msg)
        else:
            notifier.send(msg)
    else:
        log.info("변화 없음 — 조용히 대기")
    return changes
