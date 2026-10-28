"""Certificato HTTPS autofirmato per l'app del telefono.

Sul telefono i sensori di movimento (giroscopio) sono disponibili solo nelle pagine HTTPS: il
certificato viene creato una volta, valido per gli indirizzi del PC sulla rete di casa. Il telefono
mostra un avviso la prima volta ("connessione non privata"): si accetta e non viene piu' chiesto.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
from pathlib import Path


def lan_ips() -> list[str]:
    """Indirizzi IPv4 del PC sulla rete locale (il primo e' quello della connessione principale).

    Non risolve il nome host: su macOS `getaddrinfo(hostname)` puo' restare appeso per minuti quando il
    nome (spesso qualcosa.local, senza mDNS raggiungibile) non risponde, e bloccherebbe l'interfaccia.
    Gli indirizzi si leggono dal sistema con psutil (nessun DNS, millisecondi).
    """
    found: list[str] = []
    try:  # interfaccia usata per uscire verso internet (non invia nulla: UDP senza dati)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        import psutil

        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and a.address:
                    found.append(a.address)
    except Exception:  # psutil assente o rete non leggibile: restano gli indirizzi gia' trovati
        pass
    ips: list[str] = []
    for ip in found:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_private and not (addr.is_loopback or addr.is_link_local or addr.is_unspecified):
            if str(addr) not in ips:
                ips.append(str(addr))
    return ips


def ensure(folder: Path) -> tuple[Path, Path]:
    """(certificato, chiave): li crea se mancano o se gli indirizzi del PC sono cambiati."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    folder.mkdir(parents=True, exist_ok=True)
    crt, key = folder / "phone.crt", folder / "phone.key"
    ips = lan_ips()
    if crt.exists() and key.exists():
        try:
            cert = x509.load_pem_x509_certificate(crt.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
            if set(ips) <= have and cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
                return crt, key
        except Exception:
            pass
    pk = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname() or "localhost"
    cn = f"GameScraper ({host})"[:64]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    alt: list[x509.GeneralName] = [x509.DNSName("localhost")]
    if host and host != "localhost":
        try:
            alt.append(x509.DNSName(host))
        except Exception:
            pass
    for i in ["127.0.0.1", *ips]:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(i)))
        except Exception:
            pass
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(pk, hashes.SHA256()))
    key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return crt, key
