"""Local language estimates, with profile and search evidence kept separate."""
import re
from collections import Counter
from functools import lru_cache

NAMES = {'en':'Английский','ru':'Русский','es':'Испанский','pt':'Португальский',
         'fr':'Французский','it':'Итальянский','tr':'Турецкий','pl':'Польский',
         'ja':'Японский','de':'Немецкий','ar':'Арабский','ko':'Корейский',
         'id':'Индонезийский','uk':'Украинский','ca':'Каталанский','et':'Эстонский','hi':'Хинди','unknown':'Не определён'}
SUPPORTED = ('en','ru','es','pt','fr','it','tr','pl','ja','de')


@lru_cache(maxsize=5000)
def detect_text(text):
    from langdetect import DetectorFactory, detect_langs, LangDetectException
    DetectorFactory.seed = 0
    text = re.sub(r'https?://\S+|[@#][\w.]+|[\w.+-]+@[\w.-]+', ' ', text)
    text = re.sub(r'\b[\d.,]+\s*(?:Followers|Following|Posts)\b', ' ', text, flags=re.I)
    minimum = 15 if len(re.findall(r'[а-яё]', text, re.I)) >= 15 else 25
    if sum(c.isalpha() for c in text) < minimum:
        return {'code':'unknown','confidence':0}
    try:
        candidates = detect_langs(text[:12000])
    except LangDetectException:
        return {'code':'unknown','confidence':0}
    top = candidates[0]
    return {'code':top.lang if top.prob >= .8 else 'unknown','confidence':round(top.prob,3)}


def analyze_language(lead, include_search=True):
    bio = lead.get('biography', '')
    captions = '\n'.join(p.get('caption', '') for p in lead.get('photo_posts', []))
    if not captions:
        captions = '\n'.join(lead.get('recent_captions', []))
    for source, text in (('profile', bio), ('captions', captions), ('search', '\n'.join(e.get('snippet', '') for e in lead.get('evidence', [])) if include_search else '')):
        result = detect_text(text)
        if result['code'] != 'unknown':
            code = result['code']
            return {**result, 'name': NAMES.get(code, code.upper()), 'source': source,
                    'estimated': source == 'search', 'demo_language': code if code in SUPPORTED else 'en',
                    'supported': code in SUPPORTED}
    return {'code':'unknown','name':NAMES['unknown'],'confidence':0,'source':'none','estimated':False,'demo_language':'en','supported':False}


def language_statistics(rows):
    profiles, estimates = Counter(), Counter()
    unknown = 0
    for row in rows:
        info = analyze_language(row['data'])
        row['language_info'] = info
        if info['code'] == 'unknown':
            unknown += 1
        elif info['estimated']:
            estimates[info['code']] += 1
        else:
            profiles[info['code']] += 1
    return {'total':len(rows),'profile_count':sum(profiles.values()),'estimate_count':sum(estimates.values()),'unknown':unknown,
            'languages':[{'code':code,'name':NAMES.get(code,code.upper()),'profile':profiles[code],'estimated':estimates[code],
                          'total':profiles[code]+estimates[code],'supported':code in SUPPORTED}
                         for code in sorted(set(profiles)|set(estimates),key=lambda c:-(profiles[c]+estimates[c]))]}
