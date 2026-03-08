import pyautogui
from pyzbar.pyzbar import decode


def get_csp_url_from_screen() -> str | None:
    """Extract csp companion app urls from on-screen qr codes (takes screenshot automatically)"""
    screenshot = pyautogui.screenshot()
    qr_codes = decode(screenshot)

    if not qr_codes:
        print("No QR code found on screen.")
        return None

    for url in iter(qr.data.decode("utf-8") for qr in qr_codes):
        if url.startswith("https://companion.clip-studio.com/rc"):
            return url
    return None


if __name__ == "__main__":
    print(get_csp_url_from_screen())
