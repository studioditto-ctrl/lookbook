"""설정 로드.

비밀값(토큰·API 키)은 레포 밖 %USERPROFILE%\\.lookbook\\places.env 에,
나머지는 레포의 places.yaml 에 둔다. 이 레포는 공개라서 장소 기록도
레포가 아니라 같은 폴더의 places.json 에 쌓는다.
"""

import os
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
HOME_DIR = Path(os.environ.get("LOOKBOOK_HOME") or Path.home() / ".lookbook")
ENV_PATH = HOME_DIR / "places.env"
CONFIG_PATH = REPO / "places.yaml"

SECRET_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "ANTHROPIC_API_KEY",
)
# 없어도 돈다. 넣으면 국내 장소의 주소·좌표·동네 상권이 정확해진다.
OPTIONAL_KEYS = ("KAKAO_REST_API_KEY",)


def load_env(path=ENV_PATH):
    """KEY=VALUE 형식 파일을 읽어 os.environ 에 없는 값만 채운다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


LOCAL_CONFIG_PATH = HOME_DIR / "places.yaml"


def _merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=CONFIG_PATH, local_path=LOCAL_CONFIG_PATH):
    """레포 기본값 위에 PC 쪽 places.yaml(vault 경로 등)을 덮어쓴다."""
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if local_path.exists():
        with open(local_path, encoding="utf-8-sig") as f:
            _merge(config, yaml.safe_load(f) or {})
    config.setdefault("data_dir", str(HOME_DIR))
    return config


def missing_secrets():
    return [k for k in SECRET_KEYS if not os.environ.get(k)]
