#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# CGV 표 감시기 — 깔끔 제거 스크립트
#
# setup_gcp.sh가 만든 것만 지운다:
#   1) systemd 서비스 (cgv-watcher)
#   2) 설치 폴더 (기본 ~/cgv_watcher, CGV_DIR로 지정했다면 그 위치)
#   3) (선택) Playwright 브라우저 캐시 — 다른 프로그램이 Playwright를
#      쓰고 있다면 지우지 말 것
#
# apt로 설치된 git/python3-venv 등 시스템 패키지는 다른 프로그램과
# 공유되므로 건드리지 않는다.
#
# 사용:  bash deploy/uninstall.sh
#   또는 설치 폴더 밖에서:  bash ~/cgv_watcher/deploy/uninstall.sh
# ─────────────────────────────────────────────────────────────────

APP_DIR="${CGV_DIR:-$HOME/cgv_watcher}"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

log "1/3 systemd 서비스 제거"
if [ -f /etc/systemd/system/cgv-watcher.service ]; then
  sudo systemctl disable --now cgv-watcher 2>/dev/null || true
  sudo rm /etc/systemd/system/cgv-watcher.service
  sudo systemctl daemon-reload
  echo "  cgv-watcher 서비스 제거됨"
else
  echo "  서비스 없음 (로컬 설치였거나 이미 제거됨)"
fi

log "2/3 설치 폴더 삭제"
if [ -d "$APP_DIR" ]; then
  read -rp "  $APP_DIR 를 삭제할까요? (config.yaml의 토큰 포함 전부 삭제) [y/N] " ok </dev/tty
  if [ "${ok:-n}" = "y" ] || [ "${ok:-n}" = "Y" ]; then
    rm -rf "$APP_DIR"
    echo "  삭제 완료"
  else
    echo "  건너뜀"
  fi
else
  echo "  $APP_DIR 없음"
fi

log "3/3 Playwright 브라우저 캐시 (선택)"
PW_CACHE="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
if [ -d "$PW_CACHE" ]; then
  du_size=$(du -sh "$PW_CACHE" 2>/dev/null | cut -f1)
  echo "  $PW_CACHE ($du_size)"
  echo "  ⚠️ 다른 프로그램이 Playwright를 쓰고 있다면 지우지 마세요."
  read -rp "  삭제할까요? [y/N] " ok </dev/tty
  if [ "${ok:-n}" = "y" ] || [ "${ok:-n}" = "Y" ]; then
    rm -rf "$PW_CACHE"
    echo "  삭제 완료"
  else
    echo "  건너뜀"
  fi
else
  echo "  캐시 없음"
fi

echo
echo "완료. 텔레그램 봇 자체를 없애려면 @BotFather에서 /deletebot 하세요."
