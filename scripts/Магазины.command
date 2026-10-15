#!/bin/zsh
set -eu
cd "${0:A:h}/.."
if [[ ! -x .venv/bin/python ]]; then
  printf 'Сначала установите зависимости по docs/local-desk.md\n'
  exit 1
fi
if /usr/bin/curl -fsS --max-time 2 http://127.0.0.1:8765/api/state >/dev/null 2>&1; then
  /usr/bin/open http://127.0.0.1:8765
  exit 0
fi
(sleep 1; /usr/bin/open http://127.0.0.1:8765) &
exec .venv/bin/python -m store_finder desk
