#!/usr/bin/env python3
"""
Sekiro Save Slot Copier v1.0

IMPORTANT:
This is for SEKIRO's S0000.sl2 format and follows the layout/algorithm used by
uberhalit/SimpleSekiroSavegameHelper.

Sekiro (per that source):
  slot payload = 0x100000 bytes
  slot checksum = 0x10 bytes
  slot stride = 0x100010
  first checksum = 0x300
  first slot SteamID = 0x34164
  settings checksum = 0xA003A0
  settings SteamID = 0xA003D4
  general settings = 0xA003DC, 0x2C
  user settings = 0xA029E0, 0x454
  settings payload length = 0x60000

The safest way to make a single imported slot behave like a normal imported
Sekiro slot is to preserve the source file's global save metadata, while
putting the destination's other nine character slots back. This is analogous
to the original helper's proven "source slots + destination settings" import.

Earlier versions therefore:
  - starts from SOURCE, so slot-associated global metadata stays coherent;
  - replaces all 9 non-selected character slots with DESTINATION's versions;
  - leaves the selected destination slot as SOURCE slot;
  - copies destination general/user settings;
  - uses destination account SteamID;
  - patches SteamID in every active slot to destination SteamID, just like
    the original helper;
  - recalculates every active slot checksum and the settings checksum;
  - creates a full backup of the destination before writing.

It also updates Sekiro USER_DATA010 slot-occupancy bytes (10 bytes at payload + 0xD4),
which are separate from the character slot payloads and are required for the
game to display the imported slot. It deliberately does NOT use an Elden Ring-style ProfileSummary.
"""

import hashlib
import re
import os
import shutil
import re
import tempfile
import tkinter as tk
from pathlib import Path
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

MAGIC = b"BND4\x00\x00\x00\x00"
MIN_LENGTH = 0x00A603B0

SLOT_COUNT = 10
SLOT_CHECKSUM_LENGTH = 0x10
SLOT_DATA_LENGTH = 0x00100000
SLOT_STRIDE = SLOT_DATA_LENGTH + SLOT_CHECKSUM_LENGTH

FIRST_SLOT_CHECKSUM = 0x00000300
SLOT_STEAM_ID = 0x00034164

SETTINGS_CHECKSUM = 0x00A003A0
SETTINGS_DATA_LENGTH = 0x00060000
SETTINGS_STEAM_ID = 0x00A003D4
GENERAL_SETTINGS = 0x00A003DC
GENERAL_SETTINGS_LENGTH = 0x2C
USER_SETTINGS = 0x00A029E0
USER_SETTINGS_LENGTH = 0x454

# USER_DATA010/profile payload layout (Sekiro):
# payload starts immediately after the 16-byte SETTINGS_CHECKSUM.
PROFILE_PAYLOAD = SETTINGS_CHECKSUM + SLOT_CHECKSUM_LENGTH
PROFILE_STEAM_ID_REL = 36
PROFILE_OCCUPANCY_REL = 212
PROFILE_OCCUPANCY = PROFILE_PAYLOAD + PROFILE_OCCUPANCY_REL


def load(path):
    data = bytearray(Path(path).read_bytes())
    if len(data) < MIN_LENGTH:
        raise ValueError(
            f"File is too small for a Sekiro S0000.sl2 save: {len(data):,} bytes."
        )
    if data[:8] != MAGIC:
        raise ValueError("File does not have the Sekiro BND4 signature.")
    return data


def slot_checksum_offset(slot):
    return FIRST_SLOT_CHECKSUM + slot * SLOT_STRIDE


def slot_data_offset(slot):
    return slot_checksum_offset(slot) + SLOT_CHECKSUM_LENGTH


def slot_steam_id_offset(slot):
    return SLOT_STEAM_ID + slot * SLOT_STRIDE


def get_slot_steam_id(data, slot):
    off = slot_steam_id_offset(slot)
    return int.from_bytes(data[off:off + 8], "little")


def get_settings_steam_id(data):
    return int.from_bytes(data[SETTINGS_STEAM_ID:SETTINGS_STEAM_ID + 8], "little")


