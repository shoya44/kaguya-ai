import re
from pathlib import Path
import os

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


# Stage 6 (home Wi-Fi only, no HTTPS/pairing yet): the iPhone's Safari sends
# an Origin like http://192.168.1.20:8765, which cannot be listed in advance.
# Only RFC1918 private-network addresses on our own fixed port are accepted;
# anything else (a public IP, a different port) is rejected same as before.
PRIVATE_ORIGIN_PATTERN = re.compile(
    r'^http://(192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|'
    r'172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}):8765$'
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / '.env',
        env_file_encoding='utf-8', extra='ignore',
    )
    gemini_api_key: SecretStr = SecretStr('')
    gemini_model: str = ''
    gemini_live_model: str = 'gemini-3.1-flash-live-preview'
    database_url: SecretStr = SecretStr('')
    # This first milestone is loopback-only, one process, one worker.
    internal_base_url: str = 'http://127.0.0.1:8765'
    backend_instance: str = ''
    data_dir: Path = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KaguyaAI'
    # http://127.0.0.1:5173 is the Vite dev server (plain-browser testing).
    # The tauri.localhost / tauri://localhost entries are the Origin the
    # Tauri webview itself sends, which differs by OS (Windows uses
    # http://tauri.localhost; macOS/Linux use tauri://localhost) — see spec
    # section 5 (loopback only) and section 3 (shared web code, Tauri-only
    # wrapper). Private-LAN origins (iPhone on the same home Wi-Fi) are
    # matched separately via PRIVATE_ORIGIN_PATTERN, not listed here.
    allowed_origins: list[str] = [
        'http://127.0.0.1:5173',
        'http://tauri.localhost',
        'https://tauri.localhost',
        'tauri://localhost',
    ]
    llm_timeout_seconds: float = 30
    max_output_tokens: int = 1024

    def origin_allowed(self, origin: str) -> bool:
        from .pc import load_config
        return (origin in self.allowed_origins or bool(PRIVATE_ORIGIN_PATTERN.match(origin))
                or origin == load_config().tailscale_origin and bool(origin))
