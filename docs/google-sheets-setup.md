# Подключить автоматическую запись в таблицу

Таблица уже создана и содержит первые результаты. Эта настройка нужна только
для записи из GitHub Actions: подключение Google Drive в чате не передаёт
доступ серверным заданиям GitHub.

## 1. Создать сервисный аккаунт

1. Откройте [Google Cloud Console](https://console.cloud.google.com/).
2. Создайте проект, например `instagram-store-finder`, или выберите существующий.
3. В **APIs & Services → Library** включите **Google Sheets API**.
4. В **IAM & Admin → Service Accounts** нажмите **Create service account**.
5. Назовите аккаунт `instagram-store-finder`. Роли на весь проект назначать не нужно.
6. Откройте созданный аккаунт → **Keys → Add key → Create new key → JSON**.
7. Сохраните скачанный JSON у себя. Найдите в нём поле `client_email`.

## 2. Разрешить доступ к конкретной таблице

В таблице нажмите **Настройки доступа / Share**. Добавьте `client_email`
сервисного аккаунта как **Редактор**. Общий доступ «Все, у кого есть ссылка»
включать не требуется.

## 3. Добавить секрет GitHub

Откройте [настройки секретов репозитория](https://github.com/Mirrorby/instagram-store-finder/settings/secrets/actions).

**New repository secret**:

- Имя: `GOOGLE_SERVICE_ACCOUNT_JSON`.
- Значение: всё содержимое скачанного JSON, включая фигурные скобки.

ID готовой таблицы уже указан в `config/destination.json`.
Для другой таблицы можно задать секрет `GOOGLE_SHEET_ID`.

## 4. Проверить запись

В [Actions](https://github.com/Mirrorby/instagram-store-finder/actions)
выберите **Discover stores → Run workflow**. Оставьте `smoke` выключенным.

После успешного запуска в Summary будет `persisted: true`, а в `sheets` —
число обновлённых и добавленных строк. Следующие плановые запуски продолжат
очередь и не добавят повторный аккаунт.

Если запуск остановился:

| Ошибка | Что проверить |
| --- | --- |
| Нет доступа к таблице | Добавлен ли именно `client_email` из JSON как Редактор |
| Ошибка Google API | Включён ли Google Sheets API в проекте сервисного аккаунта |
| Ошибка формата ключа | Добавлен ли весь JSON в `GOOGLE_SERVICE_ACCOUNT_JSON` |
| Неправильные заголовки | Не переименованы ли столбцы или листы таблицы |
| Источники поиска недоступны | Смотрите агрегированные ошибки в Summary; база сохранена |

Ключ используется только из Secrets. Его содержимое не печатается в логах.
Сервисный аккаунт запрашивает только область доступа Google Sheets и получает
доступ лишь к таблицам, которыми с ним поделились.

Документация библиотеки: [gspread — Service Account](https://docs.gspread.org/en/latest/oauth2.html#for-bots-using-service-account).
