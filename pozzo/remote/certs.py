"""Certificato HTTPS per l'app del telefono, firmato da una CA locale.

Un certificato autofirmato cifra la connessione ma il telefono lo segnala come «connessione non privata»; con un
certificato non fidato il browser rifiuta anche il service worker, quindi l'app **non si puo' installare**. Per
questo qui c'e' una piccola CA (autorita' di certificazione) del PC: si installa una volta sul telefono
(Impostazioni › Telefono e streaming › certificato) e da li' in poi il certificato del server e' fidato, l'avviso
sparisce e l'app si installa.

La CA resta la stessa per 10 anni anche quando cambiano gli indirizzi del PC: si rifa' solo il certificato del
server, firmato dalla stessa CA, quindi il telefono non va riconfigurato. Le chiavi stanno nella cartella certs/
accanto alle impostazioni: ca.key e' la chiave che rende fidato il PC, non va copiata altrove (chi la avesse
potrebbe firmare certificati che i telefoni accettano).
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import threading
from pathlib import Path

_LOCK = threading.Lock()  # due richieste insieme (avvio del server e download del certificato) non devono creare due CA

CA_FILE, CA_KEY_FILE = "ca.crt", "ca.key"
CRT_FILE, KEY_FILE = "phone.crt", "phone.key"
CA_DAYS = 3650  # 10 anni: il telefono non va riconfigurato
LEAF_DAYS = 825  # limite accettato da iOS per i certificati dei server


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


def ca_path(folder: Path) -> Path:
    """Percorso del certificato della CA da installare sul telefono (lo crea se manca)."""
    ensure(folder)
    return folder / CA_FILE


def _ca(folder: Path):
    """(certificato della CA, chiave della CA): creati una volta sola e riusati per sempre."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    crt, key = folder / CA_FILE, folder / CA_KEY_FILE
    now = dt.datetime.now(dt.timezone.utc)
    if crt.exists() and key.exists():
        try:
            cert = x509.load_pem_x509_certificate(crt.read_bytes())
            if cert.not_valid_after_utc > now + dt.timedelta(days=365):
                return cert, serialization.load_pem_private_key(key.read_bytes(), None)  # chiave della CA senza passphrase
        except Exception:  # file rovinato o chiave illeggibile: si rifa' tutto, il telefono va reinstallato
            pass
    pk = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname() or "pc"
    name = x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Pozzo Launcher"),
                      x509.NameAttribute(NameOID.COMMON_NAME, f"Pozzo Launcher CA ({host})"[:64])])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=CA_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            # Android accetta la CA come tale solo con keyCertSign
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=True,
                                         crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(pk.public_key()), critical=False)
            .sign(pk, hashes.SHA256()))
    key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert, pk


def ensure(folder: Path) -> tuple[Path, Path, Path]:
    """(certificato del server, chiave del server, certificato della CA): li crea o li rifa' quando serve.

    Il certificato del server si rifa' se gli indirizzi del PC sono cambiati, se sta per scadere o se era ancora
    autofirmato (versioni precedenti a questa): la CA invece resta, quindi il telefono lo accetta senza reinstallare
    niente. Quello vecchio non era fidato, quindi rigenerarlo non peggiora nulla.
    """
    with _LOCK:  # la CA e il certificato si scrivono una volta sola: due scritture insieme lasciano file incoerenti
        return _ensure(folder)


def _ensure(folder: Path) -> tuple[Path, Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    folder.mkdir(parents=True, exist_ok=True)
    ca_cert, ca_key = _ca(folder)
    crt, key, ca_crt = folder / CRT_FILE, folder / KEY_FILE, folder / CA_FILE
    ips = lan_ips()
    now = dt.datetime.now(dt.timezone.utc)
    if crt.exists() and key.exists():
        try:
            cert = x509.load_pem_x509_certificate(crt.read_bytes())
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            have = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
            # firmato da QUESTA CA (non solo con lo stesso nome: serve la firma, quindi l'AKI uguale al SKI della CA)
            aki = cert.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value.key_identifier
            ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
            signed_by_us = cert.issuer == ca_cert.subject and aki == ski
            if signed_by_us and set(ips) <= have and cert.not_valid_after_utc > now + dt.timedelta(days=30):
                return crt, key, ca_crt
        except Exception:
            pass
    pk = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname() or "localhost"
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Pozzo Launcher ({host})"[:64])])
    alt: list[x509.GeneralName] = [x509.DNSName("localhost")]
    if host and host != "localhost":
        try:
            alt.append(x509.DNSName(host))
            if "." not in host:  # nome di Windows: aggiungere .local aiuta dove c'e' il mDNS (es. iPhone)
                alt.append(x509.DNSName(f"{host}.local"))
        except Exception:
            pass
    for i in ["127.0.0.1", *ips]:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(i)))
        except Exception:
            pass
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca_cert.subject).public_key(pk.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(pk.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return crt, key, ca_crt
