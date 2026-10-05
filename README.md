# Sekiro Save Slot Manager

A small Windows GUI utility for **Sekiro: Shadows Die Twice** that copies exactly one character slot from one `S0000.sl2` save into a selected slot in another save.

## Features

- Scans all 10 character slots and identifies recognizable character data.
- Shows useful slot properties such as HP, Attack Power, NG+, Souls, Skill Points, inventory count, playtime, and Steam IDs.
- Shows both the source account SteamID and destination account SteamID before copying.
- Lets you choose which account SteamID to keep when the two saves differ.
- Uses the **destination save as the base**, so the other 9 destination character slots are preserved.
- Updates only the selected destination character slot and the required roster/SteamID/checksum fields.
- Recalculates Sekiro slot and settings MD5 checksums.
- Performs extensive pre-write verification.
- Reads the written save back from disk and verifies it again.
- Creates a timestamped full backup before replacing the destination save.

## Important warning

Always close Sekiro before using the tool. Make a separate backup of your `S0000.sl2` before testing any save editor. Steam Cloud can overwrite local saves, so disable/pause it while testing if necessary.

This tool is provided as-is. Use it at your own risk.

## Run from source

The source uses only Python's standard library (`tkinter`, `hashlib`, `pathlib`, etc.). No third-party Python package is required to run it from source on a normal Windows Python installation with Tk available.

1. Install Python 3 from https://www.python.org/downloads/windows/
2. During installation, enable **Add Python to PATH**.
3. Open Command Prompt in the repository folder.
4. Run:

```bat
python src\SekiroSaveSlotCopier.py
```

## Build a standalone Windows EXE

The recommended release build uses PyInstaller. PyInstaller bundles the Python application so end users do not need Python installed.

### One-time setup

```bat
python -m pip install --upgrade pyinstaller
```

### Build

```bat
python -m PyInstaller --noconfirm --clean --onefile --windowed --name SekiroSaveSlotCopier src\SekiroSaveSlotCopier.py
```

The executable will be created at:

```text
dist\SekiroSaveSlotCopier.exe
```

You can alternatively run `build_windows.bat`.


## How copying works

The destination save is loaded as the base. The selected source slot's checksum + 1 MiB character block is copied into the selected destination slot. The remaining nine destination character blocks are compared byte-for-byte before the file is written.

The imported character's SteamID is changed to the selected account SteamID. The selected slot checksum is recalculated. The destination profile occupancy list is preserved and only the selected destination slot is activated. The settings checksum is then recalculated.

Before writing, the program verifies:

- the nine non-selected destination slots are byte-for-byte unchanged;
- the imported payload matches the selected source payload except for the intentional character SteamID change;
- the imported slot checksum is correct;
- all 10 character-slot checksums are correct;
- the settings checksum is correct;
- the account SteamID and imported character SteamID agree;
- the BND4 signature and file size remain valid.

After writing, the file is read back from disk and the checksum checks are repeated.

## SteamID choice

For normal transfers, **DESTINATION account SteamID** is recommended. This keeps the resulting save associated with the account represented by the destination save.

The SOURCE option is provided for advanced use only.

### Credits

- [uberhalit](https://github.com/uberhalit)
- [Ahmed alfizari](https://github.com/alfizari)