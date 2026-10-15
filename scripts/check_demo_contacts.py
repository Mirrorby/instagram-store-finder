"""Browser check of local storefront assets with isolated synthetic API responses."""
import json
import re
from pathlib import Path
from store_finder.browser import BrowserProfiles

root = Path(__file__).resolve().parents[2] / 'delokay'
public = root / 'public'
dictionary = re.search(r'export const english: Record<string, string> = (\{[\s\S]*?\n\});', (root / 'src/language.ts').read_text()).group(1)
script = 'const english = ' + dictionary + ';const extras = ' + (root / 'src/storefront-locales.json').read_text() + ';const STOREFRONT_LOCALES = {en:english};for(const [code,copy] of Object.entries(extras))STOREFRONT_LOCALES[code]=Object.fromEntries(Object.entries(english).map(([k,v])=>[k,copy[v]||v]));\n' + (public / 'storefront.js').read_text()
settings = {'language': 'en', 'theme': 'minimal', 'currencies': ['USD'], 'texts': {},
            'description': 'Handmade jewelry', 'deliveryMethods': ['Delivery'],
            'instagram': 'the.jewelry.shop', 'telegram': '', 'whatsapp': ''}
store = {'name': 'The Jewelry Shop', 'status': 'demo', 'acceptingOrders': False, 'settings': settings}
errors = []
with BrowserProfiles(attach_chrome=True, cdp_endpoint='ws://127.0.0.1:9222/devtools/browser') as browser:
    page = browser.context.new_page()
    def route(request):
        path = request.request.url.split('https://demo-check.invalid', 1)[-1].split('?', 1)[0]
        if path == '/api/stores/test':
            request.fulfill(json={'store': store, 'products': []})
        elif path == '/s/app.js':
            request.fulfill(body=script, content_type='application/javascript')
        elif path == '/storefront.css':
            request.fulfill(body=(public / 'storefront.css').read_text(), content_type='text/css')
        elif path == '/s/test':
            request.fulfill(body=(public / 'storefront.html').read_text(), content_type='text/html')
        else:
            request.fulfill(status=204)
    page.route('https://demo-check.invalid/**', route)
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        for lang in ('en', 'ru'):
            settings['language'] = lang
            page.goto('https://demo-check.invalid/s/test')
            page.wait_for_function("document.getElementById('hero-ig').getAttribute('href') === 'https://www.instagram.com/the.jewelry.shop/'")
            for channel in ('wa', 'tg', 'ig'):
                assert page.locator('#hero-' + channel).is_visible()
            assert page.locator('#hero-wa').get_attribute('href') is None
            assert page.locator('#hero-tg').get_attribute('aria-disabled') == 'true'
            assert page.locator('html').get_attribute('lang') == lang
            assert page.locator('#hero-wa').get_attribute('title') == ('Contact will be added' if lang == 'en' else 'Контакт будет добавлен')
        settings.update(telegram='my_shop', whatsapp='375291234567')
        page.reload()
        page.wait_for_function("document.getElementById('hero-tg').getAttribute('href') === 'https://t.me/my_shop'")
        assert page.locator('#hero-wa').get_attribute('href') == 'https://wa.me/375291234567'
        store['status'] = 'active'
        settings.update(telegram='', whatsapp='', instagram='')
        page.reload()
        page.wait_for_function("document.getElementById('hero-wa').hidden")
        assert not page.locator('.hero-contacts').is_visible()
        assert not errors, errors
        print(json.dumps({'result': 'passed', 'checks': ['English/Russian demos', 'visible placeholders', 'real contact URLs', 'active store without contacts', 'no JavaScript errors']}))
    finally:
        page.close()
