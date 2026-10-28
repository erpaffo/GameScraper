"""Metadati dei giochi Switch letti dal file stesso (NSP/NSZ/XCI/XCZ) con le chiavi dell'utente (prod.keys).

Come fanno gli emulatori: dentro il pacchetto c'e' un NCA di tipo "Control" con il file control.nacp
(titolo e publisher in 15 lingue) e le icone 256x256. Si legge solo quel pezzo (poche centinaia di KB) del
file, che pesa anche decine di GB: niente rete e niente hash dell'intero gioco.
Nei pacchetti compressi (.nsz/.xcz) solo gli NCA grandi di gioco sono compressi in .ncz, mentre il Control NCA
resta normalmente un .nca non compresso leggibile senza dipendenze aggiuntive.

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
PACKAGE_EXTS = (".nsp", ".xci", ".nsz", ".xcz")
CNMT_TYPES = {0x80: "base", 0x81: "update", 0x82: "dlc", 0x83: "update"}
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
    """File contenuti in un PFS0 (NSP/NSZ) o HFS0 (XCI/XCZ) che comincia a `base`: [(nome, offset assoluto, dimensione)]."""
    f.seek(base)
    head = f.read(16)
    if len(head) < 16:
        raise SwitchError("non e' un NSP/XCI valido")
    magic, count, strsize, _ = struct.unpack("<4sIII", head)
    if magic not in (b"PFS0", b"HFS0") or count > 4096:
        raise SwitchError("non e' un NSP/XCI valido")
    esize = 0x40 if magic == b"HFS0" else 0x18
    raw = f.read(esize * count)
    strtab = f.read(strsize)
    if len(raw) < esize * count or len(strtab) < strsize:
        raise SwitchError("intestazione PFS0/HFS0 troncata")
    data0 = base + 16 + esize * count + strsize
    out = []
    for i in range(count):
        off, size, noff = struct.unpack_from("<QQI", raw, i * esize)
        end = strtab.find(b"\0", noff)
        name = strtab[noff:(end if end != -1 else len(strtab))].decode("utf8", "replace")
        out.append((name, data0 + off, size))
    return out


def _package_files(f) -> list[tuple[str, int, int]]:
    f.seek(0)
    head = f.read(4)
    if head == b"PFS0":
        return _entries(f, 0, False)
    f.seek(0x100)
    if f.read(4) == b"HEAD":  # XCI/XCZ: partizione root -> "secure" (o altre partizioni HFS0) -> NCA
        f.seek(0x130)
        root = struct.unpack("<Q", f.read(8))[0]
        parts = _entries(f, root, True)
        for name, off, _ in parts:
            if name == "secure":
                sec = _entries(f, off, True)
                if sec:
                    return sec
        out = []
        for _, off, size in parts:
            if size >= 16:
                out.extend(_entries(f, off, True))
        if out:
            return out
    raise SwitchError("formato non riconosciuto (servono .nsp, .nsz, .xci o .xcz)")


def _title_key(keys, tik: bytes, rights_id: bytes, gen: int) -> bytes | None:
    if len(tik) < 4:
        return None
    sig = struct.unpack_from("<I", tik, 0)[0]
    body = 4 + SIG_SIZE.get(sig, 0x13C)
    if len(tik) < body + 0x170 or tik[body + 0x160:body + 0x170] != rights_id:
        return None
    kek = keys.get(f"titlekek_{gen:02x}")
    return _ecb_dec(kek, tik[body + 0x40:body + 0x50]) if kek else None


def _nca_section0_key(keys: dict[str, bytes], hdr: bytes, tiks: list[bytes]) -> bytes:
    """Ricava la chiave AES-CTR della sezione 0 dall'intestazione NCA3 decifrata (o dal ticket)."""
    gen = max(hdr[0x206], hdr[0x220])
    gen = gen - 1 if gen else 0
    rights = hdr[0x230:0x240]
    if any(rights):
        key = next((k for t in tiks if (k := _title_key(keys, t, rights, gen))), None)
        if not key:
            raise SwitchError("ticket/chiave del titolo non trovati")
        return key
    kak = keys.get(f"key_area_key_application_{gen:02x}")
    if not kak:
        raise SwitchError(f"manca key_area_key_application_{gen:02x} nel prod.keys")
    return _ecb_dec(kak, hdr[0x300:0x340])[0x20:0x30]  # chiave AES-CTR = terza della key area


