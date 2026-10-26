"""Metadati dei giochi Switch letti dal file stesso (NSP/XCI) con le chiavi dell'utente (prod.keys).

Come fanno gli emulatori: dentro il pacchetto c'e' un NCA di tipo "Control" con il file control.nacp
(titolo e publisher in 15 lingue) e le icone 256x256. Si legge solo quel pezzo (poche centinaia di KB) del
file, che pesa anche decine di GB: niente rete e niente hash dell'intero gioco.

Le chiavi NON vengono copiate ne' salvate: si legge il prod.keys che c'e' gia' sul PC (Ryujinx, Eden/yuzu...)
oppure quello indicato in config ([paths] switch_keys).
"""

from __future__ import annotations

import os
import re
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MEDIA = 0x200
# lingue del NACP, nell'ordine del formato
LANGS = ["AmericanEnglish", "BritishEnglish", "Japanese", "French", "German", "LatinAmericanSpanish", "Spanish",
         "Italian", "Dutch", "CanadianFrench", "Portuguese", "Russian", "Korean", "TraditionalChinese",
         "SimplifiedChinese"]
LANG_CODE = {"en": 0, "ja": 2, "jp": 2, "fr": 3, "de": 4, "es": 6, "it": 7, "nl": 8, "pt": 10, "ru": 11, "ko": 12}
SIG_SIZE = {0x10000: 0x23C, 0x10001: 0x13C, 0x10002: 0x7C, 0x10003: 0x23C, 0x10004: 0x13C, 0x10005: 0x7C}


class SwitchError(Exception):
    pass


def key_candidates(configured: str = "") -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    home = Path.home()
    c = [Path(configured)] if configured else []
    c += [appdata / "Ryujinx" / "system" / "prod.keys", appdata / "eden" / "keys" / "prod.keys",
          appdata / "yuzu" / "keys" / "prod.keys", home / ".switch" / "prod.keys",
          home / ".local" / "share" / "Ryujinx" / "system" / "prod.keys",
          home / ".local" / "share" / "eden" / "keys" / "prod.keys"]
    return [p for p in c if str(p) not in ("", ".") and p.is_file()]


def load_keys(configured: str = "") -> dict[str, bytes]:
    found = key_candidates(configured)
    if not found:
        raise SwitchError("prod.keys non trovato (indicalo in config.toml, [paths] switch_keys)")
    keys: dict[str, bytes] = {}
    for line in found[0].read_text(errors="ignore").splitlines():
        m = re.match(r"\s*([\w]+)\s*=\s*([0-9a-fA-F]+)\s*$", line)
        if m:
            keys[m.group(1).lower()] = bytes.fromhex(m.group(2))
    if "header_key" not in keys:
        raise SwitchError(f"{found[0]}: manca header_key (prod.keys incompleto)")
    return keys


def _ecb_dec(key: bytes, data: bytes) -> bytes:
    d = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return d.update(data) + d.finalize()


def _ctr(key: bytes, counter: bytes, data: bytes) -> bytes:
    d = Cipher(algorithms.AES(key), modes.CTR(counter)).decryptor()
    return d.update(data) + d.finalize()


