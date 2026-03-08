import colorsys
import json
import logging
import socket
import time
from typing import Any

# These strings contain special control characters:
# \x01 is the SOH (Start of Header)
# \x1e is the RS (Record Separator)

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

KEY = "74b2925b4a21da"  # Key for decrypting url message
KEY2 = "b6d592c4a783e1b6"  # Key for encrypting OTP to send to server
COLOR_MAX = (1 << 32) - 1  # All HSV values are scaled to 2^32


def hsl_to_hsv(h: int, s: int, l: int) -> tuple[int, int, int]:
    """Converts HSL to HSV, HSL values are 0 to 255"""
    r, g, b = colorsys.hls_to_rgb(h / 255, l / 255, s / 255)
    h_final, s_final, v_final = colorsys.rgb_to_hsv(r, g, b)
    return round(h_final * 255), round(s_final * 255), round(v_final * 255)


def xor_decrypt(hex_ciphertext: str, hex_key: str) -> str:
    """Decrypt hex cipher text (1 byte = 1 char) by xoring with a cyclic hex string key"""
    ciphertext, key = bytes.fromhex(hex_ciphertext), bytes.fromhex(hex_key)
    decrypted = bytearray()

    for i in range(len(ciphertext)):
        char = ciphertext[i] ^ key[i % len(key)]
        decrypted.append(char)
    return decrypted.decode("utf-8", errors="replace")


def xor_encrypt(plaintext: str, hex_key: str) -> str:
    """
    Encrypt hex cipher text (1 byte = 1 char) by xoring with a
    cyclic hex string key then returning as hex string
    """
    data, key = plaintext.encode("utf-8"), bytes.fromhex(hex_key)
    encrypted = bytearray()

    for i in range(len(data)):
        encrypted.append(data[i] ^ key[i % len(key)])
    return encrypted.hex()


class ServerMessage:
    def __init__(self, msg_bytes: bytes):
        parts = msg_bytes.decode("utf-8", errors="ignore").strip("\x00\x15\x01").split("\x1e")
        data = {k.lstrip("$"): v for p in parts if "=" in p for k, v in [p.split("=", 1)]}

        self.command = data.get("command")
        self.serial = data.get("serial")

        details = data.get("detail", "")
        self.detail = json.loads(details) if details.startswith(("{", "[")) else details

    def __repr__(self):
        return f"Message({self.command}, SN:{self.serial})"


