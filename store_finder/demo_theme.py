"""Conservative, explainable template selection from observed account text."""
import re
import unicodedata

TERMS = {
    'fashion': 'clothing clothes fashion apparel dress dresses jewelry jewellery accessories shoes bags rings earrings necklaces bracelets handmadejewelry одежда платья украшения бижутерия ювелирный обувь сумки аксессуары ropa vestidos joyas zapatos moda roupas joias sapatos bijoux vêtements chaussures abbigliamento gioielli scarpe kleidung schmuck schuhe odzież biżuteria buty giyim takı ayakkabı ファッション ジュエリー アクセサリー 服 バッグ',
    'home': 'furniture homeware decor ceramics pottery bedding candles интерьер мебель декор керамика посуда свечи текстиль muebles decoración cerámica velas móveis decoração cerâmica bougies meubles décoration céramique arredamento ceramica candele möbel keramik deko świece meble ceramika mobilya dekorasyon seramik 家具 インテリア 陶器 食器 キャンドル',
    'beauty': 'cosmetics skincare makeup perfume perfumes fragrance beauty косметика парфюм парфюмерия макияж крем сыворотка cosméticos maquillaje perfumes cosméticos maquiagem cosmétique maquillage parfum cosmetici profumi trucco kosmetik parfum makijaż kosmetyki perfumy kozmetik makyaj parfüm コスメ 化粧品 スキンケア 香水',
}


def normalize(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c))


def choose_theme(lead, requested='auto'):
    if requested != 'auto':
        if requested not in {'minimal', *TERMS}:
            raise ValueError('Неизвестный шаблон')
        return {'theme': requested, 'reason': 'Выбран вручную', 'matches': []}
    profile = ' '.join(str(lead.get(k, '')) for k in ('title', 'biography'))
    captions = [p.get('caption', '') for p in lead.get('photo_posts', [])][:12]
    if not captions:
        captions = lead.get('recent_captions', [])[:12]
    scores, matches = {}, {}
    for theme, words in TERMS.items():
        matches[theme] = []
        score = 0
        for term in words.split():
            pattern = normalize(term)
            if not re.search(r'[\u3040-\u9fff]', term):
                pattern = r'(?<![^\W_])' + re.escape(pattern) + r'(?![^\W_])'
            found = False
            for text, weight in [(profile, 3)] + [(c, 1) for c in captions]:
                if re.search(pattern, normalize(text)):
                    score += weight
                    found = True
            if found:
                matches[theme].append(term)
        scores[theme] = score
    ranked = sorted(scores, key=scores.get, reverse=True)
    winner, second = ranked[:2]
    if scores[winner] < 3 or scores[winner] <= scores[second] * 1.4:
        return {'theme': 'minimal', 'reason': 'Ассортимент не определён однозначно', 'matches': []}
    return {'theme': winner, 'reason': 'По названию, описанию и подписям товаров', 'matches': matches[winner]}
