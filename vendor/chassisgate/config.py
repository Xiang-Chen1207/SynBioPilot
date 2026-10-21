from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent
ENV_PATH = REPO_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    model: str

def get_settings() -> Settings:
    return Settings(
        api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro"),  # paper: all LLM calls use DeepSeek-V4pro
    )
