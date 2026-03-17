from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                               QLineEdit, QPushButton, QScrollArea, QGridLayout, 
                               QFrame, QProgressBar, QMessageBox)
from PySide6.QtCore import Qt, Signal, QThread, QSize, QTimer, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QIcon
import requests
import json
import time
import hashlib
import certifi
from pathlib import Path
from sources.romsfun_adapter import RomsFunAdapter
import webbrowser

# --- Worker Threads ---
class SearchWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    
    def __init__(self, adapter, query, system, page=1, page_size=50):
        super().__init__()
        self.adapter = adapter
        self.query = query
        self.system = system
        self.page = page
        self.page_size = page_size
        
    def run(self):
        try:
            results = self.adapter.search_games(
                query=self.query,
                system=self.system,
                page=self.page,
                page_size=self.page_size,
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


class RunnableSignals(QObject):
    finished = Signal()
    error = Signal(str)
    result = Signal(object)


class PageFetchRunnable(QRunnable):
    def __init__(self, adapter, cache_dir, system, query, page, page_size, ttl_seconds=600):
        super().__init__()
        self.adapter = adapter
        self.cache_dir = Path(cache_dir)
        self.system = system
        self.query = query or ""
        self.page = page
        self.page_size = page_size
        self.ttl_seconds = ttl_seconds
        self.signals = RunnableSignals()

    def _cache_key(self):
        raw = f"{self.system}|{self.query}|{self.page}|{self.page_size}".encode("utf-8")
        return hashlib.md5(raw).hexdigest()

    def _cache_path(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir / f"page_{self._cache_key()}.json"

    def _read_cache(self):
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ts = payload.get("ts", 0)
            if time.time() - ts > self.ttl_seconds:
                return None
            return payload.get("games", [])
        except Exception:
            return None

    def _write_cache(self, games):
        path = self._cache_path()
        payload = {"ts": time.time(), "games": games}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def run(self):
        try:
            cached = self._read_cache()
            if cached is not None:
                self.signals.result.emit(cached)
                self.signals.finished.emit()
                return

            games = self.adapter.search_games(
                query=self.query,
                system=self.system,
                page=self.page,
                page_size=self.page_size,
            )
            self._write_cache(games)
            self.signals.result.emit(games)
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit()


class ImageLoadRunnable(QRunnable):
    def __init__(self, image_url, cache_path):
        super().__init__()
        self.image_url = image_url
        self.cache_path = Path(cache_path)
        self.signals = RunnableSignals()

    def run(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            if self.cache_path.exists() and self.cache_path.stat().st_size > 0:
                data = self.cache_path.read_bytes()
                self.signals.result.emit(data)
                self.signals.finished.emit()
                return

            if not self.image_url:
                self.signals.result.emit(None)
                self.signals.finished.emit()
                return

            response = requests.get(
                self.image_url,
                timeout=8,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
                },
                verify=certifi.where(),
            )
            response.raise_for_status()
            data = response.content
            self.cache_path.write_bytes(data)
            self.signals.result.emit(data)
            self.signals.finished.emit()
        except Exception:
            self.signals.result.emit(None)
            self.signals.finished.emit()

class DownloadWorker(QThread):
    progress = Signal(int, str) # percent, speed/status
    finished = Signal(str) # filepath
    error = Signal(str)
    
    def __init__(self, adapter, game_url, save_dir):
        super().__init__()
        self.adapter = adapter
        self.game_url = game_url
        self.save_dir = Path(save_dir)
        self._is_running = True
        
    def run(self):
        try:
            self.progress.emit(0, "Fetching link...")
            url, filename = self.adapter.get_download_info(self.game_url)
            
            if not url:
                self.error.emit("Could not find download link.")
                return
            
            if filename == "page_link":
                # Fallback to browser
                webbrowser.open(url)
                self.error.emit("Download link protected. Opened in browser.")
                return

            if not self.save_dir.exists():
                self.save_dir.mkdir(parents=True, exist_ok=True)
                
            local_path = self.save_dir / filename
            
            self.progress.emit(0, "Starting download...")
            
            with self.adapter.session.get(
                url,
                stream=True,
                headers=self.adapter.headers,
                timeout=15,
                verify=certifi.where(),
            ) as r:
                r.raise_for_status()
                total_length = int(r.headers.get('content-length', 0))
                downloaded = 0
                
                with open(local_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if not self._is_running:
                            r.close()
                            local_path.unlink() # Delete partial
                            return
                        
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if total_length > 0:
                            percent = int((downloaded / total_length) * 100)
                            self.progress.emit(percent, f"{percent}%")
                        else:
                            self.progress.emit(0, "Downloading...")
                            
            self.finished.emit(str(local_path))
            
        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        self._is_running = False

# --- UI Components ---
class GameCard(QFrame):
    clicked = Signal(dict)
    
    def __init__(self, game_data, parent=None):
        super().__init__(parent)
        self.game_data = game_data
        self.setFixedSize(160, 240)
        self._focused = False
        self._apply_style()
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Cover
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(140, 160)
        self.cover_lbl.setAlignment(Qt.AlignCenter)
        self.cover_lbl.setStyleSheet("background-color: #111; border-radius: 5px;")
        layout.addWidget(self.cover_lbl)
        
        # Load cover async (simplified: just do it here or via another worker)
        # For prototype, we'll set a placeholder and load later if needed
        # Or just use a simple request if not blocking main thread too much 
        # (WARNING: This blocks UI. Should use NetworkAccessManager or thread. 
        # But let's leave it empty for now to speed up UI dev)
        self.cover_lbl.setText("Loading...")
        
        # Title
        title = QLabel(game_data['title'])
        title.setWordWrap(True)
        title.setFont(QFont("Segoe UI", 10, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        
        # System
        sys_lbl = QLabel(game_data['system'])
        sys_lbl.setFont(QFont("Segoe UI", 8))
        sys_lbl.setStyleSheet("color: #aaa;")
        sys_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(sys_lbl)
        
    def mousePressEvent(self, event):
        self.clicked.emit(self.game_data)

    def _apply_style(self):
        border = "#00f0ff" if self._focused else "transparent"
        bg = "#252540" if self._focused else "#1a1a2e"
        self.setStyleSheet(f"""
            QFrame {{ 
                background-color: {bg}; 
                border-radius: 10px; 
                border: 2px solid {border};
            }}
            QLabel {{ color: white; border: none; background: transparent; }}
        """)

    def set_focused(self, focused):
        self._focused = focused
        self._apply_style()
        
    def set_cover_image(self, data):
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.cover_lbl.setPixmap(pixmap.scaled(140, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.cover_lbl.setText("")

class ImageLoader(QThread):
    loaded = Signal(bytes)
    
    def __init__(self, url):
        super().__init__()
        self.url = url
        
    def run(self):
        try:
            if self.url:
                r = requests.get(self.url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
                if r.status_code == 200:
                    self.loaded.emit(r.content)
        except: pass

class DiscoverWidget(QWidget):
    request_close = Signal() # Signal to return to main menu
    library_updated = Signal() # Signal when a game is downloaded

    def __init__(self, parent=None):
        super().__init__(parent)
        self.adapter = RomsFunAdapter()
        self.dl_worker = None
        self.search_worker = None
        self.search_request_id = 0
        self.search_workers = {}
        self.active_image_loaders = []
        self.search_in_progress = False
        self.game_cards = []
        self.games_data = []
        self.selected_idx = -1
        self.max_cols = 5
        self.page_size = 50
        self.current_page = 1
        self.last_result_count = 0
        self.is_loading_page = False
        self.loading_page_key = None
        self.loading_image_indices = set()
        self.loaded_image_indices = set()
        self.cover_cache_dir = Path("cache") / "covers"
        self.pages_cache_dir = Path("cache") / "pages"
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(4)
        self.active_page_tasks = {}
        self.active_image_tasks = {}
        self.is_closing = False
        self.setFocusPolicy(Qt.StrongFocus)
        
        # Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # Header
        header = QHBoxLayout()
        title = QLabel("DISCOVER")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        header.addWidget(title)
        
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search games...")
        self.search_bar.setStyleSheet("""
            QLineEdit { 
                padding: 10px; border-radius: 20px; 
                background-color: #222; color: white; border: 1px solid #444; 
                font-size: 14px;
            }
        """)
        self.search_bar.returnPressed.connect(self.do_search)
        self.search_bar.textChanged.connect(self.on_search_text_changed)
        header.addWidget(self.search_bar)

        self.page_label = QLabel("Page 1")
        self.page_label.setStyleSheet("color: #bbb; font-size: 14px;")
        header.addWidget(self.page_label)

        self.prev_btn = QPushButton("Prev")
        self.prev_btn.setStyleSheet("background: #333; color: white; padding: 8px 12px; border-radius: 6px;")
        self.prev_btn.clicked.connect(self.prev_page)
        header.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next")
        self.next_btn.setStyleSheet("background: #333; color: white; padding: 8px 12px; border-radius: 6px;")
        self.next_btn.clicked.connect(self.next_page)
        header.addWidget(self.next_btn)
        
        close_btn = QPushButton("EXIT")
        close_btn.clicked.connect(self.request_close.emit)
        close_btn.setStyleSheet("background: #d32f2f; color: white; font-weight: bold; padding: 8px 16px; border-radius: 4px;")
        header.addWidget(close_btn)
        
        main_layout.addLayout(header)
        
        # Filters
        filters_layout = QHBoxLayout()
        self.filters = ["All", "PS2", "GBA", "NDS"]
        self.filter_btns = []
        for f in self.filters:
            btn = QPushButton(f)
            btn.setCheckable(True)
            if f == "All": btn.setChecked(True)
            btn.clicked.connect(lambda ch, name=f: self.set_filter(name))
            btn.setStyleSheet("""
                QPushButton { 
                    background-color: #333; color: white; padding: 8px 16px; 
                    border-radius: 15px; border: 1px solid #444; 
                }
                QPushButton:checked { 
                    background-color: #00f0ff; color: black; border: 1px solid #00f0ff; font-weight: bold;
                }
            """)
            filters_layout.addWidget(btn)
            self.filter_btns.append(btn)
        filters_layout.addStretch()
        main_layout.addLayout(filters_layout)
        
        # Grid Area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        self.grid_widget = QWidget()
        self.grid_layout = QGridLayout(self.grid_widget)
        self.grid_layout.setSpacing(20)
        self.scroll.setWidget(self.grid_widget)
        main_layout.addWidget(self.scroll)
        self.scroll.verticalScrollBar().valueChanged.connect(self.on_grid_scrolled)

        self.loading_overlay = QFrame(self.scroll.viewport())
        self.loading_overlay.setStyleSheet("background-color: rgba(0,0,0,150); border-radius: 8px;")
        self.loading_overlay.hide()
        ov = QVBoxLayout(self.loading_overlay)
        ov.setContentsMargins(20, 20, 20, 20)
        ov.addStretch()
        self.loading_label = QLabel("Loading games...")
        self.loading_label.setStyleSheet("color: white; font-size: 18px; font-weight: bold;")
        self.loading_label.setAlignment(Qt.AlignCenter)
        ov.addWidget(self.loading_label)
        self.retry_btn = QPushButton("Retry")
        self.retry_btn.setVisible(False)
        self.retry_btn.setStyleSheet("background: #444; color: white; padding: 8px 16px; border-radius: 6px;")
        self.retry_btn.clicked.connect(self.do_search)
        ov.addWidget(self.retry_btn, alignment=Qt.AlignCenter)
        ov.addStretch()
        
        # Details / Status Panel
        self.status_bar = QLabel("Ready")
        self.status_bar.setStyleSheet("color: #aaa;")
        main_layout.addWidget(self.status_bar)
        
        # Download Progress
        self.dl_frame = QFrame()
        self.dl_frame.setVisible(False)
        self.dl_frame.setStyleSheet("background: #222; border-radius: 5px; padding: 10px;")
        dl_layout = QVBoxLayout(self.dl_frame)
        self.dl_label = QLabel("Downloading...")
        self.dl_label.setStyleSheet("color: white;")
        dl_layout.addWidget(self.dl_label)
        self.dl_prog = QProgressBar()
        self.dl_prog.setStyleSheet("QProgressBar::chunk { background-color: #00f0ff; }")
        dl_layout.addWidget(self.dl_prog)
        main_layout.addWidget(self.dl_frame)
        
        # Initial Load
        self.current_system = "All"
        self.do_search()

    def on_search_text_changed(self, _):
        self.current_page = 1
        if not hasattr(self, '_search_timer'):
            self._search_timer = QTimer(self)
            self._search_timer.setSingleShot(True)
            self._search_timer.timeout.connect(self.do_search)
        self._search_timer.start(350)

    def prev_page(self):
        if self.is_loading_page:
            return
        if self.current_page > 1:
            self.current_page -= 1
            self.do_search()

    def next_page(self):
        if self.is_loading_page:
            return
        # only advance if current page is full
        if self.last_result_count >= self.page_size:
            self.current_page += 1
            self.do_search()
        
    def set_filter(self, name):
        self.current_system = name
        self.current_page = 1
        # Update UI
        for btn in self.filter_btns:
            btn.setChecked(btn.text() == name)
        self.do_search()
        
    def do_search(self):
        if self.is_closing:
            return
        query = self.search_bar.text()
        page_key = f"{self.current_system}|{query}|{self.current_page}|{self.page_size}"
        if self.is_loading_page and self.loading_page_key == page_key:
            return

        self.status_bar.setText(f"Searching {self.current_system}...")
        self.search_in_progress = True
        self.is_loading_page = True
        self.loading_page_key = page_key
        self.show_loading_overlay("Loading games...", retry=False)
        
        # Clear grid
        for i in reversed(range(self.grid_layout.count())): 
            item = self.grid_layout.itemAt(i)
            widget = item.widget() if item else None
            if widget:
                if isinstance(widget, GameCard):
                    widget.cover_lbl.clear()
                widget.setParent(None)
                widget.deleteLater()
        self.game_cards = []
        self.games_data = []
        self.selected_idx = -1
        self.loaded_image_indices.clear()
        self.loading_image_indices.clear()
        self.active_image_tasks.clear()
            
        self.page_label.setText(f"Page {self.current_page}")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)

        self.search_request_id += 1
        req_id = self.search_request_id
        runnable = PageFetchRunnable(
            self.adapter,
            self.pages_cache_dir,
            self.current_system,
            query,
            self.current_page,
            self.page_size,
        )
        runnable.signals.result.connect(lambda games, rid=req_id: self.on_search_finished(rid, games))
        runnable.signals.error.connect(lambda err, rid=req_id: self.on_search_error(rid, err))
        runnable.signals.finished.connect(lambda rid=req_id: self.on_search_complete(rid))
        self.active_page_tasks[req_id] = runnable
        self.thread_pool.start(runnable)

    def cleanup_search_worker(self, request_id):
        worker = self.search_workers.pop(request_id, None)
        if worker:
            worker.deleteLater()

    def on_search_complete(self, request_id):
        self.active_page_tasks.pop(request_id, None)
        if request_id != self.search_request_id:
            return
        self.search_in_progress = False
        self.is_loading_page = False
        self.loading_page_key = None
        self.hide_loading_overlay()

    def on_search_finished(self, request_id, games):
        if request_id != self.search_request_id:
            return
        self.populate_grid(games)

    def on_search_error(self, request_id, error_text):
        if request_id != self.search_request_id:
            return
        self.status_bar.setText(f"Error: {error_text}")
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(False)
        self.show_loading_overlay("Failed to load page.", retry=True)
        
    def populate_grid(self, games):
        if not games:
            self.status_bar.setText("No games found. Check internet connection or try another filter.")
            msg = QLabel("No games found.")
            msg.setStyleSheet("color: white; font-size: 20px;")
            self.grid_layout.addWidget(msg, 0, 0)
            self.last_result_count = 0
            self.next_btn.setEnabled(False)
            return

        self.last_result_count = len(games)
        self.status_bar.setText(f"Showing {len(games)} games (page {self.current_page}).")
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(len(games) >= self.page_size)
        row = 0
        col = 0
        max_cols = self.max_cols
        self.games_data = games
        
        for g in self.games_data:
            card = GameCard(g)
            card.clicked.connect(self.show_game_details)
            self.grid_layout.addWidget(card, row, col)
            self.game_cards.append(card)
            
            # Start image loader
            if g['cover']:
                # Lazy image loading: start only for visible cards.
                pass
            
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        if self.game_cards:
            self.set_selected_index(0)
            QTimer.singleShot(0, self.load_visible_images)

    def show_loading_overlay(self, text, retry=False):
        self.loading_label.setText(text)
        self.retry_btn.setVisible(retry)
        self.loading_overlay.setGeometry(self.scroll.viewport().rect())
        self.loading_overlay.show()
        self.loading_overlay.raise_()

    def hide_loading_overlay(self):
        self.loading_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.loading_overlay.isVisible():
            self.loading_overlay.setGeometry(self.scroll.viewport().rect())

    def on_grid_scrolled(self, _):
        self.load_visible_images()

    def load_visible_images(self):
        if not self.game_cards:
            return
        scroll_val = self.scroll.verticalScrollBar().value()
        viewport_h = self.scroll.viewport().height()
        row_h = 260
        start_row = max(0, (scroll_val // row_h) - 1)
        end_row = ((scroll_val + viewport_h) // row_h) + 1
        start_idx = start_row * self.max_cols
        end_idx = min(len(self.game_cards), (end_row + 1) * self.max_cols)

        for idx in range(start_idx, end_idx):
            if idx in self.loaded_image_indices or idx in self.loading_image_indices:
                continue
            game = self.games_data[idx]
            cover_url = game.get('cover')
            if not cover_url:
                continue

            slug = hashlib.md5((cover_url or str(idx)).encode('utf-8')).hexdigest()
            ext = '.jpg'
            low = cover_url.lower()
            if '.png' in low:
                ext = '.png'
            cache_path = self.cover_cache_dir / f"{slug}{ext}"

            self.loading_image_indices.add(idx)
            runnable = ImageLoadRunnable(cover_url, cache_path)
            runnable.signals.result.connect(lambda data, i=idx: self.on_card_image_loaded(i, data))
            runnable.signals.finished.connect(lambda i=idx: self.on_card_image_finished(i))
            self.active_image_tasks[idx] = runnable
            self.thread_pool.start(runnable)

    def on_card_image_loaded(self, idx, data):
        if self.is_closing:
            return
        if idx < 0 or idx >= len(self.game_cards):
            return
        if data:
            self.game_cards[idx].set_cover_image(data)

    def on_card_image_finished(self, idx):
        self.active_image_tasks.pop(idx, None)
        self.loading_image_indices.discard(idx)
        self.loaded_image_indices.add(idx)

    def set_selected_index(self, index):
        if not self.game_cards:
            self.selected_idx = -1
            return
        index = max(0, min(index, len(self.game_cards) - 1))
        self.selected_idx = index
        for i, card in enumerate(self.game_cards):
            card.set_focused(i == self.selected_idx)
        selected = self.game_cards[self.selected_idx]
        self.scroll.ensureWidgetVisible(selected, 40, 40)

    def navigate_grid(self, direction):
        if not self.game_cards:
            return
        idx = self.selected_idx if self.selected_idx >= 0 else 0
        if direction == 'left':
            idx -= 1
        elif direction == 'right':
            idx += 1
        elif direction == 'up':
            idx -= self.max_cols
        elif direction == 'down':
            idx += self.max_cols
        self.set_selected_index(idx)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.request_close.emit()
            return

        if key == Qt.Key_Left:
            self.navigate_grid('left')
            return
        if key == Qt.Key_Right:
            self.navigate_grid('right')
            return
        if key == Qt.Key_Up:
            self.navigate_grid('up')
            return
        if key == Qt.Key_Down:
            self.navigate_grid('down')
            return

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if 0 <= self.selected_idx < len(self.games_data):
                self.show_game_details(self.games_data[self.selected_idx])
            return

        if key in (Qt.Key_Space, Qt.Key_X):
            if 0 <= self.selected_idx < len(self.games_data):
                self.start_download(self.games_data[self.selected_idx])
            return

        super().keyPressEvent(event)

    def handle_controller_button(self, btn):
        if btn == 'LEFT':
            self.navigate_grid('left')
        elif btn == 'RIGHT':
            self.navigate_grid('right')
        elif btn == 'UP':
            self.navigate_grid('up')
        elif btn == 'DOWN':
            self.navigate_grid('down')
        elif btn == 'A' and 0 <= self.selected_idx < len(self.games_data):
            self.show_game_details(self.games_data[self.selected_idx])
        elif btn == 'X' and 0 <= self.selected_idx < len(self.games_data):
            self.start_download(self.games_data[self.selected_idx])
        elif btn == 'B':
            self.request_close.emit()
        elif btn == 'L1':
            self.prev_page()
        elif btn == 'R1':
            self.next_page()
                
    def show_game_details(self, game_data):
        # Determine download path based on system
        base_roms = Path("Roms")
        # Simple mapping
        sys_folder_map = {
            "Game Boy Advance": "gba", # Simplified, usually just root or specific
            "Nintendo DS": "nds",
            "PlayStation 2": "ps2"
        }
        # Actually our launcher just dumps everything in 'Roms' for now based on the file listing earlier
        # User file listing showed: Roms/Pokemon...gba
        # So we download to Roms main folder
        
        msg = QMessageBox(self)
        msg.setWindowTitle(game_data['title'])
        msg.setText(f"Download {game_data['title']}?")
        msg.setInformativeText(f"System: {game_data['system']}\n\nThis will download the ROM file.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setStyleSheet("QMessageBox { background-color: #222; color: white; } QLabel { color: white; } QPushButton { background-color: #444; color: white; }")
        
        if msg.exec() == QMessageBox.Yes:
            self.start_download(game_data)
            
    def start_download(self, game_data):
        self.dl_frame.setVisible(True)
        self.dl_label.setText(f"Downloading {game_data['title']}...")
        self.dl_prog.setValue(0)
        
        # Save to Roms folder
        save_path = Path("Roms").absolute()
        
        self.dl_worker = DownloadWorker(self.adapter, game_data['url'], save_path)
        self.dl_worker.progress.connect(self.update_progress)
        self.dl_worker.finished.connect(self.download_finished)
        self.dl_worker.error.connect(self.download_error)
        self.dl_worker.start()
        
    def update_progress(self, val, text):
        self.dl_prog.setValue(val)
        self.dl_label.setText(f"Downloading... {text}")
        
    def download_finished(self, path):
        self.dl_frame.setVisible(False)
        QMessageBox.information(self, "Download Complete", f"Saved to:\n{path}")
        self.library_updated.emit()
        
    def download_error(self, err):
        self.dl_frame.setVisible(False)
        QMessageBox.warning(self, "Download Error", err)

    def on_image_loader_finished(self, loader):
        try:
            if loader in self.active_image_loaders:
                self.active_image_loaders.remove(loader)
            loader.deleteLater()
        except Exception:
            pass

    def closeEvent(self, event):
        # Prevent QThread destruction while running.
        self.is_closing = True
        try:
            for req_id, worker in list(self.search_workers.items()):
                if worker and worker.isRunning():
                    worker.terminate()
                    worker.wait(300)
                self.cleanup_search_worker(req_id)
        except Exception:
            pass

        try:
            if self.dl_worker and self.dl_worker.isRunning():
                self.dl_worker.stop()
                self.dl_worker.wait(500)
        except Exception:
            pass

        try:
            for loader in list(self.active_image_loaders):
                if loader and loader.isRunning():
                    loader.wait(300)
                self.on_image_loader_finished(loader)
        except Exception:
            pass

        try:
            self.thread_pool.waitForDone(1000)
        except Exception:
            pass

        super().closeEvent(event)

