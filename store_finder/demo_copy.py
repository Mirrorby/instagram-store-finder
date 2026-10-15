"""Shared local demo interface copy and outreach drafts."""
import json
from pathlib import Path
COPY = json.loads(Path(__file__).with_name('demo_locales.json').read_text())
def demo_text(value, language):
    if language == 'ru':
        return {'Product':'Товар','Catalog':'Каталог','Confirm option':'Уточнить вариант','Price on request':'Цена по запросу'}.get(value,value)
    return COPY['ui'].get(language,{}).get(value,value)
