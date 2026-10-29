import json
import logging
import re
import shutil
import threading
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import certifi

from sources.romsfun_adapter import RomsFunAdapter

REGION_PATTERN = re.compile(r"\((usa|europe|japan|world|us|eu|jp|eng|en,fr,de|en)\)", re.IGNORECASE)
REV_PATTERN = re.compile(r"\b(rev\s*\d+|v\d+(?:\.\d+)*)\b", re.IGNORECASE)
BRACKET_PATTERN = re.compile(r"\[[^\]]*\]")
PAREN_PATTERN = re.compile(r"\([^\)]*\)")
EXTRA_TAG_PATTERN = re.compile(r"\b([bhx!]|proto|beta|sample|mult\d+)\b", re.IGNORECASE)

COMPOUND_SUFFIXES = (
    ".nkit.iso",
    ".nkit.gcz",
    ".nkit.wbfs",
    ".ps3",
)


def _basename_without_compound_suffix(filename: str) -> str:
    lower = filename.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lower.endswith(suffix):
            return filename[: -len(suffix)]
    return Path(filename).stem


def normalize_title(filename: str) -> str:
    name = _basename_without_compound_suffix(filename)
    name = name.replace("_", " ").replace("*", " ").replace("-", " ")
    name = BRACKET_PATTERN.sub(" ", name)
    name = REGION_PATTERN.sub(" ", name)
    name = PAREN_PATTERN.sub(" ", name)
    name = REV_PATTERN.sub(" ", name)
    name = EXTRA_TAG_PATTERN.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    words = []
    for word in name.split():
        if word.isupper() and len(word) > 1:
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words) if words else Path(filename).stem


def slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "game"


class MetadataManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.cache_dir = self.base_dir / "cache"
        self.covers_dir = self.cache_dir / "covers"
        self.backgrounds_dir = self.cache_dir / "backgrounds"
        self.games_cache_file = self.cache_dir / "games.json"
        self.config_dir = self.base_dir / "config"
        self.custom_file = self.config_dir / "custom_metadata.json"
        self.logs_dir = self.base_dir / "logs"
        self.log_file = self.logs_dir / "metadata.log"

        self._lock = threading.Lock()
        self.adapter = RomsFunAdapter()

        self._ensure_paths()
        self.logger = self._setup_logger()

    def _ensure_paths(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.backgrounds_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if not self.games_cache_file.exists():
            self.games_cache_file.write_text("{}", encoding="utf-8")
        if not self.custom_file.exists():
            self.custom_file.write_text("{}", encoding="utf-8")

    def _setup_logger(self):
        logger = logging.getLogger("retrobat.metadata")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.FileHandler(self.log_file, encoding="utf-8")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger

    def _load_json(self, path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_json(self, path: Path, data):
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _cache_key(self, filename: str):
        return normalize_title(filename).lower().replace(" ", "_")

    def _legacy_cache_key(self, filename: str):
        return filename.lower().replace(" ", "_")

    def get_custom_overrides(self):
        with self._lock:
            return self._load_json(self.custom_file)

    def save_custom_override(self, filename: str, updates: dict):
        key = self._cache_key(filename)
        legacy_key = self._legacy_cache_key(filename)
        with self._lock:
            custom = self._load_json(self.custom_file)
            entry = dict(custom.get(legacy_key, {}))
            entry.update(custom.get(key, {}))
            for k, v in updates.items():
                if v:
                    entry[k] = v
            custom[key] = entry
            if legacy_key != key:
                custom[legacy_key] = dict(entry)
            self._save_json(self.custom_file, custom)

    def get_cached_metadata(self, filename: str):
        key = self._cache_key(filename)
        legacy_key = self._legacy_cache_key(filename)
        with self._lock:
            games = self._load_json(self.games_cache_file)
            custom = self._load_json(self.custom_file)
        merged = dict(games.get(legacy_key, {}))
        merged.update(custom.get(legacy_key, {}))
        merged.update(games.get(key, {}))
        merged.update(custom.get(key, {}))
        return merged

    def save_cached_metadata(self, filename: str, metadata: dict):
        key = self._cache_key(filename)
        legacy_key = self._legacy_cache_key(filename)
        with self._lock:
            games = self._load_json(self.games_cache_file)
            games[key] = metadata
            if legacy_key != key:
                games[legacy_key] = metadata
            self._save_json(self.games_cache_file, games)

    def _request_with_retry(self, url, timeout=5, attempts=3):
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                self.logger.info("request attempt=%s url=%s", attempt, url)
                response = requests.get(
                    url,
                    headers=self.adapter.headers,
                    timeout=timeout,
                    verify=certifi.where(),
                )
                response.raise_for_status()
                return response
            except Exception as e:
                last_error = e
                self.logger.warning("request failed attempt=%s url=%s error=%s", attempt, url, e)
                if attempt < attempts:
                    time.sleep(1 + (attempt * 0.5))
        raise last_error

    def _map_system_query(self, system_name: str):
        lower = (system_name or "").lower()
        if "playstation 2" in lower or "ps2" in lower:
            return "PS2"
        if "advance" in lower or "gba" in lower:
            return "GBA"
        if "nintendo ds" in lower or "nds" in lower:
            return "NDS"
        if "gamecube" in lower or re.search(r"\bgc\b", lower):
            return "GameCube"
        if lower == "wii" or " nintendo wii" in lower or "gamecube / wii" in lower:
            return "Wii"
        if "playstation 3" in lower or "ps3" in lower:
            return "PS3"
        if "psp" in lower:
            return "PSP"
        if "nintendo 64" in lower or "n64" in lower:
            return "N64"
        if "nes" in lower:
            return "NES"
        return "All"

    def _build_fallback_queries(self, normalized_title: str, system_name: str):
        system_label = system_name or ""
        candidates = [normalized_title]
        if system_label:
            candidates.append(f"{normalized_title} {system_label}")
            short_label = re.sub(r"[^a-z0-9]+", " ", system_label.lower()).strip()
            if short_label and short_label not in system_label.lower():
                candidates.append(f"{normalized_title} {short_label}")
        if "playstation" in (system_label or "").lower():
            candidates.append(f"{normalized_title} PS")
        if any(token in (system_label or "").lower() for token in ("gamecube", "wii", "nintendo")):
            candidates.append(f"{normalized_title} Nintendo")
        candidates.append(f"{normalized_title} rom")
        # Preserve order and uniqueness
        out = []
        seen = set()
        for q in candidates:
            key = q.lower().strip()
            if key and key not in seen:
                out.append(q)
                seen.add(key)
        return out

    def _best_match(self, normalized_title: str, results: list):
        best = None
        best_score = 0.0
        title_l = normalized_title.lower()
        for item in results:
            candidate = (item.get("title") or "").lower()
            if not candidate:
                continue
            score = SequenceMatcher(None, title_l, candidate).ratio()
            if title_l in candidate or candidate in title_l:
                score = max(score, 0.9)
            if score > best_score:
                best_score = score
                best = item
        if best_score < 0.55:
            return None
        return best

    def _download_artwork(self, url: str, path: Path):
        if not url:
            return None
        if path.exists() and path.stat().st_size > 0:
            return path
        try:
            resp = self._request_with_retry(url, timeout=5, attempts=3)
            path.write_bytes(resp.content)
            return path
        except Exception as e:
            self.logger.warning("artwork download failed url=%s error=%s", url, e)
            return None

    def _fetch_description(self, game_url: str):
        if not game_url:
            return ""
        try:
            resp = self._request_with_retry(game_url, timeout=5, attempts=3)
            soup = BeautifulSoup(resp.content, "html.parser")
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                return meta.get("content").strip()
            p = soup.find("p")
            if p:
                return p.get_text(" ", strip=True)
        except Exception as e:
            self.logger.warning("description fetch failed url=%s error=%s", game_url, e)
        return ""

    def resolve_local_asset(self, value: str):
        if not value:
            return None
        p = Path(value)
        if p.is_absolute() and p.exists():
            return str(p)
        candidate = self.base_dir / value
        if candidate.exists():
            return str(candidate)
        return None

    def build_initial_metadata(self, filename: str, system: str):
        normalized = normalize_title(filename)
        data = self.get_cached_metadata(filename)
        title = data.get("title") or normalized
        return {
            "title": title,
            "system": data.get("system") or system,
            "cover": data.get("cover"),
            "background": data.get("background"),
            "description": data.get("description", ""),
            "last_updated": data.get("last_updated")
        }

    def fetch_and_cache(self, filename: str, system: str):
        normalized = normalize_title(filename)
        system_query = self._map_system_query(system)
        queries = self._build_fallback_queries(normalized, system)

        self.logger.info("metadata lookup filename=%s normalized=%s", filename, normalized)
        best_match = None
        for query in queries:
            self.logger.info("search query=%s system=%s", query, system_query)
            results = self.adapter.search_games(query=query, system=system_query)
            if not results and system_query != "All":
                results = self.adapter.search_games(query=query, system="All")
            best_match = self._best_match(normalized, results)
            if best_match:
                break

        key = slugify(Path(filename).stem)
        cover_rel = None
        bg_rel = None
        description = ""
        resolved_title = normalized

        if best_match:
            resolved_title = best_match.get("title") or normalized
            cover_url = best_match.get("cover")
            game_url = best_match.get("url")
            description = self._fetch_description(game_url)

            cover_ext = ".jpg"
            if cover_url and ".png" in cover_url.lower():
                cover_ext = ".png"
            cover_path = self.covers_dir / f"{key}{cover_ext}"
            bg_path = self.backgrounds_dir / f"{key}{cover_ext}"

            dl_cover = self._download_artwork(cover_url, cover_path)
            if dl_cover:
                cover_rel = str(dl_cover.relative_to(self.base_dir)).replace("\\", "/")
                if not bg_path.exists():
                    try:
                        shutil.copyfile(dl_cover, bg_path)
                    except Exception:
                        pass
                if bg_path.exists():
                    bg_rel = str(bg_path.relative_to(self.base_dir)).replace("\\", "/")

        metadata = {
            "title": resolved_title,
            "system": system,
            "cover": cover_rel,
            "background": bg_rel,
            "description": description,
            "last_updated": datetime.now().strftime("%Y-%m-%d")
        }
        self.save_cached_metadata(filename, metadata)
        # Return merged with overrides
        merged = self.get_cached_metadata(filename)
        if not merged.get("title"):
            merged["title"] = metadata["title"]
        if not merged.get("system"):
            merged["system"] = metadata["system"]
        if not merged.get("cover"):
            merged["cover"] = metadata["cover"]
        if not merged.get("background"):
            merged["background"] = metadata["background"]
        if not merged.get("description"):
            merged["description"] = metadata["description"]
        return merged