def _decrypt_section0(keys: dict[str, bytes], nca: bytes, tiks: list[bytes]) -> tuple[bytes, bytes, bytes]:
    """Restituisce (hdr, fsh, sec0_decifrata) da un NCA3 completo gia' letto in memoria."""
    if len(nca) < 0xC00:
        raise SwitchError("NCA troncato")
    hdr = _xts_header(keys["header_key"], nca[:0xC00])
    if hdr[0x200:0x204] != b"NCA3":
        raise SwitchError("NCA non supportato")
    start, end = struct.unpack_from("<II", hdr, 0x240)
    start, end = start * MEDIA, end * MEDIA
    if end > len(nca) or start >= end:
        raise SwitchError("sezione NCA troncata o assente")
    fsh = hdr[0x400:0x600]
    if fsh[4] == 1:  # non cifrata
        return hdr, fsh, nca[start:end]
    if fsh[4] != 3:
        raise SwitchError(f"cifratura sezione {fsh[4]} non gestita")
    key = _nca_section0_key(keys, hdr, tiks)
    ctr = fsh[0x140:0x148][::-1] + (start >> 4).to_bytes(8, "big")
    return hdr, fsh, _ctr(key, ctr, nca[start:end])


def _decrypt_control(keys, nca: bytes, tiks: list[bytes]):
    """(titolo/publisher per lingua, icone per lingua, versione) dall'NCA Control gia' letto in memoria."""
    _, fsh, sec = _decrypt_section0(keys, nca, tiks)
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
        fsize = os.fstat(f.fileno()).st_size
        files = _package_files(f)
        truncated = any(off + size > fsize for _, off, size in files)
        tiks = []
        for name, off, size in files:
            if name.endswith(".tik") and size < 0x1000 and off + size <= fsize:
                f.seek(off)
                tiks.append(f.read(size))
        ncz_control = False
        for name, off, size in files:
            low = name.lower()
            if not low.endswith((".nca", ".ncz")) or low.endswith((".cnmt.nca", ".cnmt.ncz")):
                continue
            if off + 0x400 > fsize:
                continue
            if low.endswith(".nca") and size > 64 * 1024 * 1024:
                continue
            f.seek(off)
            head = _xts_header(keys["header_key"], f.read(0x400))
            if head[0x200:0x204] != b"NCA3" or head[0x205] != 2:  # 2 = Control
                continue
            if low.endswith(".ncz"):
                ncz_control = True
                continue
            if off + size > fsize:
                truncated = True
                continue
            f.seek(off)
            titles, icons, version = _decrypt_control(keys, f.read(size), tiks)
            order = [LANGS[LANG_CODE[c]] for c in languages if c in LANG_CODE] + ["AmericanEnglish", "BritishEnglish"]
            lang = next((l for l in order if l in titles), next(iter(titles)))
            icon = icons.get(lang) or next(iter(icons.values()), b"")
            return {"title": titles[lang][0], "publisher": titles[lang][1], "version": version, "icon": icon,
                    "title_id": f"{struct.unpack_from('<Q', head, 0x210)[0]:016X}"}
        if ncz_control:
            raise SwitchError("NCA Control compresso (.ncz)")
        if truncated:
            raise SwitchError("pacchetto troncato o incompleto (NCA Control oltre la fine del file)")
    raise SwitchError("NCA Control non trovato nel pacchetto")


