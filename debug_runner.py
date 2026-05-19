"""
debug_runner.py — Mirrors exact _run_session flow with full logging.
Run: .venv/bin/python3.12 debug_runner.py
"""
import asyncio
from playwright.async_api import async_playwright
import playwright_runner as pw

BOT_ID  = "x1752566713212"
BOT_URL = f"https://nexus.yellow.ai/liveBot/{BOT_ID}?region=&version=v3"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, slow_mo=0)

        # Mirror actual runner: fresh context per test
        ctx  = await browser.new_context(viewport={"width": 1280, "height": 800})
        page = await ctx.new_page()

        print("→ goto")
        await page.goto(BOT_URL, wait_until="networkidle", timeout=30_000)
        await asyncio.sleep(2)

        # Wait for iframe
        print("→ waiting for ym_frame")
        ym_frame = None
        for i in range(10):
            ym_frame = await pw._get_ym_frame(page)
            if ym_frame:
                print(f"  frame found at iteration {i}: {ym_frame.url}")
                break
            await asyncio.sleep(1)
        if not ym_frame:
            print("  NO FRAME FOUND")

        # Open widget
        opened = await pw._open_widget(page)
        print(f"→ _open_widget: {opened}")

        # _wait_for_stable_bot_count
        print("→ _wait_for_stable_bot_count (settle=2s, max=12s)")
        prev_count = await pw._wait_for_stable_bot_count(page)
        print(f"   prev_count = {prev_count}")
        initial = await pw._get_bot_texts(page)
        print(f"   current texts ({len(initial)}):")
        for t in initial: print(f"     • {t[:80]!r}")

        # Send first message
        msg = "Can you compare the Classic 350 and Meteor 350?"
        print(f"\n→ sending: {msg!r}")
        ok = await pw._send_message(page, msg)
        print(f"   send ok: {ok}")

        # Poll for full 90 seconds — never break early
        print("\n→ polling for response (90s, no early exit):")
        deadline = asyncio.get_event_loop().time() + 90
        last_count = -1
        while asyncio.get_event_loop().time() < deadline:
            texts = await pw._get_bot_texts(page)
            thinking = await pw._is_bot_thinking(page)
            elapsed = asyncio.get_event_loop().time() - (deadline - 90)
            count = len(texts)
            if count != last_count:
                print(f"   *** COUNT CHANGED at t={elapsed:.1f}s: {last_count} → {count}  thinking={thinking}")
                for t in texts: print(f"     • {t[:120]!r}")
                last_count = count
            else:
                print(f"   t={elapsed:.1f}s  count={count}  thinking={thinking}")
            await asyncio.sleep(1)

        # Dump all iframe elements with tag names
        print("\n→ full iframe element dump (with tags):")
        if ym_frame:
            try:
                elements = await ym_frame.evaluate("""() => {
                    const out = [];
                    for (const el of document.querySelectorAll('*')) {
                        const t = el.innerText?.trim();
                        if (t && t.length > 2 && t.length < 300 && el.children.length === 0)
                            out.push({
                                tag: el.tagName,
                                cls: el.className?.substring(0,120),
                                txt: t.substring(0,120)
                            });
                    }
                    return out;
                }""")
                for el in elements[:60]:
                    print(f"  <{el['tag'].lower()}>  cls={el['cls']!r:60s}  txt={el['txt']!r}")
            except Exception as e:
                print(f"  DUMP ERROR: {e}")

            # Also check yai-group elements specifically
            print("\n→ yai-group elements:")
            try:
                groups = await ym_frame.locator("[class*='yai-group']").all()
                print(f"  total yai-group elements: {len(groups)}")
                for i, g in enumerate(groups):
                    cls = await g.get_attribute("class") or ""
                    txt = (await g.inner_text()).strip()[:120]
                    print(f"  [{i}] items-start={('yai-items-start' in cls)}  items-end={('yai-items-end' in cls)}  txt={txt!r}")
            except Exception as e:
                print(f"  yai-group ERROR: {e}")
        else:
            print("  no ym_frame available")

        await ctx.close()
        await browser.close()
        print("\n✓ done")

asyncio.run(main())
