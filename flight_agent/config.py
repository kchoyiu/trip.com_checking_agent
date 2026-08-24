from dataclasses import dataclass
from pathlib import Path
import yaml

@dataclass
class Settings:
    raw: dict
    @property
    def alerts(self): return self.raw.get("alerts", {})
    @property
    def queue(self): return self.raw.get("queue", {})
    @property
    def scraper(self): return self.raw.get("scraper", {})

def load_settings(path="config.yaml") -> Settings:
    with Path(path).open(encoding="utf-8") as f:
        return Settings(yaml.safe_load(f) or {})

