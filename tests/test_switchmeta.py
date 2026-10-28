"""Test di rilevamento formato, lettura metadati, CNMT (base/update/DLC) e gestione errori per pacchetti Switch.

Tutti i dati e le chiavi in questo file sono sintetici e generati in memoria (nessuna chiave o ROM reale).

Esegui: python tests/test_switchmeta.py
"""

import os
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes  # noqa: E402

from pozzo import platforms as P  # noqa: E402
from pozzo import switchmeta  # noqa: E402
from pozzo.scanner import Game  # noqa: E402
from pozzo.sources.nsp import Nsp  # noqa: E402

# Chiavi finte generate per il test (non sono chiavi reali)
FAKE_HEADER_KEY = bytes((i * 7 + 3) & 0xFF for i in range(32))
FAKE_KAK_00 = bytes((i * 11 + 5) & 0xFF for i in range(16))
FAKE_CTR_KEY = bytes((i * 13 + 9) & 0xFF for i in range(16))
FAKE_KEYS = {
    "header_key": FAKE_HEADER_KEY,
    "key_area_key_application_00": FAKE_KAK_00,
}


def _xts_encrypt(key: bytes, plain: bytes) -> bytes:
    out = b""
    for n in range(len(plain) // 0x200):
        enc = Cipher(algorithms.AES(key), modes.XTS(n.to_bytes(16, "big"))).encryptor()
        out += enc.update(plain[n * 0x200:(n + 1) * 0x200]) + enc.finalize()
    return out


def _ecb_encrypt(key: bytes, plain: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(plain) + enc.finalize()


def _ctr_encrypt(key: bytes, counter: bytes, plain: bytes) -> bytes:
    enc = Cipher(algorithms.AES(key), modes.CTR(counter)).encryptor()
    return enc.update(plain) + enc.finalize()


def make_pfs0(entries: list[tuple[str, bytes, int | None]]) -> bytes:
    """Costruisce un contenitore PFS0 sintetico: [(nome, bytes_reali, dimensione_dichiarata_opzionale)]."""
    strtab = b""
    noff_list = []
    for name, _, _ in entries:
        noff_list.append(len(strtab))
        strtab += name.encode("utf-8") + b"\0"
    if len(strtab) % 0x20:
        strtab += b"\0" * (0x20 - (len(strtab) % 0x20))

    table = b""
    blobs = b""
    off = 0
    for (name, data, decl_size), noff in zip(entries, noff_list):
        sz = len(data) if decl_size is None else decl_size
        table += struct.pack("<QQII", off, sz, noff, 0)
        blobs += data
        off += sz

    head = struct.pack("<4sIII", b"PFS0", len(entries), len(strtab), 0)
    return head + table + strtab + blobs


def make_fake_control_nca(title: str, publisher: str, version: str = "1.0.0",
                          title_id: int = 0x0100BF7006BCA000, icon_bytes: bytes = b"\xff\xd8\xff\xe0FAKEJPEG") -> bytes:
    """Costruisce un NCA3 Control sintetico cifrato con FAKE_KEYS."""
    nacp = bytearray(0x4000)
    # Italiano (indice 7) e AmericanEnglish (indice 0)
    for idx in (0, 7):
        t_b = title.encode("utf-8")[:0x1FF]
        p_b = publisher.encode("utf-8")[:0xFF]
        nacp[idx * 0x300:idx * 0x300 + len(t_b)] = t_b
        nacp[idx * 0x300 + 0x200:idx * 0x300 + 0x200 + len(p_b)] = p_b
    v_b = version.encode("ascii")[:0x0F]
    nacp[0x3060:0x3060 + len(v_b)] = v_b

    # RomFS minimale con control.nacp e icon_Italian.dat
    fentries = [("control.nacp", bytes(nacp)), ("icon_Italian.dat", icon_bytes)]
    meta = bytearray()
    fdata = bytearray()
    for fname, content in fentries:
        nb = fname.encode("utf-8")
        padded_nlen = (len(nb) + 3) & ~3
        entry = struct.pack("<IIQQII", 0, 0, len(fdata), len(content), 0, len(nb)) + nb + b"\0" * (padded_nlen - len(nb))
        meta += entry
        fdata += content

    rom0 = 0x200
    fmeta_off = 0x50
    fmeta_size = len(meta)
    fdata_off = fmeta_off + ((fmeta_size + 0xF) & ~0xF)
    rh = struct.pack("<10Q", 0x50, 0, 0, 0, 0, 0, 0, fmeta_off, fmeta_size, fdata_off)
    sec_plain = bytearray(rom0 + fdata_off + len(fdata))
    sec_plain[rom0:rom0 + len(rh)] = rh
    sec_plain[rom0 + fmeta_off:rom0 + fmeta_off + fmeta_size] = meta
    sec_plain[rom0 + fdata_off:rom0 + fdata_off + len(fdata)] = fdata
    if len(sec_plain) % switchmeta.MEDIA:
        sec_plain += b"\0" * (switchmeta.MEDIA - (len(sec_plain) % switchmeta.MEDIA))

    start = 0xC00
    end = start + len(sec_plain)
    hdr = bytearray(0xC00)
    hdr[0x200:0x204] = b"NCA3"
    hdr[0x205] = 2  # Control NCA
    hdr[0x206] = 1  # key generation 0
    struct.pack_into("<Q", hdr, 0x210, title_id)
    struct.pack_into("<II", hdr, 0x240, start // switchmeta.MEDIA, end // switchmeta.MEDIA)

    # Key area (4 blocchi da 16 byte, il terzo e' la chiave AES-CTR)
    key_area_plain = b"\0" * 0x20 + FAKE_CTR_KEY + b"\0" * 0x10
    hdr[0x300:0x340] = _ecb_encrypt(FAKE_KAK_00, key_area_plain)

    # FS header (0x400..0x600)
    fsh = bytearray(0x200)
    fsh[4] = 3  # AES-CTR
    struct.pack_into("<Q", fsh, 0x18 + 5 * 0x18, rom0)
    iv_hi = b"\x01\x02\x03\x04\x05\x06\x07\x08"
    fsh[0x140:0x148] = iv_hi[::-1]
    hdr[0x400:0x600] = fsh

    ctr = iv_hi + (start >> 4).to_bytes(8, "big")
    sec_enc = _ctr_encrypt(FAKE_CTR_KEY, ctr, bytes(sec_plain))
    return _xts_encrypt(FAKE_HEADER_KEY, bytes(hdr)) + sec_enc


def make_fake_cnmt_nca(content_meta_type: int, title_id: int, base_title_id: int, version: int = 0) -> bytes:
    """Costruisce un Meta NCA (.cnmt.nca, content_type=1) sintetico con il descrittore .cnmt."""
    cnmt = bytearray(0x30)
    struct.pack_into("<QIB", cnmt, 0x00, title_id, version, content_meta_type)
    struct.pack_into("<H", cnmt, 0x0E, 0x10)  # extended header size = 16 byte
    struct.pack_into("<Q", cnmt, 0x20, base_title_id)

    pfs0_off = 0x200
    inner_pfs0 = make_pfs0([(f"Meta_{title_id:016x}.cnmt", bytes(cnmt), None)])
    sec_plain = bytearray(pfs0_off + len(inner_pfs0))
    sec_plain[pfs0_off:pfs0_off + len(inner_pfs0)] = inner_pfs0
    if len(sec_plain) % switchmeta.MEDIA:
        sec_plain += b"\0" * (switchmeta.MEDIA - (len(sec_plain) % switchmeta.MEDIA))

    start = 0xC00
    end = start + len(sec_plain)
    hdr = bytearray(0xC00)
    hdr[0x200:0x204] = b"NCA3"
    hdr[0x205] = 1  # Meta NCA (CNMT)
    hdr[0x206] = 1
    struct.pack_into("<Q", hdr, 0x210, title_id)
    struct.pack_into("<II", hdr, 0x240, start // switchmeta.MEDIA, end // switchmeta.MEDIA)

    key_area_plain = b"\0" * 0x20 + FAKE_CTR_KEY + b"\0" * 0x10
    hdr[0x300:0x340] = _ecb_encrypt(FAKE_KAK_00, key_area_plain)

    fsh = bytearray(0x200)
    fsh[2] = 2  # PartitionFS
    fsh[4] = 3  # AES-CTR
    struct.pack_into("<Q", fsh, 0x38, 0x20)      # hash table size
    struct.pack_into("<Q", fsh, 0x40, pfs0_off)  # PFS0 offset dentro la sezione 0
    iv_hi = b"\x11\x22\x33\x44\x55\x66\x77\x88"
    fsh[0x140:0x148] = iv_hi[::-1]
    hdr[0x400:0x600] = fsh

    ctr = iv_hi + (start >> 4).to_bytes(8, "big")
    sec_enc = _ctr_encrypt(FAKE_CTR_KEY, ctr, bytes(sec_plain))
    return _xts_encrypt(FAKE_HEADER_KEY, bytes(hdr)) + sec_enc


def make_header_only_nca(content_type: int, title_id: int = 0x0100BF7006BCA000) -> bytes:
    hdr = bytearray(0x400)
    hdr[0x200:0x204] = b"NCA3"
    hdr[0x205] = content_type
    struct.pack_into("<Q", hdr, 0x210, title_id)
    return _xts_encrypt(FAKE_HEADER_KEY, bytes(hdr))


def test_package_selection(tmp: Path):
    sw = P.get("switch")
    gdir = tmp / "Crawl"
    gdir.mkdir()
    nsz = gdir / "Crawl [0100BF7006BCA000][v0].nsz"
    upd = gdir / "Crawl [0100BF7006BCA800][v65536].nsp"
    nsz.write_bytes(b"x" * 100)
    upd.write_bytes(b"y" * 200)

    g = Game(gdir, sw, 0, 0, is_dir=True)
    assert Nsp._package(g) == nsz, Nsp._package(g)

    # Anche per un .xcz singolo
    xcz = tmp / "Gioco [0100111000222000].xcz"
    xcz.write_bytes(b"z" * 50)
    assert Nsp._package(Game(xcz, sw, 50, 0, is_dir=False)) == xcz

    # File non Switch ignorato
    junk = tmp / "sync.ffs_db"
    junk.write_bytes(b"not a switch file")
    assert Nsp._package(Game(junk, sw, 17, 0, is_dir=False)) is None
    print("ok  _package riconosce .nsz/.xcz, sceglie il gioco base 000 e ignora altri file")


def test_nsz_with_uncompressed_control(tmp: Path):
    """In un .nsz il Program NCA e' .ncz mentre il Control NCA e' un .nca normale."""
    prog_ncz = make_header_only_nca(0) + b"ZSTD_STREAM"
    cnmt_nca = make_header_only_nca(1)
    ctrl_nca = make_fake_control_nca("Gioco Sintetico", "Editore Test", "1.2.0")
    nsz_data = make_pfs0([
        ("prog.ncz", prog_ncz, None),
        ("meta.cnmt.nca", cnmt_nca, None),
        ("ctrl.nca", ctrl_nca, None),
    ])
    p = tmp / "gioco.nsz"
    p.write_bytes(nsz_data)

    meta = switchmeta.read_meta(p, ["it", "en"], FAKE_KEYS)
    assert meta["title"] == "Gioco Sintetico", meta
    assert meta["publisher"] == "Editore Test", meta
    assert meta["version"] == "1.2.0", meta
    assert meta["title_id"] == "0100BF7006BCA000", meta
    assert meta["icon"].startswith(b"\xff\xd8\xff\xe0"), meta
    print("ok  lettura metadati da .nsz (Program .ncz + Control .nca)")


def test_error_cases(tmp: Path):
    # 1. Formato non valido
    bad = tmp / "non_valido.nsp"
    bad.write_bytes(b"NOT_A_PFS0_OR_XCI_HEADER")
    try:
        switchmeta.read_meta(bad, ["it"], FAKE_KEYS)
        raise AssertionError("doveva fallire per formato non riconosciuto")
    except switchmeta.SwitchError as e:
        assert "formato non riconosciuto" in str(e), e

    # 2. PFS0 senza Control NCA (solo .cnmt.nca e Program .nca)
    no_ctrl = tmp / "no_ctrl.nsp"
    no_ctrl.write_bytes(make_pfs0([
        ("meta.cnmt.nca", make_header_only_nca(1), None),
        ("prog.nca", make_header_only_nca(0), None),
    ]))
    try:
        switchmeta.read_meta(no_ctrl, ["it"], FAKE_KEYS)
        raise AssertionError("doveva fallire per assenza di Control NCA")
    except switchmeta.SwitchError as e:
        assert "NCA Control non trovato" in str(e), e

    # 3. PFS0 con Control NCA compresso (.ncz)
    ncz_ctrl = tmp / "ncz_ctrl.nsz"
    ncz_ctrl.write_bytes(make_pfs0([
        ("ctrl.ncz", make_header_only_nca(2), None),
    ]))
    try:
        switchmeta.read_meta(ncz_ctrl, ["it"], FAKE_KEYS)
        raise AssertionError("doveva segnalare Control .ncz compresso")
    except switchmeta.SwitchError as e:
        assert ".ncz" in str(e), e

    # 4. Pacchetto troncato (es. tagliato a 4 GiB prima del Control NCA)
    trunc = tmp / "troncato.nsp"
    prog = make_header_only_nca(0)
    ctrl = make_header_only_nca(2)
    raw_pfs0 = make_pfs0([
        ("prog.nca", prog, 10 * 1024 * 1024),
        ("ctrl.nca", ctrl, len(ctrl)),
    ])
    trunc.write_bytes(raw_pfs0[:1024])
    try:
        switchmeta.read_meta(trunc, ["it"], FAKE_KEYS)
        raise AssertionError("doveva segnalare pacchetto troncato")
    except switchmeta.SwitchError as e:
        assert "troncato" in str(e), e

    print("ok  errori distinti per formato non valido, Control mancante, Control .ncz e file troncato")


def test_package_info_and_folder_contents(tmp: Path):
    base_tid = 0x0100BF7006BCA000
    upd_tid = 0x0100BF7006BCA800
    dlc1_tid = 0x0100BF7006BCB001
    dlc2_tid = 0x0100BF7006BCB002

    fdir = tmp / "GiocoConUpdateEDlc"
    fdir.mkdir()

    # 1. Pacchetto base (Application = 0x80)
    base_pkg = fdir / "Gioco [0100BF7006BCA000][v0].nsz"
    base_pkg.write_bytes(make_pfs0([
        ("base.cnmt.nca", make_fake_cnmt_nca(0x80, base_tid, base_tid, 0), None),
        ("ctrl.nca", make_fake_control_nca("Gioco Completo", "Studio IT", "1.0.0", base_tid), None),
    ]))

    # 2. Due pacchetti di aggiornamento (Patch = 0x81): v65536 (1.1.0) e v131072 (1.2.0)
    upd_old = fdir / "Gioco [0100BF7006BCA800][v65536].nsp"
    upd_old.write_bytes(make_pfs0([
        ("upd1.cnmt.nca", make_fake_cnmt_nca(0x81, upd_tid, base_tid, 65536), None),
        ("ctrl1.nca", make_fake_control_nca("Gioco Completo", "Studio IT", "1.1.0", upd_tid), None),
    ]))
    upd_new = fdir / "Gioco [0100BF7006BCA800][v131072].nsp"
    upd_new.write_bytes(make_pfs0([
        ("upd2.cnmt.nca", make_fake_cnmt_nca(0x81, upd_tid, base_tid, 131072), None),
        ("ctrl2.nca", make_fake_control_nca("Gioco Completo", "Studio IT", "1.2.0", upd_tid), None),
    ]))

    # 3. Due DLC (AddOnContent = 0x82)
    dlc1 = fdir / "Gioco DLC 1 [0100BF7006BCB001][v0].nsp"
    dlc1.write_bytes(make_pfs0([
        ("dlc1.cnmt.nca", make_fake_cnmt_nca(0x82, dlc1_tid, base_tid, 0), None),
    ]))
    dlc2 = fdir / "Gioco DLC 2 [0100BF7006BCB002][v0].nsp"
    dlc2.write_bytes(make_pfs0([
        ("dlc2.cnmt.nca", make_fake_cnmt_nca(0x82, dlc2_tid, base_tid, 0), None),
    ]))

    # Verifica package_info sui singoli pacchetti
    bi = switchmeta.package_info(base_pkg, FAKE_KEYS)
    assert bi == {"type": "base", "title_id": "0100BF7006BCA000", "base_title_id": "0100BF7006BCA000", "version": 0}, bi

    ui = switchmeta.package_info(upd_new, FAKE_KEYS)
    assert ui == {"type": "update", "title_id": "0100BF7006BCA800", "base_title_id": "0100BF7006BCA000", "version": 131072}, ui

    di = switchmeta.package_info(dlc1, FAKE_KEYS)
    assert di == {"type": "dlc", "title_id": "0100BF7006BCB001", "base_title_id": "0100BF7006BCA000", "version": 0}, di

    # Verifica folder_contents sulla cartella
    fc = switchmeta.folder_contents(fdir, FAKE_KEYS)
    assert fc["base"] == bi, fc
    assert fc["update"] == 131072, fc
    assert fc["update_display"] == "1.2.0", fc
    assert fc["dlc"] == 2, fc
    assert fc["errors"] == [], fc

    # Verifica che Nsp._read aggiunga switch_update e switch_dlc ai campi restituiti
    ctx = SimpleNamespace(cfg=SimpleNamespace(languages=["it", "en"], cache=str(tmp / "cache")),
                          cache=None, client=None, log=lambda *a: None)
    src = Nsp(ctx)
    src.keys = FAKE_KEYS
    g = Game(fdir, P.get("switch"), 0, 0, is_dir=True)
    res = src._read(g)
    assert res is not None
    assert res.fields["title"] == "Gioco Completo", res.fields
    assert res.fields["switch_update"] == "1.2.0", res.fields
    assert res.fields["switch_dlc"] == 2, res.fields
    assert Nsp.provides == {"title", "publisher", "media:cover"}, Nsp.provides
    print("ok  package_info, folder_contents e campi switch_update/switch_dlc in Nsp._read")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        tmp = Path(t)
        test_package_selection(tmp)
        test_nsz_with_uncompressed_control(tmp)
        test_error_cases(tmp)
        test_package_info_and_folder_contents(tmp)
    print("TUTTI I TEST OK")
