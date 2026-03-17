import sys
import os
import json
import ctypes
import subprocess
import threading
import shlex
from pathlib import Path
from PySide6.QtCore import (Qt, QSize, QTimer, QPropertyAnimation, QEasingCurve, 
                            QPointF, QRectF, Signal, Slot, QThread, QObject, QEvent, QUrl)
from PySide6.QtGui import (QPixmap, QColor, QFont, QPainter, QBrush, QPen, 
                           QLinearGradient, QImage, QPainterPath, QIcon)
from PySide6.QtWidgets import (QApplication, QMainWindow, QGraphicsView, QGraphicsScene, 
                               QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, 
                               QGraphicsDropShadowEffect, QFrame, QLabel, QVBoxLayout, 
                               QHBoxLayout, QWidget, QGraphicsEffect, QGraphicsOpacityEffect, 
                               QGraphicsBlurEffect, QLineEdit, QPushButton, QFileDialog, QStackedWidget)
from discover_widget import DiscoverWidget
from metadata_pipeline import MetadataManager, normalize_title

# --- Configuration ---
BASE_DIR = Path(__file__).parent
ROMS_DIR = BASE_DIR / "Roms"  
if not ROMS_DIR.exists() and (BASE_DIR/"roms").exists():
    ROMS_DIR = BASE_DIR / "roms"

# Keep all ROM scanning on the local Retrobat Roms directory.
CLOUD_ROMS_DIR = None

EMULATORS_DIR = BASE_DIR / "emulators"
if not EMULATORS_DIR.exists() and (BASE_DIR / "emulator").exists():
    EMULATORS_DIR = BASE_DIR / "emulator"
ASSETS_DIR = BASE_DIR / "assets"
CONFIG_DIR = BASE_DIR / "config"
SYSTEMS_CONFIG = CONFIG_DIR / "systems.json"
METADATA_FILE = CONFIG_DIR / "metadata.json"
CUSTOM_METADATA_FILE = CONFIG_DIR / "custom_metadata.json"

METADATA_MANAGER = MetadataManager(BASE_DIR)

DEFAULT_SYSTEMS_CONFIG = {
    "gba": {
        "name": "Game Boy Advance",
        "badge": "GBA",
        "extensions": [".gba", ".srl"],
        "emulator": "visualboyadvance.exe",
    },
    "nds": {
        "name": "Nintendo DS",
        "badge": "NDS",
        "extensions": [".nds", ".dsi", ".srl"],
        "emulator": "desmume.exe",
    },
    "nes": {
        "name": "Nintendo NES / Famicom",
        "badge": "NES",
        "extensions": [".nes", ".nez", ".fc", ".fds"],
        "emulator": "fceux.exe",
    },
    "switch": {
        "name": "Nintendo Switch",
        "badge": "SWITCH",
        "extensions": [".nsp", ".xci", ".nsz", ".xcz"],
        "emulator": "ryujinx.exe",
    },
    "ps2": {
        "name": "PlayStation 2",
        "badge": "PS2",
        "extensions": [".iso", ".bin", ".cue", ".chd"],
        "emulator": "pcsx2.exe",
    },
    "ps3": {
        "name": "PlayStation 3",
        "badge": "PS3",
        "extensions": [".pkg", ".iso", ".folder"],
        "emulator": "rpcs3.exe",
        "folder_game": True,
    },
    "gamecube": {
        "name": "GameCube",
        "badge": "GC",
        "extensions": [".iso", ".gcm"],
        "emulator": "dolphin.exe",
    },
    "wii": {
        "name": "Wii",
        "badge": "WII",
        "extensions": [".iso", ".wbfs"],
        "emulator": "dolphin.exe",
    },
    "psp": {
        "name": "PSP",
        "badge": "PSP",
        "extensions": [".iso", ".cso"],
        "emulator": "ppsspp.exe",
    },
    "n64": {
        "name": "Nintendo 64",
        "badge": "N64",
        "extensions": [".n64", ".z64", ".v64"],
        "emulator": "project64.exe",
    },
}


def load_systems_definitions():
    data = {}
    if SYSTEMS_CONFIG.exists():
        try:
            with open(SYSTEMS_CONFIG, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    # Backward compatibility: old config shape {".gba": "emu.exe"}
    if data and all(str(k).startswith(".") for k in data.keys()):
        converted = {}
        for ext, emu in data.items():
            sid = ext.lstrip(".").lower() or "misc"
            if sid not in converted:
                converted[sid] = {
                    "name": sid.upper(),
                    "badge": sid.upper(),
                    "extensions": [],
                    "emulator": emu,
                }
            converted[sid]["extensions"].append(ext.lower())
        data = converted

    merged = dict(DEFAULT_SYSTEMS_CONFIG)
    for sid, cfg in data.items():
        sid_l = str(sid).lower()
        if sid_l not in merged:
            merged[sid_l] = {
                "name": sid_l.upper(),
                "badge": sid_l.upper(),
                "extensions": [],
                "emulator": "",
            }
        if isinstance(cfg, dict):
            merged[sid_l].update(cfg)

    for sid, cfg in merged.items():
        cfg["extensions"] = [str(x).lower() for x in cfg.get("extensions", [])]
        cfg.setdefault("name", sid.upper())
        cfg.setdefault("badge", sid.upper())
        cfg.setdefault("emulator", "")

    return merged


def build_extension_index(systems_defs):
    ext_index = {}
    for sid, cfg in systems_defs.items():
        for ext in cfg.get("extensions", []):
            ext_index.setdefault(ext.lower(), []).append(sid)
    return ext_index


SYSTEMS_DEFS = load_systems_definitions()
EXTENSION_INDEX = build_extension_index(SYSTEMS_DEFS)


def get_rom_roots():
    roots = []
    for p in [ROMS_DIR]:
        try:
            if p and p.exists() and p.is_dir():
                roots.append(p)
        except Exception:
            continue
    return roots

def load_metadata(filename):
    if METADATA_FILE.exists():
        try:
           with open(METADATA_FILE, 'r') as f:
               data = json.load(f)
               return data.get(filename, {})
        except: pass
    return {}

def save_metadata(filename, key, value):
    data = {}
    if METADATA_FILE.exists():
        try:
           with open(METADATA_FILE, 'r') as f:
               data = json.load(f)
        except: pass
    
    if filename not in data:
        data[filename] = {}
        
    data[filename][key] = value
    
    with open(METADATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# --- XInput Structs ---
class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort),
                ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]

