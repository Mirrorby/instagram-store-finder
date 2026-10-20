#!/bin/zsh
set -eu
cd "${0:A:h}/.."
printf 'Instagram username [the.jewelry.shop]: '
IFS= read -r demo_account
demo_account=${demo_account:-the.jewelry.shop}
printf 'Ключ панели Delokay (ввод скрыт): '
IFS= read -rs PLATFORM_API_KEY
printf '\n'
export PLATFORM_API_KEY
export STORE_API_ORIGIN=https://catalog-factory.nikita-karpof.workers.dev
.venv/bin/python -m store_finder demos --accounts "$demo_account" --limit 1 --language auto --currency BYN --output output/demos-v2 --cdp-endpoint ws://127.0.0.1:9222/devtools/browser --publish --update-demo-profile
