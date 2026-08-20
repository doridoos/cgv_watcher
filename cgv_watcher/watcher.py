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


def run_once(cfg: Config, client: CgvClient | None = None, dry_run: bool = False) -> list[Change]:
    """한 번 조회하고 필요 시 알림. 발생한 변화 목록을 반환."""
    client = client or CgvClient(cfg)
    store = StateStore(cfg.state_dir)
    notifier = Notifier(cfg.telegram, cfg.poll.timeout_sec)

    matched_keys: set[str] = set()
    changes: list[Change] = []

    for date in cfg.target.dates():
        shows = client.fetch_showtimes(date)
        targets = [s for s in shows if match_showtime(s, cfg.target)]
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

    to_alert = [c for c in changes if should_alert(c, cfg.alert)]
    if to_alert:
        msg = build_alert_message(to_alert, _title_prefix(cfg))
        if dry_run:
            log.info("[dry-run] 알림 생략:\n%s", msg)
        else:
            notifier.send(msg)
    else:
        log.info("변화 없음 — 조용히 대기")
    return changes