def _xts_header(key: bytes, data: bytes) -> bytes:
    """Decifra l'intestazione NCA: AES-XTS a settori da 0x200, tweak big-endian (variante Nintendo)."""
    out = b""
    for n in range(len(data) // 0x200):
        d = Cipher(algorithms.AES(key), modes.XTS(n.to_bytes(16, "big"))).decryptor()
        out += d.update(data[n * 0x200:(n + 1) * 0x200]) + d.finalize()
    return out


def _entries(f, base: int, hfs: bool):
    """File contenuti in un PFS0 (NSP) o HFS0 (XCI) che comincia a `base`: [(nome, offset assoluto, dimensione)]."""
    f.seek(base)
    magic, count, strsize, _ = struct.unpack("<4sIII", f.read(16))
    if magic not in (b"PFS0", b"HFS0") or count > 4096:
        raise SwitchError("non e' un NSP/XCI valido")
    esize = 0x40 if magic == b"HFS0" else 0x18
    raw = f.read(esize * count)
    strtab = f.read(strsize)
    data0 = base + 16 + esize * count + strsize
    out = []
    for i in range(count):
        off, size, noff = struct.unpack_from("<QQI", raw, i * esize)
        name = strtab[noff:strtab.index(b"\0", noff)].decode("utf8", "replace")
        out.append((name, data0 + off, size))
    return out


def _package_files(f) -> list[tuple[str, int, int]]:
    f.seek(0)
    head = f.read(4)
    if head == b"PFS0":
        return _entries(f, 0, False)
    f.seek(0x100)
    if f.read(4) == b"HEAD":  # XCI: partizione root -> "secure" -> NCA
        f.seek(0x130)
        root = struct.unpack("<Q", f.read(8))[0]
        for name, off, _ in _entries(f, root, True):
            if name == "secure":
                return _entries(f, off, True)
    raise SwitchError("formato non riconosciuto (servono .nsp o .xci)")


def _title_key(keys, tik: bytes, rights_id: bytes, gen: int) -> bytes | None:
    sig = struct.unpack_from("<I", tik, 0)[0]
    body = 4 + SIG_SIZE.get(sig, 0x13C)
    if tik[body + 0x160:body + 0x170] != rights_id:
        return None
    kek = keys.get(f"titlekek_{gen:02x}")
    return _ecb_dec(kek, tik[body + 0x40:body + 0x50]) if kek else None


def _decrypt_control(keys, nca: bytes, tiks: list[bytes]):
    """(titolo/publisher per lingua, icone per lingua) dall'NCA Control gia' letto in memoria."""
    hdr = _xts_header(keys["header_key"], nca[:0xC00])
    if hdr[0x200:0x204] != b"NCA3":
        raise SwitchError("NCA non supportato")
    gen = max(hdr[0x206], hdr[0x220])
    gen = gen - 1 if gen else 0
    rights = hdr[0x230:0x240]
    if any(rights):
        key = next((k for t in tiks if (k := _title_key(keys, t, rights, gen))), None)
        if not key:
            raise SwitchError("ticket/chiave del titolo non trovati")
    else:
        kak = keys.get(f"key_area_key_application_{gen:02x}")
        if not kak:
            raise SwitchError(f"manca key_area_key_application_{gen:02x} nel prod.keys")
        key = _ecb_dec(kak, hdr[0x300:0x340])[0x20:0x30]  # chiave AES-CTR = terza della key area
    start, end = struct.unpack_from("<II", hdr, 0x240)
    start, end = start * MEDIA, end * MEDIA
    fsh = hdr[0x400:0x600]
    if fsh[4] != 3:
        raise SwitchError(f"cifratura sezione {fsh[4]} non gestita")
    ctr = fsh[0x140:0x148][::-1] + (start >> 4).to_bytes(8, "big")
    sec = _ctr(key, ctr, nca[start:end])
    rom0 = struct.unpack_from("<Q", fsh, 0x18 + 5 * 0x18)[0]  # IVFC: ultimo livello = dati RomFS
    rh = struct.unpack_from("<10Q", sec, rom0)
    fmeta_off, fmeta_size, fdata_off = rh[7], rh[8], rh[9]
    files: dict[str, bytes] = {}
    pos = 0
    meta = sec[rom0 + fmeta_off:rom0 + fmeta_off + fmeta_size]
    while pos + 0x20 <= len(meta):
        doff, dsize, _, nlen = struct.unpack_from("<QQII", meta, pos + 8)
        name = meta[pos + 0x20:pos + 0x20 + nlen].decode("utf8", "replace")
        files[name] = sec[rom0 + fdata_off + doff:rom0 + fdata_off + doff + dsize]
        pos += 0x20 + ((nlen + 3) & ~3)
    nacp = files.get("control.nacp")
    if not nacp:
        raise SwitchError("control.nacp assente")
    titles = {}
    for i, lang in enumerate(LANGS):
        n = nacp[i * 0x300:i * 0x300 + 0x200].split(b"\0")[0].decode("utf8", "replace")
        p = nacp[i * 0x300 + 0x200:(i + 1) * 0x300].split(b"\0")[0].decode("utf8", "replace")
        if n:
            titles[lang] = (n, p)
    icons = {n[5:-4]: d for n, d in files.items() if n.startswith("icon_") and n.endswith(".dat")}
    version = nacp[0x3060:0x3070].split(b"\0")[0].decode("ascii", "ignore")
    return titles, icons, version


def read_meta(path: Path, languages: list[str], keys: dict[str, bytes]) -> dict:
    """{'title', 'publisher', 'version', 'icon': bytes (JPEG), 'title_id'} oppure SwitchError."""
    with open(path, "rb") as f:
        files = _package_files(f)
        tiks = []
        for name, off, size in files:
            if name.endswith(".tik") and size < 0x1000:
                f.seek(off)
                tiks.append(f.read(size))
        for name, off, size in files:
            if not name.endswith(".nca") or name.endswith(".cnmt.nca") or size > 32 * 1024 * 1024:
                continue
            f.seek(off)
            head = _xts_header(keys["header_key"], f.read(0x400))
            if head[0x200:0x204] != b"NCA3" or head[0x205] != 2:  # 2 = Control
                continue
            f.seek(off)
            titles, icons, version = _decrypt_control(keys, f.read(size), tiks)
            order = [LANGS[LANG_CODE[c]] for c in languages if c in LANG_CODE] + ["AmericanEnglish", "BritishEnglish"]
            lang = next((l for l in order if l in titles), next(iter(titles)))
            icon = icons.get(lang) or next(iter(icons.values()), b"")
            return {"title": titles[lang][0], "publisher": titles[lang][1], "version": version, "icon": icon,
                    "title_id": f"{struct.unpack_from('<Q', head, 0x210)[0]:016X}"}
    raise SwitchError("NCA Control non trovato nel pacchetto")
