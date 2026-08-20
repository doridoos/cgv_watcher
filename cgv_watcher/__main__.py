"""CLI 진입점.

  python -m cgv_watcher watch            # 폴링 루프 시작
  python -m cgv_watcher once             # 1회 조회 (cron용)
  python -m cgv_watcher once --dry-run   # 알림 없이 조회만
  python -m cgv_watcher probe            # 엔드포인트 진단 (파싱 결과 + 후보 목록)
  python -m cgv_watcher burst -m 20      # 20분 동안 버스트 간격으로 전환
  python -m cgv_watcher test-telegram    # 텔레그램 설정 확인
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime

from .cgv_api import CgvClient
from .config import ConfigError, load_config
from .notify import Notifier
from .scheduler import run_loop, set_burst
from .watcher import run_once


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgv-watcher", description="CGV 표 감시기")
    parser.add_argument("-c", "--config", default="config.yaml", help="설정 파일 경로")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("watch", help="폴링 루프 시작")
    p_once = sub.add_parser("once", help="1회 조회 (cron에서 사용)")
    p_once.add_argument("--dry-run", action="store_true", help="알림을 보내지 않음")
    p_probe = sub.add_parser("probe", help="엔드포인트 진단")
    p_probe.add_argument("--date", default=None, help="YYYYMMDD (기본: 감시 첫 날짜)")
    p_probe.add_argument("--raw", action="store_true", help="원본 응답 전체 출력")
    p_burst = sub.add_parser("burst", help="일시적으로 짧은 주기로 전환")
    p_burst.add_argument("-m", "--minutes", type=int, default=20)
    p_burst.add_argument("-i", "--interval", type=int, default=None, help="버스트 간격(초)")
    sub.add_parser("test-telegram", help="텔레그램 테스트 메시지 전송")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    if args.command == "watch":
        try:
            run_loop(cfg)
        except KeyboardInterrupt:
            print("\n감시 종료")
        return 0

    if args.command == "once":
        run_once(cfg, dry_run=args.dry_run)
        return 0

    if args.command == "probe":
        date = args.date or cfg.target.dates()[0]
        result = CgvClient(cfg).probe(date)
        print(f"== {date} 파싱된 회차 {len(result['parsed'])}개 ==")
        for st in result["parsed"]:
            print(
                f"  {st.date} {st.start_time}  movie={st.movie!r} hall={st.hall!r} "
                f"remaining={st.remaining} id={st.screening_id!r}"
            )
        print("\n== 응답 안의 리스트 후보 (auto가 잘못 짚으면 list_path로 지정) ==")
        for c in result["list_candidates"]:
            print(f"  {c['path']}  (항목 {c['size']}개) keys={c['sample_keys']}")
        discovered = cfg.state_dir / "discovered.json"
        if discovered.exists():
            print(f"\n캡처된 실제 API 요청 목록: {discovered}")
            print("(이 URL을 endpoints.showtimes.url로 옮기면 가벼운 api 모드로 전환 가능)")
        if args.raw:
            print("\n== 원본 응답 ==")
            print(json.dumps(result["raw"], ensure_ascii=False, indent=2)[:20000])
        return 0

    if args.command == "burst":
        until = set_burst(cfg, args.minutes, args.interval)
        print(
            f"{args.minutes}분 동안 버스트 모드 "
            f"(간격 {args.interval or cfg.poll.burst_interval_sec}초, "
            f"{until.astimezone().strftime('%H:%M:%S')}까지). 이후 자동 복귀."
        )
        return 0

    if args.command == "test-telegram":
        ok = Notifier(cfg.telegram).send(
            f"✅ CGV 표 감시기 테스트 메시지 ({datetime.now():%Y-%m-%d %H:%M:%S})"
        )
        print("전송 성공" if ok else "전송 실패 — 토큰/chat_id 확인")
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
