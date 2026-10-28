# Componenti di terze parti

Pozzo Launcher usa queste librerie, incluse negli eseguibili delle release. Licenze lette dai metadati dei pacchetti; il testo completo di ciascuna è nel pacchetto stesso e sul sito del progetto.

| Componente | Licenza | Progetto |
|---|---|---|
| httpx | BSD-3-Clause | https://github.com/encode/httpx |
| rich | MIT | https://github.com/Textualize/rich |
| RapidFuzz | MIT | https://github.com/rapidfuzz/RapidFuzz |
| Beautiful Soup 4 | MIT | https://www.crummy.com/software/BeautifulSoup/ |
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pillow | MIT-CMU | https://github.com/python-pillow/Pillow |
| pywebview | BSD-3-Clause | https://github.com/r0x0r/pywebview |
| psutil | BSD-3-Clause | https://github.com/giampaolo/psutil |
| PyYAML | MIT | https://github.com/yaml/pyyaml |
| howlongtobeatpy | MIT | https://github.com/ScrappyCocco/HowLongToBeat-PythonAPI |
| qrcode | BSD | https://github.com/lincolnloop/python-qrcode |
| cryptography | Apache-2.0 o BSD-3-Clause | https://github.com/pyca/cryptography |
| ViGEmClient (`pozzo/remote/vendor/ViGEmClient.dll`) | MIT, vedi [LICENSE-ViGEm.txt](pozzo/remote/vendor/LICENSE-ViGEm.txt) | https://github.com/nefarius/ViGEmClient |

Gli eseguibili includono anche le dipendenze di queste librerie e l'interprete Python (licenza PSF). L'eseguibile **Linux** include anche Qt tramite `pywebview[qt]`, cioè **PyQt6 e PyQt6-WebEngine (GPL-3.0)**: chi lo ridistribuisce deve rispettarne i termini.

**Dati scaricati durante l'uso**, non inclusi nel repository né negli eseguibili: DAT e immagini di libretro, database di LaunchBox e TheGamesDB, manifest di Ludusavi e i dati delle altre fonti elencate in [PRIVACY.md](PRIVACY.md). Restano soggetti ai termini dei rispettivi progetti.
