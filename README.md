# Universal Emulator Launcher (Windows)

A single-library launcher for ROMs that auto-detects console/system by file extension and launches the matching emulator.

## Folder Layout

The launcher supports both your current and target naming:

- `Retrobat/roms` or `Retrobat/Roms`
- `Retrobat/emulators` or `Retrobat/emulator`

All ROM files must be directly in the ROM folder.
All emulator files must be directly in the emulator folder.

Supported ROM extensions:

- `.gba` (Game Boy Advance)
- `.nds` (Nintendo DS)
- `.iso` (PlayStation 2)
- `.nsp` / `.xci` (Nintendo Switch)

Supported emulator executable detection:

- `.exe`
- `.lnk` (Windows shortcuts)

## Emulator Mapping

The launcher auto-maps emulator files by filename pattern:

- `.gba` -> name contains `visualboyadvance` or `vba`
- `.nds` -> name contains `desmume`
- `.iso` -> name contains `pcsx2`
- `.nsp` / `.xci` -> name contains `ryujinx`

## UI Features

- Unified library for all ROMs
- Grid view and list view (`Tab`)
- Fullscreen (`F11`)
- Keyboard navigation (`Arrow Keys`, `Enter`)
- Controller navigation via Windows XInput (`D-Pad`, `A`, `B`, `Start`, `Back`)
- System filter (`F` to cycle, `0-4` direct)
- Auto library refresh on startup
- Manual rescan (`R`)

## Run

1. Open terminal in `Retrobat`
2. Launch:

```powershell
python launcher.py
```

No external dependencies are required (uses standard-library `tkinter`).

## Notes

- If an emulator is not detected, rename the emulator executable/shortcut so it includes the expected keyword (for example `ryujinx.exe`, `pcsx2.exe`, `desmume.exe`, `visualboyadvance.exe`).
- Shortcut (`.lnk`) launch is supported; the launcher resolves shortcut target and passes the ROM path as argument.
