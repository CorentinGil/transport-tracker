import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "data", "config.json")

DEFAULT_CONFIG = {
    "prim_api_key": "",
    "scrape_interval_minutes": 5,
}


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            saved = json.load(f)
            return {**DEFAULT_CONFIG, **saved}
    return DEFAULT_CONFIG.copy()


def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_api_key():
    return load_config().get("prim_api_key", "")
