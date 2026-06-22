from src.browser.playwright_adapter import PlaywrightBrowserAdapter
from src.adapters.kwork import LISTING_EXTRACTOR_JS

b = PlaywrightBrowserAdapter()
b.navigate("https://kwork.ru/projects?c=41")
p = b._ensure_page()
checks = p.evaluate("""() => ({
  articles: document.querySelectorAll('article.project-card, [data-project-id], a[href*="/projects/"]').length,
  cards: document.querySelectorAll('article.project-card').length,
  dataIds: document.querySelectorAll('[data-project-id]').length,
  links: document.querySelectorAll('a[href*="/projects/"]').length,
  sampleHref: document.querySelector('a[href*="/projects/"]')?.getAttribute('href'),
  sampleClasses: document.querySelector('a[href*="/projects/"]')?.closest('div')?.className?.slice(0,120),
})""")
print("checks", checks)
raw = b.evaluate(LISTING_EXTRACTOR_JS)
print("extractor count", len(raw) if isinstance(raw, list) else type(raw), raw[:2] if isinstance(raw, list) else raw)
b.close()