def _parse_cnmt_nca(keys: dict[str, bytes], nca: bytes, tiks: list[bytes]) -> dict:
    """Estrae {'type': 'base'|'update'|'dlc', 'title_id', 'base_title_id', 'version': int} da un .cnmt.nca."""
    _, fsh, sec = _decrypt_section0(keys, nca, tiks)
    pfs0_off = struct.unpack_from("<Q", fsh, 0x40)[0]
    if pfs0_off + 16 > len(sec) or sec[pfs0_off:pfs0_off + 4] != b"PFS0":
        pfs0_off = sec.find(b"PFS0")
        if pfs0_off == -1 or pfs0_off + 16 > len(sec):
            raise SwitchError("PFS0 non trovato nel CNMT NCA")
    _, cnt, strsz, _ = struct.unpack_from("<4sIII", sec, pfs0_off)
    if cnt > 256:
        raise SwitchError("tabella PFS0 del CNMT non valida")
    data0 = pfs0_off + 16 + cnt * 0x18 + strsz
    strtab = sec[pfs0_off + 16 + cnt * 0x18:data0]
    for i in range(cnt):
        c_off, c_sz, c_noff = struct.unpack_from("<QQI", sec, pfs0_off + 16 + i * 0x18)
        end_s = strtab.find(b"\0", c_noff)
        cname = strtab[c_noff:(end_s if end_s != -1 else len(strtab))].decode("utf8", "replace")
        if not cname.lower().endswith(".cnmt") or c_sz < 0x20:
            continue
        pos = data0 + c_off
        if pos + 0x20 > len(sec):
            raise SwitchError("dati CNMT troncati")
        tid, ver, ctype = struct.unpack_from("<QIB", sec, pos)
        ext_sz = struct.unpack_from("<H", sec, pos + 0x0E)[0]
        ext_tid = struct.unpack_from("<Q", sec, pos + 0x20)[0] if ext_sz >= 8 and pos + 0x28 <= len(sec) else 0
        kind = CNMT_TYPES.get(ctype)
        if not kind:
            if (tid & 0xFFF) == 0x800:
                kind = "update"
            elif (tid & 0xFFF) != 0x000:
                kind = "dlc"
            else:
                kind = "base"
        if kind == "base":
            base_tid = tid
        elif kind == "update":
            base_tid = (ext_tid or tid) & ~0xFFF
        else:
            base_tid = (ext_tid & ~0xFFF) if ext_tid else ((tid & ~0xFFF) - 0x1000)
        return {
            "type": kind,
            "title_id": f"{tid:016X}",
            "base_title_id": f"{base_tid:016X}",
            "version": int(ver),
        }
    raise SwitchError("file .cnmt non trovato nel Meta NCA")


def _package_cnmts(path: Path, keys: dict[str, bytes]) -> list[dict]:
    """Tutti i descrittori CNMT presenti in un pacchetto (.nsp/.nsz/.xci/.xcz), leggendo solo pochi KB."""
    with open(path, "rb") as f:
        fsize = os.fstat(f.fileno()).st_size
        files = _package_files(f)
        truncated = any(off + size > fsize for _, off, size in files)
        tiks = []
        for name, off, size in files:
            if name.endswith(".tik") and size < 0x1000 and off + size <= fsize:
                f.seek(off)
                tiks.append(f.read(size))
        cnmt_candidates = [e for e in files if e[0].lower().endswith(".cnmt.nca")]
        if not cnmt_candidates:
            cnmt_candidates = [e for e in files if e[0].lower().endswith(".nca") and e[2] <= 64 * 1024]
        out: list[dict] = []
        for _, off, size in cnmt_candidates:
            if off + 0x400 > fsize or off + size > fsize:
                truncated = True
                continue
            if size > 256 * 1024:
                continue
            f.seek(off)
            head = _xts_header(keys["header_key"], f.read(0x400))
            if head[0x200:0x204] != b"NCA3" or head[0x205] != 1:  # 1 = Meta (CNMT)
                continue
            f.seek(off)
            out.append(_parse_cnmt_nca(keys, f.read(size), tiks))
        if out:
            return out
        if truncated:
            raise SwitchError("pacchetto troncato o incompleto (CNMT NCA oltre la fine del file)")
    raise SwitchError("CNMT NCA non trovato nel pacchetto")


def package_info(path: Path, keys: dict[str, bytes]) -> dict:
    """Legge il CNMT di un pacchetto Switch (.nsp/.nsz/.xci/.xcz).

    Ritorna {'type': 'base'|'update'|'dlc', 'title_id': str, 'base_title_id': str, 'version': int}.
    """
    items = _package_cnmts(path, keys)
    order = {"base": 0, "update": 1, "dlc": 2}
    items.sort(key=lambda d: (order.get(d["type"], 9), -d["version"]))
    return items[0]


