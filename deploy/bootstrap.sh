#!/usr/bin/env bash
# Стенд «Взаимно» для жюри на чистом VPS одной командой: Docker, секреты, HTTPS (Caddy), пул заданий, демо-мир.
#
#   git clone <репозиторий> /opt/vzaimno && cd /opt/vzaimno && sudo bash deploy/bootstrap.sh
#
# Параметры (все необязательны):
#   --domain vzaimno.example   свой домен с A-записью на VPS; по умолчанию <ip-через-дефисы>.sslip.io — публичный
#                              wildcard-DNS, сертификат Let's Encrypt выпускается на него так же, домен покупать не нужно
#   --no-demo                  боевой режим: без демо-мира и входа жюри (тогда задайте реальный SMTP в .env)
#   --smtp-host/--smtp-user/--smtp-password/--mail-from  реальная почта; без них письма не уходят, а в демо-режиме
#                              регистрация подтверждается автоматически (settings.demo_auto_verify_email, docs/DEPLOY.md п. 5)
#
# Повторный запуск безопасен: .env не перезаписывается, compose пересобирает изменившееся, пул и демо-мир доливаются.
# Требования: Ubuntu 22.04/24.04 или Debian 12, 2+ vCPU (рекомендуется 4), 4+ ГБ ОЗУ, открытые 80 и 443.
set -euo pipefail

DOMAIN=""
DEMO=true
SMTP_HOST="" SMTP_USER="" SMTP_PASSWORD="" MAIL_FROM=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --no-demo) DEMO=false; shift ;;
    --smtp-host) SMTP_HOST="$2"; shift 2 ;;
    --smtp-user) SMTP_USER="$2"; shift 2 ;;
    --smtp-password) SMTP_PASSWORD="$2"; shift 2 ;;
    --mail-from) MAIL_FROM="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Неизвестный параметр: $1 (см. --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