def profile_occupancy(data, slot):
    if PROFILE_OCCUPANCY + slot >= len(data):
        return 0
    return data[PROFILE_OCCUPANCY + slot]


# Character properties documented by alfizari/Sekiro-Save-Editor. These are
# offsets inside each 0x100000-byte character payload.
CHAR_STEAM_ID = 0x33E54
CHAR_NG_PLUS = 0x33F34
CHAR_PLAYTIME = 0x33F80
CHAR_HP = 0x3446C
CHAR_GUARD = 0x34488
CHAR_SOULS = 0x344D0
CHAR_ATTACK = 0x3449C
CHAR_EMBLEMS = 0x3459A
CHAR_SKILL_POINTS = 0x345B4
CHAR_INVENTORY = 0x8F70C
CHAR_INVENTORY_LENGTH = 0x7000


def u32_at(data, off):
    return int.from_bytes(data[off:off + 4], "little")


def u64_at(data, off):
    return int.from_bytes(data[off:off + 8], "little")


def scan_slot(data, slot):
    start = slot_data_offset(slot)
    payload = data[start:start + SLOT_DATA_LENGTH]
    if len(payload) != SLOT_DATA_LENGTH:
        raise ValueError(f"Slot {slot} payload is truncated.")

    steam = u64_at(payload, CHAR_STEAM_ID)
    ng = payload[CHAR_NG_PLUS]
    playtime_seconds = u32_at(payload, CHAR_PLAYTIME)
    hp = u32_at(payload, CHAR_HP)
    guard = u32_at(payload, CHAR_GUARD)
    souls = u32_at(payload, CHAR_SOULS)
    attack = payload[CHAR_ATTACK]
    emblems = payload[CHAR_EMBLEMS]
    skills = u32_at(payload, CHAR_SKILL_POINTS)

    # Count non-empty inventory records using the same item representation
    # as alfizari/Sekiro-Save-Editor: each record is 16 bytes and the first
    # DWORD contains the item type/handle.
    inv = payload[CHAR_INVENTORY:CHAR_INVENTORY + CHAR_INVENTORY_LENGTH]
    inventory_count = 0
    for off in range(0, len(inv), 16):
        handle = int.from_bytes(inv[off:off + 4], "little")
        if handle != 0:
            inventory_count += 1

    marker = u32_at(payload, 0)
    divisor = u32_at(payload, 4)
    flag_structure_ok = marker == 0xFFFFFFFF and 0 < divisor <= 100000

    occupied_flag = profile_occupancy(data, slot) == 1
    steam_flag = steam != 0

    # Strong indicators are deliberately separated from weak/default-looking
    # values. We use these to label the UI, not to rewrite the save.
    indicators = []
    if occupied_flag:
        indicators.append("roster")
    if steam_flag:
        indicators.append("SteamID")
    if flag_structure_ok:
        indicators.append("event data")
    if inventory_count:
        indicators.append(f"{inventory_count} inventory")
    if hp or attack or ng or souls or skills or emblems:
        indicators.append("character stats")

    meaningful = occupied_flag or steam_flag or inventory_count > 0 or (
        flag_structure_ok and (hp or attack or ng or souls or skills or emblems)
    )

    return {
        "slot": slot,
        "occupied": occupied_flag,
        "steam_id": steam,
        "ng_plus": ng,
        "playtime_seconds": playtime_seconds,
        "hp": hp,
        "guard": guard,
        "souls": souls,
        "attack": attack,
        "emblems": emblems,
        "skill_points": skills,
        "inventory_count": inventory_count,
        "flag_structure_ok": flag_structure_ok,
        "meaningful": meaningful,
        "indicators": indicators,
    }