class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong),
                ("Gamepad", XINPUT_GAMEPAD)]

# --- Controller Input (XInput) ---
class XInputController(QObject):
    button_pressed = Signal(str)
    
    # XInput Constants
    XINPUT_GAMEPAD_DPAD_UP = 0x0001
    XINPUT_GAMEPAD_DPAD_DOWN = 0x0002
    XINPUT_GAMEPAD_DPAD_LEFT = 0x0004
    XINPUT_GAMEPAD_DPAD_RIGHT = 0x0008
    XINPUT_GAMEPAD_START = 0x0010
    XINPUT_GAMEPAD_BACK = 0x0020
    XINPUT_GAMEPAD_LEFT_THUMB = 0x0040
    XINPUT_GAMEPAD_RIGHT_THUMB = 0x0080
    XINPUT_GAMEPAD_LEFT_SHOULDER = 0x0100
    XINPUT_GAMEPAD_RIGHT_SHOULDER = 0x0200
    XINPUT_GAMEPAD_A = 0x1000
    XINPUT_GAMEPAD_B = 0x2000
    XINPUT_GAMEPAD_X = 0x4000
    XINPUT_GAMEPAD_Y = 0x8000

    def __init__(self):
        super().__init__()
        try:
            self.xinput = ctypes.windll.xinput1_4
        except OSError:
            try:
                self.xinput = ctypes.windll.xinput9_1_0
            except OSError:
                self.xinput = None
                print("XInput DLL not found.")

        self.last_packet = 0
        self.last_buttons = 0
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)
        if self.xinput:
            self.timer.start(16) # 60Hz

    def poll(self):
        state = XINPUT_STATE()

        res = self.xinput.XInputGetState(0, ctypes.byref(state))
        
        if res == 0:
            if state.dwPacketNumber != self.last_packet:
                self.last_packet = state.dwPacketNumber
                buttons = state.Gamepad.wButtons
                
                # Check for button presses (transitions from 0 to 1)
                changed = buttons ^ self.last_buttons
                pressed = changed & buttons
                
                if pressed & self.XINPUT_GAMEPAD_A: self.button_pressed.emit('A')
                if pressed & self.XINPUT_GAMEPAD_B: self.button_pressed.emit('B')
                if pressed & self.XINPUT_GAMEPAD_X: self.button_pressed.emit('X')
                if pressed & self.XINPUT_GAMEPAD_Y: self.button_pressed.emit('Y')
                if pressed & self.XINPUT_GAMEPAD_START: self.button_pressed.emit('START')
                if pressed & self.XINPUT_GAMEPAD_BACK: self.button_pressed.emit('BACK')
                if pressed & self.XINPUT_GAMEPAD_LEFT_SHOULDER: self.button_pressed.emit('L1')
                if pressed & self.XINPUT_GAMEPAD_RIGHT_SHOULDER: self.button_pressed.emit('R1')
                if pressed & self.XINPUT_GAMEPAD_DPAD_UP: self.button_pressed.emit('UP')
                if pressed & self.XINPUT_GAMEPAD_DPAD_DOWN: self.button_pressed.emit('DOWN')
                if pressed & self.XINPUT_GAMEPAD_DPAD_LEFT: self.button_pressed.emit('LEFT')
                if pressed & self.XINPUT_GAMEPAD_DPAD_RIGHT: self.button_pressed.emit('RIGHT')
                
                self.last_buttons = buttons

