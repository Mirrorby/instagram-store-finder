from datetime import datetime, timezone


def qualify(lead: dict, config: dict):
    reasons = []
    current = datetime.now(timezone.utc)
    latest = lead.get("last_post_at")
    days = max(0, (current - datetime.fromisoformat(latest)).days) if latest else None
    lead["days_since_post"] = days
    if days is not None and days <= config["active_within_days"]:
        lead["activity_status"] = "active"
    elif days is not None and lead.get("activity_complete"):
        lead["activity_status"] = "inactive"
    else:
        lead["activity_status"] = "unknown"
    followers = lead.get("followers")
    site = lead.get("website_status", "unknown")
    rejected = False
    if any(reason.get("signal") == "non_target" for reason in lead.get("reasons", [])):
        reasons.append("Услуги вместо магазина товаров")
        rejected = True
    if lead.get("is_private"):
        reasons.append("Закрытый аккаунт")
        rejected = True
    if lead["activity_status"] == "inactive":
        reasons.append(f"Нет публикаций более {config['active_within_days']} дней")
        rejected = True
    if site == "working":
        reasons.append("Есть доступный сайт")
        rejected = True
    if followers is not None and not lead.get("followers_approximate"):
        for key, comparison, label in [("min_followers", lambda x, threshold: x < threshold, "Меньше заданного числа подписчиков"),
                                       ("max_followers", lambda x, threshold: x > threshold, "Больше заданного числа подписчиков")]:
            threshold = config.get(key)
            if threshold is not None and comparison(followers, threshold):
                rejected = True
                reasons.append(label)
    if lead.get("classification") != "likely_store":
        reasons.append("Недостаточно признаков продажи товаров")
    if followers is None:
        reasons.append("Подписчики не определены")
    if lead["activity_status"] == "unknown":
        reasons.append("Актуальность публикаций не подтверждена")
    if site == "unknown":
        reasons.append("Ссылка на сайт не установлена")
    if site in {"broken", "empty", "tls_error"}:
        reasons.append("Сайт указан, не работает")
        lead["outreach_reason"] = "В профиле указан сайт, но ссылка не открывает рабочую страницу; предложить готовую витрину"
    elif site == "unreachable":
        reasons.append("Сайт не открылся — перепроверить DNS/соединение")
        lead["outreach_reason"] = "Перепроверить ссылку в браузере; при подтверждении ошибки предложить рабочую витрину"
    elif site == "missing":
        reasons.append("В профиле нет ссылки на сайт")
        lead["outreach_reason"] = "Предложить готовую витрину для заказов"
    else:
        lead["outreach_reason"] = ""
    if site in {"restricted", "temporary", "working_hub"}:
        reasons.append({"restricted": "Сайт ограничил автоматическую проверку", "temporary": "Временная ошибка сайта — нужна повторная проверка", "working_hub": "Есть страница ссылок; наличие магазина требует проверки"}[site])
    confirmed_site = lead.get("website_source") in {"profile_html", "apify", "chromium"}
    eligible = (not rejected and lead.get("classification") == "likely_store" and followers is not None
                and lead["activity_status"] == "active" and confirmed_site
                and site in {"missing", "broken", "empty", "tls_error"})
    lead["qualification"] = "rejected" if rejected else "suitable" if eligible else "review"
    lead["qualification_reason"] = "; ".join(reasons) or "Активный магазин без рабочего сайта"
    bonus = 25 if site in {"broken", "empty", "tls_error"} else 15 if site == "missing" else 0
    bonus += 10 if lead["activity_status"] == "active" else 0
    if followers is not None and config["preferred_followers_min"] <= followers <= config["preferred_followers_max"]:
        bonus += 10
    if rejected:
        bonus -= 50
    lead["score"] = min(100, max(0, lead.get("score", 0) + bonus))