def _control_display_version(path: Path, keys: dict[str, bytes]) -> str | None:
    """Legge la display version ('1.2.0') da control.nacp decifrando solo i primi blocchi del Control NCA."""
    try:
        with open(path, "rb") as f:
            fsize = os.fstat(f.fileno()).st_size
            files = _package_files(f)
            tiks = []
            for name, off, size in files:
                if name.endswith(".tik") and size < 0x1000 and off + size <= fsize:
                    f.seek(off)
                    tiks.append(f.read(size))
            best_ver: str | None = None
            for name, off, size in files:
                low = name.lower()
                if not low.endswith(".nca") or low.endswith(".cnmt.nca") or off + 0xC00 > fsize or off + size > fsize:
                    continue
                if size > 64 * 1024 * 1024:
                    continue
                f.seek(off)
                hdr = _xts_header(keys["header_key"], f.read(0xC00))
                if hdr[0x200:0x204] != b"NCA3" or hdr[0x205] != 2:  # 2 = Control
                    continue
                start, end = struct.unpack_from("<II", hdr, 0x240)
                start, end = start * MEDIA, end * MEDIA
                fsh = hdr[0x400:0x600]
                if fsh[4] not in (1, 3) or end > size or start >= end:
                    continue
                rom0 = struct.unpack_from("<Q", fsh, 0x18 + 5 * 0x18)[0]
                key = b"" if fsh[4] == 1 else _nca_section0_key(keys, hdr, tiks)
                iv_hi = fsh[0x140:0x148][::-1]

                def _read_sec(rel_off: int, length: int) -> bytes:
                    blk_off = (rel_off // 16) * 16
                    blk_len = ((rel_off - blk_off + length + 15) // 16) * 16
                    abs_pos = start + blk_off
                    if abs_pos + blk_len > size:
                        blk_len = ((size - abs_pos) // 16) * 16
                    f.seek(off + abs_pos)
                    raw = f.read(blk_len)
                    if fsh[4] == 1:
                        dec = raw
                    else:
                        ctr = iv_hi + (abs_pos >> 4).to_bytes(8, "big")
                        dec = _ctr(key, ctr, raw)
                    delta = rel_off - blk_off
                    return dec[delta:delta + length]

                rh_bytes = _read_sec(rom0, 0x50)
                if len(rh_bytes) < 0x50:
                    continue
                rh = struct.unpack_from("<10Q", rh_bytes, 0)
                fmeta_off, fmeta_size, fdata_off = rh[7], rh[8], rh[9]
                if fmeta_size > 64 * 1024:
                    continue
                meta = _read_sec(rom0 + fmeta_off, fmeta_size)
                pos = 0
                while pos + 0x20 <= len(meta):
                    doff, dsize, _, nlen = struct.unpack_from("<QQII", meta, pos + 8)
                    fname = meta[pos + 0x20:pos + 0x20 + nlen].decode("utf8", "replace")
                    if fname == "control.nacp" and dsize >= 0x3070:
                        ver_raw = _read_sec(rom0 + fdata_off + doff + 0x3060, 0x10)
                        v = ver_raw.split(b"\0")[0].decode("ascii", "ignore").strip()
                        if v:
                            best_ver = v
                        break
                    pos += 0x20 + ((nlen + 3) & ~3)
            return best_ver
    except Exception:
        return None


def folder_contents(folder: Path, keys: dict[str, bytes]) -> dict:
    """Analizza i pacchetti Switch in una cartella (leggendo solo le intestazioni CNMT/NACP, pochi KB per file).

    Ritorna:
        {
            "base": dict | None,
            "update": int | None,
            "update_display": str | None,
            "dlc": int,
            "errors": list[str],
        }
    """
    if folder.is_file():
        files = [folder] if folder.suffix.lower() in PACKAGE_EXTS else []
    elif folder.is_dir():
        seen: set[Path] = set()
        files = []
        for pat in ("*", "*/*"):
            for f in sorted(folder.glob(pat)):
                if f.is_file() and f.suffix.lower() in PACKAGE_EXTS and f not in seen:
                    seen.add(f)
                    files.append(f)
    else:
        files = []

    base_info: dict | None = None
    best_update: int | None = None
    best_update_file: Path | None = None
    dlc_ids: set[str] = set()
    errors: list[str] = []

    for f in files:
        try:
            infos = _package_cnmts(f, keys)
        except (SwitchError, OSError, ValueError, struct.error) as e:
            errors.append(f"{f.name}: {e}")
            continue
        for info in infos:
            t = info["type"]
            if t == "base":
                if base_info is None or info["version"] > base_info["version"]:
                    base_info = info
            elif t == "update":
                if best_update is None or info["version"] > best_update:
                    best_update = info["version"]
                    best_update_file = f
            elif t == "dlc":
                dlc_ids.add(info["title_id"])

    update_display: str | None = None
    if best_update is not None and best_update_file is not None:
        update_display = _control_display_version(best_update_file, keys)
        if not update_display:
            m = re.search(r"\bv?(\d+\.\d+(?:\.\d+)+)\b", best_update_file.stem, re.IGNORECASE)
            if m:
                update_display = m.group(1)

    return {
        "base": base_info,
        "update": best_update,
        "update_display": update_display,
        "dlc": len(dlc_ids),
        "errors": errors,
    }
