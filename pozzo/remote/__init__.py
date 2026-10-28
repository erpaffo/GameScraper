"""Gioco da remoto e telefono.

  vigem.py    controller Xbox virtuali sul PC (driver ViGEmBus) mossi dal telefono
  dsu.py      server DSU/cemuhook: giroscopio e accelerometro del telefono per Cemu, Ryujinx, Dolphin, Azahar...
  phone.py    app per il telefono (HTTPS sulla rete di casa, abbinamento con QR): libreria, avvio, controller
  certs.py    HTTPS del telefono: una CA locale (da fidare una volta sul telefono) firma il certificato del PC,
              cosi' il browser non mostra avvisi e accetta il service worker (app installabile)
  sunshine.py streaming con Sunshine/Moonlight: pubblica la Big Picture e i giochi come app di Moonlight
"""
