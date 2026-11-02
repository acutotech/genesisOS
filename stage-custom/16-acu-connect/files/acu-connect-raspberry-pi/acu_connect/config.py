import json
import os
from pathlib import Path


def config_dir():
    return Path(os.environ.get("ACU_CONFIG_DIR") or Path.home() / ".config" / "acu-connect")


def config_file():
    return config_dir() / "agent.json"


def load_config():
    try:
        return json.loads(config_file().read_text())
    except (OSError, ValueError):
        return None


def save_config(config):
    """Written to disk before it replaces the old file: a power cut or restart right
    after linking must not leave an empty file, which loses the link."""
    config_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = config_file().with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, config_file())
    dir_fd = os.open(config_dir(), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def delete_config():
    config_file().unlink(missing_ok=True)
