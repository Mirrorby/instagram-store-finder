# Проверка Instagram на Mac

## Если Instagram уже открыт в обычном Chrome

Этот режим использует существующую сессию. Новый вход и файл сессии не нужны.
Chrome должен быть версии 144 или новее.

1. Оставьте обычный Chrome открытым с рабочей сессией Instagram.
2. В адресной строке откройте `chrome://inspect/#remote-debugging`.
3. Включите **Allow remote debugging for this browser instance**.
4. В папке проекта выполните:

```sh
git pull
python -m store_finder local-test --chrome --accounts ACCOUNT1 ACCOUNT2 ACCOUNT3
```

Если Chrome покажет запрос подключения, подтвердите его. Парсер подключится
к открытому браузеру и создаст собственные временные вкладки Instagram.
Он закроет только эти вкладки; Chrome и ранее открытые вкладки останутся.
Cookies не копируются в файл и не включаются в отчёт. Google Sheets не изменяется.
После теста выключите разрешение удалённой отладки.

Если Chrome показывает **Server running at: 127.0.0.1:9222**, используйте
WebSocket-адрес напрямую, без поиска служебного файла и HTTP discovery:

```sh
python -m store_finder local-test --chrome --cdp-endpoint ws://127.0.0.1:9222/devtools/browser --accounts ACCOUNT1 ACCOUNT2 ACCOUNT3
```

В параметре `--cdp-endpoint` разрешены только локальные адреса. С ним файл
`DevToolsActivePort` и сохранённый файл сессии не требуются.

В режиме `chrome://inspect/#remote-debugging` отсутствие `/json/version`
и ответ HTTP 404 нормальны: используется WebSocket. Если указан HTTP-адрес,
парсер при discovery 404 попробует этот WebSocket-путь один раз. При отказе
в доступе или таймауте повторов не будет.

Если подключение не получилось, пришлите сообщение **Причина:**. Предупреждение LibreSSL
от requests/urllib3 относится к проверке сайтов, а подключение CDP использует
другой транспорт — оно само по себе не объясняет ошибку соединения с Chrome.

Используется локальное подключение CDP. Адрес берётся только из служебного
`DevToolsActivePort` в каталоге Chrome, подключение ограничено `127.0.0.1`.
Если у Chrome другой каталог, укажите его через `--chrome-user-data-dir`.
Каталог можно посмотреть в `chrome://version`: это родитель **Profile Path**.
Парсер не открывает базы паролей и cookies на диске.

Документация: https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session

## Вход в отдельном Chromium

При повторяющейся CAPTCHA прекратите этот способ и используйте режим обычного
Chrome выше, если в нём уже есть рабочая сессия. Сам по себе Playwright не гарантирует
успешный вход в Instagram.

Для локального теста нужны Python 3.9 или новее и копия репозитория. Если Python отсутствует,
установите его с https://www.python.org/downloads/macos/.

В Терминале перейдите в папку проекта. Для новой копии:

```sh
git clone https://github.com/Mirrorby/instagram-store-finder.git
cd instagram-store-finder
```

Для существующей копии выполните `git pull` в её папке.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-local.txt
python -m playwright install chromium
python -m store_finder login
```

Локальный режим устанавливает только Playwright и requests. Поиск магазинов
и серверная запись в Google Sheets используют полный `requirements.txt`
и Python 3.10+. Ошибка `No matching distribution found for ddgs==9.11.4`
при установке полного набора на Python 3.9 устраняется переходом на этот
локальный набор; менять системный Python для проверки браузера не требуется.

В открывшемся Chromium войдите в Instagram самостоятельно, завершите подтверждение
входа, дождитесь ленты, вернитесь в Терминал и нажмите Enter. Окно закроется.
Сессия останется в `.auth/instagram.json`, который исключён из git и доступен
только владельцу файла. Этот файл содержит доступ к аккаунту: не отправляйте
его в чат и не публикуйте.

Укажите до 10 имён аккаунтов через пробел (можно также передать ссылки на профили):

```sh
python -m store_finder local-test --accounts ACCOUNT1 ACCOUNT2 ACCOUNT3
```

Парсер откроет видимый Chromium с сохранённой сессией. Он выведет подписчиков,
ссылки и даты постов, если Instagram их отдаст. При ограничении профиля или
HTTP 429 тест остановится. Полный отчёт появится в `output/local-test.json`.
Google Sheets и её служебные счётчики этим режимом не изменяются.

Для повторного теста в новой вкладке Терминала сначала войдите в папку проекта
и выполните `source .venv/bin/activate`. Если сессия истекла, повторите команду
`python -m store_finder login`. Для разбора результатов достаточно итогового
вывода Терминала или отчёта `output/local-test.json`.
