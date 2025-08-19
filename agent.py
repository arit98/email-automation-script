import argparse, asyncio, os, shutil, sys, re
from dotenv import load_dotenv
from playwright.async_api import (
    async_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

def find_chrome_executable() -> str | None:
    for env_var in ("PLAYWRIGHT_CHROME_EXECUTABLE", "CHROME_PATH", "CHROME_EXECUTABLE"):
        env_path = os.getenv(env_var)
        if env_path and os.path.exists(env_path):
            return env_path

    for candidate in (
        "chrome",
        "google-chrome",
        "chrome.exe",
        "chromium",
        "chromium-browser",
        "google-chrome-stable",
    ):
        path = shutil.which(candidate)
        if path:
            return path

    if sys.platform.startswith("win"):
        possible = [
            os.path.join(
                os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                "Google",
                "Chrome",
                "Application",
                "chrome.exe",
            ),
            os.path.join(
                os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
                "Google",
                "Chrome",
                "Application",
                "chrome.exe",
            ),
            os.path.join(
                os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
                "Google",
                "Chrome",
                "Application",
                "chrome.exe",
            ),
        ]
        for p in possible:
            if os.path.exists(p):
                return p
    elif sys.platform == "darwin":
        mac_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if os.path.exists(mac_path):
            return mac_path
    else:
        linux_paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]
        for p in linux_paths:
            if os.path.exists(p):
                return p
    return None


async def understand_instruction(text: str):
    recipient, body = None, ""
    if " to " in text:
        try:
            recipient = text.split(" to ", 1)[1].split()[0].strip()
        except Exception:
            pass
    if "saying" in text:
        body = text.split("saying", 1)[1].strip()
        if (body.startswith("'") and body.endswith("'")) or (
            body.startswith('"') and body.endswith('"')
        ):
            body = body[1:-1]
    subject = (body[:30] + ("..." if len(body) > 30 else "")) if body else None
    return recipient, subject, body


async def send_with_gmail(page, recipient, subject, body):
    print("✉️ Composing (Gmail)...")
    await page.goto("https://mail.google.com", wait_until="domcontentloaded")
    await page.wait_for_selector("div[role='button']", timeout=30000)

    compose_button = page.locator(
        "div[role='button'][gh='cm'], div[role='button']:has-text('Compose')"
    )
    await compose_button.first.click()

    await page.wait_for_selector("input[aria-label='To recipients']", timeout=20000)
    await page.fill("input[aria-label='To recipients']", recipient)
    await page.fill("input[aria-label='Subject']", subject)
    print("Subject: ",subject)
    await page.fill("div[aria-label='Message Body']", body)

    await page.wait_for_selector(
        "div[role=button][data-tooltip*='Send']", timeout=10000
    )
    await page.locator("div[role=button][data-tooltip*='Send']").first.click()

    await asyncio.sleep(5)

    print("☑️ Gmail email sent.")


async def login_gmail(page, email: str, password: str):
    try:
        await page.goto("https://accounts.google.com/ServiceLogin")
        await page.fill("input[type='email']", email)
        await page.click("#identifierNext, button:has-text('Next')")
        await page.wait_for_selector("input[type='password']", timeout=10000)
        await page.fill("input[type='password']", password)
        await page.click("#passwordNext, button:has-text('Next')")
        await page.wait_for_load_state("networkidle")
        await page.goto("https://mail.google.com")
        await page.wait_for_selector("div[role='button']", timeout=15000)
        print("🔐 Gmail automated login succeeded.")
    except PlaywrightTimeoutError:
        print("⚠️ Gmail login timed out — manual login may be required.")
    except Exception as exc:
        print(f"⚠️ Gmail automated login failed: {exc}")


def detect_subject_with_genai(email_text: str) -> str:
    # impoort api key
    api_key = os.environ.get("GOOGLE_API_KEY")
    # chexing api key
    if api_key:
        os.environ.pop("GOOGLE_API_KEY", None)
    try:
        import google.generativeai as genai

        if api_key:
            genai.configure(api_key=api_key)

        prompt = (
            "act as a text extractor and return a concise email subject (max 80 characters)"
            " from the following email body. Return only the subject line, no extra text.\n\n"
            f"Email:\n{email_text}"
        )

        model = genai.GenerativeModel(model_name="gemini-1.5-flash-002")
        resp = model.generate_content(prompt)

        # return response 
        text = ""
        if isinstance(resp, dict):
            if resp.get("candidates"):
                text = resp["candidates"][0].get("content", "")
            else:
                text = resp.get("output", "") or resp.get("text", "")
        else:
            text = getattr(resp, "output", None) or getattr(resp, "text", None) or ""

        if text:
            return text.strip().strip('"\'')
    except ModuleNotFoundError:
        # gen ai installed or not
        pass
    except Exception as exc:
        print(f"⚠️ GenAI client call failed: {exc}")

    # no generated response
    if not email_text:
        return "(No subject)"
    # searching using regex
    m = re.search(r"^\s*(.*?)[\.\!\?](?:\s|$)", email_text.strip(), flags=re.DOTALL)
    if m:
        first_sentence = m.group(1).strip()
        if first_sentence:
            return first_sentence

    # checking punctuation
    for sep in ("\n\n", "\n"):
        if sep in email_text:
            first = email_text.split(sep, 1)[0].strip()
            if first:
                return first[:80].strip()

    words = email_text.split()
    return " ".join(words[:8]).strip() if words else "(No subject)"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("instruction", nargs="+")
    args = parser.parse_args()

    instrution = " ".join(args.instruction)

    body = None

    if " saying" in instrution:
        body = instrution.split("saying", 1)[1].strip()
        if (body.startswith("'") and body.endswith("'")) or (
            body.startswith('"') and body.endswith('"')
        ):
            body = body[1:-1]
    recipient = None
    if " to " in instrution:
        recipient = instrution.split(" to ", 1)[1].split()[0]

    load_dotenv()
    email = os.getenv("EMAIL")
    password = os.getenv("PASSWORD")
    subject = detect_subject_with_genai(body)

    async with async_playwright() as p:
        chrome_exec = find_chrome_executable()
        if chrome_exec:
            context = await p.chromium.launch_persistent_context(
                user_data_dir="./gmail_session",
                headless=False,
                executable_path=chrome_exec,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
            )
        else:
            print("⚠️ Chrome/Chromium not found; using bundled Chromium.")
            context = await p.chromium.launch_persistent_context(
                user_data_dir="./gmail_session",
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
                ignore_default_args=["--enable-automation"],
            )
        page = await context.new_page()
        await page.add_init_script(
            """() => {
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
            }"""
        )
        await page.set_extra_http_headers(
            {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            }
        )

        await page.goto("https://mail.google.com")
        if not await page.locator("div[role='button'][gh='cm']").count():
            if email and password:
                print("🔐 Attempting automated Gmail login using .env credentials...")
                await login_gmail(page, email, password)
            else:
                print("⚠️ Please log in manually. Your session will be saved.")
                await page.wait_for_selector("div[role='button'][gh='cm']", timeout=0)

        # checking subject generated
        if body:
            print(f"🤖 Generated subject: {subject}")

        await send_with_gmail(page, recipient, subject, body)
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
