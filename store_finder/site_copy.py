"""Editable catalogue copy based on observed facts, without invented specifications."""
import re

CATEGORIES = [
    (r'earring|серьг', 'Серьги', 'Earrings'),
    (r'necklace|pendant|колье|подвеск|кулон', 'Колье и подвески', 'Necklaces and pendants'),
    (r'bracelet|браслет', 'Браслеты', 'Bracelets'),
    (r'\bring\b|кольц', 'Кольца', 'Rings'),
    (r'jewel|украшен', 'Украшения', 'Jewelry'),
    (r'\bbag\b|сумк', 'Сумки', 'Bags'),
    (r'shirt|рубаш|футбол', 'Одежда', 'Clothing'),
    (r'cream|serum|крем|сыворот', 'Уход', 'Skin care'),
    (r'vase|ваза', 'Для дома', 'Home decor'),
]
CTA = re.compile(r'follow|subscribe|link in bio|\bdm\b|shop now|order now|подпис|пишите|ссылк.*профил|директ|закажи|скидк|распродаж|free shipping|бесплатн.*достав', re.I)
FACTS = re.compile(r'material|sterling|silver|gold|steel|cotton|linen|leather|wood|ceramic|size|length|width|weight|diameter|color|материал|серебр|золот|сталь|хлоп|льн|кож|дерев|керами|размер|длин|ширин|вес|диаметр|цвет|\b\d+(?:[.,]\d+)?\s*(?:cm|mm|inch|oz|gram|см|мм|г|кг)\b', re.I)

def clean(value):
    value = re.sub(r'https?://\S+|#[\w]+|@[\w.]+', '', value or '')
    value = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27ff]', '', value)
    return re.sub(r'\s+', ' ', value).strip(' •|—-:')

def product_copy(caption, brand, language, number=1):
    ru = language == 'ru'
    lines = [clean(line) for line in (caption or '').splitlines()]
    lines = [line for line in lines if len(line) >= 4 and not CTA.search(line)]
    category = next((r if ru else e for pattern, r, e in CATEGORIES if re.search(pattern, caption or '', re.I)), 'Товары' if ru else 'Products')
    title = next((line[:120] for line in lines if not re.match(r'^(email|тел|phone|www\.)', line, re.I)), category + ' ' + str(number))
    facts = list(dict.fromkeys(line[:300] for line in lines if FACTS.search(line)))[:8]
    if ru:
        body = f'{title} — {category.lower()} в каталоге {brand}.'
        if facts:
            body += '\n\nИнформация о товаре:\n' + '\n'.join('• ' + line for line in facts)
        body += '\n\nПеред заказом уточните доступные варианты, стоимость и наличие. Добавьте товар в заказ или свяжитесь с магазином.'
    else:
        body = f'{title} — {category.lower()} in the {brand} catalog.'
        if facts:
            body += '\n\nProduct details:\n' + '\n'.join('• ' + line for line in facts)
        body += '\n\nConfirm available options, price and availability before ordering. Add the item to your order or contact the store.'
    return title, category, body

def store_copy(brand, products, language):
    categories = list(dict.fromkeys(p['category'] for p in products))[:4]
    if language == 'ru':
        assortment = ', '.join(c.lower() for c in categories) or 'товары магазина'
        return f'{brand} — каталог: {assortment}. Изучите фотографии и описания, выберите подходящие товары и отправьте заказ. Наличие, варианты и условия покупки уточняются у магазина.'
    assortment = ', '.join(c.lower() for c in categories) or 'the store collection'
    return f'{brand} — {assortment}. Browse product photos and details, choose your items and submit an order. Confirm availability, options and purchase terms with the store.'
