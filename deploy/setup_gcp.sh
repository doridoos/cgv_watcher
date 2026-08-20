#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# CGV 표 감시기 — GCP(우분투/데비안) VM 원클릭 설치 스크립트
#
# 사용 (SSH 접속 후 한 줄):
#   curl -fsSL https://raw.githubusercontent.com/doridoos/cgv_watcher/claude/cgv-ticket-monitor-57h7t0/deploy/setup_gcp.sh | bash
#
# 비대화식으로 쓰려면 미리:
#   export TELEGRAM_BOT_TOKEN=123456:ABC...
#   export TELEGRAM_CHAT_ID=111111111
#
# 하는 일: 패키지 설치 → KST 타임존 → (1GB 램이면) 스왑 → 코드 클론 →
#          venv + Chromium → config.yaml 생성 → systemd 서비스 등록/시작 →
#          텔레그램 테스트 + CGV 조회 진단(probe)
# 재실행해도 안전(멱등)합니다.
# ─────────────────────────────────────────────────────────────────

BRANCH="${CGV_BRANCH:-claude/cgv-ticket-monitor-57h7t0}"
REPO="${CGV_REPO:-https://github.com/doridoos/cgv_watcher.git}"
APP_DIR="${CGV_DIR:-$HOME/cgv_watcher}"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

log "1/7 시스템 패키지 설치"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-pip git curl

log "2/7 타임존을 Asia/Seoul로 (롤링 날짜 계산이 KST 기준이어야 함)"
sudo timedatectl set-timezone Asia/Seoul || echo "  (타임존 설정 실패 — 무시하고 진행)"

log "3/7 스왑 확인 (e2-micro 1GB 대비)"
mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
swap_kb=$(awk '/SwapTotal/{print $2}' /proc/meminfo)
if [ "$mem_kb" -lt 1500000 ] && [ "$swap_kb" -eq 0 ]; then
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  echo "  램 ${mem_kb}kB → 스왑 1GB 생성 (Chromium 실행용)"
else
  echo "  스왑 불필요하거나 이미 있음 (램 ${mem_kb}kB, 스왑 ${swap_kb}kB)"
fi

log "4/7 코드 받기 ($BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  git clone -b "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

log "5/7 파이썬 환경 + Chromium"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
sudo .venv/bin/playwright install-deps chromium
.venv/bin/playwright install chromium

log "6/7 config.yaml"
[ -f config.yaml ] || cp config.example.yaml config.yaml
# 클라우드 VM용 Chromium 인자 활성화
grep -q '^browser_args:' config.yaml || \
  sed -i 's|^# browser_args:|browser_args:|' config.yaml

TOKEN="${TELEGRAM_BOT_TOKEN:-}"
CHATID="${TELEGRAM_CHAT_ID:-}"
# curl | bash 로 실행돼도 입력받을 수 있게 /dev/tty 사용
if grep -q '^  bot_token: ""' config.yaml; then
  if [ -z "$TOKEN" ]; then
    read -rp "텔레그램 봇 토큰 (@BotFather에서 발급): " TOKEN </dev/tty
  fi
  sed -i "s|^  bot_token: \"\"|  bot_token: \"$TOKEN\"|" config.yaml
fi
if grep -q '^  chat_id: ""' config.yaml; then
  if [ -z "$CHATID" ]; then
    read -rp "텔레그램 chat_id (@userinfobot에서 확인): " CHATID </dev/tty
  fi
  sed -i "s|^  chat_id: \"\"|  chat_id: \"$CHATID\"|" config.yaml
fi
echo "  config.yaml 준비 완료 (세부 감시 설정은 텔레그램 봇 버튼으로)"

log "7/7 systemd 서비스 등록·시작"
sudo tee /etc/systemd/system/cgv-watcher.service >/dev/null <<UNIT
[Unit]
Description=CGV ticket watcher bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m cgv_watcher bot
Restart=on-failure
RestartSec=30
Environment=TZ=Asia/Seoul

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now cgv-watcher

log "검증: 텔레그램 전송 테스트"
.venv/bin/python -m cgv_watcher test-telegram || true

log "검증: CGV 조회 진단 (probe — 미국 리전이면 여기서 판가름 납니다)"
if .venv/bin/python -m cgv_watcher probe; then
  echo
  echo "🎉 조회 성공! 텔레그램에서 봇에게 /start 를 보내 버튼으로 감시를 시작하세요."
else
  echo
  echo "⚠️  CGV 조회 실패. 미국 리전 IP를 CGV/Cloudflare가 막았을 가능성이 큽니다."
  echo "   - 재시도: cd $APP_DIR && .venv/bin/python -m cgv_watcher probe -v"
  echo "   - 해결책: 서울 리전(asia-northeast3) VM으로 이전하거나,"
  echo "             서울 리전이 있는 무료 대안(예: Oracle Cloud Free Tier 춘천/서울)을 고려하세요."
  echo "   봇 서비스 자체는 켜져 있으므로, 텔레그램 /start 와 상태 버튼은 동작합니다."
fi

echo
echo "로그 보기:      journalctl -u cgv-watcher -f"
echo "서비스 재시작:  sudo systemctl restart cgv-watcher"
