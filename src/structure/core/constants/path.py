from pathlib import Path

CACHE_FOLDER = Path(__file__).parent.parent / ".cache"
LOG_FOLDER = Path(__file__).parent.parent / "logs"
PROJECT_ROOT = Path(__file__).parent.parent.parent
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = PROJECT_ROOT / "src" / "structure"
