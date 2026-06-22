from src.browser.playwright_adapter import PlaywrightBrowserAdapter

b = PlaywrightBrowserAdapter()
b.navigate("https://kwork.ru/projects?c=41")
p = b._ensure_page()
print("url", p.url)
print("title", p.title())
links = p.evaluate('document.querySelectorAll("a[href*=\'/projects/\']").length')
print("links", links)
html = b.snapshot()
print("html_len", len(html))
Path = __import__("pathlib").Path
Path("/opt/freelance-responder/logs/kwork_listing.html").write_text(html[:50000], encoding="utf-8")
print("saved snippet")
b.close()
