"""Windows / Xbox (Microsoft Store e Game Pass per PC): giochi installati.

Si riconoscono i pacchetti che contengono MicrosoftGame.config (il formato dei giochi Xbox per PC)
e si avviano dal menu Start (shell:AppsFolder). La libreria posseduta richiederebbe
l'autenticazione Xbox Live: per ora solo gli installati.
"""

from __future__ import annotations

from .base import StoreGame, StoreProvider, is_windows, powershell_json

SCRIPT = r"""
$apps = Get-StartApps
Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.InstallLocation -and
    (Test-Path -LiteralPath (Join-Path $_.InstallLocation 'MicrosoftGame.config')) } | ForEach-Object {
  $p = $_
  $a = $apps | Where-Object { $_.AppID -like ($p.PackageFamilyName + '!*') } | Select-Object -First 1
  $title = $null
  try { $title = ([xml](Get-Content -LiteralPath (Join-Path $p.InstallLocation 'MicrosoftGame.config'))).Game.ShellVisuals.DefaultDisplayName } catch {}
  [pscustomobject]@{ Name = $p.Name; PFN = $p.PackageFamilyName; Loc = $p.InstallLocation;
                     AppID = $a.AppID; Title = $(if ($a.Name) { $a.Name } else { $title }) }
} | ConvertTo-Json -Compress
"""
# gestiti da provider dedicati
EXCLUDE = ("4297127D64EC6", "MinecraftUWP", "Microsoft.GamingApp", "Microsoft.XboxApp")


class Xbox(StoreProvider):
    id = "xbox"
    name = "Windows / Xbox"
    owned_note = "solo giochi installati: la libreria Xbox/Game Pass richiede l'accesso a Xbox Live"

    def detected(self) -> bool:
        return is_windows()

    def account(self) -> dict:
        return {"connected": is_windows(), "note": self.owned_note}

    async def fetch(self) -> list[StoreGame]:
        rows = powershell_json(SCRIPT) or []
        games = []
        for r in rows:
            if not r.get("AppID") or any(x in (r.get("PFN") or "") for x in EXCLUDE):
                continue
            games.append(StoreGame(
                store=self.id, id=r["PFN"], title=r.get("Title") or r.get("Name"), installed=True,
                install_dir=r.get("Loc") or "",
                launch={"type": "exe", "target": "explorer.exe", "args": [f"shell:AppsFolder\\{r['AppID']}"],
                        "track_dir": r.get("Loc") or ""},
                install_uri=f"ms-windows-store://pdp/?PFN={r['PFN']}",
                page=f"ms-windows-store://pdp/?PFN={r['PFN']}"))
        self.log(f"Windows/Xbox: {len(games)} giochi installati")
        return games