# --- Model ---
class Game:
    def __init__(self, path, system_id=None, emulator=None, system_name=None, launch_path=None):
        self.path = Path(path)
        self.launch_path = Path(launch_path) if launch_path else self.path
        self.filename = self.path.name
        self.stem = self.path.stem
        self.ext = self.path.suffix.lower() if self.path.is_file() else ".folder"
        self.system_id = (system_id or "").lower() if system_id else None
        self.emulator = emulator
        self.system = system_name or self.detect_system(self.ext)
        self.description = ""

        # Legacy metadata support
        self.meta = load_metadata(self.filename)
        self.normalized_title = normalize_title(self.filename)

        cache_meta = METADATA_MANAGER.build_initial_metadata(self.filename, self.system)

        self.display_name = (
            self.meta.get('title')
            or cache_meta.get('title')
            or self.normalized_title
        )

        self.cover_path = METADATA_MANAGER.resolve_local_asset(cache_meta.get('cover'))
        self.bg_path = METADATA_MANAGER.resolve_local_asset(cache_meta.get('background'))
        self.description = cache_meta.get('description', '')

        if not self.cover_path:
            self.cover_path = self.find_asset("covers")
        if not self.bg_path:
            self.bg_path = self.find_asset("backgrounds")

    def detect_system(self, ext):
        candidates = EXTENSION_INDEX.get(ext.lower(), [])
        if not candidates:
            return "Unknown System"
        sid = candidates[0]
        return SYSTEMS_DEFS.get(sid, {}).get("name", sid.upper())

    def find_asset(self, folder):
        import re
        from difflib import SequenceMatcher

        # 1. Try exact match first
        for ext in ['.png', '.jpg', '.jpeg']:
            p = ASSETS_DIR / folder / (self.stem + ext)
            if p.exists(): return str(p)
            
        # 2. Fuzzy / Smart match
        target_dir = ASSETS_DIR / folder
        if not target_dir.exists(): return None
        
        candidates = []
        for f in target_dir.iterdir():
            if f.suffix.lower() in ['.png', '.jpg', '.jpeg']:
                candidates.append(f)

        def clean_name(name):
            name = name.lower()
            name = re.sub(r'\([^)]*\)', '', name) # Remove (USA)
            name = re.sub(r'\[[^]]*\]', '', name) # Remove [v0]
            name = name.replace('-', ' ').replace('_', ' ')
            name = re.sub(r'\s+', ' ', name).strip()
            return name

        rom_clean = clean_name(self.stem)
        # Create a version without spaces for "HeartGold" vs "Heart Gold" checks
        rom_nospaces = rom_clean.replace(" ", "")
        
        best_match = None
        best_score = 0.0
        
        for cand in candidates:
            cand_clean = clean_name(cand.stem)
            cand_nospaces = cand_clean.replace(" ", "")
            
            score = 0.0
            
            # Check 1: Exact containment (Highest Priority)
            if cand_clean and (cand_clean in rom_clean or rom_clean in cand_clean):
                 score = 0.95
                 # Bonus for length match (prevents "Po" matching "Pokemon")
                 if len(cand_clean) > len(rom_clean) * 0.8: score = 0.99
            
            # Check 2: No-spaces containment (Fixes HeartGold vs Heart Gold)
            elif cand_nospaces and (cand_nospaces in rom_nospaces or rom_nospaces in cand_nospaces):
                 score = 0.90
            
            # Check 3: Word Intersection (Better than fuzzy for multi-word titles)
            else:
                rom_words = set(rom_clean.split())
                cand_words = set(cand_clean.split())
                if rom_words and cand_words:
                    common = rom_words.intersection(cand_words)
                    # Score based on % of words covered
                    overlap = len(common) / max(len(rom_words), len(cand_words))
                    if overlap > 0.5: # At least half the words match
                        score = 0.7 + (overlap * 0.2)
                    else:
                        # Fallback to difflib for pure typos
                        ratio = SequenceMatcher(None, rom_clean, cand_clean).ratio()
                        if ratio > 0.6: score = ratio
            
            if score > best_score:
                best_score = score
                best_match = cand

        # Threshold: 0.6 is risky for "Pokemon Emerald" vs "Pokemon FireRed"
        # Only accept if score is reasonably high
        if best_match and best_score > 0.65:
            return str(best_match)
        return None

class GameScanner(QThread):
    finished_scanning = Signal(list)

    @staticmethod
    def is_cloud_root(path_obj):
        try:
            return str(path_obj).lower() == str(CLOUD_ROMS_DIR).lower()
        except Exception:
            return False

    def choose_system_for_file(self, file_path, candidate_ids):
        if not candidate_ids:
            return None
        if len(candidate_ids) == 1:
            return candidate_ids[0]

        parent_name = file_path.parent.name.lower()
        for sid in candidate_ids:
            if sid in parent_name:
                return sid

        name_l = file_path.stem.lower()
        keyword_map = {
            "ps2": ["ps2", "playstation 2"],
            "ps3": ["ps3", "playstation 3"],
            "psp": ["psp"],
            "gamecube": ["gamecube", "gc"],
            "wii": ["wii"],
        }
        for sid in candidate_ids:
            for kw in keyword_map.get(sid, []):
                if kw in name_l:
                    return sid

        priority = ["ps2", "psp", "gamecube", "wii", "ps3"]
        for sid in priority:
            if sid in candidate_ids:
                return sid
        return candidate_ids[0]

    def run(self):
        games = []
        seen_paths = set()
        rom_roots = get_rom_roots()
        for rom_root in rom_roots:
            is_cloud_root = self.is_cloud_root(rom_root)

            # Folder-based games (e.g., PS3 with EBOOT.BIN)
            if not is_cloud_root:
                for sid, cfg in SYSTEMS_DEFS.items():
                    if not cfg.get("folder_game"):
                        continue
                    emulator = cfg.get("emulator")
                    system_name = cfg.get("name", sid.upper())
                    for d in rom_root.iterdir():
                        if not d.is_dir():
                            continue
                        eboot = d / "EBOOT.BIN"
                        if not eboot.exists():
                            # common path for PS3 folder dumps
                            eboot = d / "PS3_GAME" / "USRDIR" / "EBOOT.BIN"
                        if eboot.exists():
                            key = str(d).lower()
                            if key in seen_paths:
                                continue
                            games.append(Game(d, system_id=sid, emulator=emulator, system_name=system_name, launch_path=d))
                            seen_paths.add(key)

            file_iter = rom_root.iterdir()
            for f in file_iter:
                if not f.is_file():
                    continue
                ext = f.suffix.lower()

                candidate_ids = EXTENSION_INDEX.get(ext, [])
                if not candidate_ids:
                    continue

                # Prefer CUE over BIN for disc images.
                if ext == ".bin":
                    cue = f.with_suffix(".cue")
                    if cue.exists():
                        continue

                sid = self.choose_system_for_file(f, candidate_ids)
                if not sid:
                    continue
                cfg = SYSTEMS_DEFS.get(sid, {})
                emulator = cfg.get("emulator")
                system_name = cfg.get("name", sid.upper())
                key = str(f).lower()
                if key in seen_paths:
                    continue
                games.append(Game(f, system_id=sid, emulator=emulator, system_name=system_name, launch_path=f))
                seen_paths.add(key)
        self.finished_scanning.emit(games)


