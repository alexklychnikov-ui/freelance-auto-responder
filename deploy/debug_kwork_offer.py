from src.browser.playwright_adapter import PlaywrightBrowserAdapter

PROJECT_ID = "3202099"
browser = PlaywrightBrowserAdapter()
try:
    browser.navigate(f"https://kwork.ru/projects/{PROJECT_ID}")
    browser.wait_ms(2000)
    info = browser.evaluate("""() => ({
      url: location.href,
      title: document.title,
      textareas: document.querySelectorAll('textarea').length,
      inputs: document.querySelectorAll('input').length,
      selects: document.querySelectorAll('select').length,
      buttons: [...document.querySelectorAll('a, button')].slice(0, 30).map(el => ({
        tag: el.tagName,
        text: (el.textContent || '').trim().slice(0, 80),
        href: el.getAttribute('href'),
        cls: (el.className || '').slice(0, 80),
      })),
      loginHints: document.body.innerText.includes('Вход'),
      offerHints: [...document.querySelectorAll('a, button')].filter(el =>
        /предложить|отклик/i.test(el.textContent || '')
      ).map(el => (el.textContent || '').trim().slice(0, 100)),
    })""")
    print(info)
    browser.screenshot()
    Path = __import__("pathlib").Path
    Path("/opt/freelance-responder/logs/offer_debug.png").write_bytes(browser.screenshot())
    print("saved logs/offer_debug.png")
finally:
    browser.close()