class CSPClient:
    """
    Reverse engineered mobile client for the CSP
    smart phone connect app API (tested with CSP4)
    """

    def __init__(self, url: str, sync_rate=0.25):
        """
        :param url: Url from the QR code when you click File > Connect to Smartphone in CSP
        :param sync_rate: How often to poll in seconds (float). If set too high csp will dc
        """

        self.url = url.strip()
        decrypted_url = xor_decrypt(url.split("?s=")[1], KEY)
        logger.debug(f"{decrypted_url=}")
        split_url = decrypted_url.split("\t")

        self.ips, self.port, self.otp, self.version = split_url
        self.port = int(self.port)
        self.encrypted_otp = xor_encrypt(self.otp, KEY2)
        self.host = self.ips.split(",")[0]

        logger.debug(f"{self.ips=} => {self.host=}:{self.port}")
        logger.debug(f"{self.otp=} => {self.encrypted_otp}")
        logger.debug(f"{self.version=}")

        # State
        self.color_space = "HSV"  # or HSL
        self.hsv_main = [0, 0, 0]  # Autoconverted to hsv, normalized from 0 to 255
        self.hsv_sub = [0, 0, 0]  # Autoconverted to hsv, normalized from 0 to 255
        self.cached_current_color = [0, 0, 0, 1]  # HSVA for current selected color, updated immediately when set color is called

        self.color_is_main = True
        self.brush_size = 0  # in px
        self.brush_opacity = 0  # 0 to 1
        self.transparent_color = False
        self.sync_rate = sync_rate
        self.connected = False

    def _getmsg(self) -> ServerMessage | None:
        """Read message from server as ServerMessage"""
        data = self.client_socket.recv(4096)
        return ServerMessage(data) if data else None

    def _sendcmd(self, command: str, detail: Any = ""):
        """Send command to server with optional json args"""
        self.serial += 1
        if isinstance(detail, (list, dict)):
            detail = json.dumps(detail, separators=(",", ":"))

        parts = ["\x01$tcp_remote_command_protocol_version=1.0", f"$command={command}", f"$serial={self.serial}", f"$detail={detail}"]
        try:
            self.client_socket.sendall(("\x1e".join(parts) + "\x1e\x00").encode("utf-8"))
        except OSError:
            self.client_socket.close()
            self.connected = False

    def _sendheartbeat(self):
        """Heartbeat must be sent every second"""
        self._sendcmd("TellHeartBeat")

    def _send_uisync(self):
        """Send command to request color UI + brush size & opacity state sync"""
        self._sendcmd("SyncColorCircleUIState")

    def setcolor(self, h: int, s: int, v: int, *, q_hsl: bool = False, color_index: int | None = None, transparent: bool = False):
        """
        Generates an HSV or HLS byte string for the tcp_remote_command protocol
        HSL or HSV are in [0, 255]. HSL (HLS by CSP) is used when the triangle
        color picker is enabled

        :param q_hsl: Use triangle color picker instead
        :param color_index: Which color to set, 0 = main, 1 = sub, defaults to current selection in csp
        :param transparent: If true sets color to "erase" (transparent)
        """
        scale = COLOR_MAX / 255  # Color values in [1, 1<<32)
        self.cached_current_color = [h, s, v, self.cached_current_color[3]]
        h, s, v = [round(x * scale) for x in (h, s, v)]
        if color_index is None:
            color_index = 0 if self.color_is_main else 1

        keys = ["HLSColorH", "HLSColorS", "HLSColorL"] if q_hsl else ["HSVColorH", "HSVColorS", "HSVColorV"]
        detail = {
            keys[0]: h,
            keys[1]: s,
            keys[2]: v,
            "ColorSpaceKind": "HLS" if q_hsl else "HSV",
            "IsColorTransparent": transparent,
            "ColorIndex": color_index,
        }
        self._sendcmd("SetCurrentColor", detail)

    def set_brush_opacity(self, opacity: float):
        """Set opacity [0, 1]"""
        opacity = max(0, min(1, opacity))
        self._sendcmd("SetAlpha", round(opacity * 100))

    def connect(self):
        self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.serial = 0

        try:
            self.client_socket.connect((self.host, self.port))
            logger.debug(f"[*] Connected to {self.host}:{self.port}")

            # Do auth, parameters are json [version, encrypted otp with KEY2,
            # encrypted random 8 char ascii password (not used by server)]
            self._sendcmd("Authenticate", [self.version, self.encrypted_otp, "8bb5f2eccdd2c997"])
            auth_resp = self._getmsg()
            if auth_resp.detail["AuthErrorReason"] == "PasswordMismatch":
                raise RuntimeError("Failed to auth ('PasswordMismatch'), is the url up to date?")
            self._sendheartbeat()
            self.connected = True

            while True:
                msg = self._getmsg()
                if not msg:
                    logger.info("Connection closed by server")
                    break

                # Process messages
                if msg.command == "SyncColorCircleUIState":
                    self.color_space = msg.detail["ColorSelectionModel"]
                    keys2 = (
                        ["HLSColorSubH", "HLSColorSubS", "HLSColorSubL"]
                        if self.color_space != "HSV"
                        else ["HSVColorSubH", "HSVColorSubS", "HSVColorSubV"]
                    )
                    keys1 = [x.replace("ColorSub", "ColorMain") for x in keys2]
                    self.hsv_main = [round(msg.detail[k] / COLOR_MAX * 255) for k in keys1]
                    self.hsv_sub = [round(msg.detail[k] / COLOR_MAX * 255) for k in keys2]

                    if self.color_space != "HSV":
                        self.hsv_main = hsl_to_hsv(*self.hsv_main)
                        self.hsv_sub = hsl_to_hsv(*self.hsv_sub)

                    self.color_is_main = msg.detail["CurrentColorIndex"] == 0
                    self.brush_size = msg.detail.get("CurrentToolBrushSize", 0)
                    self.brush_opacity = msg.detail.get("CurrentToolAlphaPercent", 0) / 100
                    self.transparent_color = bool(msg.detail.get("IsColorTransparent"))
                    self.cached_current_color = [*(self.hsv_main if self.color_is_main else self.hsv_sub), self.brush_opacity]

                self._sendheartbeat()
                self._send_uisync()
                time.sleep(self.sync_rate)

        except ConnectionRefusedError as e:
            logger.error(f"Could not connect to the server: {e}")
        except KeyboardInterrupt:
            logger.warning("\n[*] Keyboard interrupt")
        except:
            print("ERROR: Latest msg:", msg.command, msg.serial, msg.detail)
            raise
        finally:
            self.client_socket.close()


if __name__ == "__main__":
    import argparse
    import threading
    import traceback

    parser = argparse.ArgumentParser("Sample client")
    parser.add_argument("url", help="URL for connect to smartphone, ie https://companion.clip-studio.com/rc/en-us?s=...")
    args = parser.parse_args()

    s = CSPClient(args.url)
    t = threading.Thread(target=s.connect, daemon=True)
    t.start()

    time.sleep(1) # Give some time to connect

    print("Simple client, type a command. Commands:")
    print("  opacity/o [0-1]                      - Set brush opacity to given value")
    print("  hsl/c [h 0-255] [s 0-255] [v 0-255]  - Set color to given HSV or HSL value")
    print("  print/p                              - Print current state")

    while True:
        userargs = input("> ").split()
        try:
            command = userargs[0]
            if command in ("opacity", "o"):
                s.set_brush_opacity(float(userargs[1]))
            elif command in ("hsl", "c", "color"):
                s.setcolor(int(userargs[1]), int(userargs[2]), int(userargs[3]))
            elif command in ("p", "print"):
                print(f"Color space      : {s.color_space}")
                print(f"HSV Main         : {s.hsv_main}")
                print(f"HSV Sub          : {s.hsv_sub}")
                print(f"Is main selected : {s.color_is_main}")
                print(f"Brush size       : {s.brush_size}")
                print(f"Brush opacity    : {s.brush_opacity}")
                print(f"Is color eraser  : {s.transparent_color}")
            else:
                print(f"Unrecognized command: '{command}'")
        except Exception:
            traceback.print_exc()
