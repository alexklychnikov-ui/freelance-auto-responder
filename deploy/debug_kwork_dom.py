from src.browser.playwright_adapter import PlaywrightBrowserAdapter

b = PlaywrightBrowserAdapter()
b.navigate("https://kwork.ru/projects?c=41")
p = b._ensure_page()
info = p.evaluate("""() => {
  const links = [...document.querySelectorAll('a[href*="/projects/"]')].slice(0,3);
  return links.map(link => {
    const top = link.closest('.wants-card__top');
    const item = link.closest('[class*="wants-card"]');
    let p = link;
    const chain = [];
    for (let i = 0; i < 6 && p; i++) { chain.push(p.className || p.tagName); p = p.parentElement; }
    const root = top?.parentElement;
    return {
      href: link.getAttribute('href'),
      title: link.textContent.trim(),
      topClass: top?.className,
      rootClass: root?.className,
      rootTag: root?.tagName,
      price: top?.querySelector('.wants-card__price')?.textContent?.replace(/\\s+/g,' ').trim(),
      chain,
    };
  });
}""")
for x in info:
    print(x)
b.close()