def format_slot(scan):
    if not scan["meaningful"]:
        return f"Slot {scan['slot']} — EMPTY / NO CHARACTER DATA DETECTED"
    state = "GAME DATA" if scan["occupied"] or scan["steam_id"] else "DATA DETECTED"
    total_minutes = scan["playtime_seconds"] // 60
    hours = total_minutes // 60
    minutes = total_minutes % 60
    details = (
        f"HP {scan['hp']} | Attack {scan['attack']} | NG+ {scan['ng_plus']} | "
        f"Playtime {hours}:{minutes:02d} | "
        f"Souls {scan['souls']:,} | Skills {scan['skill_points']} | "
        f"Inventory {scan['inventory_count']}"
    )
    return f"Slot {scan['slot']} — {state} — {details}"


def slot_is_used(data, slot):
    return get_slot_steam_id(data, slot) != 0


def copy_bytes(dst, src, offset, length):
    dst[offset:offset + length] = src[offset:offset + length]


def patch_slot_steam_id(data, slot, steam_id):
    off = slot_steam_id_offset(slot)
    data[off:off + 8] = int(steam_id).to_bytes(8, "little")


def recalc_slot_checksum(data, slot):
    checksum = slot_checksum_offset(slot)
    payload = checksum + SLOT_CHECKSUM_LENGTH
    digest = hashlib.md5(
        bytes(data[payload:payload + SLOT_DATA_LENGTH])
    ).digest()
    data[checksum:checksum + SLOT_CHECKSUM_LENGTH] = digest


def recalc_settings_checksum(data):
    payload = SETTINGS_CHECKSUM + SLOT_CHECKSUM_LENGTH
    digest = hashlib.md5(
        bytes(data[payload:payload + SETTINGS_DATA_LENGTH])
    ).digest()
    data[SETTINGS_CHECKSUM:SETTINGS_CHECKSUM + SLOT_CHECKSUM_LENGTH] = digest


def make_backup(path):
    p = Path(path)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    backup = p.with_name(
        f"{p.stem}_before_slot_copy_{stamp}{p.suffix}.bak"
    )
    n = 1
    while backup.exists():
        backup = p.with_name(
            f"{p.stem}_before_slot_copy_{stamp}_{n}{p.suffix}.bak"
        )
        n += 1
    shutil.copy2(p, backup)
    return backup


