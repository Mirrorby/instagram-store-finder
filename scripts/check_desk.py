"""Read/live UI checks. Restores the one local note used to check persistence."""
from store_finder.browser import BrowserProfiles

with BrowserProfiles(attach_chrome=True, cdp_endpoint='ws://127.0.0.1:9222/devtools/browser') as browser:
    page = next((p for p in browser.context.pages if p.url.startswith('http://127.0.0.1:8765')), None)
    if page is None:
        page = browser.context.new_page()
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.set_viewport_size({'width': 1440, 'height': 1000})
    page.goto('http://127.0.0.1:8765/', wait_until='networkidle')
    page.locator('#search').fill('the.jewelry.shop')
    page.locator('[data-account="the.jewelry.shop"]').click()
    page.locator('#detail-message').wait_for()
    assert page.locator('#detail-message').input_value().startswith('Hi!')
    assert page.locator('.product-photos img').count() == 11
    original = page.locator('#detail-note').input_value()
    try:
        page.locator('#detail-note').fill(original + '\nПроверка сохранения панели')
        page.locator('#save-account').click()
        page.wait_for_timeout(400)
        page.reload(wait_until='networkidle')
        page.locator('#search').fill('the.jewelry.shop')
        page.locator('[data-account="the.jewelry.shop"]').click()
        assert page.locator('#detail-note').input_value() == original + '\nПроверка сохранения панели'
    finally:
        page.locator('#detail-note').fill(original)
        page.locator('#save-account').click()
        page.wait_for_timeout(300)
    page.locator('#detail-dialog .close-dialog').click()
    page.locator('#search').fill('accessory106')
    assert page.locator('#accounts tr').count() == 1
    page.locator('[data-account="accessory106"]').click()
    assert page.locator('.product-photos img').count() == 25
    preview = browser.context.new_page()
    try:
        preview.goto('http://127.0.0.1:8765/preview/accessory106', wait_until='networkidle')
        assert preview.locator('html').get_attribute('lang') == 'ja'
        assert preview.locator('.photos img').count() == 25
        preview.locator('.photos img').evaluate_all('(images) => images.forEach(i => i.loading = "eager")')
        for _ in range(50):
            if preview.locator('.photos img').evaluate_all('(images) => images.every(i => i.complete && i.naturalWidth > 0)'):
                break
            preview.wait_for_timeout(200)
        else:
            print(preview.locator('.photos img').evaluate_all('(images) => images.filter(i => !i.naturalWidth).slice(0,3).map(i => ({src:i.currentSrc,complete:i.complete}))'))
            print('image response',preview.request.get(preview.locator('.photos img').first.get_attribute('src')).status)
            raise AssertionError('Local preview images did not load')
        assert '価格はお問い合わせください' in preview.locator('body').inner_text()
    finally:
        preview.close()
    page.locator('#detail-dialog .close-dialog').click()
    page.locator('#search').fill('')
    page.locator('#open-settings').click()
    assert page.locator('input[name="api_key"]').get_attribute('type') == 'password'
    page.locator('#settings-dialog .close-dialog').click()
    result = page.evaluate('''async () => {
      const r = await fetch('/api/settings', {method:'POST',headers:{'Content-Type':'application/json'},body:'{"auto":true}'});
      return r.status;
    }''')
    assert result == 403
    assert page.locator('#language-bars .lang-row').count() >= 10
    assert '230' in page.locator('#language-summary').inner_text()
    assert page.locator('.brand-image').evaluate('(img) => img.complete && img.naturalWidth > 0')
    page.locator('#language-filter').select_option('ru')
    assert '37' in page.locator('#list-count').inner_text()
    assert page.locator('#accounts tr').count() == 30
    page.locator('#next-page').click()
    assert page.locator('#accounts tr').count() == 7
    page.locator('#language-filter').select_option('all')
    assert not errors, errors
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path='output/desk/desk-desktop.png', full_page=False)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path='output/desk/desk-mobile.png', full_page=False)
    page.set_viewport_size({'width': 1440, 'height': 1000})
    print('Passed: cards, 36 local product images, English draft, saved notes, search, settings, CSRF, mobile width, no JS errors')
