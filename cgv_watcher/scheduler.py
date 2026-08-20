"""폴링 루프.

- 평소: interval_sec(기본 5분) 간격
- 버스트: `cgv-watcher burst` 명령으로 일정 시간 동안만 짧은 간격, 끝나면 자동 복귀
  (블로그의 "20분 동안만 2분 간격" 아이디어. 버스트 상태는 파일로 공유되므로
  실행 중인 루프를 재시작할 필요가 없다.)
- 종료 시각을 코드에 박아두지 않는다 — 블로그의 '15:00 하드코딩' 사고 방지.
"""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .browser_fetch import BlockedError
from .cgv_api import CgvClient
from .config import Config
from .notify import Notifier
from .watcher import run_once

log = logging.getLogger("cgv_watcher.loop")


def _burst_path(cfg: Config) -> Path:
    return cfg.state_dir / "burst.json"


def set_burst(cfg: Config, minutes: int, interval_sec: int | None = None) -> datetime:
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    _burst_path(cfg).write_text(
        json.dumps(
            {
                "until": until.isoformat(),
                "interval_sec": interval_sec or cfg.poll.burst_interval_sec,
            }
        ),
        encoding="utf-8",
    )
    return until


def current_interval(cfg: Config) -> int:
    """버스트가 유효하면 버스트 간격, 아니면 기본 간격."""
    p = _burst_path(cfg)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            until = datetime.fromisoformat(data["until"])
            if datetime.now(timezone.utc) < until:
                return int(data.get("interval_sec", cfg.poll.burst_interval_sec))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
        p.unlink(missing_ok=True)  # 만료/손상된 버스트는 정리하고 기본 주기로 복귀
        log.info("버스트 종료 — 기본 주기로 복귀")
    return cfg.poll.interval_sec


def run_loop(cfg: Config) -> None:
    client = CgvClient(cfg)
    notifier = Notifier(cfg.telegram, cfg.poll.timeout_sec)
    consecutive_errors = 0

    log.info(
        "감시 시작: %s ~ %s, %s-%s, 주기 %d초",
        cfg.target.dates()[0],
        cfg.target.dates()[-1],
        cfg.target.time_from,
        cfg.target.time_to,
        cfg.poll.interval_sec,
    )
    while True:
        try:
            run_once(cfg, client=client)
            consecutive_errors = 0
        except KeyboardInterrupt:
            raise
        except BlockedError as e:
            # IP 차단은 재시도로 풀리지 않고 오히려 길어진다 — 즉시 중단
            log.error("IP 차단 감지 — 감시를 중단합니다: %s", e)
            notifier.send(
                "⛔ CGV가 이 IP의 접속을 제한했습니다. 감시를 중단합니다.\n"
                "재시도는 차단을 길게 만들 수 있으니, 시간을 두고 다른 네트워크"
                "(국내 가정용 IP)에서 다시 시작해주세요."
            )
            return
        except Exception as e:
            consecutive_errors += 1
            log.error("조회 실패 (%d연속): %s", consecutive_errors, e)
            if consecutive_errors == cfg.poll.error_notify_after:
                # 조용히 죽어있는 감시기는 없느니만 못하다 — 딱 한 번 경고
                notifier.send(
                    f"⚠️ CGV 감시기: {consecutive_errors}회 연속 조회 실패.\n"
                    f"마지막 오류: {e}\n엔드포인트/네트워크 확인이 필요합니다."
                )

        interval = current_interval(cfg)
        sleep_for = interval + random.uniform(0, cfg.poll.jitter_sec)
        log.debug("%.0f초 대기", sleep_for)
        time.sleep(sleep_for)
