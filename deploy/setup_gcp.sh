#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────────────
# CGV 표 감시기 — GCP(우분투/데비안) VM 설치 스크립트 (최소 변경 원칙)
#
# 사용 (SSH 접속 후 한 줄):
#   curl -fsSL https://raw.githubusercontent.com/doridoos/cgv_watcher/claude/cgv-ticket-monitor-57h7t0/deploy/setup_gcp.sh | bash
#
# 비대화식으로 쓰려면 미리:
#   export TELEGRAM_BOT_TOKEN=123456:ABC...
#   export TELEGRAM_CHAT_ID=111111111
#
# 다른 프로그램이 함께 도는 서버를 전제로, 시스템 설정은 건드리지 않는다:
#   - 시스템 타임존 변경 없음 (서비스만 TZ=Asia/Seoul 환경변수로 KST 동작)
#   - 스왑/fstab 변경 없음 (메모리 상태는 확인만 하고 알려줌)
#   - 패키지/Chromium은 없을 때만 설치, systemd 유닛은 내용이 바뀌었을 때만 갱신
# 재실행해도 안전(멱등)합니다.
# ─────────────────────────────────────────────────────────────────

BRANCH="${CGV_BRANCH:-claude/cgv-ticket-monitor-57h7t0}"
REPO="${CGV_REPO:-https://github.com/doridoos/cgv_watcher.git}"
APP_DIR="${CGV_DIR:-$HOME/cgv_watcher}"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

log "1/6 시스템 패키지 확인 (없는 것만 설치)"
missing=()
command -v git >/dev/null || missing+=(git)
command -v curl >/dev/null || missing+=(curl)
python3 -c 'import venv' 2>/dev/null || missing+=(python3-venv)
if [ "${#missing[@]}" -gt 0 ]; then
  echo "  설치: ${missing[*]}"
  sudo apt-get update -qq
  sudo apt-get install -y -qq "${missing[@]}"
else
  echo "  모두 있음 — 아무것도 설치하지 않음"
fi

log "2/6 환경 확인 (변경하지 않음)"
sys_tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo "unknown")
if [ "$sys_tz" = "Asia/Seoul" ]; then
  echo "  타임존: Asia/Seoul ✓"
else
  echo "  타임존: $sys_tz — 시스템은 그대로 둡니다."
  echo "         (감시 서비스는 TZ=Asia/Seoul 환경변수로 실행되므로 KST로 동작)"
fi
mem_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)
swap_kb=$(awk '/SwapTotal/{print $2}' /proc/meminfo)
echo "  메모리: $((mem_kb / 1024))MB / 스왑: $((swap_kb / 1024))MB ✓ (변경 안 함)"
if [ "$((mem_kb + swap_kb))" -lt 1500000 ]; then
  echo "  ⚠️ 램+스왑이 1.5GB 미만이라 Chromium 실행이 빠듯할 수 있습니다."
fi

log "3/6 코드 받기 ($BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" checkout "$BRANCH"
  git -C "$APP_DIR" pull --ff-only origin "$BRANCH"
else
  git clone -b "$BRANCH" "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"

log "4/6 파이썬 환경 + Chromium (이미 있으면 건너뜀)"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if ls "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"/chromium*/chrome-linux/chrome >/dev/null 2>&1; then
  echo "  Chromium 이미 설치됨 — 건너뜀"
else
  sudo .venv/bin/playwright install-deps chromium
  .venv/bin/playwright install chromium
fi

log "5/6 config.yaml"
[ -f config.yaml ] || cp config.example.yaml config.yaml
# 클라우드 VM용 Chromium 인자 활성화 (이 프로젝트 설정 파일만 수정)
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

log "6/6 systemd 서비스 (내용이 바뀌었을 때만 갱신)"
unit_file=/etc/systemd/system/cgv-watcher.service
unit_content="[Unit]
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
WantedBy=multi-user.target"
if [ -f "$unit_file" ] && [ "$(cat "$unit_file")" = "$unit_content" ]; then
  echo "  유닛 파일 동일 — 그대로 둠"
  sudo systemctl is-active --quiet cgv-watcher || sudo systemctl start cgv-watcher
else
  printf '%s\n' "$unit_content" | sudo tee "$unit_file" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now cgv-watcher
  sudo systemctl restart cgv-watcher
fi

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
