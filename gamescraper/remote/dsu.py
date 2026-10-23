"""Server DSU ("cemuhook"): manda il movimento del telefono (giroscopio e accelerometro) agli emulatori.

Protocollo UDP standard per i sensori di movimento, letto da Cemu (Wii U), Ryujinx (Switch), Dolphin
(Wii/GameCube), Azahar/Citra (3DS) e altri: nell'emulatore si sceglie come sorgente del movimento
"DSU/cemuhook" con indirizzo 127.0.0.1 e porta 26760 (predefiniti).

Pacchetto: intestazione di 16 byte (magic "DSUS", versione 1001, lunghezza, CRC32, id del server)
seguita dal tipo di messaggio. Il client chiede la versione (0x100000), le informazioni sugli slot
(0x100001) e si iscrive ai dati (0x100002), ripetendo la richiesta di continuo: a chi smette di
chiedere per piu' di 5 secondi non si manda piu' niente.

Unita': accelerometro in g, giroscopio in gradi/s; assi come un DualShock 4 (X destra, Y verso l'alto
dalla faccia superiore, Z verso il giocatore). Fino a 4 slot (un telefono per slot).
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
import zlib

PORT = 26760
VERSION = 1001
MSG_VERSION, MSG_INFO, MSG_DATA = 0x100000, 0x100001, 0x100002
SLOTS = 4
TIMEOUT = 5.0


def _packet(server_id: int, msg_type: int, payload: bytes) -> bytes:
    body = struct.pack("<I", msg_type) + payload
    head = struct.pack("<4sHHII", b"DSUS", VERSION, len(body), 0, server_id)
    pkt = bytearray(head + body)
    struct.pack_into("<I", pkt, 8, zlib.crc32(pkt) & 0xFFFFFFFF)
    return bytes(pkt)


class Slot:
    def __init__(self, n: int):
        self.n = n
        self.connected = False
        self.mac = bytes([0x02, 0x47, 0x53, 0x00, 0x00, n + 1])  # indirizzo finto ma stabile per slot
        self.accel = (0.0, 0.0, 0.0)
        self.gyro = (0.0, 0.0, 0.0)
        self.buttons = 0
        self.sticks = (128, 128, 128, 128)
        self.counter = 0

    def shared(self) -> bytes:
        # slot, stato (2 = connesso), modello (2 = giroscopio completo), connessione (2 = Bluetooth), MAC, batteria (5 = carica)
        return struct.pack("<BBBB6sB", self.n, 2 if self.connected else 0, 2, 2, self.mac, 5 if self.connected else 0)


class DSUServer:
    def __init__(self, host: str = "127.0.0.1", port: int = PORT):
        self.addr = (host, port)
        self.server_id = int.from_bytes(os.urandom(4), "little")
        self.slots = [Slot(i) for i in range(SLOTS)]
        self.clients: dict[tuple, dict] = {}  # indirizzo -> {"t": ultima richiesta, "slots": set | None}
        self.sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.running = False

    # ------------------------------------------------------------ dati dal telefono
    def connect(self, slot: int):
        with self.lock:
            self.slots[slot].connected = True

    def disconnect(self, slot: int):
        with self.lock:
            s = self.slots[slot]
            s.connected = False
            s.accel = s.gyro = (0.0, 0.0, 0.0)
        self._send_data(slot)

    def update_motion(self, slot: int, accel: tuple[float, float, float], gyro: tuple[float, float, float]):
        """accel in g (x, y, z); gyro in gradi/s (pitch, yaw, roll)."""
        with self.lock:
            s = self.slots[slot]
            s.connected = True
            s.accel = tuple(float(v) for v in accel)
            s.gyro = tuple(float(v) for v in gyro)
        self._send_data(slot)

    def free_slot(self) -> int | None:
        with self.lock:
            return next((s.n for s in self.slots if not s.connected), None)

    # ------------------------------------------------------------ protocollo
    def _data_payload(self, s: Slot) -> bytes:
        s.counter = (s.counter + 1) & 0xFFFFFFFF
        ts = int(time.monotonic() * 1_000_000)
        lx, ly, rx, ry = s.sticks
        return (s.shared() + struct.pack("<BI", 1 if s.connected else 0, s.counter)
                + struct.pack("<BBBB", 0, 0, 0, 0)          # pulsanti, PS, touch (il movimento e' quello che conta)
                + struct.pack("<BBBB", lx, ly, rx, ry)
                + bytes(4) + bytes(4) + bytes(4)            # dpad, ABXY e dorsali analogici
                + bytes(12)                                  # due tocchi del touchpad (non usati)
                + struct.pack("<Q", ts)
                + struct.pack("<fff", *s.accel)
                + struct.pack("<fff", *s.gyro))

    def _send_data(self, slot: int):
        if not self.sock:
            return
        now = time.time()
        with self.lock:
            s = self.slots[slot]
            targets = [a for a, c in self.clients.items() if now - c["t"] < TIMEOUT and (c["slots"] is None or slot in c["slots"])]
            if not targets:
                return
            pkt = _packet(self.server_id, MSG_DATA, self._data_payload(s))
        for a in targets:
            try:
                self.sock.sendto(pkt, a)
            except OSError:
                pass

    def handle(self, data: bytes, addr) -> list[bytes]:
        """Risposte a un pacchetto del client (separato dalla rete per poterlo provare)."""
        if len(data) < 20 or data[:4] != b"DSUC":
            return []
        crc = struct.unpack_from("<I", data, 8)[0]
        chk = bytearray(data)
        struct.pack_into("<I", chk, 8, 0)
        if zlib.crc32(chk) & 0xFFFFFFFF != crc:
            return []
        msg = struct.unpack_from("<I", data, 16)[0]
        out = []
        if msg == MSG_VERSION:
            out.append(_packet(self.server_id, MSG_VERSION, struct.pack("<H", VERSION)))
        elif msg == MSG_INFO and len(data) >= 24:
            n = struct.unpack_from("<i", data, 20)[0]
            for i in range(max(0, min(n, 4))):
                if 24 + i < len(data):
                    sl = data[24 + i]
                    if sl < SLOTS:
                        with self.lock:
                            out.append(_packet(self.server_id, MSG_INFO, self.slots[sl].shared() + b"\0"))
        elif msg == MSG_DATA and len(data) >= 28:
            flags, slot = data[20], data[21]
            mac = data[22:28]
            with self.lock:
                if flags == 0:
                    want = None
                elif flags & 1:
                    want = {slot}
                else:
                    want = {s.n for s in self.slots if s.mac == mac}
                c = self.clients.setdefault(addr, {"t": 0, "slots": want})
                c["t"] = time.time()
                if want is None or c["slots"] is None:
                    c["slots"] = None
                else:
                    c["slots"] = set(c["slots"]) | want
        return out

    # ------------------------------------------------------------ rete
    def start(self) -> bool:
        if self.running:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(self.addr)
        except OSError:
            self.sock = None
            return False  # porta gia' usata (es. un altro server DSU)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._keepalive, daemon=True).start()
        return True

    def _loop(self):
        while self.running and self.sock:
            try:
                data, addr = self.sock.recvfrom(1024)
            except OSError:
                if not self.running:
                    break
                continue
            for pkt in self.handle(data, addr):
                try:
                    self.sock.sendto(pkt, addr)
                except OSError:
                    pass

    def _keepalive(self):
        # alcuni emulatori considerano scollegato un controller senza dati recenti: si ripete l'ultimo stato
        while self.running:
            time.sleep(0.1)
            for s in self.slots:
                if s.connected:
                    self._send_data(s.n)

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None