class MetadataRefreshWorker(QThread):
    game_updated = Signal(str)
    completed = Signal()

    def __init__(self, games, force=False):
        super().__init__()
        self.games = games
        self.force = force

    def run(self):
        for game in self.games:
            cache = METADATA_MANAGER.get_cached_metadata(game.filename)
            has_cover = bool(cache.get('cover'))
            has_title = bool(cache.get('title'))
            if not self.force and has_cover and has_title:
                continue
            try:
                METADATA_MANAGER.fetch_and_cache(game.filename, game.system)
                self.game_updated.emit(game.filename)
            except Exception:
                continue
        self.completed.emit()

# --- Dialogs ---
class CustomizeDialog(QWidget):
    saved = Signal()
    cancelled = Signal()

    def __init__(self, parent=None, game=None):
        super().__init__(parent)
        self.game = game
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet("""
            QWidget { background-color: rgba(10, 15, 30, 240); }
            QLabel { color: white; font-size: 18px; font-weight: bold; font-family: 'Segoe UI'; }
            QLineEdit { 
                background-color: #202035; color: white; padding: 10px; 
                border: 1px solid #444; border-radius: 6px; font-size: 16px; 
            }
            QPushButton { 
                background-color: #333; color: white; border: none; padding: 10px; border-radius: 6px; font-weight: bold;
            }
            QPushButton:hover { background-color: #444; }
            #SaveButton { background-color: #00f0ff; color: #111; }
            #SaveButton:hover { background-color: #00d0df; }
        """)
        
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignCenter)
        
        # Container
        container = QFrame()
        container.setFixedSize(620, 420)
        container.setStyleSheet("background-color: #0d0d18; border: 1px solid #333; border-radius: 16px;")
        
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(30, 30, 30, 30)
        vbox.setSpacing(14)
        
        # Title
        vbox.addWidget(QLabel("EDIT METADATA"))
        
        # Input
        self.input = QLineEdit()
        self.input.setText(game.display_name if game else "")
        self.input.setPlaceholderText("Display Title")
        vbox.addWidget(self.input)

        cover_row = QHBoxLayout()
        self.cover_input = QLineEdit()
        self.cover_input.setPlaceholderText("Custom Cover Image Path")
        current_cover = METADATA_MANAGER.get_cached_metadata(game.filename).get('cover') if game else ""
        if current_cover:
            resolved = METADATA_MANAGER.resolve_local_asset(current_cover)
            self.cover_input.setText(resolved or current_cover)
        cover_btn = QPushButton("Browse Cover")
        cover_btn.clicked.connect(lambda: self.pick_file(self.cover_input))
        cover_row.addWidget(self.cover_input)
        cover_row.addWidget(cover_btn)
        vbox.addLayout(cover_row)

        bg_row = QHBoxLayout()
        self.bg_input = QLineEdit()
        self.bg_input.setPlaceholderText("Custom Background Image Path")
        current_bg = METADATA_MANAGER.get_cached_metadata(game.filename).get('background') if game else ""
        if current_bg:
            resolved = METADATA_MANAGER.resolve_local_asset(current_bg)
            self.bg_input.setText(resolved or current_bg)
        bg_btn = QPushButton("Browse Background")
        bg_btn.clicked.connect(lambda: self.pick_file(self.bg_input))
        bg_row.addWidget(self.bg_input)
        bg_row.addWidget(bg_btn)
        vbox.addLayout(bg_row)
        
        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()
        
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.cancel)
        save = QPushButton("Save")
        save.setObjectName("SaveButton")
        save.clicked.connect(self.save)
        
        btns.addWidget(cancel)
        btns.addWidget(save)
        
        vbox.addLayout(btns)
        main_layout.addWidget(container)
        
        # Resize to full overlay
        if parent:
            self.resize(parent.size())

    def showEvent(self, event):
        self.input.setFocus()
        super().showEvent(event)

    def pick_file(self, target_input):
        path, _ = QFileDialog.getOpenFileName(self, "Select Image", str(BASE_DIR), "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            target_input.setText(path)

    def save(self):
        new_name = self.input.text().strip()
        if self.game and new_name:
            self.game.display_name = new_name
            updates = {'title': new_name}

            cover = self.cover_input.text().strip()
            if cover:
                try:
                    rel_cover = str(Path(cover).resolve().relative_to(BASE_DIR.resolve())).replace('\\', '/')
                except Exception:
                    rel_cover = cover
                updates['cover'] = rel_cover
                self.game.cover_path = METADATA_MANAGER.resolve_local_asset(rel_cover) or cover

            bg = self.bg_input.text().strip()
            if bg:
                try:
                    rel_bg = str(Path(bg).resolve().relative_to(BASE_DIR.resolve())).replace('\\', '/')
                except Exception:
                    rel_bg = bg
                updates['background'] = rel_bg
                self.game.bg_path = METADATA_MANAGER.resolve_local_asset(rel_bg) or bg

            save_metadata(self.game.filename, 'title', new_name)
            METADATA_MANAGER.save_custom_override(self.game.filename, updates)
            self.saved.emit()
        self.close()

    def cancel(self):
        self.cancelled.emit()
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape: self.cancel()
        elif event.key() == Qt.Key_Return: self.save()

# --- Start Menu Overlay ---
class StartMenu(QWidget):
    close_requested = Signal()
    option_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.SubWindow)
        # self.setAttribute(Qt.WA_TranslucentBackground) # Handled by stylesheet
        
        self.options = [
            "Settings", 
            "Add Games", 
            "Game Store", 
            "Customize", 
            "Refresh Metadata",
            "Reset to Default", 
            "Exit Launcher"
        ]
        self.selected_index = 0
        self.item_labels = []

        # UI Construction
        main_layout = QVBoxLayout(self)
        main_layout.setAlignment(Qt.AlignCenter)
        
        # Glass Panel
        self.panel = QFrame()
        self.panel.setObjectName("GlassPanel")
        self.panel.setFixedSize(400, 600)
        
        # Style
        self.setStyleSheet("""
            #GlassPanel {
                background-color: rgba(10, 15, 30, 240);
                border-radius: 24px;
                border: 1px solid rgba(255, 255, 255, 30);
            }
            QLabel {
                font-family: 'Segoe UI';
                color: rgba(255, 255, 255, 0.7);
            }
        """)

        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(0, 40, 0, 40)
        panel_layout.setSpacing(15)

        # Header
        hdr = QLabel("SYSTEM MENU")
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet("font-size: 14px; letter-spacing: 2px; font-weight: bold; color: rgba(255,255,255,0.4); margin-bottom: 20px;")
        panel_layout.addWidget(hdr)

        # Items
        for txt in self.options:
            lbl = QLabel(txt)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFont(QFont("Segoe UI", 20))
            lbl.setFixedHeight(50)
            panel_layout.addWidget(lbl)
            self.item_labels.append(lbl)

        panel_layout.addStretch()
        
        # Footer
        ftr = QLabel("A Select  •  B Close")
        ftr.setAlignment(Qt.AlignCenter)
        ftr.setStyleSheet("font-size: 14px; color: rgba(255,255,255,0.3);")
        panel_layout.addWidget(ftr)

        main_layout.addWidget(self.panel)
        
        self.resize(1920, 1080) # Default large, will adjust
        self.update_ui()

    def update_ui(self):
        for i, lbl in enumerate(self.item_labels):
            if i == self.selected_index:
                # Highlight
                lbl.setStyleSheet("""
                    color: #00f0ff;
                    font-weight: bold;
                    background-color: rgba(0, 240, 255, 0.1);
                    border-left: 4px solid #00f0ff;
                """)
            else:
                lbl.setStyleSheet("""
                    color: rgba(255, 255, 255, 0.7);
                    font-weight: normal;
                    background-color: transparent;
                    border: none;
                """)

    def navigate(self, delta):
        self.selected_index = (self.selected_index + delta) % len(self.options)
        self.update_ui()
    
    def select(self):
        opt = self.options[self.selected_index]
        self.option_selected.emit(opt)

