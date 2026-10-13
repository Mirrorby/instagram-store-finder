"""Transparent heuristics. Query text itself is never evidence that an account sells."""
import re
import unicodedata
from .models import own_snippet

COMMERCIAL = [
    "shop", "store", "boutique", "магазин", "заказ", "доставка", "продаж", "купить",
    "tienda", "venta", "pedido", "envío", "envíos", "loja", "venda", "encomenda", "entrega",
    "boutique", "commande", "livraison", "verkauf", "bestell", "versand", "negozio",
    "vendita", "ordine", "spedizione", "mağaza", "sipariş", "kargo", "toko", "pesan",
    "jual", "pengiriman", "sklep", "zamów", "wysył", "متجر", "طلبات", "توصيل",
    "فروشگاه", "سفارش", "ارسال", "दुकान", "ऑर्डर", "通販", "販売", "注文", "판매", "주문",
]
ORDER = [
    "dm to order", "dm for orders", "order via dm", "order by dm", "orders via dm",
    "dm for order", "dm us to order", "whatsapp", "заказ в директ", "заказы в директ",
    "заказ в direct", "заказы в direct", "пиши в директ", "pedidos por dm",
    "pedido por dm", "pedidos al dm", "commande en dm", "commandes en dm",
    "bestellung per dm", "ordini in dm", "sipariş için dm", "order via dm",
    "pesan via dm", "zamówienia dm", "للطلب", "سفارش در دایرکت", "dmで注文", "dm 주문",
]
PRODUCTS = [
    "jewelry", "jewellery", "necklace", "earring", "clothing", "clothes", "dress", "fashion",
    "bags", "handbag", "shoes", "sneakers", "candles", "candle", "ceramics", "ceramic",
    "pottery", "cosmetics", "skincare", "decor", "furniture", "toy", "toys", "accessories",
    "украшени", "бижутери", "одежд", "плать", "сумк", "обув", "свеч", "керамик",
    "косметик", "мебел", "игруш", "аксессуар", "joyas", "joyería", "ropa", "vestido",
    "bolso", "zapato", "velas", "joias", "roupa", "bolsa", "sapato", "bijoux",
    "vêtement", "sac", "chaussure", "bougie", "schmuck", "kleidung", "tasche",
    "schuhe", "kerze", "gioiell", "abbigliamento", "borsa", "scarpe", "takı", "giyim",
    "çanta", "ayakkabı", "perhiasan", "pakaian", "tas", "sepatu", "biżuteria",
    "odzież", "torb", "buty", "świec", "مجوهرات", "ملابس", "حقائب", "أحذية",
    "زیورآلات", "پوشاک", "کیف", "जूते", "कपड़े", "アクセサリー", "服", "バッグ", "주얼리", "의류", "가방",
]
NON_TARGET = ["marketing agency", "social media agency", "web design", "website design",
              "digital marketing", "online course", "life coach", "photography services",
              "салон красоты", "обучение", "онлайн курс", "маркетинговое агентство",
              "agencia de marketing", "curso online", "salon de beauté", "hair salon", "beauty salon", "barbershop", "парикмахерская"]


def normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def matches(text: str, terms: list[str]) -> list[str]:
    found = []
    for term in terms:
        term = normalized(term)
        # Boundaries prevent 'tas' matching 'fantastic' and 'shop' matching 'photoshop'.
        # Stems intentionally match suffixes, e.g. магазин/магазины and заказ/заказы.
        pattern = r"(?<!\w)" + re.escape(term)
        if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", term):
            pattern = re.escape(term)
        elif re.search(r"[\u0600-\u06ff]", term):
            pattern = r"(?<!\w)(?:ال)?" + re.escape(term)
        if re.fullmatch(r"[a-z ]+", term):
            pattern += r"(?:s|es)?(?!\w)"
        if re.search(pattern, text):
            found.append(term)
    return found


def score(lead: dict) -> dict:
    lead.pop("previous_reason_text", None)
    indexed_parts = []
    for evidence in lead["evidence"]:
        title = evidence["title"]
        marker = re.search(r"\(@" + re.escape(lead["username"]) + r"\)", title, re.I)
        if marker:
            title = title[:marker.end()]
        indexed_parts.append(title + " " + own_snippet(evidence["snippet"], lead["username"]))
    indexed = " ".join(indexed_parts)
    profile_text = lead.get("biography", "") + " " + " ".join(lead.get("recent_captions", []))
    text = normalized((lead.get("title", "") + " " + profile_text) if
                      lead.get("profile_status") == "verified" and profile_text.strip() else indexed + " " + profile_text)
    commercial, orders, products, non_target = [matches(text, terms) for terms in
                                               [COMMERCIAL, ORDER, PRODUCTS, NON_TARGET]]
    value = (25 if commercial else 0) + (35 if orders else 0) + (30 if products else 0)
    if non_target:
        value -= 45
    lead["score"] = max(0, min(100, value))
    lead["reasons"] = []
    for label, found in [("commerce", commercial), ("direct_orders", orders),
                         ("physical_products", products), ("non_target", non_target)]:
        if found:
            lead["reasons"].append({"signal": label, "matched": found[:8]})
    lead["classification"] = ("likely_store" if products and (commercial or orders)
                              and not non_target else "needs_review")
    return lead