def atomic_write(path, data):
    p = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".tmp_", dir=str(p.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def steamid_text(value):
    if value == 0:
        return "0 (NOT SET)"
    return str(value)


def md5_for_slot_payload(data, slot):
    off = slot_data_offset(slot)
    return hashlib.md5(bytes(data[off:off + SLOT_DATA_LENGTH])).hexdigest()


def sha256_bytes(blob):
    return hashlib.sha256(bytes(blob)).hexdigest()


def verify_all_checksums(data):
    """Return a list of checksum errors across all 10 slots + settings."""
    errors = []
    for slot in range(SLOT_COUNT):
        c = slot_checksum_offset(slot)
        expected = hashlib.md5(bytes(data[c + SLOT_CHECKSUM_LENGTH:c + SLOT_CHECKSUM_LENGTH + SLOT_DATA_LENGTH])).digest()
        actual = bytes(data[c:c + SLOT_CHECKSUM_LENGTH])
        if actual != expected:
            errors.append(f"slot {slot} checksum mismatch")
    c = SETTINGS_CHECKSUM
    expected = hashlib.md5(bytes(data[c + SLOT_CHECKSUM_LENGTH:c + SLOT_CHECKSUM_LENGTH + SETTINGS_DATA_LENGTH])).digest()
    actual = bytes(data[c:c + SLOT_CHECKSUM_LENGTH])
    if actual != expected:
        errors.append("settings checksum mismatch")
    return errors


def copy_one_slot(source_path, destination_path, source_slot, destination_slot, steam_choice="destination"):
    """Copy exactly one character slot into an existing destination save.

    v1.0 deliberately starts from DESTINATION, not SOURCE. This is the key
    safety change: no source global/profile/settings data can overwrite the
    destination's nine existing characters.
    """
    source = load(source_path)
    dest_original = load(destination_path)

    if Path(source_path).resolve() == Path(destination_path).resolve():
        raise ValueError("Source and destination must be different files.")

    source_info = scan_slot(source, source_slot)
    if not source_info["meaningful"]:
        raise ValueError(f"Source slot {source_slot} does not contain recognizable character data.")

    source_account = get_settings_steam_id(source)
    destination_account = get_settings_steam_id(dest_original)
    source_slot_id = get_slot_steam_id(source, source_slot)
    destination_slot_id = get_slot_steam_id(dest_original, destination_slot)

    if source_account == 0 or destination_account == 0:
        raise ValueError("One of the saves has a zero account SteamID; refusing to copy.")

    if steam_choice not in ("destination", "source"):
        raise ValueError("Invalid SteamID choice.")

    kept_steam = destination_account if steam_choice == "destination" else source_account

    # Start from DESTINATION. Only the selected destination slot is replaced.
    # Every other byte of every other character slot is preserved.
    result = bytearray(dest_original)

    src_slot_off = slot_checksum_offset(source_slot)
    dst_slot_off = slot_checksum_offset(destination_slot)
    slot_total = SLOT_CHECKSUM_LENGTH + SLOT_DATA_LENGTH
    result[dst_slot_off:dst_slot_off + slot_total] = source[src_slot_off:src_slot_off + slot_total]

    # Preserve the destination account/settings by default. If the user
    # explicitly chooses SOURCE SteamID, change the save account identity too.
    result[SETTINGS_STEAM_ID:SETTINGS_STEAM_ID + 8] = kept_steam.to_bytes(8, "little")

    # The per-character SteamID is the same field documented two ways:
    # absolute 0x34164 for slot 0, or relative 0x33E54 inside each 1 MiB slot.
    imported_char_off = slot_data_offset(destination_slot) + CHAR_STEAM_ID
    result[imported_char_off:imported_char_off + 8] = kept_steam.to_bytes(8, "little")

    # Rebuild ONLY the selected destination slot checksum.
    recalc_slot_checksum(result, destination_slot)

    # Preserve destination roster occupancy and only activate the selected slot.
    result[PROFILE_OCCUPANCY:PROFILE_OCCUPANCY + SLOT_COUNT] = dest_original[PROFILE_OCCUPANCY:PROFILE_OCCUPANCY + SLOT_COUNT]
    result[PROFILE_OCCUPANCY + destination_slot] = 1

    # Recalculate only the global settings/profile checksum after its SteamID
    # and occupancy byte changed.
    recalc_settings_checksum(result)

    # --- Strong post-build verification before touching the user's file ---
    # 1) All non-selected character blocks must be byte-for-byte identical.
    unchanged_slots = []
    for slot in range(SLOT_COUNT):
        if slot == destination_slot:
            continue
        a = dest_original[slot_checksum_offset(slot):slot_checksum_offset(slot) + slot_total]
        b = result[slot_checksum_offset(slot):slot_checksum_offset(slot) + slot_total]
        if a != b:
            raise ValueError(f"SAFETY CHECK FAILED: destination slot {slot} changed unexpectedly.")
        unchanged_slots.append(slot)

    # 2) Imported payload must equal source payload everywhere except the
    #    character SteamID, and its checksum must match its final payload.
    src_payload = bytes(source[slot_data_offset(source_slot):slot_data_offset(source_slot) + SLOT_DATA_LENGTH])
    result_payload = bytes(result[slot_data_offset(destination_slot):slot_data_offset(destination_slot) + SLOT_DATA_LENGTH])
    expected_payload = bytearray(src_payload)
    expected_payload[CHAR_STEAM_ID:CHAR_STEAM_ID + 8] = kept_steam.to_bytes(8, "little")
    if result_payload != bytes(expected_payload):
        raise ValueError("SAFETY CHECK FAILED: imported slot differs from the selected source slot outside the SteamID field.")

    # 3) The selected slot checksum must be the MD5 of its final payload.
    c = slot_checksum_offset(destination_slot)
    expected_slot_checksum = hashlib.md5(result_payload).digest()
    if bytes(result[c:c + SLOT_CHECKSUM_LENGTH]) != expected_slot_checksum:
        raise ValueError("SAFETY CHECK FAILED: imported slot checksum is incorrect.")

    # 4) Every checksum in the final archive must verify.
    checksum_errors = verify_all_checksums(result)
    if checksum_errors:
        raise ValueError("SAFETY CHECK FAILED: " + "; ".join(checksum_errors))

    # 5) Account SteamID and selected character SteamID must agree.
    final_account = get_settings_steam_id(result)
    final_char_id = get_slot_steam_id(result, destination_slot)
    if final_account != kept_steam or final_char_id != kept_steam:
        raise ValueError("SAFETY CHECK FAILED: final SteamID fields do not agree.")

    # 6) File size/container signature must be unchanged.
    if len(result) != len(dest_original) or result[:8] != MAGIC:
        raise ValueError("SAFETY CHECK FAILED: destination container size/signature changed.")

    backup = make_backup(destination_path)
    atomic_write(destination_path, result)

    # Read the written file back and verify again. This catches write/replace
    # problems instead of trusting the in-memory buffer alone.
    written = load(destination_path)
    write_errors = verify_all_checksums(written)
    if write_errors:
        raise ValueError("POST-WRITE CHECK FAILED: " + "; ".join(write_errors))
    for slot in unchanged_slots:
        a = dest_original[slot_checksum_offset(slot):slot_checksum_offset(slot) + slot_total]
        b = written[slot_checksum_offset(slot):slot_checksum_offset(slot) + slot_total]
        if a != b:
            raise ValueError(f"POST-WRITE CHECK FAILED: destination slot {slot} changed.")
    written_payload = bytes(written[slot_data_offset(destination_slot):slot_data_offset(destination_slot) + SLOT_DATA_LENGTH])
    if written_payload != bytes(expected_payload):
        raise ValueError("POST-WRITE CHECK FAILED: imported slot no longer matches the expected source-derived payload.")

    return {
        "backup": backup,
        "source_account": source_account,
        "destination_account": destination_account,
        "source_slot_id": source_slot_id,
        "destination_slot_id": destination_slot_id,
        "kept_steam": kept_steam,
        "unchanged_slots": unchanged_slots,
        "source_payload_sha256": sha256_bytes(src_payload),
        "final_imported_payload_sha256": sha256_bytes(written_payload),
        "final_slot_md5": hashlib.md5(written_payload).hexdigest(),
        "final_checksums_ok": True,
    }

def slot_number_from_selection(value):
    """Accept either the numeric StringVar value ('0') or a scanner label."""
    text = str(value).strip()
    if re.fullmatch(r"[0-9]+", text):
        slot = int(text)
    else:
        m = re.match(r"\s*Slot\s+([0-9]+)\b", text)
        if not m:
            raise ValueError(f"Could not determine slot number from selection: {text!r}")
        slot = int(m.group(1))
    if not 0 <= slot < SLOT_COUNT:
        raise ValueError(f"Invalid slot number: {slot}")
    return slot



class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Sekiro Save Slot Copier v1.0")
        self.geometry("820x520")
        self.minsize(780, 480)

        self.source = tk.StringVar()
        self.destination = tk.StringVar()
        self.source_slot = tk.StringVar(value="Slot 0")
        self.destination_slot = tk.StringVar(value="Slot 0")
        self.status = tk.StringVar(value="Select source and destination saves.")
        self.steam_choice = tk.StringVar(value="destination")
        self.source_account_label = tk.StringVar(value="Source account SteamID: —")
        self.destination_account_label = tk.StringVar(value="Destination account SteamID: —")
        self.source_selected_id_label = tk.StringVar(value="Selected source character SteamID: —")
        self.destination_selected_id_label = tk.StringVar(value="Selected destination character SteamID: —")

        self.build()

    def build(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(
            root,
            text="SEKIRO SAVE SLOT COPIER v1.0",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            root,
            text=(
                "Transfers one Sekiro character while preserving the other "
                "nine destination characters."
            ),
        ).pack(anchor="w", pady=(3, 16))

        self.file_row(root, "Source save", self.source, self.choose_source)
        self.file_row(root, "Destination save", self.destination, self.choose_destination)

        steam_box = ttk.LabelFrame(root, text="SteamID check", padding=8)
        steam_box.pack(fill="x", pady=(8, 0))
        ttk.Label(steam_box, textvariable=self.source_account_label).grid(row=0, column=0, sticky="w", padx=4)
        ttk.Label(steam_box, textvariable=self.destination_account_label).grid(row=0, column=1, sticky="w", padx=20)
        ttk.Label(steam_box, textvariable=self.source_selected_id_label).grid(row=1, column=0, sticky="w", padx=4, pady=(4, 0))
        ttk.Label(steam_box, textvariable=self.destination_selected_id_label).grid(row=1, column=1, sticky="w", padx=20, pady=(4, 0))
        ttk.Label(steam_box, text="SteamID to keep in copied slot/save:").grid(row=2, column=0, sticky="w", padx=4, pady=(7, 0))
        ttk.Radiobutton(steam_box, text="DESTINATION account (recommended)", variable=self.steam_choice, value="destination").grid(row=2, column=1, sticky="w", padx=20, pady=(7, 0))
        ttk.Radiobutton(steam_box, text="SOURCE account (advanced — changes save ownership)", variable=self.steam_choice, value="source").grid(row=3, column=1, sticky="w", padx=20)
        ttk.Label(steam_box, text="Keep DESTINATION unless you intentionally want the resulting save to belong to the source Steam account.", foreground="#9b3b16", wraplength=760).grid(row=4, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 0))

        box = ttk.LabelFrame(root, text="Character slot scanner", padding=12)
        box.pack(fill="both", expand=True, pady=14)

        ttk.Label(box, text="Source slot").grid(row=0, column=0, sticky="w")
        self.source_combo = ttk.Combobox(
            box, textvariable=self.source_slot, state="readonly", width=82
        )
        self.source_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self.source_combo.bind("<<ComboboxSelected>>", self.select_source_slot)

        ttk.Label(box, text="Destination slot").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.destination_combo = ttk.Combobox(
            box, textvariable=self.destination_slot, state="readonly", width=82
        )
        self.destination_combo.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=(8, 0))
        self.destination_combo.bind("<<ComboboxSelected>>", self.select_destination_slot)

        box.columnconfigure(1, weight=1)

        self.source_scan_label = ttk.Label(
            box, text="Choose a source save, then click SCAN SLOTS.", wraplength=650
        )
        self.source_scan_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self.destination_scan_label = ttk.Label(
            box, text="Destination slots will also be scanned.", wraplength=650
        )
        self.destination_scan_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        scan_buttons = ttk.Frame(box)
        scan_buttons.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Button(scan_buttons, text="SCAN SLOTS", command=self.scan_files).pack(side="left")
        ttk.Button(scan_buttons, text="COPY SELECTED SLOT", command=self.do_copy).pack(side="left", padx=(10, 0))

        ttk.Label(
            box,
            text=(
                "The scanner uses Sekiro character fields documented by the linked "
                "save editor: Steam ID, HP, Attack Power, NG+, Souls, Skill Points "
                "and inventory, plus Sekiro's profile occupancy flag. It does not "
                "modify the save during scanning."
            ),
            wraplength=700,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Label(
            root,
            text=(
                "v3.4 uses Sekiro's actual 0x100000-byte slot format. It does NOT "
                "use the 0x280000 Elden Ring layout from the previous build."
            ),
            wraplength=770,
            foreground="#9b3b16",
        ).pack(anchor="w", pady=(2, 8))

        ttk.Label(
            root,
            text=(
                "The destination is backed up first. The resulting file keeps "
                "the source's global slot metadata, destination settings, and "
                "the destination's other nine character slots."
            ),
            wraplength=770,
        ).pack(anchor="w", pady=(0, 14))


        ttk.Separator(root).pack(fill="x", pady=12)

        ttk.Label(root, textvariable=self.status, wraplength=770).pack(
            anchor="w"
        )

    def file_row(self, parent, label, variable, command):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=4)

        ttk.Label(row, text=label, width=20).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(
            side="left", fill="x", expand=True, padx=7
        )
        ttk.Button(row, text="Browse...", command=command).pack(side="left")

    def choose_source(self):
        p = filedialog.askopenfilename(
            title="Choose SOURCE S0000.sl2",
            filetypes=[
                ("Sekiro saves", "*.sl2 *.bak"),
                ("All files", "*.*"),
            ],
        )
        if p:
            self.source.set(p)

    def choose_destination(self):
        p = filedialog.askopenfilename(
            title="Choose DESTINATION S0000.sl2",
            filetypes=[
                ("Sekiro saves", "*.sl2 *.bak"),
                ("All files", "*.*"),
            ],
        )
        if p:
            self.destination.set(p)

    def select_source_slot(self, event=None):
        value = self.source_combo.get()
        if value:
            try:
                slot = int(value.split()[1])
                self.source_slot.set(str(slot))
                if Path(self.source.get().strip()).is_file() and Path(self.destination.get().strip()).is_file():
                    sd = load(self.source.get().strip()); dd = load(self.destination.get().strip())
                    self.update_steam_labels(sd, dd, slot, slot_number_from_selection(self.destination_slot.get()))
            except Exception:
                pass

    def select_destination_slot(self, event=None):
        value = self.destination_combo.get()
        if value:
            try:
                slot = int(value.split()[1])
                self.destination_slot.set(str(slot))
                if Path(self.source.get().strip()).is_file() and Path(self.destination.get().strip()).is_file():
                    sd = load(self.source.get().strip()); dd = load(self.destination.get().strip())
                    self.update_steam_labels(sd, dd, slot_number_from_selection(self.source_slot.get()), slot)
            except Exception:
                pass

    def update_steam_labels(self, source_data, destination_data, source_slot, destination_slot):
        sa = get_settings_steam_id(source_data)
        da = get_settings_steam_id(destination_data)
        ss = get_slot_steam_id(source_data, source_slot)
        ds = get_slot_steam_id(destination_data, destination_slot)
        self.source_account_label.set(f"Source account SteamID: {steamid_text(sa)}")
        self.destination_account_label.set(f"Destination account SteamID: {steamid_text(da)}")
        self.source_selected_id_label.set(f"Selected source character SteamID: {steamid_text(ss)}")
        self.destination_selected_id_label.set(f"Selected destination character SteamID: {steamid_text(ds)}")

    def scan_one_file(self, path):
        data = load(path)
        scans = [scan_slot(data, i) for i in range(SLOT_COUNT)]
        return data, scans

    def scan_files(self):
        try:
            src = self.source.get().strip()
            dst = self.destination.get().strip()
            if not src or not Path(src).is_file():
                raise ValueError("Choose a valid SOURCE S0000.sl2 first.")
            if not dst or not Path(dst).is_file():
                raise ValueError("Choose a valid DESTINATION S0000.sl2 first.")
            source_data, source_scans = self.scan_one_file(src)
            destination_data, destination_scans = self.scan_one_file(dst)
            source_account = get_settings_steam_id(source_data)
            destination_account = get_settings_steam_id(destination_data)

            source_values = [format_slot(x) for x in source_scans]
            destination_values = [format_slot(x) for x in destination_scans]
            self.source_combo["values"] = source_values
            self.destination_combo["values"] = destination_values

            # Prefer the first actual character detected, not necessarily slot 0.
            source_pick = next((x["slot"] for x in source_scans if x["meaningful"]), 0)
            destination_pick = next((x["slot"] for x in destination_scans if not x["meaningful"]), 0)
            self.source_combo.set(source_values[source_pick])
            self.destination_combo.set(destination_values[destination_pick])
            self.source_slot.set(str(source_pick))
            self.destination_slot.set(str(destination_pick))
            self.update_steam_labels(source_data, destination_data, source_pick, destination_pick)

            src_count = sum(1 for x in source_scans if x["meaningful"])
            dst_count = sum(1 for x in destination_scans if x["meaningful"])
            self.source_scan_label.config(
                text=f"SOURCE: detected {src_count}/10 slots containing character data.\n"
                     + "\n".join(source_values)
            )
            self.destination_scan_label.config(
                text=f"DESTINATION: detected {dst_count}/10 slots containing character data.\n"
                     + "\n".join(destination_values)
            )
            self.status.set(
                f"Scan complete. Source: {src_count}/10 with data; destination: {dst_count}/10 with data."
            )
        except Exception as e:
            self.status.set("SCAN ERROR: " + str(e))
            messagebox.showerror("Slot scan failed", str(e))


    def do_copy(self):
        src = self.source.get().strip()
        dst = self.destination.get().strip()

        try:
            ss = slot_number_from_selection(self.source_slot.get())
            ds = slot_number_from_selection(self.destination_slot.get())
            if not src or not dst:
                raise ValueError("Choose both files.")
            if not Path(src).is_file() or not Path(dst).is_file():
                raise ValueError("Both source and destination files must exist.")

            source = load(src)
            destination = load(dst)
            source_info = scan_slot(source, ss)
            if not source_info["meaningful"]:
                raise ValueError(f"Source slot {ss} does not appear to contain a real character.")

            source_account = get_settings_steam_id(source)
            destination_account = get_settings_steam_id(destination)
            source_slot_id = get_slot_steam_id(source, ss)
            destination_slot_id = get_slot_steam_id(destination, ds)
            same_account = source_account == destination_account

            prompt = (
                f"COPY ONLY SOURCE SLOT {ss} -> DESTINATION SLOT {ds}?\n\n"
                f"SOURCE account SteamID: {source_account}\n"
                f"SOURCE selected-slot SteamID: {source_slot_id}\n\n"
                f"DESTINATION account SteamID: {destination_account}\n"
                f"DESTINATION selected-slot SteamID: {destination_slot_id}\n\n"
                f"SteamID to keep: {'DESTINATION' if self.steam_choice.get() == 'destination' else 'SOURCE'}\n\n"
                f"Source character: HP {source_info['hp']}, Attack {source_info['attack']}, NG+ {source_info['ng_plus']}, "
                f"Playtime {source_info['playtime_seconds']//3600}:{(source_info['playtime_seconds']//60)%60:02d}, "
                f"Sen {source_info['souls']:,}\n\n"
                "SAFETY: the destination is the starting file. Only the selected destination slot, its occupancy byte, "
                "and the required SteamID/checksum fields will be changed. The other 9 destination slots are verified "
                "byte-for-byte unchanged before and after writing.\n\n"
                + ("The SteamIDs already match.\n\n" if same_account else
                   "WARNING: the account SteamIDs differ. DESTINATION is recommended so the save remains owned by the current account.\n\n")
                + "A full destination backup will be created first."
            )
            if not messagebox.askyesno("Confirm single-slot copy", prompt, icon="warning"):
                return

            result = copy_one_slot(src, dst, ss, ds, self.steam_choice.get())
            self.status.set(
                f"SUCCESS. Only destination slot {ds} was replaced.\n"
                f"Other 9 destination slots verified unchanged.\n"
                f"All checksums verified after writing.\n"
                f"Backup: {result['backup']}"
            )
            messagebox.showinfo(
                "Copy verified",
                f"Source slot {ss} -> destination slot {ds} completed.\n\n"
                f"Kept SteamID: {result['kept_steam']}\n\n"
                f"Source payload SHA-256:\n{result['source_payload_sha256']}\n\n"
                f"Final imported payload SHA-256:\n{result['final_imported_payload_sha256']}\n\n"
                f"Final slot MD5:\n{result['final_slot_md5']}\n\n"
                "VERIFIED:\n"
                "• all 9 other destination slots are byte-for-byte unchanged\n"
                "• imported slot matches the selected source slot except the intentional SteamID change\n"
                "• all 10 slot checksums + settings checksum are valid\n"
                "• the written file was read back and checked again\n\n"
                f"Backup:\n{result['backup']}"
            )
        except Exception as e:
            self.status.set("ERROR: " + str(e))
            messagebox.showerror("Slot copy failed", str(e))


if __name__ == "__main__":
    App().mainloop()
