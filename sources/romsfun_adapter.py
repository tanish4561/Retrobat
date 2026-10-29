import logging
import math
import re
import time
import urllib.parse
from pathlib import Path

import certifi
import requests
from bs4 import BeautifulSoup


class RomsFunAdapter:
    BASE_URL = "https://romsfun.com"

    SYSTEM_URLS = {
        "All": "https://romsfun.com/roms/",
        "PS2": "https://romsfun.com/roms/playstation-2/",
        "GBA": "https://romsfun.com/roms/game-boy-advance/",
        "NDS": "https://romsfun.com/roms/nintendo-ds/",
        "GameCube": "https://romsfun.com/roms/gamecube/",
        "Wii": "https://romsfun.com/roms/wii/",
        "PS3": "https://romsfun.com/roms/playstation-3/",
        "PSP": "https://romsfun.com/roms/playstation-portable/",
        "N64": "https://romsfun.com/roms/nintendo-64/",
        "NES": "https://romsfun.com/roms/nes/",
    }

    SYSTEM_SLUGS = {
        "All": None,
        "PS2": "playstation-2",
        "GBA": "game-boy-advance",
        "NDS": "nintendo-ds",
        "GameCube": "gamecube",
        "Wii": "wii",
        "PS3": "playstation-3",
        "PSP": "playstation-portable",
        "N64": "nintendo-64",
        "NES": "nes",
    }

    QUERY_REMOVE_PATTERN = re.compile(r"\[[^\]]*\]|\([^\)]*\)")
    QUERY_TAGS_PATTERN = re.compile(
        r"\b(nsp|xci|switch|nintendo|base\s*game|usa|europe|japan|world|rev\s*\d+|v\d+(?:\.\d+)*)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Referer": self.BASE_URL,
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        self.logs_dir = Path(__file__).resolve().parent.parent / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("retrobat.fetch")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / "fetch.log", encoding="utf-8")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)

    def normalize_query(self, name):
        if not name:
            return ""
        value = name.replace("_", " ")
        value = self.QUERY_REMOVE_PATTERN.sub(" ", value)
        value = self.QUERY_TAGS_PATTERN.sub(" ", value)
        value = re.sub(r"[^\w\s-]", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value

    def _build_query_variants(self, normalized_query):
        if not normalized_query:
            return [""]
        variants = [normalized_query]
        words = normalized_query.split()
        if len(words) >= 3:
            variants.append(" ".join(words[:3]))
        if len(words) >= 2:
            variants.append(" ".join(words[:2]))
        variants.append(words[0])
        out = []
        seen = set()
        for v in variants:
            key = v.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(v)
        return out

    def build_search_url(self, query, console=None, page=1):
        base = "https://romsfun.com/roms"
        if console:
            url = f"{base}/{console}/"
        else:
            url = f"{base}/"

        if page and page > 1:
            url = url.rstrip("/") + f"/page/{page}/"

        normalized_query = self.normalize_query(query)
        if normalized_query:
            quoted = urllib.parse.quote(normalized_query)
            joiner = "&" if "?" in url else "?"
            url = f"{url}{joiner}q={quoted}"
        return url

    def _normalize_url(self, url):
        if not url:
            return None
        if url.startswith("http"):
            return url
        if url.startswith("/"):
            return self.BASE_URL + url
        return self.BASE_URL + "/" + url

    def _request_with_retry(self, url, attempts=3, timeout=10):
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                self.logger.info("request attempt=%s url=%s", attempt, url)
                response = self.session.get(url, timeout=timeout, verify=certifi.where())
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as exc:
                last_error = exc
                self.logger.warning("request failed attempt=%s url=%s error=%s", attempt, url, exc)
                if attempt < attempts:
                    time.sleep(2)
        raise last_error

    def _extract_size(self, card):
        text = card.get_text(" ", strip=True)
        match = re.search(r"\b\d+(?:\.\d+)?\s?(?:KB|MB|GB)\b", text, re.IGNORECASE)
        if match:
            return match.group(0)
        return "Unknown"

    def _parse_games_from_soup(self, soup, default_system="Unknown", source_url=""):
        games = []
        seen = set()

        selectors = [
            "div.bg-white.rounded-lg.shadow-md",
            "article",
            ".post",
            ".game-card",
            "div[class*='shadow'][class*='rounded']",
        ]

        cards = []
        selector_used = None
        for selector in selectors:
            cards = soup.select(selector)
            if cards:
                selector_used = selector
                break

        if not cards:
            snippet = soup.get_text(" ", strip=True)[:1000]
            self.logger.warning("No games parsed from page source=%s snippet=%s", source_url, snippet)
            return []

        self.logger.info("parse selector=%s source=%s cards=%s", selector_used, source_url, len(cards))

        for card in cards:
            try:
                link_elem = card.find("a", href=lambda h: h and "/roms/" in h and h.endswith(".html"))
                if not link_elem:
                    continue

                game_url = self._normalize_url(link_elem.get("href"))
                if not game_url or game_url in seen:
                    continue

                title = "Unknown"
                title_elem = card.find("h3") or card.find("h2")
                if title_elem:
                    title = title_elem.get_text(strip=True)
                else:
                    img = card.find("img")
                    if img and img.get("alt"):
                        title = img.get("alt").strip()

                img_elem = card.find("img")
                cover = None
                if img_elem:
                    cover = img_elem.get("src") or img_elem.get("data-src") or img_elem.get("data-lazy-src")
                    cover = self._normalize_url(cover)

                system_name = default_system
                system_link = card.find("a", href=lambda h: h and "/roms/" in h and not h.endswith(".html"))
                if system_link:
                    text = system_link.get_text(strip=True)
                    if text:
                        system_name = text

                size = self._extract_size(card)

                games.append(
                    {
                        "title": title,
                        "cover": cover,
                        "url": game_url,
                        "system": system_name,
                        "size": size,
                    }
                )
                seen.add(game_url)
            except Exception as exc:
                self.logger.warning("parse card failed source=%s error=%s", source_url, exc)
                continue

        return games

    def _collect_from_url(self, system, query=None, pages=1):
        merged = []
        seen = set()
        slug = self.SYSTEM_SLUGS.get(system)

        for page in range(1, pages + 1):
            url = self.build_search_url(query=query, console=slug, page=page)
            try:
                response = self._request_with_retry(url)
                soup = BeautifulSoup(response.text, "html.parser")
                parsed = self._parse_games_from_soup(soup, default_system=system, source_url=url)

                if response.status_code == 200 and len(parsed) == 0:
                    self.logger.warning("HTTP 200 but parse returned 0 games url=%s", url)

                if not parsed:
                    self.logger.info("pagination stop system=%s page=%s url=%s", system, page, url)
                    break

                for game in parsed:
                    if game["url"] in seen:
                        continue
                    merged.append(game)
                    seen.add(game["url"])

            except Exception as exc:
                self.logger.error("collect failed system=%s page=%s url=%s error=%s", system, page, url, exc)
                continue

        return merged

    def search_games(self, query=None, system="All", page=1, page_size=50):
        page = max(1, int(page))
        page_size = max(1, int(page_size))

        normalized_query = self.normalize_query(query or "")
        if query and not normalized_query:
            normalized_query = query.strip()
        self.logger.info("Searching romsfun: query=%s system=%s page=%s", normalized_query, system, page)

        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        try:
            query_variants = self._build_query_variants(normalized_query)
            for qv in query_variants:
                if system != "All":
                    pages_needed = min(60, max(1, math.ceil(end_idx / 12) + 1))
                    games = self._collect_from_url(system=system, query=qv, pages=pages_needed)
                    self.logger.info("parsed games=%s system=%s variant=%s", len(games), system, qv)
                    if games:
                        return games[start_idx:end_idx]
                    continue

                merged = []
                seen = set()
                for source in ("PS2", "GBA", "NDS", "GameCube", "Wii", "PS3", "PSP", "N64", "NES"):
                    pages_needed = min(40, max(1, math.ceil(end_idx / 36) + 1))
                    parsed = self._collect_from_url(system=source, query=qv, pages=pages_needed)
                    for game in parsed:
                        if game["url"] in seen:
                            continue
                        merged.append(game)
                        seen.add(game["url"])

                self.logger.info("parsed games=%s system=All variant=%s", len(merged), qv)
                if merged:
                    return merged[start_idx:end_idx]

            return []

        except Exception as exc:
            self.logger.error("search failed query=%s system=%s page=%s error=%s", normalized_query, system, page, exc)
            return []

    def get_download_info(self, game_url):
        try:
            self.logger.info("Fetching game page for download: %s", game_url)
            response = self._request_with_retry(game_url)
            soup = BeautifulSoup(response.text, "html.parser")

            dl_page_link = None
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                if "/download/" in href:
                    dl_page_link = self._normalize_url(href)
                    break

            if not dl_page_link:
                return None, None

            dl_response = self._request_with_retry(dl_page_link)
            dl_soup = BeautifulSoup(dl_response.text, "html.parser")

            extensions = (".zip", ".7z", ".rar", ".iso", ".nds", ".gba", ".nsp", ".xci")
            final_link = None

            for anchor in dl_soup.find_all("a", href=True):
                href = anchor["href"]
                if href.lower().endswith(extensions):
                    final_link = self._normalize_url(href)
                    break

            if not final_link:
                for anchor in dl_soup.find_all("a", href=True):
                    text = anchor.get_text(" ", strip=True).lower()
                    if "click here" in text or "start download" in text:
                        final_link = self._normalize_url(anchor["href"])
                        break

            if final_link:
                filename = urllib.parse.unquote(final_link.split("/")[-1])
                return final_link, filename

            return dl_page_link, "page_link"

        except Exception as exc:
            self.logger.error("Download fetch failed game_url=%s error=%s", game_url, exc)
            return None, None