# --- UI Items ---
class GameCardItem(QGraphicsPixmapItem):
    def __init__(self, game, w, h, index=None, click_callback=None):
        super().__init__()
        self.game = game
        self.w, self.h = w, h
        self.index = index
        self.click_callback = click_callback
        self.base_y = 0
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        
        # Load Raw Pixmap
        if game.cover_path:
            raw = QPixmap(game.cover_path)
            if raw.isNull():
                 raw = QPixmap(w, h)
                 raw.fill(QColor("#2a2a40"))
        else:
            raw = QPixmap(w, h)
            raw.fill(QColor("#2a2a40"))

        # Create the final card surface
        final_pixmap = QPixmap(w, h)
        final_pixmap.fill(Qt.transparent)
        
        painter = QPainter(final_pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # Draw scaled image centered (Contain/Fit)
        scaled = raw.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        x = (w - scaled.width()) / 2
        y = (h - scaled.height()) / 2
        
        # Draw shadow
        # painter.setBrush(QColor(0,0,0,100))
        # painter.setPen(Qt.NoPen)
        # painter.drawRoundedRect(x+5, y+5, scaled.width(), scaled.height(), 4, 4)

        painter.drawPixmap(int(x), int(y), scaled)
        painter.end()
        
        self.setPixmap(final_pixmap)
        self.setTransformOriginPoint(w/2, h/2)
        
        # State
        self.is_focused = False
        self.target_y = 0

    def set_focus(self, focused):
        self.is_focused = focused
        # We handle animation in main loop or property anim, 
        # but for simplicity setting immediate values or using simple logic here
        if focused:
            # Lift up effect (NO BOX)
            self.setScale(1.15)
            self.setY(self.base_y - 40) # Float up
            self.setZValue(100)
            self.setOpacity(1.0)
        else:
            self.setScale(1.0)
            self.setY(self.base_y)
            self.setZValue(0)
            self.setOpacity(0.6) # Dim non-selected items

    def mousePressEvent(self, event):
        if self.click_callback is not None and self.index is not None:
            self.click_callback(self.index)
        super().mousePressEvent(event)

# --- Main Application ---
class MainWindow(QMainWindow):
    restore_signal = Signal() # Signal to process on GUI thread

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Retrobat Link")
        self.setWindowFlags(Qt.FramelessWindowHint)
        
        # Get screen size
        screen_geo = QApplication.primaryScreen().geometry()
        self.setGeometry(screen_geo)
        
        # Master Style
        self.setStyleSheet("""
            QMainWindow { background-color: #050510; }
            QLabel { color: white; font-family: 'Segoe UI'; }
        """)

        # Background Image with Blur
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(self.rect())
        self.bg_label.setScaledContents(True)
        # Add opacity/dim
        self.dim_layer = QWidget(self)
        self.dim_layer.setGeometry(self.rect())
        self.dim_layer.setStyleSheet("background-color: rgba(0,0,0,180);")

        # Main Layout (Stack)
        self.stack = QStackedWidget(self)
        self.setCentralWidget(self.stack)

        # Library Page
        self.central = QWidget()
        self.stack.addWidget(self.central)
        layout = QVBoxLayout(self.central)
        layout.setContentsMargins(60, 60, 60, 60)
        
        # Discover Page
        self.discover_view = DiscoverWidget()
        self.discover_view.request_close.connect(lambda: self.stack.setCurrentWidget(self.central))
        self.discover_view.library_updated.connect(lambda: self.scanner.start())
        self.stack.addWidget(self.discover_view)

        # Header
        header = QHBoxLayout()
        title = QLabel("Library")
        title.setFont(QFont("Segoe UI", 28, QFont.Bold))
        header.addWidget(title)
        header.addStretch()
        
        time_lbl = QLabel("12:00 PM") # Placeholder
        time_lbl.setFont(QFont("Segoe UI", 16))
        header.addWidget(time_lbl)
        
        layout.addLayout(header)
        layout.addStretch(1)

        # Carousel (QGraphicsView)
        self.scene = QGraphicsScene()
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background: transparent; border: none;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setFixedHeight(500) # Increased height for breathing room
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setRenderHint(QPainter.SmoothPixmapTransform)
        
        layout.addWidget(self.view)
        layout.addStretch(1)

        # Game Info
        self.info_title = QLabel("Loading...")
        self.info_title.setFont(QFont("Segoe UI", 32, QFont.Bold))
        self.info_title.setWordWrap(True) # Wrap long titles
        self.info_sys = QLabel("")
        self.info_sys.setFont(QFont("Segoe UI", 18))
        self.info_sys.setStyleSheet("color: #00f0ff;")
        
        layout.addWidget(self.info_title)
        layout.addWidget(self.info_sys)
        
        # Footer
        footer = QLabel("A: Play  •  B: Back  •  X: Filter  •  Start: Settings")
        footer.setStyleSheet("color: #888; margin-top: 20px;")
        layout.addWidget(footer)

        # State
        self.card_w, self.card_h = 240, 360 # Slightly larger
        self.gap = 50 # Increased gap
        self.games = []
        self.items = []
        self.focus_idx = 0
        
        # Controller
        self.controller = XInputController()
        self.controller.button_pressed.connect(self.on_controller)

        # Restore signal
        self.restore_signal.connect(self.on_restore)

        # Initial Load
        self.scanner = GameScanner()
        self.scanner.finished_scanning.connect(self.populate_library)
        self.scanner.start()
        self.metadata_worker = None
        self.metadata_refresh_pending = False
        self.shortcut_info_cache = {}
        self.launch_in_progress = False
        
        # Menu
        self.menu_overlay = StartMenu(self)
        self.menu_overlay.hide()
        self.menu_active = False
        self.menu_overlay.option_selected.connect(self.handle_menu_action)

    def on_card_clicked(self, idx):
        if idx < 0 or idx >= len(self.games):
            return
        if self.launch_in_progress:
            return
        if self.focus_idx != idx:
            self.focus_idx = idx
            self.update_selection()
        self.launch_game()

    def resolve_emulator_path(self, configured_exe):
        configured_path = Path(configured_exe)
        candidates = []

        # Absolute path from config
        if configured_path.is_absolute():
            candidates.append(configured_path)
        else:
            # Relative paths from config
            candidates.append(EMULATORS_DIR / configured_exe)
            candidates.append(BASE_DIR / configured_exe)

        stem = configured_path.stem
        # Common local naming patterns in this workspace.
        candidates.extend([
            EMULATORS_DIR / f"{stem}.exe",
            EMULATORS_DIR / f"{stem}.lnk",
            EMULATORS_DIR / f"{stem} - Shortcut.lnk",
        ])

        # Last resort: any shortcut starting with emulator stem
        candidates.extend(sorted(EMULATORS_DIR.glob(f"{stem}*.lnk")))

        for cand in candidates:
            if cand.exists():
                return cand

        # Allow configured name from PATH as fallback
        return configured_exe

    def get_shortcut_info(self, shortcut_path):
        shortcut_path = str(Path(shortcut_path).resolve())
        if shortcut_path in self.shortcut_info_cache:
            return self.shortcut_info_cache[shortcut_path]

        ps_safe_path = shortcut_path.replace("'", "''")
        ps_cmd = (
            "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('"
            f"{ps_safe_path}"
            "'); "
            "$o=[pscustomobject]@{"
            "TargetPath=$s.TargetPath;"
            "Arguments=$s.Arguments;"
            "WorkingDirectory=$s.WorkingDirectory"
            "}; $o | ConvertTo-Json -Compress"
        )

        info_raw = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            text=True,
        ).strip()
        info = json.loads(info_raw) if info_raw else {}
        self.shortcut_info_cache[shortcut_path] = info
        return info

    def get_launch_working_dir(self, target_path):
        p = Path(target_path)
        if p.exists() and p.is_dir():
            return str(p)
        return str(p.parent)

    def launch_with_shortcut(self, shortcut_path, rom_path, launch_cwd=None):
        info = self.get_shortcut_info(shortcut_path)

        target = (info.get("TargetPath") or "").strip()
        if not target:
            raise RuntimeError(f"Shortcut has no target: {shortcut_path}")

        target_path = Path(target)
        if not target_path.exists():
            raise FileNotFoundError(f"Shortcut target not found: {target_path}")

        raw_args = (info.get("Arguments") or "").strip()
        if raw_args:
            merged = raw_args.replace("%1", f'"{rom_path}"').replace("%L", f'"{rom_path}"')
            if merged == raw_args:
                merged = f'{raw_args} "{rom_path}"'
            emulator_args = shlex.split(merged, posix=False)
        else:
            emulator_args = [rom_path]

        working_dir = (launch_cwd or "").strip()
        if not working_dir:
            working_dir = (info.get("WorkingDirectory") or "").strip()
        if not working_dir:
            working_dir = str(target_path.parent)

        return subprocess.Popen([str(target_path), *emulator_args], cwd=working_dir)

    def resizeEvent(self, event):
        self.menu_overlay.resize(self.rect().size())
        super().resizeEvent(event)

    @Slot(list)
    def populate_library(self, games):
        self.scene.clear()
        self.items = []
        self.focus_idx = 0
        self.games = games
        if not games:
            self.info_title.setText("No Games Found")
            # Add dummy item to see UI
            return

        for i, game in enumerate(games):
            item = GameCardItem(
                game,
                self.card_w,
                self.card_h,
                index=i,
                click_callback=self.on_card_clicked,
            )
            x_pos = i * (self.card_w + self.gap)
            item.setPos(x_pos, 0)
            self.scene.addItem(item)
            self.items.append(item)
        
        self.update_selection()
        if not self.metadata_refresh_pending:
            self.start_metadata_refresh(force=False)

    def start_metadata_refresh(self, force=False):
        if self.metadata_worker and self.metadata_worker.isRunning():
            return
        if not self.games:
            return
        self.metadata_refresh_pending = True
        self.metadata_worker = MetadataRefreshWorker(self.games, force=force)
        self.metadata_worker.game_updated.connect(self.on_metadata_game_updated)
        self.metadata_worker.completed.connect(self.on_metadata_refresh_completed)
        self.metadata_worker.start()

    def on_metadata_game_updated(self, filename):
        for idx, game in enumerate(self.games):
            if game.filename != filename:
                continue
            refreshed = Game(game.path)
            self.games[idx] = refreshed
            if idx < len(self.items):
                self.scene.removeItem(self.items[idx])
                item = GameCardItem(
                    refreshed,
                    self.card_w,
                    self.card_h,
                    index=idx,
                    click_callback=self.on_card_clicked,
                )
                item.setPos(idx * (self.card_w + self.gap), 0)
                self.scene.addItem(item)
                self.items[idx] = item
            if idx == self.focus_idx:
                self.update_selection()
            break

    def on_metadata_refresh_completed(self):
        self.metadata_refresh_pending = False

    def update_selection(self):
        if not self.items: return
        
        # Update focus state
        for i, item in enumerate(self.items):
            item.set_focus(i == self.focus_idx)
            
        # Update text
        game = self.games[self.focus_idx]
        # Use stored display_name if available, else fallback to filename
        title = getattr(game, 'display_name', game.filename.replace(game.ext, ""))
        self.info_title.setText(title)
        self.info_sys.setText(game.system)
        
        # Update Background
        if game.bg_path:
            self.bg_label.setPixmap(QPixmap(game.bg_path))
        else:
            self.bg_label.clear()

        # Scroll
        target_item = self.items[self.focus_idx]
        center_x = target_item.pos().x() + (self.card_w / 2)
        # Center in view
        view_w = self.view.width()
        scroll_x = center_x - (view_w / 2)
        self.view.horizontalScrollBar().setValue(int(scroll_x))

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key_Escape:
            self.toggle_menu()
            return

        if self.menu_active:
            if key == Qt.Key_Up: self.menu_overlay.navigate(-1)
            elif key == Qt.Key_Down: self.menu_overlay.navigate(1)
            elif key == Qt.Key_Return: self.menu_overlay.select()
            elif key == Qt.Key_Backspace: self.toggle_menu()
            return

        if not self.games: return

        if key == Qt.Key_Right:
            if self.focus_idx < len(self.games) - 1:
                self.focus_idx += 1
                self.update_selection()
        elif key == Qt.Key_Left:
            if self.focus_idx > 0:
                self.focus_idx -= 1
                self.update_selection()
        elif key == Qt.Key_Return: # Enter
            self.launch_game()

    def on_controller(self, btn):
        # Check if Discover is active
        if self.stack.currentWidget() == self.discover_view:
            self.route_to_discover(btn)
            return

        # print(f"Controller: {btn}") # Debug
        if btn == 'START':
            self.toggle_menu()
            return
            
        if self.menu_active:
            if btn == 'UP': self.menu_overlay.navigate(-1)
            elif btn == 'DOWN': self.menu_overlay.navigate(1)
            elif btn == 'A': 
                self.menu_overlay.select()
            elif btn == 'B': 
                self.toggle_menu()
            return

        if not self.games: return 
        
        if btn == 'RIGHT':
            if self.focus_idx < len(self.games) - 1:
                self.focus_idx += 1
                self.update_selection()
        elif btn == 'LEFT':
            if self.focus_idx > 0:
                self.focus_idx -= 1
                self.update_selection()
        elif btn == 'A':
            self.launch_game()

    def route_to_discover(self, btn):
        if hasattr(self.discover_view, 'handle_controller_button'):
            self.discover_view.handle_controller_button(btn)
            return

        from PySide6.QtGui import QKeyEvent
        key_map = {
            'UP': Qt.Key_Up, 'DOWN': Qt.Key_Down,
            'LEFT': Qt.Key_Left, 'RIGHT': Qt.Key_Right,
            'A': Qt.Key_Return, 'B': Qt.Key_Escape,
            'X': Qt.Key_Space, 'Y': Qt.Key_Tab
        }
        if btn == 'B':
            self.stack.setCurrentWidget(self.central)
            return
        if btn in key_map:
            key = key_map[btn]
            focus_w = self.discover_view.focusWidget() or self.discover_view
            QApplication.postEvent(focus_w, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))
            QApplication.postEvent(focus_w, QKeyEvent(QEvent.KeyRelease, key, Qt.NoModifier))

    def toggle_menu(self):
        self.menu_active = not self.menu_active
        if self.menu_active:
            # Blur
            eff = QGraphicsBlurEffect()
            eff.setBlurRadius(20)
            self.central.setGraphicsEffect(eff)
            self.menu_overlay.show()
            self.menu_overlay.raise_()
            self.menu_overlay.setFocus() # Ensure focus hits overlay logic if needed
        else:
            self.central.setGraphicsEffect(None)
            self.menu_overlay.hide()
            self.activateWindow() # Return focus to main

    def handle_menu_action(self, action):
        if action == "Exit Launcher":
            QApplication.quit()
        elif action == "Customize":
            self.open_customization()
        elif action == "Refresh Metadata":
            self.toggle_menu()
            self.show_notification("Refreshing metadata...")
            self.start_metadata_refresh(force=True)
        elif action == "Game Store" or action == "Discover":
            self.toggle_menu() # Close the menu
            self.stack.setCurrentWidget(self.discover_view)
            self.discover_view.setFocus()
        else:
            # Show feedback for unimplemented features
            self.show_notification(f"Feature: {action}")

    def open_customization(self):
        if not self.games: return
        self.toggle_menu() # Close Start Menu
        
        game = self.games[self.focus_idx]
        self.customize_dialog = CustomizeDialog(self, game)
        self.customize_dialog.saved.connect(self.on_game_renamed)
        self.customize_dialog.show()

    def on_game_renamed(self):
        # Update current view
        game = self.games[self.focus_idx]
        self.info_title.setText(game.display_name)
        # Maybe refresh whole list if sort changes? For now just visual update
        self.show_notification(f"Renamed to: {game.display_name}")

    def show_notification(self, text):
        if not hasattr(self, 'notification_label'):
            self.notification_label = QLabel(self)
            self.notification_label.setStyleSheet("""
                background-color: rgba(0, 0, 0, 200);
                color: #00f0ff;
                border: 2px solid #00f0ff;
                border-radius: 15px;
                padding: 15px 30px;
                font-size: 24px;
                font-weight: bold;
            """)
            self.notification_label.setAlignment(Qt.AlignCenter)
            self.notification_label.hide()
            
        self.notification_label.setText(text)
        self.notification_label.adjustSize()
        
        # Center
        geo = self.geometry()
        x = (geo.width() - self.notification_label.width()) // 2
        y = (geo.height() - self.notification_label.height()) // 2
        self.notification_label.move(x, y)
        
        self.notification_label.show()
        self.notification_label.raise_()
        
        QTimer.singleShot(2000, self.notification_label.hide)

    def launch_game(self):
        if self.launch_in_progress:
            return

        game = self.games[self.focus_idx]

        exe = game.emulator
        if not exe and game.system_id:
            exe = SYSTEMS_DEFS.get(game.system_id, {}).get("emulator")
        if not exe and game.ext in EXTENSION_INDEX:
            sid = EXTENSION_INDEX[game.ext][0]
            exe = SYSTEMS_DEFS.get(sid, {}).get("emulator")

        if not exe:
            print(f"No emulator for {game.ext}")
            return

        emu_path = self.resolve_emulator_path(exe)
        emu_suffix = Path(emu_path).suffix.lower() if isinstance(emu_path, Path) else ""
        if isinstance(emu_path, Path):
            print(f"Resolved emulator: {emu_path}")

        target_to_launch = game.launch_path if hasattr(game, "launch_path") else game.path
        launch_cwd = self.get_launch_working_dir(target_to_launch)
        cmd = [str(emu_path), str(target_to_launch)]
        print(f"Launching: {cmd}")

        self.launch_in_progress = True
        
        self.hide()
        
        def run():
            proc = None
            try:
                if emu_suffix == ".lnk":
                    proc = self.launch_with_shortcut(
                        str(emu_path),
                        str(target_to_launch),
                        launch_cwd=launch_cwd,
                    )
                else:
                    proc = subprocess.Popen(cmd, cwd=launch_cwd)
                if proc is not None:
                    proc.wait()
            except Exception as e:
                print(f"Launch error: {e}")
            self.restore_signal.emit()
            
        threading.Thread(target=run, daemon=True).start()

    @Slot()
    def on_restore(self):
        self.launch_in_progress = False
        self.showFullScreen()
        self.activateWindow()
        self.raise_()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
