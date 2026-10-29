#!/bin/zsh
cd "${0:A:h:h}" || exit 1
umask 077
if [[ ! -x .auth/telegram-venv/bin/python ]]; then
  mkdir -p .auth
  python3 -m venv .auth/telegram-venv || exit 1
fi
if ! .auth/telegram-venv/bin/python -c 'import telethon, gspread' 2>/dev/null; then
  .auth/telegram-venv/bin/python -m pip install -r requirements-telegram.txt || exit 1
fi
.auth/telegram-venv/bin/python -m store_finder.telegram_members "$@"
result=$?
echo
read "?Нажмите Enter, чтобы закрыть окно…"
exit $result
