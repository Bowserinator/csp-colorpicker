import json
from pathlib import Path


class Config:
    def __init__(self, path: str, window_stats_path: str, web_data_path: str):
        # Defaults
        self.port: int = 8765
        self.host: str = "127.0.0.1"
        self.web_base_dir: Path = Path(__file__).parent / "web"
        self.client_refresh_rate_s: float | int = 0.05
        self.auto_hide: bool = True

        self.window_title = "CSP Colorpicker"
        self.icon_path = Path(__file__).parent / "assets" / "icon.png"
        self.win_pos: list[int] | None = None
        self.win_size: list[int] | None = None

        self.path = Path(path)
        self.window_stats_path = Path(window_stats_path)
        self.web_data_path = Path(web_data_path)
        self.web_data_path.parent.mkdir(exist_ok=True)

        self._load_main_config()
        self._load_win_pos()
        if not self.path.exists():
            self.save()

    def _load_main_config(self):
        if not self.path.exists():
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            schema = {"port": int, "host": str, "web_base_dir": (str, Path), "client_refresh_rate_s": (float, int), "auto_hide": bool}
            for key, expected_type in schema.items():
                if key in data and isinstance(data[key], expected_type):
                    setattr(self, key, data[key])
        except (OSError, json.JSONDecodeError, PermissionError):
            pass

    def _load_win_pos(self):
        if not self.window_stats_path.exists():
            return
        try:
            with open(self.window_stats_path, encoding="utf-8") as f:
                data = json.load(f)
                self.win_pos = [data["x"], data["y"]]
                self.win_size = [data["width"], data["height"]]
        except (json.JSONDecodeError, OSError):
            pass

    def save(self):
        """Saves current configuration to the JSON path."""
        data = {
            "port": self.port,
            "host": self.host,
            "web_base_dir": str(self.web_base_dir),
            "client_refresh_rate_s": self.client_refresh_rate_s,
            "auto_hide": self.auto_hide,
        }
        try:
            self.path.parent.mkdir(exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except OSError as e:
            print(f"Failed to save config: {e}")

    def save_window_position(self, winx: int, winy: int, win_width: int, win_height: int):
        """Saves window dimensions to the secondary stats file."""
        stats = {"x": winx, "y": winy, "width": win_width, "height": win_height}
        try:
            self.window_stats_path.parent.mkdir(exist_ok=True)
            with open(self.window_stats_path, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=4)
        except OSError as e:
            print(f"Failed to save window stats: {e}")


config = Config("./config/config.json", "./config/.window.position", "./config/web_data")
