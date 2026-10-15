"""Choose a supported storefront language and extract explicit contact links."""
import re
from urllib.parse import parse_qs, urlparse


def account_language(lead):
    from .account_languages import analyze_language
    return analyze_language(lead, include_search=False)['demo_language']


def account_contacts(lead):
    result = {'instagram': lead['username'], 'telegram': '', 'whatsapp': ''}
    values = list(lead.get('website_urls', []))
    values += re.findall(r'(?:https?://)?(?:t\.me|telegram\.me|wa\.me|api\.whatsapp\.com|web\.whatsapp\.com)/[^\s<>]+', lead.get('biography', ''), re.I)
    for value in values:
        if not isinstance(value, str):
            continue
        parsed = urlparse(value if '://' in value else 'https://' + value)
        try:
            port = parsed.port
        except ValueError:
            continue
        if parsed.scheme != 'https' or parsed.username or parsed.password or port not in {None, 443}:
            continue
        host = (parsed.hostname or '').lower().removeprefix('www.')
        path = parsed.path.strip('/')
        if host in {'t.me', 'telegram.me'} and re.fullmatch(r'[a-zA-Z0-9_]{5,32}', path) and path.lower() not in {'share', 'joinchat', 'addstickers', 'addemoji', 'proxy', 'socks', 'login'}:
            result['telegram'] = result['telegram'] or path
        phone = path if host == 'wa.me' else (parse_qs(parsed.query).get('phone') or [''])[0] if host in {'api.whatsapp.com', 'web.whatsapp.com'} and path == 'send' else ''
        phone = phone.removeprefix('+')
        if re.fullmatch(r'\d{7,15}', phone):
            result['whatsapp'] = result['whatsapp'] or phone
    return result
