# KODI Stéblo

Kodi repozitář a zdrojový kód doplňku **PvS Cinema** (`plugin.video.pvscinema`) pro televizi Samsung s boxem T-Mobile
(Skyworth, Android TV 12, Kodi 21.2 32bit).

## Struktura
- `src/plugin.video.pvscinema/` – zdrojový kód doplňku (filmy a seriály z Webshare.cz, katalog TMDB)
- `src/repository.pvs/` – doplněk repozitáře (odkazuje na raw.githubusercontent.com, GitHub Pages netřeba)
- `zips/` – sestavené ZIPy, `addons.xml` a `addons.xml.md5`, ze kterých Kodi stahuje aktualizace
- `repository.pvs-*.zip` – instalační ZIP repozitáře (instalace z flashky)
- `build.py` – sestavení: `python3 build.py`

## Vydání nové verze
1. Uprav kód v `src/plugin.video.pvscinema/`
2. Zvyš `version` v `src/plugin.video.pvscinema/addon.xml`
3. `python3 build.py`, commit a push
4. V Kodi: PvS Repository → Check for updates (GitHub drží starou verzi ~5 min v mezipaměti)

## Poznámky k boxu
- Doplněk s „webshare“ v ID (`plugin.video.webshare.hledani`) box nespustil – Python soubory po instalaci chyběly.
  Proto ID `plugin.video.pvscinema`.
- Instalace jen přes repozitář (Install from repository), ne přímo ze ZIPu doplňku.
- Sekce 18+: v Hledat zadat `#4711`. Odemčení je dočasné (do návratu do hlavního menu nebo restartu Kodi),
  v hlavním menu se neukazuje, hledá v kategorii `adult` na Webshare.
- Otevřené: přihlášení k Webshare na boxu hlásí `LOGIN_FATAL_1` (Invalid credentials), stejný účet jinde funguje.
