# Проверка Instagram на Mac

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