die() { printf '\033[1;31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запустите от root: sudo bash deploy/bootstrap.sh"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ -f docker-compose.prod.yml ]] || die "не найден docker-compose.prod.yml — запускайте из клона репозитория"
COMPOSE=(docker compose -f docker-compose.prod.yml)

say "1/7 Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
docker compose version >/dev/null 2>&1 || die "нет плагина docker compose v2 (apt install docker-compose-plugin)"
systemctl enable --now docker >/dev/null 2>&1 || true

say "2/7 Память и сеть"
# Сборка образов и Argon2 (64 МБ на хеширование) на 4 ГБ без подкачки упираются в OOM: добавляем swap 4 ГБ один раз.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [[ $MEM_MB -lt 6000 ]] && ! swapon --show | grep -q .; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap 4 ГБ включён (ОЗУ ${MEM_MB} МБ)"
fi
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 22/tcp >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443 >/dev/null
fi
if [[ -z "$DOMAIN" ]]; then
  IP=$(curl -fsS4 --max-time 10 https://api.ipify.org || curl -fsS4 --max-time 10 https://ifconfig.me || true)
  [[ $IP =~ ^[0-9]+(\.[0-9]+){3}$ ]] || die "не удалось определить публичный IPv4; задайте --domain"
  DOMAIN="${IP//./-}.sslip.io"
fi
echo "Адрес стенда: https://${DOMAIN}"

say "3/7 Секреты (.env)"
if [[ -f .env ]]; then
  echo ".env уже есть — не трогаю (удалите его, чтобы сгенерировать заново)"
  DOMAIN=$(grep -E '^VZ_PUBLIC_DOMAIN=' .env | cut -d= -f2-)
else
  secret() { openssl rand -base64 48 | tr -d '/+=\n' | cut -c1-48; }
  CPUS=$(nproc)
  WORKERS=$(( CPUS >= 4 ? 4 : (CPUS >= 2 ? 2 : 1) ))
  cat > .env <<EOF
# Сгенерировано deploy/bootstrap.sh $(date -u +%Y-%m-%dT%H:%MZ). Хранить вне git; без VZ_APP_SECRET ключи клонов не прочитать.
VZ_PUBLIC_DOMAIN=${DOMAIN}
VZ_DB_PASSWORD=$(secret)
VZ_JWT_SECRET=$(secret)
VZ_APP_SECRET=$(secret)
VZ_PSEUDONYM_SECRET=$(secret)
VZ_DEMO_CONTROL_KEY=$(secret)
VZ_WORKERS=${WORKERS}
VZ_DEMO_MODE=${DEMO}
VZ_FSP_DEMO_LOGIN_ENABLED=${DEMO}
VZ_SMTP_HOST=${SMTP_HOST:-localhost}
VZ_SMTP_PORT=587
VZ_SMTP_SECURITY=starttls
VZ_SMTP_USERNAME=${SMTP_USER}
VZ_SMTP_PASSWORD=${SMTP_PASSWORD}
VZ_MAIL_FROM=${MAIL_FROM:-Взаимно <no-reply@${DOMAIN}>}
EOF
  chmod 600 .env
  echo ".env создан (процессов бэкенда: ${WORKERS})"
fi

say "4/7 Сборка и запуск (5–10 минут при первом запуске)"
"${COMPOSE[@]}" up -d --build

say "5/7 Готовность бэкенда и пул заданий (≈ 3–5 минут)"
for _ in $(seq 1 120); do
  st=$("${COMPOSE[@]}" ps --format '{{.Service}} {{.Health}}' | awk '$1=="backend"{print $2}')
  [[ "$st" == "healthy" ]] && break
  sleep 5
done
[[ "${st:-}" == "healthy" ]] || { "${COMPOSE[@]}" logs --tail=80 backend; die "backend не стал healthy"; }
POOL_ID=$("${COMPOSE[@]}" ps -aq pool)
if [[ -n "$POOL_ID" ]]; then
  code=$(docker wait "$POOL_ID")
  [[ "$code" == "0" ]] || { "${COMPOSE[@]}" logs --tail=80 pool; die "генерация пула завершилась с кодом $code"; }
fi
echo "бэкенд готов, пул заданий сгенерирован"

if [[ "$DEMO" == "true" ]]; then
  say "6/7 Демо-мир (≈ 5–10 минут: синтетические кандидаты проходят настоящие тесты)"
  # seed_demo.py отказывается работать при VZ_ENV=prod (защита боевой БД). Стенд жюри — осознанное исключение:
  # VZ_ENV=dev задаётся только этому одноразовому процессу, сервер остаётся в prod (cookie Secure, лимиты и т.д.).
  "${COMPOSE[@]}" run --rm --no-deps -e VZ_ENV=dev -e VZ_DEMO_MODE=true pool python scripts/seed_demo.py
  "${COMPOSE[@]}" run --rm --no-deps pool cat var/demo_accounts.md > demo_accounts.local.md 2>/dev/null || true
  chmod 600 demo_accounts.local.md 2>/dev/null || true
else
  say "6/7 Демо-мир пропущен (--no-demo)"
fi

say "7/7 Проверка"
ok=false
for _ in $(seq 1 60); do
  if curl -fsS --max-time 10 "https://${DOMAIN}/api/health" | grep -q '"db":"ok"'; then ok=true; break; fi
  sleep 5   # Caddy получает сертификат при первом обращении
done
$ok || echo "HTTPS пока не отвечает: проверьте, что порты 80/443 открыты у провайдера, и '${COMPOSE[*]} logs caddy'"
curl -fsS --max-time 10 "https://${DOMAIN}/api/health" || true
cat <<EOF

Готово.
  Стенд:            https://${DOMAIN}/
  Вход жюри:        https://${DOMAIN}/jury
  Документация API: https://${DOMAIN}/api/docs
  Учётные записи:   ${ROOT}/demo_accounts.local.md (не публиковать)
  Журналы:          ${COMPOSE[*]} logs -f backend
  Обновить код:     git pull && sudo bash deploy/bootstrap.sh
EOF
