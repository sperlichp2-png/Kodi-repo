# Webshare Hledání – doplněk pro Kodi 19+
# Katalog filmů a seriálů z TMDB (české názvy, popisy, plakáty, hodnocení), přehrávání z Webshare.cz,
# výběr streamu s technickými údaji (kvalita, kodek, audio stopy, bitrate) a automatické titulky.
# Ovládání a vzhled po vzoru Stream Cinema.
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib import tmdb as T
from resources.lib.wsapi import Webshare, WebshareError, vip_text

ADDON = xbmcaddon.Addon()
BASE = sys.argv[0]
HANDLE = int(sys.argv[1])
PARAMS = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))

SORTS = ['', 'recent', 'rating', 'largest', 'smallest']
VIDEO_EXT = ('.mkv', '.mp4', '.avi', '.m4v', '.ts', '.wmv', '.mov', '.mpg', '.mpeg', '.webm', '.m2ts', '.vob')
SUB_EXT = ('.srt', '.sub', '.ssa', '.ass', '.vtt', '.smi')
EP_PATTERNS = [
    re.compile(r'[Ss](\d{1,2})\s?[._-]?\s?[Ee](\d{1,3})'),       # S01E02, s1e2, S01.E02
    re.compile(r'(?<!\d)(\d{1,2})[xX](\d{2,3})(?!\d)'),           # 1x02
]
AVAIL_TTL = 6 * 3600

# ---------- kvalita ----------
QUALITIES = ['4K', '1080p', '720p', 'SD', 'Neznámá']
QUALITY_RX = [
    ('4K', re.compile(r'2160p|\b4k\b|\buhd\b', re.I)),
    ('1080p', re.compile(r'1080[pi]|full\s?hd|\bfhd\b', re.I)),
    ('720p', re.compile(r'720p|\bhd\s?rip\b|\bhdtv\b', re.I)),
    ('SD', re.compile(r'480p|576p|360p|dvd\s?rip|\bdvd\b|xvid|divx|\bsd\b|\bvhs\b|\btvrip\b|\bcam\b', re.I)),
]
QUALITY_COLORS = {'4K': 'FFAB47BC', '1080p': 'FF43A047', '720p': 'FF1E88E5', 'SD': 'FFFB8C00', 'Neznámá': 'FF757575'}
QUALITY_RANK = {q: i for i, q in enumerate(QUALITIES)}


def quality_of(name):
    n = name.replace('.', ' ').replace('_', ' ')
    for q, rx in QUALITY_RX:
        if rx.search(n):
            return q
    return 'Neznámá'


def quality_from_info(fi):
    w, h = (fi or {}).get('width') or 0, (fi or {}).get('height') or 0
    if not w and not h:
        return None
    if h >= 1600 or w >= 3200:
        return '4K'
    if h >= 900 or w >= 1800:
        return '1080p'
    if h >= 650 or w >= 1200:
        return '720p'
    return 'SD'


def tags_of(name):
    t = tokens(name)
    tags = []
    if t & {'hdr', 'hdr10', 'dv', 'dovi'}:
        tags.append('HDR')
    if t & {'atmos'}:
        tags.append('Atmos')
    return tags


# ---------- jazyk ----------
LANGS = ['CZ dabing', 'CZ titulky', 'EN', 'SK', 'Ostatní']
LANG_RANK = {l: i for i, l in enumerate(LANGS)}
LANG_SHORT = {'CZ dabing': 'CZ', 'CZ titulky': 'CZ tit', 'EN': 'EN', 'SK': 'SK', 'Ostatní': '?'}
LANG_COLORS = {'CZ dabing': 'FFE53935', 'CZ titulky': 'FFEF9A9A', 'EN': 'FF42A5F5',
               'SK': 'FF26A69A', 'Ostatní': 'FF9E9E9E'}
CZ_T = {'cz', 'cze', 'czech', 'cesky', 'cestina', 'czdab', 'czdabing', 'czsk', 'dabcz', 'dabingcz', 'czdub'}
DUB_T = {'dabing', 'dab', 'dabingem', 'dub', 'dubbing', 'czdab', 'czdabing', 'dabcz', 'dabingcz', 'czdub'}
TIT_T = {'tit', 'titulky', 'titulkama', 'cztit', 'cztitulky', 'sub', 'subs', 'subtitles'}
SK_T = {'sk', 'slovak', 'slovensky', 'skdab', 'skdabing'}
EN_T = {'en', 'eng', 'english'}
# typické značky původních (anglických) release, když jazyk v názvu chybí
SCENE_T = {'bluray', 'bdrip', 'brrip', 'web', 'webrip', 'webdl', 'dl', 'x264', 'x265', 'h264', 'h265', 'hevc',
           'amzn', 'nf', 'dsnp', 'hmax', 'atvp', 'yify', 'yts', 'rarbg', 'proper', 'repack', 'ddp5', 'aac'}
LANGCODE = {'cs': 'CZ', 'cz': 'CZ', 'cze': 'CZ', 'ces': 'CZ', 'czech': 'CZ', 'sk': 'SK', 'slk': 'SK', 'slo': 'SK',
            'slovak': 'SK', 'en': 'EN', 'eng': 'EN', 'english': 'EN', 'pol': 'PL', 'pl': 'PL', 'ger': 'DE',
            'deu': 'DE', 'fre': 'FR', 'fra': 'FR', 'spa': 'ES', 'ita': 'IT', 'hun': 'HU', 'rus': 'RU', 'ukr': 'UK',
            'jpn': 'JA', 'kor': 'KO', 'chi': 'ZH', 'zho': 'ZH', 'por': 'PT', 'dut': 'NL', 'nld': 'NL'}


def lang_of(name):
    t = tokens(name)
    if t & SK_T and not t & CZ_T:
        return 'SK'
    if t & CZ_T or t & DUB_T:
        if t & TIT_T and not t & DUB_T:
            return 'CZ titulky'
        return 'CZ dabing'
    if t & TIT_T:
        return 'CZ titulky'
    if t & SK_T:
        return 'SK'
    if t & EN_T or t & SCENE_T:
        return 'EN'
    return 'Ostatní'


def lcode(l):
    l = re.split(r'[-_ (/]', (l or '').lower().strip())[0]     # „cs-CZ“, „cze (Czech)“ → „cs“, „cze“
    return LANGCODE.get(l, l[:2].upper() if l else '')


def langs_summary(files):
    """Krátký přehled jazyků pro seznam titulů, např. „CZ, CZ tit, EN“."""
    have = {lang_of(f['name']) for f in files}
    out = [LANG_SHORT[l] for l in LANGS if l in have and l != 'Ostatní']
    return ', '.join(out or (['?'] if files else []))


# ---------- pomocné funkce ----------
def build_url(**kw):
    return BASE + '?' + urllib.parse.urlencode({k: v for k, v in kw.items() if v not in (None, '')})


def notify(msg, error=True):
    icon = xbmcgui.NOTIFICATION_ERROR if error else ADDON.getAddonInfo('icon')
    xbmcgui.Dialog().notification('PvS Cinema', msg, icon, 5000, False)   # False = bez zvuku


def log(msg):
    xbmc.log('[Webshare Hledání] %s' % msg, xbmc.LOGINFO)


def setting(key):
    return xbmcaddon.Addon().getSetting(key)


def setting_int(key, default=0):
    try:
        return int(float(setting(key)))
    except (ValueError, TypeError):
        return default


def setting_bool(key, default=False):
    v = setting(key)
    return default if v == '' else v == 'true'


def human_size(b):
    for unit in ('B', 'KB', 'MB'):
        if b < 1024:
            return '%.0f %s' % (b, unit)
        b /= 1024.0
    return ('%.2f GB' % b).replace('.', ',')


def hms(sec):
    sec = int(sec or 0)
    return '%d:%02d:%02d' % (sec // 3600, sec // 60 % 60, sec % 60) if sec else ''


def strip_diacritics(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '') if not unicodedata.combining(c))


def tokens(s):
    return {t for t in re.split(r'[\W_]+', strip_diacritics(s).lower()) if t}


def norm_text(s):
    return ' '.join(t for t in re.split(r'[\W_]+', strip_diacritics(s).lower()) if t)


def uniq(seq):
    out = []
    for x in seq:
        if x and x not in out:
            out.append(x)
    return out


def _path(*names):
    p = os.path.join(PROFILE, *names)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def load_json(name, default):
    try:
        with open(_path(name), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, data):
    with open(_path(name), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def add_dir(label, icon='DefaultFolder.png', art=None, info=None, label2=None, context=None, folder=True, **kw):
    """folder=False pro položky, které jen něco provedou (dialog, nastavení) a nevypisují seznam."""
    li = xbmcgui.ListItem(label)
    if label2:
        li.setLabel2(label2)
    li.setArt(art or {'icon': icon})
    if info:
        set_info(li, info)
    if context:
        li.addContextMenuItems(context)
    xbmcplugin.addDirectoryItem(HANDLE, build_url(**kw), li, folder)


VIEW_IDS = [None, 50, 51, 54, 55, 500]   # Estuary: seznam s náhledem, plakáty, info stěna, široký seznam, stěna
_TITLE_VIEW = False


def titles_view():
    """Označí aktuální obrazovku jako seznam titulů – po vykreslení se přepne zobrazení s náhledy."""
    global _TITLE_VIEW
    _TITLE_VIEW = True


def end_dir(handle, **kw):
    xbmcplugin.endOfDirectory(handle, **kw)
    # všechny obrazovky doplňku (i menu Filmy / Seriály) jako seznam pod sebou
    if kw.get('succeeded', True):
        view = VIEW_IDS[min(setting_int('view_mode', 1), len(VIEW_IDS) - 1)]
        if view:
            # Kodi po načtení seznamu obnoví zobrazení, které si pro daný typ (např. díly) pamatuje –
            # proto počkej, až se seznam opravdu ukáže, a teprve pak zobrazení přepni.
            me = BASE + sys.argv[2]
            mon = xbmc.Monitor()
            for _ in range(40):
                if xbmc.getInfoLabel('Container.FolderPath') == me or mon.waitForAbort(0.1):
                    break
            mon.waitForAbort(0.2)
            # u dílů Estuary seznam 50 nenabízí → zkus „Široký seznam“ (55), ať jsou díly pod sebou
            # Estuary nabízí „Seznam“ (50) jen u filmů, seriálů, sérií a souborů; u dílů a menu (bez typu
            # obsahu) je seznamem pod sebou „Široký seznam“ (55)
            content = xbmc.getInfoLabel('Container.Content')
            if view == 50 and content not in ('movies', 'tvshows', 'seasons', 'videos'):
                wanted = [55, 50]
            else:
                wanted = [view]
            if xbmc.getInfoLabel('System.CurrentControlID') in [str(v) for v in wanted]:
                return      # Kodi už zobrazuje správně – nepřekreslovat
            done = False
            for _ in range(5):      # Kodi občas první pokus ignoruje, když ještě dokresluje
                for v in wanted:
                    xbmc.executebuiltin('Container.SetViewMode(%d)' % v)
                    mon.waitForAbort(0.4)
                    if xbmc.getInfoLabel('System.CurrentControlID') == str(v):
                        done = True
                        break
                if done or mon.waitForAbort(0.3):
                    break
            log('Zobrazení: %s' % xbmc.getInfoLabel('Container.Viewmode'))


def ask(heading, default=''):
    kb = xbmcgui.Dialog().input(heading, default)
    return kb.strip() if kb else ''


def set_info(li, info):
    """Nastaví metadata položky – funguje v Kodi 19 i 20+. Hodnocení je na stupnici 0–10."""
    try:
        tag = li.getVideoInfoTag()
        if info.get('title'):
            tag.setTitle(info['title'])
        if info.get('originaltitle'):
            tag.setOriginalTitle(info['originaltitle'])
        if info.get('year'):
            tag.setYear(int(info['year']))
        if info.get('plot'):
            tag.setPlot(info['plot'])
        if info.get('genre'):
            tag.setGenres(info['genre'])
        if info.get('rating'):
            tag.setRating(float(info['rating']), int(info.get('votes') or 0), 'tmdb', True)
        if info.get('tvshowtitle'):
            tag.setTvShowTitle(info['tvshowtitle'])
        if info.get('season') is not None:
            tag.setSeason(int(info['season']))
        if info.get('episode') is not None:
            tag.setEpisode(int(info['episode']))
        if info.get('aired'):
            tag.setFirstAired(info['aired'])
        if info.get('duration'):
            tag.setDuration(int(info['duration']))
        if info.get('imdb'):
            tag.setIMDBNumber(info['imdb'])
            tag.setUniqueIDs({'imdb': info['imdb']}, 'imdb')
        if info.get('mediatype'):
            tag.setMediaType(info['mediatype'])
    except AttributeError:  # Kodi 19
        old = {k: v for k, v in info.items() if k not in ('imdb', 'votes') and v not in (None, '')}
        if info.get('imdb'):
            old['imdbnumber'] = info['imdb']
        li.setInfo('video', old)


# ---------- technické údaje streamu (Webshare file_info) ----------
VCODEC = {'avc': 'H264', 'h264': 'H264', 'avc1': 'H264', 'hevc': 'H265', 'h265': 'H265', 'mpeg4': 'MPEG4',
          'mpeg-4 visual': 'MPEG4', 'xvid': 'XviD', 'divx': 'DivX', 'av1': 'AV1', 'vp9': 'VP9', 'vp8': 'VP8',
          'vc-1': 'VC1', 'mpeg video': 'MPEG2', 'mpeg2': 'MPEG2', 'wmv3': 'WMV'}
KODI_VCODEC = {'H264': 'h264', 'H265': 'hevc', 'MPEG4': 'mpeg4', 'XviD': 'xvid', 'DivX': 'divx', 'AV1': 'av1',
               'VP9': 'vp9', 'VP8': 'vp8', 'VC1': 'vc1', 'MPEG2': 'mpeg2video', 'WMV': 'wmv3'}
ACODEC = {'ac-3': 'ac3', 'ac3': 'ac3', 'e-ac-3': 'eac3', 'eac3': 'eac3', 'aac': 'aac', 'dts': 'dts', 'mp3': 'mp3',
          'mpeg audio': 'mp3', 'truehd': 'truehd', 'mlp fba': 'truehd', 'flac': 'flac', 'opus': 'opus',
          'vorbis': 'vorbis', 'pcm': 'pcm', 'mp2': 'mp2', 'wma': 'wma'}
CHANNELS = {1: '1.0', 2: '2.0', 3: '2.1', 6: '5.1', 7: '6.1', 8: '7.1'}


def vcodec(fmt):
    f = (fmt or '').lower().strip()
    return VCODEC.get(f, f.upper()[:6] if f else '')


def acodec(fmt):
    f = (fmt or '').lower().strip()
    return ACODEC.get(f, f.split(' ')[0] if f else '?')


def finfo_many(ws, files):
    """file_info pro více souborů paralelně, s trvalou cache (údaje o souboru se nemění)."""
    cache = load_json('finfo.json', {})
    missing = [f['ident'] for f in files if f['ident'] not in cache]
    if missing:
        def one(ident):
            try:
                return ident, ws.file_info(ident)
            except Exception as e:
                log('file_info %s: %s' % (ident, e))
                return ident, None
        with ThreadPoolExecutor(max_workers=8) as ex:
            for ident, fi in ex.map(one, missing):
                if fi:
                    fi.pop('stripe', None)
                    cache[ident] = fi
        if len(cache) > 3000:
            for k in list(cache)[:len(cache) - 3000]:
                del cache[k]
        save_json('finfo.json', cache)
    return {f['ident']: cache.get(f['ident']) for f in files}


def describe(f, fi):
    """Spojí název souboru a technické údaje do popisu streamu."""
    fi = fi or {}
    name = f['name']
    q = quality_from_info(fi) or quality_of(name)
    v = (fi.get('video') or [{}])[0]
    vc = vcodec(v.get('format') or fi.get('format'))
    auds = [{'codec': acodec(a['format']), 'ch': CHANNELS.get(a['channels'], '%dch' % a['channels'] if a['channels'] else ''),
             'channels': a['channels'], 'lang': lcode(a['lang'])} for a in fi.get('audio') or []]
    alangs = uniq(a['lang'] for a in auds)
    fl = lang_of(name)
    if 'CZ' in alangs:
        group = 'CZ dabing'
    elif fl in ('CZ dabing', 'CZ titulky', 'SK'):
        group = fl
    elif 'SK' in alangs:
        group = 'SK'
    elif 'EN' in alangs:
        group = 'EN'
    else:
        group = fl
    tit = bool(tokens(name) & TIT_T) or group == 'CZ titulky'
    shown = alangs or [LANG_SHORT[fl]]
    if 'CZ' not in shown and group == 'CZ dabing':
        shown = ['CZ'] + shown
    return {
        'f': f, 'fi': fi, 'q': q, 'vc': vc, 'hdr': 'HDR' if 'HDR' in tags_of(name) else 'SDR',
        'audio': auds, 'group': group, 'langs': shown, 'tit': tit,
        'length': fi.get('length') or 0, 'bitrate': fi.get('bitrate') or 0,
        'width': fi.get('width') or v.get('width') or 0, 'height': fi.get('height') or v.get('height') or 0,
    }


def sort_described(entries):
    return sorted(entries, key=lambda d: (LANG_RANK.get(d['group'], 9), QUALITY_RANK[d['q']], -d['f']['size']))


def stream_labels(d):
    """Dva řádky popisu streamu jako ve Stream Cinema."""
    qc = QUALITY_COLORS[d['q']]
    lang = LANG_SHORT[d['group']]
    aud = ', '.join('[%s]' % ' '.join(x for x in (a['codec'], a['ch'], a['lang']) if x) for a in d['audio'][:3])
    line1 = '[COLOR %s][B]%s[/B][/COLOR] - [COLOR %s][B]%s[/B][/COLOR]  [B]%s[/B]  %s%s' % (
        LANG_COLORS[d['group']], lang, qc, d['q'] if d['q'] != 'Neznámá' else '???', human_size(d['f']['size']),
        ' '.join(x for x in (d['vc'], d['hdr'] if d['vc'] else '') if x),
        (', ' + aud) if aud else '')
    parts = []
    if d['bitrate']:
        parts.append('bitrate: [B]%.2f Mbps[/B]' % (d['bitrate'] / 1e6))
    parts.append('audio: [B]%s%s[/B]' % (', '.join(d['langs']), ', +tit' if d['tit'] else ''))
    if d['length']:
        parts.append('délka: [B]%s[/B]' % hms(d['length']))
    parts.append('[COLOR gray]%s[/COLOR]' % d['f']['name'])
    return line1, '   '.join(parts)


def add_stream_details(li, d):
    """Vlajky dole na obrazovce (délka, kodek, rozlišení, audio) – Kodi 20+, ve 19 přes addStreamInfo."""
    if not d or not d['fi']:
        return
    w, h = d['width'], d['height']
    aspect = round(w / float(h), 2) if w and h else 0
    vc = KODI_VCODEC.get(d['vc'], (d['vc'] or '').lower())
    try:
        tag = li.getVideoInfoTag()
        tag.addVideoStream(xbmc.VideoStreamDetail(width=w, height=h, aspect=aspect, duration=d['length'],
                                                  codec=vc, hdrtype='hdr10' if d['hdr'] == 'HDR' else ''))
        for a in d['audio'][:2]:
            tag.addAudioStream(xbmc.AudioStreamDetail(channels=a['channels'], codec=a['codec'],
                                                      language=a['lang'].lower()))
        if d['length']:
            tag.setDuration(d['length'])
    except (AttributeError, TypeError):
        li.addStreamInfo('video', {'codec': vc, 'width': w, 'height': h, 'aspect': aspect, 'duration': d['length']})
        for a in d['audio'][:2]:
            li.addStreamInfo('audio', {'codec': a['codec'], 'channels': a['channels'], 'language': a['lang'].lower()})


# ---------- přihlášení k Webshare ----------
_WS = None


def api():
    global _WS
    if _WS:
        return _WS
    addon = xbmcaddon.Addon()
    uuid_ = addon.getSetting('device_uuid')
    if not uuid_:
        import uuid
        uuid_ = str(uuid.uuid4())
        addon.setSetting('device_uuid', uuid_)
    ws = Webshare(addon.getSetting('token'), uuid_)
    if not ws.is_logged_in():
        user, pw = addon.getSetting('username'), addon.getSetting('password')
        if not user or not pw:
            xbmcgui.Dialog().ok('Webshare', 'Nejdřív zadej v nastavení svoje přihlašovací údaje k Webshare.cz.\n'
                                            'Jak založit účet a koupit VIP: hlavní menu → „Návod: jak začít“.')
            addon.openSettings()
            addon = xbmcaddon.Addon()
            user, pw = addon.getSetting('username'), addon.getSetting('password')
            if not user or not pw:
                raise WebshareError('Chybí přihlašovací údaje')
        ws.login(user, pw)
        addon.setSetting('token', ws.token)
    _WS = ws
    return ws


def vip_status(force=False):
    """True / False / None (nezjištěno). Výsledek se pamatuje hodinu."""
    win = xbmcgui.Window(10000)
    ts = win.getProperty('wsh_vip_ts')
    if not force and ts and time.time() - float(ts) < 3600:
        v = win.getProperty('wsh_vip')
        return None if v == '' else v == '1'
    if not setting('username'):
        return None
    try:
        u = api().user_data()
    except Exception as e:
        log('VIP stav: %s' % e)
        return None
    vip = u['vip']
    win.setProperty('wsh_vip', '1' if vip else '0')
    win.setProperty('wsh_vip_text', vip_text(u) if vip else '')
    win.setProperty('wsh_vip_days', str(u.get('days_left', 0)))
    win.setProperty('wsh_vip_ts', str(time.time()))
    return vip


NO_VIP_TEXT = ('Webshare hlásí, že účet [B]%s[/B] nemá aktivní VIP.\n'
               'Bez VIP vydává jen odkazy na „free“ servery a ty místo videa vracejí webovou stránku, '
               'takže je Kodi nepřehraje.\n\nZkontroluj nebo prodluž VIP na webshare.cz (stejný účet jako v nastavení).')


def vip_gate():
    """Před výběrem streamu: (pokračovat?, uživatel už varování potvrdil?)."""
    if vip_status() is False:
        return (True, True) if warn_no_vip() else (False, False)
    return True, False


def warn_no_vip():
    """Vrátí True, když chce uživatel přesto pokračovat."""
    return xbmcgui.Dialog().yesno('Webshare – účet bez VIP', NO_VIP_TEXT % setting('username'),
                                  nolabel='Zrušit', yeslabel='Zkusit přesto')


# ---------- Vánoce ----------
MEDIA = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo('path')), 'resources', 'media')
XMAS_ICON = os.path.join(MEDIA, 'vanoce_icon.png')
XMAS_FANART = os.path.join(MEDIA, 'vanoce_fanart.jpg')
XMAS_KEYWORD_FALLBACK = '207317'        # TMDB klíčové slovo „christmas“ (ověří se za běhu)


def xmas_year():
    t = time.localtime()
    return t.tm_year - 1 if t.tm_mon < 11 else t.tm_year


def xmas_season():
    """Sekce Vánoce se ukazuje od 1. 11. do 15. 1. (nebo vždy, když je tak nastaveno)."""
    if setting('xmas_show') == '1':
        return True
    if setting('xmas_show') == '2':
        return False
    t = time.localtime()
    return t.tm_mon in (11, 12) or (t.tm_mon == 1 and t.tm_mday <= 15)


def xmas_art():
    return {'icon': XMAS_ICON, 'thumb': XMAS_ICON, 'poster': XMAS_ICON, 'fanart': XMAS_FANART}


def xmas_keyword():
    """ID klíčových slov TMDB pro Vánoce (christmas, christmas eve, santa claus…), cache na 30 dní."""
    c = load_json('xmas_keyword.json', {})
    if c.get('ids') and time.time() - c.get('ts', 0) < 30 * 86400:
        return c['ids']
    ids = []
    try:
        for q in ('christmas', 'christmas eve', 'santa claus'):
            d = tm().get('search/keyword', query=q)
            ids += [str(r['id']) for r in d.get('results', []) if (r.get('name') or '').lower() == q]
    except T.TmdbError as e:
        log('Klíčová slova Vánoce: %s' % e)
    ids = '|'.join(uniq(ids)) or XMAS_KEYWORD_FALLBACK
    if ids != XMAS_KEYWORD_FALLBACK:
        save_json('xmas_keyword.json', {'ts': time.time(), 'ids': ids})
    return ids


# ---------- aktualizace doplňku ----------
REPO_ADDONS_XML = 'https://raw.githubusercontent.com/sperlichp2-png/Kodi-repo/main/zips/addons.xml'
UPDATE_TTL = 3 * 3600
NEWS = {
    '3.5.0': 'Nová sekce [B]Vánoce %d[/B] v hlavním menu: vánoční filmy, novinky, pohádky, komedie, '
             'romantické, české a slovenské vánoční filmy, české pohádky a vánoční seriály.\n'
             'Doplněk teď po otevření sám upozorní, když je k dispozici nová verze.',
}


def vtuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', v or '0'))


def latest_version(force=False):
    """Nejnovější verze doplňku v repozitáři na GitHubu (kontrola nejvýš jednou za 3 hodiny)."""
    c = load_json('update_check.json', {})
    if not force and c.get('latest') is not None and time.time() - c.get('ts', 0) < UPDATE_TTL:
        return c['latest']
    latest = ''
    try:
        req = urllib.request.Request(REPO_ADDONS_XML + '?t=%d' % int(time.time() // 600),
                                     headers={'User-Agent': 'Kodi PvS Cinema'})
        with urllib.request.urlopen(req, timeout=4) as r:
            xml = r.read().decode('utf-8', 'replace')
        m = re.search(r'<addon[^>]*\bid="%s"[^>]*\bversion="([^"]+)"' % re.escape(ADDON.getAddonInfo('id')), xml)
        latest = m.group(1) if m else ''
    except Exception as e:
        log('Kontrola aktualizace: %s' % e)
        if c.get('latest') is not None:
            return c['latest']
    save_json('update_check.json', {'ts': time.time(), 'latest': latest})
    return latest


def update_available():
    latest = latest_version()
    return latest if latest and vtuple(latest) > vtuple(ADDON.getAddonInfo('version')) else ''


def do_update():
    """Obnoví repozitáře a otevře v Kodi seznam dostupných aktualizací."""
    xbmc.executebuiltin('UpdateAddonRepos')
    notify('Hledám aktualizace v PvS Repository…', error=False)
    xbmc.Monitor().waitForAbort(4)
    xbmc.executebuiltin('ActivateWindow(AddonBrowser,addons://outdated/,return)')


def offer_update(latest):
    """Jednou za spuštění Kodi se zeptá, jestli aktualizovat."""
    win = xbmcgui.Window(10000)
    if win.getProperty('pvs_update_asked') == latest:
        return
    win.setProperty('pvs_update_asked', latest)
    if xbmcgui.Dialog().yesno('PvS Cinema – nová verze',
                              'Je dostupná nová verze [B]%s[/B] (teď máš %s).\n'
                              'Aktualizovat teď? V seznamu aktualizací vyber PvS Cinema → Aktualizovat.'
                              % (latest, ADDON.getAddonInfo('version')),
                              nolabel='Později', yeslabel='Aktualizovat'):
        do_update()


def show_news():
    """Po aktualizaci jednou ukáže, co je nového."""
    ver = ADDON.getAddonInfo('version')
    seen = load_json('news_seen.json', {}).get('version')
    if seen == ver:
        return
    save_json('news_seen.json', {'version': ver})
    if not seen:       # první instalace – nic neukazuj
        return
    text = NEWS.get(ver)
    if text:
        if '%d' in text:
            text = text % xmas_year()
        xbmcgui.Dialog().ok('PvS Cinema %s – co je nového' % ver, text)


# ---------- TMDB ----------
_TM = None


def tm():
    global _TM
    if _TM is None:
        _TM = T.Tmdb(setting('tmdb_key'), _path('tmdb_cache.json'))
    return _TM


def need_key():
    """Když chybí klíč TMDB, vysvětlí, jak ho získat, a otevře nastavení. Vrátí True, když klíč je."""
    if setting('tmdb_key').strip():
        return True
    xbmcgui.Dialog().ok('Chybí klíč TMDB',
                        'Katalog filmů a seriálů (názvy, popisy, plakáty, hodnocení) jde z themoviedb.org.\n'
                        '1) Na počítači se zaregistruj na [B]themoviedb.org[/B] (zdarma).\n'
                        '2) Nastavení účtu → API → vytvoř klíč (typ Developer).\n'
                        '3) Zkopíruj „API Key“ nebo „API Read Access Token“ do nastavení doplňku.\n'
                        'Podrobně: hlavní menu → „Návod: jak začít“.')
    xbmcaddon.Addon().openSettings()
    global _TM
    _TM = None
    return bool(setting('tmdb_key').strip())


def item_info(it, mediatype=None):
    return {
        'title': it['title'], 'originaltitle': it.get('original'), 'year': it.get('year'), 'plot': it.get('plot'),
        'genre': it.get('genres'), 'rating': it.get('rating'), 'votes': it.get('votes'), 'imdb': it.get('imdb'),
        'mediatype': mediatype or ('tvshow' if it['type'] == 'tv' else 'movie'),
    }


def item_art(it):
    p = it.get('poster') or ''
    art = {'poster': p, 'thumb': p, 'icon': p or 'DefaultVideo.png'}
    if it.get('fanart'):
        art['fanart'] = it['fanart']
    return art


# ---------- párování souborů z Webshare s titulem ----------
ROMAN = {'ii', 'iii', 'iv', 'v', 'vi'}


def parse_episode(name):
    for rx in EP_PATTERNS:
        m = rx.search(name)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None, None


def title_match(fname, titles, year=None, series=False):
    """Patří soubor k titulu? Název musí být v souboru celý; u filmů sedí rok a nejde o díl seriálu ani pokračování."""
    base = os.path.splitext(fname)[0]
    if series:   # skupina na začátku („[Group] Název S01E01“) nevadí
        base = re.sub(r'^\s*[\[(][^\])]*[\])]\s*', '', base)
    f = ' ' + norm_text(base) + ' '
    ok = False
    for t in titles:
        t = norm_text(t)
        if len(t) < 2:
            continue
        idx = f.find(' ' + t + ' ')
        if idx < 0 or (series and idx > 0):   # díl seriálu začíná názvem („Smiling Friends“ ≠ „Friends“)
            continue
        after = f[idx + len(t) + 2:].split(' ', 1)[0]
        if not series and not re.search(r'\d$', t) and (after in ROMAN or re.fullmatch(r'[2-9]', after)):
            continue    # „Matrix 2“ není „Matrix“
        ok = True
        break
    if not ok:
        return False
    if series:
        return True
    if parse_episode(fname)[0] is not None:
        return False
    if year:
        ys = [int(y) for y in re.findall(r'(?<!\d)(19\d\d|20\d\d)(?!\d)', fname)]
        if ys and not any(abs(y - int(year)) <= 1 for y in ys):
            return False
    return True


def fetch(ws, query, limit, offset, sort=None, category='video'):
    if sort is None:
        sort = SORTS[setting_int('sort', 0)]
    total, files = ws.search(query, sort, limit, offset, category=category)
    min_b = setting_int('min_size_mb', 0) * 1024 * 1024
    files = [f for f in files
             if f['name'].lower().endswith(VIDEO_EXT) and f['size'] >= min_b and not f['password']]
    return total, files


def item_titles(it):
    return uniq([it['title'], it.get('original') if it.get('orig_lang') in ('en', 'cs', 'sk', 'fr', 'de', 'es', 'it')
                 or not it.get('orig_lang') else None] + (it.get('alt') or []))


_AVAIL = None


def avail_cache():
    global _AVAIL
    if _AVAIL is None:
        now = time.time()
        _AVAIL = {k: v for k, v in load_json('avail.json', {}).items() if now - v['ts'] < AVAIL_TTL}
    return _AVAIL


def save_avail():
    if _AVAIL is not None:
        c = _AVAIL
        if len(c) > 600:
            for k in sorted(c, key=lambda k: c[k]['ts'])[:len(c) - 600]:
                del c[k]
        save_json('avail.json', c)


def ws_files_for(ws, it, deep=False):
    """Soubory na Webshare, které patří k filmu (u seriálu jen orientačně pro seznam). Cache 6 h."""
    key = '%s:%s' % (it['type'], it['id'])
    cache = avail_cache()
    hit = cache.get(key)
    if hit and (hit['deep'] or not deep):
        return hit['files']
    titles = item_titles(it)
    queries = uniq(titles[:2] + [strip_diacritics(it['title'])])[:3 if deep else 2]
    if deep and it['type'] == 'movie' and it.get('year'):
        # „Matrix 1999“ najde mnohem víc souborů správného filmu (i HD) než samotné „Matrix“,
        # kde většinu výsledků zaberou pokračování
        queries += ['%s %s' % (t, it['year']) for t in titles[:2]]
    seen, files = set(), []

    def one(q):
        try:
            return fetch(ws, q, 100 if deep else 40, 0, sort='')[1]
        except WebshareError as e:
            log('Webshare hledání %s: %s' % (q, e))
            return []
    with ThreadPoolExecutor(max_workers=5) as ex:
        chunks = list(ex.map(one, queries))
    for chunk in chunks:
        for f in chunk:
            if f['ident'] not in seen and title_match(f['name'], titles, it.get('year'), it['type'] == 'tv'):
                seen.add(f['ident'])
                files.append(f)
    cache[key] = {'ts': time.time(), 'deep': deep, 'files': files}
    return files


# ---------- seznamy titulů ----------
def render_items(items, hide_unavailable=False, dub_only=False):
    """Vykreslí filmy/seriály jako ve Stream Cinema: „Název - jazyky (rok)“, hodnocení vpravo, plakát, popis.
    Vrátí počet zobrazených položek."""
    if not items:
        return 0
    titles_view()
    ws = api()
    bg = xbmcgui.DialogProgressBG()
    bg.create('Webshare', 'Zjišťuji dostupnost na Webshare…')
    try:
        with ThreadPoolExecutor(max_workers=8) as ex:
            files_by = dict(zip([i['id'] + i['type'] for i in items], ex.map(lambda i: ws_files_for(ws, i), items)))
        bg.update(60, 'Webshare', 'Načítám údaje o streamech…')
        best, infos = {}, {}
        if setting_bool('stream_flags', True):
            for it in items:
                fs = files_by[it['id'] + it['type']]
                if it['type'] == 'movie' and fs:
                    best[it['id']] = sort_described([describe(f, None) for f in fs])[0]['f']
            infos = finfo_many(ws, list(best.values()))
    finally:
        bg.close()
        save_avail()

    all_tv = all(i['type'] == 'tv' for i in items)
    all_movie = all(i['type'] == 'movie' for i in items)
    xbmcplugin.setContent(HANDLE, 'tvshows' if all_tv else 'movies')
    shown = 0
    for it in items:
        files = files_by[it['id'] + it['type']]
        if dub_only:
            files = [f for f in files if lang_of(f['name']) == 'CZ dabing']
        if not files and (hide_unavailable or dub_only):
            continue
        tv = it['type'] == 'tv'
        prefix = '' if (all_tv or all_movie) else ('[COLOR FF90CAF9]Seriál:[/COLOR] ' if tv else '')
        year = ' (%s)' % it['year'] if it.get('year') else ''
        if files:
            label = '%s%s - [B]%s[/B]%s' % (prefix, it['title'], langs_summary(files), year)
        else:
            label = '[COLOR gray]%s%s%s  · není na Webshare[/COLOR]' % (prefix, it['title'], year)
        li = xbmcgui.ListItem(label)
        li.setLabel2('%.1f' % it['rating'] if it.get('rating') else '')
        li.setArt(item_art(it))
        info = item_info(it)
        info['title'] = label          # jako v SC: i v panelu s popisem „Název - jazyky (rok)“
        set_info(li, info)
        ctx = [('Hledat ručně na Webshare', 'Container.Update(%s)' % build_url(action='ws_search', q=it['title']))]
        if not tv:
            f = best.get(it['id'])
            if f:
                add_stream_details(li, describe(f, infos.get(f['ident'])))
            li.setProperty('IsPlayable', 'true')
            ctx.insert(0, ('Přehrát – vybrat titulky', 'PlayMedia(%s)' % build_url(
                action='pick', id=it['id'], subs='ask')))
        li.addContextMenuItems(ctx)
        if tv:
            xbmcplugin.addDirectoryItem(HANDLE, build_url(action='tv', id=it['id']), li, True)
        else:
            xbmcplugin.addDirectoryItem(HANDLE, build_url(action='pick', id=it['id']), li, False)
        shown += 1
    sort_methods()
    return shown


def sort_methods():
    """Popisek beze změny (%L), vpravo hodnocení (%R) – jako ve Stream Cinema."""
    for m in (xbmcplugin.SORT_METHOD_UNSORTED, xbmcplugin.SORT_METHOD_VIDEO_RATING, xbmcplugin.SORT_METHOD_VIDEO_YEAR,
              xbmcplugin.SORT_METHOD_LABEL):
        try:
            xbmcplugin.addSortMethod(HANDLE, m, labelMask='%L', label2Mask='%R')
        except TypeError:   # starší Kodi bez masek
            xbmcplugin.addSortMethod(HANDLE, m)


def today():
    return time.strftime('%Y-%m-%d')


def list_spec(kind, name, genre='', year='', rmin='', rmax=''):
    """Cesta a parametry TMDB pro jednotlivé seznamy z menu."""
    m = kind == 'movie'
    date_sort = 'primary_release_date.desc' if m else 'first_air_date.desc'
    if name == 'popular':
        return '%s/popular' % kind, {}
    if name == 'trending':
        return 'trending/%s/week' % kind, {}
    if name == 'trending_day':
        return 'trending/%s/day' % kind, {}
    if name == 'top':
        return '%s/top_rated' % kind, {}
    if name == 'news':
        p = {'sort_by': date_sort, 'vote_count.gte': 5, 'with_genres': genre}
        if m:
            p.update({'release_date.lte': today(), 'with_release_type': '4|5|6'})
        else:
            p['first_air_date.lte'] = today()
        return 'discover/' + kind, p
    if name == 'genre':
        return 'discover/' + kind, {'sort_by': 'popularity.desc', 'with_genres': genre, 'vote_count.gte': 20}
    if name == 'year':
        key = 'primary_release_year' if m else 'first_air_date_year'
        return 'discover/' + kind, {'sort_by': 'popularity.desc', key: year}
    if name == 'rating':
        return 'discover/' + kind, {'sort_by': 'vote_average.desc', 'vote_average.gte': rmin,
                                    'vote_average.lte': rmax, 'vote_count.gte': 200 if m else 100}
    if name == 'kids':
        return 'discover/' + kind, {'sort_by': 'popularity.desc', 'with_genres': '16|10751' if m else '16|10762'}
    if name == 'tips':
        return 'discover/' + kind, {'sort_by': 'popularity.desc', 'vote_average.gte': 7,
                                    'vote_count.gte': 500 if m else 200}
    if name.startswith('xmas'):
        p = {'sort_by': 'popularity.desc', 'with_keywords': xmas_keyword()}
        extra = {
            'xmas': {'vote_count.gte': 10},
            'xmas_new': {('primary_release_date.gte' if m else 'first_air_date.gte'): '%d-01-01' % (xmas_year() - 1),
                         ('primary_release_date.lte' if m else 'first_air_date.lte'): today()},
            'xmas_kids': {'with_genres': '16|10751' if m else '16|10751|10762'},
            'xmas_comedy': {'with_genres': '35', 'vote_count.gte': 10},
            'xmas_romance': {'with_genres': '10749', 'vote_count.gte': 10},
            'xmas_top': {'sort_by': 'vote_average.desc', 'vote_count.gte': 300 if m else 50},
            'xmas_cz': {'with_origin_country': 'CZ|SK'},
        }[name]
        p.update(extra)
        return 'discover/' + kind, p
    if name == 'cz_fairy':
        return 'discover/' + kind, {'sort_by': 'popularity.desc', 'with_origin_country': 'CZ|SK',
                                    'with_genres': '14,10751' if m else '10751', 'vote_count.gte': 3}
    raise ValueError(name)


LIST_TITLES = {'popular': 'Populární', 'trending': 'Trendy', 'trending_day': 'Právě sledované',
               'top': 'Nejlépe hodnocené', 'news': 'Novinky', 'genre': 'Žánr', 'year': 'Rok', 'rating': 'Hodnocení',
               'kids': 'Kids', 'tips': 'Tipy na dnes', 'xmas': 'Vánoční filmy', 'xmas_new': 'Vánoční novinky',
               'xmas_kids': 'Vánoční pro děti', 'xmas_comedy': 'Vánoční komedie', 'xmas_romance': 'Vánoční romantika',
               'xmas_top': 'Nejlépe hodnocené vánoční', 'xmas_cz': 'České a slovenské vánoční',
               'cz_fairy': 'České a slovenské pohádky'}


def catalog(kind, name, page=1, dub=False, **spec):
    if not need_key():
        end_dir(HANDLE, succeeded=False)
        return
    page = int(page or 1)
    path, params = list_spec(kind, name, **spec)
    if name == 'tips' and page == 1:
        page = time.localtime().tm_yday % 15 + 1        # každý den jiný výběr
    hide = setting_bool('hide_unavailable', True)
    xbmcplugin.setPluginCategory(HANDLE, ('Novinky dabované' if dub else LIST_TITLES.get(name, '')))
    shown, pages_read, total = 0, 0, 10 ** 6     # skutečný počet stránek vrátí až TMDB
    collected = []
    try:
        # při skrývání nedostupných načti víc stránek, aby seznam nebyl poloprázdný
        while pages_read < (4 if (hide or dub) else 1) and page <= total:
            items, total = tm().page(path, page, kind=kind, **params)
            page += 1
            pages_read += 1
            collected += items
            if not (hide or dub) or len(collected) >= 40:
                break
        if name.startswith('xmas') or name == 'cz_fairy':
            for it in collected:
                it['fanart'] = XMAS_FANART
        shown = render_items(collected, hide_unavailable=hide, dub_only=dub)
    except T.TmdbError as e:
        notify(str(e))
    finally:
        tm().save()
    if collected and page <= total and page <= 500:
        more_art = xmas_art() if (name.startswith('xmas') or name == 'cz_fairy') else None
        add_dir('[B]Další >>[/B]', 'DefaultFolder.png', art=more_art, action='list', kind=kind, name=name, page=page,
                dub='1' if dub else '', **spec)
    if not shown:
        notify('Na Webshare jsem z tohoto seznamu nic nenašel' if collected else 'Seznam je prázdný', error=False)
    end_dir(HANDLE, cacheToDisc=False)


# ---------- hledání ----------
ADULT_CODE = '#4711'


ADULT_PROP = 'pvs_adult_unlocked'


def adult_on():
    """Odemčeno jen dočasně – do návratu do hlavního menu nebo do restartu Kodi."""
    return xbmcgui.Window(10000).getProperty(ADULT_PROP) == '1'


def adult_unlock():
    """Kód #4711 v hledání odemkne vlastní prostředí pro dospělé a otevře ho.
    Adresa s kódem se do historie neuloží, takže ji tlačítko Zpět znovu nespustí."""
    xbmcgui.Window(10000).setProperty(ADULT_PROP, '1')
    end_dir(HANDLE, succeeded=False)
    xbmc.executebuiltin('Container.Update(%s)' % build_url(action='adult'))


def adult_locked_redirect():
    """Zamčená sekce (např. po tlačítku Zpět): nic neukázat a vrátit se do hlavního menu."""
    end_dir(HANDLE, succeeded=False)
    xbmc.executebuiltin('Container.Update(%s,replace)' % build_url())


def adult_lock(go_home=True):
    xbmcgui.Window(10000).clearProperty(ADULT_PROP)
    if go_home:
        xbmc.executebuiltin('Container.Update(%s,replace)' % build_url())


def adult_menu():
    if not adult_on():
        adult_locked_redirect()
        return
    xbmcplugin.setPluginCategory(HANDLE, 'Pro dospělé (18+)')
    add_dir('[B]Hledat 18+[/B]', 'DefaultAddonsSearch.png', action='adult_search')
    add_dir('Nejnovější podle hledání', 'DefaultRecentlyAddedMovies.png', action='adult_search', sort='recent')
    add_dir('Nejlépe hodnocené podle hledání', 'DefaultMusicTop100.png', action='adult_search', sort='rating')
    for q in load_json('adult_history.json', [])[:15]:
        add_dir('[COLOR gray][18+][/COLOR] %s' % q, 'DefaultFolder.png', action='adult_search', q=q)
    if load_json('adult_history.json', []):
        add_dir('[I]Smazat historii 18+[/I]', 'DefaultIconError.png', action='adult_clear', folder=False)
    add_dir('[COLOR FFE53935]Zamknout sekci 18+[/COLOR]', 'DefaultIconError.png', action='adult_lock',
            folder=False)
    end_dir(HANDLE, cacheToDisc=False)


def adult_search(query=None, page=0, sort=''):
    """Hledání v prostředí 18+: přímo na Webshare, vlastní historie (nemíchá se s běžnou)."""
    if not adult_on():
        adult_locked_redirect()
        return
    if not query:
        query = ask('Hledat 18+')
        if not query:
            end_dir(HANDLE, succeeded=False)
            return
    page = int(page or 0)
    limit = setting_int('limit', 50)
    if not page:
        hist = [q for q in load_json('adult_history.json', []) if q != query]
        save_json('adult_history.json', ([query] + hist)[:30])
    total, files = fetch(api(), query, limit, page * limit, sort=sort or None, category='adult')
    xbmcplugin.setPluginCategory(HANDLE, '18+: %s' % query)
    xbmcplugin.setContent(HANDLE, 'videos')
    for d in sort_described([describe(f, None) for f in files]):
        add_file(d['f'])
    if (page + 1) * limit < total:
        add_dir('[B]Další stránka >>[/B]', action='adult_search', q=query, page=page + 1, sort=sort)
    if not files:
        notify('Nic nenalezeno', error=False)
    end_dir(HANDLE, cacheToDisc=False)


def search(query=None):
    if not query:
        query = ask('Hledat film nebo seriál')
        if not query:
            end_dir(HANDLE, succeeded=False)
            return
    if query.strip() == ADULT_CODE:
        adult_unlock()
        return
    add_history('search', query)
    xbmcplugin.setPluginCategory(HANDLE, 'Hledat: %s' % query)
    if need_key():
        try:
            items, _ = tm().search(query)
            render_items(items)
        except T.TmdbError as e:
            notify(str(e))
        finally:
            tm().save()
    add_dir('[I]Hledat „%s“ přímo na Webshare[/I]' % query, 'DefaultAddonsSearch.png', action='ws_search', q=query)
    end_dir(HANDLE, cacheToDisc=False)


def add_history(kind, query):
    hist = [h for h in load_json('history.json', []) if not (h['type'] == kind and h['q'] == query)]
    hist.insert(0, {'type': kind, 'q': query})
    save_json('history.json', hist[:30])


# ---------- výběr streamu a přehrání ----------
def choose_stream(files, heading, poster):
    """Dialog jako ve Stream Cinema: každý stream na dva řádky (kvalita, velikost, kodek, audio / bitrate, jazyky)."""
    ws = api()
    files = sorted(files, key=lambda f: (LANG_RANK[lang_of(f['name'])], QUALITY_RANK[quality_of(f['name'])],
                                         -f['size']))[:60]
    dlg = xbmcgui.DialogProgressBG()
    dlg.create('Webshare', 'Načítám údaje o streamech…')
    try:
        infos = finfo_many(ws, files)
    finally:
        dlg.close()
    entries = sort_described([describe(f, infos.get(f['ident'])) for f in files])
    lis = []
    for d in entries:
        l1, l2 = stream_labels(d)
        li = xbmcgui.ListItem(l1, l2)
        li.setArt({'thumb': poster or 'DefaultVideo.png', 'icon': poster or 'DefaultVideo.png'})
        lis.append(li)
    idx = xbmcgui.Dialog().select(heading, lis, useDetails=True)
    return entries[idx] if idx >= 0 else None


def pick_movie(tid, subs_mode=None):
    if not need_key():
        return resolve_fail()
    go, warned = vip_gate()
    if not go:
        return resolve_fail()
    try:
        it = tm().details('movie', tid)
    except T.TmdbError as e:
        notify(str(e))
        return resolve_fail()
    finally:
        tm().save()
    ws = api()
    files = ws_files_for(ws, it, deep=True)
    save_avail()
    if not files:
        xbmcgui.Dialog().ok('Webshare', 'Film „%s“ (%s) jsem na Webshare nenašel.\n\nZkus v kontextovém menu '
                                        '„Hledat ručně na Webshare“.' % (it['title'], it.get('year')))
        return resolve_fail()
    d = choose_stream(files, '%s - %s (%s)' % (it['title'], langs_summary(files), it.get('year')), it.get('poster'))
    if not d:
        return resolve_fail()
    info = item_info(it)
    play_file(d['f'], info, item_art(it), subs_mode=subs_mode, title=it['title'], vip_checked=warned, details=d,
              watched={'key': 'movie:' + tid, 'type': 'movie', 'id': tid, 'title': it['title'],
                       'poster': it.get('poster')})


def resolve_fail():
    xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem())


PLAYER_UA = 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'
LINK_TYPES = ['video_stream', 'file_download', None]


def stream_link(ws, ident):
    """Zkusí získat odkaz více způsoby; vrátí první funkční."""
    last = None
    for t in LINK_TYPES:
        try:
            return ws.file_link(ident, t)
        except WebshareError as e:
            last = e
            log('file_link (%s): %s' % (t, e))
            if 'spojení' in str(e):
                break
    raise last


def play_file(f, info, art=None, subs_mode=None, title='', season=None, episode=None, show=None,
              vip_checked=False, details=None, watched=None):
    ws = api()
    try:
        link = stream_link(ws, f['ident'])
    except WebshareError as e:
        log('file_link selhal: %s | %s' % (e, e.raw))
        xbmcgui.Dialog().ok('Webshare – stream nejde spustit',
                            'Webshare odmítl vydat odkaz:\n[B]%s[/B]\n\nSpusť Diagnostiku v hlavním menu doplňku.' % e)
        return resolve_fail()
    if '://free.' in link and not vip_checked:
        xbmcgui.Window(10000).setProperty('wsh_vip', '0')
        xbmcgui.Window(10000).setProperty('wsh_vip_ts', str(time.time()))
        if not warn_no_vip():
            return resolve_fail()
    log('Přehrávám: %s' % link)
    li = xbmcgui.ListItem(path=link + '|User-Agent=' + urllib.parse.quote(PLAYER_UA))
    set_info(li, info)
    if art:
        li.setArt(art)
    add_stream_details(li, details)

    if subs_mode is None:
        subs_mode = ['off', 'auto', 'ask'][setting_int('subs_mode', 1)]
    sub_path = pick_subtitles(ws, f['name'], title, season, episode, subs_mode)
    if sub_path:
        li.setSubtitles([sub_path])

    xbmcplugin.setResolvedUrl(HANDLE, True, li)

    if watched:
        try:
            add_watched(watched)
        except Exception as e:
            log('Uložení historie sledování: %s' % e)

    # Má soubor víc zvukových stop a česká není první? Po spuštění na ni přepni.
    cz_track = None
    if details and len(details['audio']) > 1:
        langs = [a['lang'] for a in details['audio']]
        if 'CZ' in langs and langs[0] != 'CZ':
            cz_track = langs.index('CZ')

    # Po spuštění videa zapni stažené titulky. (Okno „Hledat titulky“ se už samo neotevírá –
    # titulky jsou v menu přehrávače dole.)
    if sub_path or cz_track is not None:
        player, monitor = xbmc.Player(), xbmc.Monitor()
        for _ in range(60):
            if player.isPlayingVideo() or monitor.waitForAbort(0.5):
                break
        if player.isPlayingVideo() and cz_track is not None:
            try:
                monitor.waitForAbort(1)
                player.setAudioStream(cz_track)
                log('Přepnuto na českou zvukovou stopu (%d)' % cz_track)
            except Exception as e:
                log('Přepnutí zvukové stopy: %s' % e)
        if player.isPlayingVideo() and sub_path:
            player.showSubtitles(True)
            notify('Titulky načteny z Webshare', error=False)


def play_raw(ident, name, subs_mode=None):
    """Přehrání souboru vybraného přímo ze seznamu Webshare (bez katalogu)."""
    go, warned = vip_gate()
    if not go:
        return resolve_fail()
    s, e = parse_episode(name)
    play_file({'ident': ident, 'name': name}, {'title': name, 'mediatype': 'video'}, subs_mode=subs_mode,
              season=s, episode=e, vip_checked=warned, watched={'key': 'ws:' + name, 'title': name})


# ---------- seriály ----------
def ws_collect(ws, jobs, titles, progress=None):
    """Paralelně projde dotazy (dotaz, stránka) na Webshare a vrátí soubory, které patří k seriálu."""
    def one(job):
        q, p = job
        try:
            return fetch(ws, q, 100, p * 100, sort='')[1]
        except WebshareError as e:
            log('Webshare hledání %s: %s' % (q, e))
            return []
    seen, files = set(), []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for n, chunk in enumerate(ex.map(one, jobs)):
            if progress:
                progress.update(int((n + 1) * 100 / len(jobs)))
            for f in chunk:
                if f['ident'] not in seen and (not titles or title_match(f['name'], titles, series=True)):
                    seen.add(f['ident'])
                    files.append(f)
    return files


def load_series(key, queries, titles, refresh=False, season_nums=()):
    """Všechny díly seriálu z Webshare rozdělené podle série a dílu (cache 6 h).
    Kromě obecného hledání názvu hledá cíleně každou sérii („Přátelé S01“, „Friends S01“) – u dlouhých
    seriálů by jinak obecné hledání (max. stovky výsledků) většinu dílů a HD verzí minulo."""
    cache = load_json('series_cache.json', {})
    hit = cache.get(key)
    if hit and not refresh and time.time() - hit.get('ts', 0) < AVAIL_TTL:
        return hit
    ws = api()
    pages = max(1, setting_int('series_pages', 3))
    jobs = [(queries[0], p) for p in range(pages)] + [(q, 0) for q in queries[1:]]
    for n in season_nums:
        for t in queries[:2]:
            jobs += [('%s S%02d' % (t, n), 0), ('%s S%02d' % (t, n), 1)]
    bg = xbmcgui.DialogProgressBG()
    bg.create('Webshare', 'Načítám díly seriálu „%s“…' % queries[0])
    try:
        files = ws_collect(ws, jobs, titles, bg)
    finally:
        bg.close()

    seasons, other = {}, []
    for f in files:
        s, e = parse_episode(f['name'])
        if s is None:
            other.append(f)
        else:
            f['s'], f['e'] = s, e
            seasons.setdefault(str(s), []).append(f)
    data = {'ts': time.time(), 'seasons': seasons, 'other': other}
    cache[key] = data
    if len(cache) > 10:  # jen posledních 10 seriálů
        for k in sorted(cache, key=lambda k: cache[k].get('ts', 0))[:len(cache) - 10]:
            del cache[k]
    save_json('series_cache.json', cache)
    return data


def tv_context(tid):
    it = tm().details('tv', tid)
    tm().save()
    titles = item_titles(it)
    queries = uniq(titles[:2] + [strip_diacritics(it['title'])])[:3]
    return it, titles, queries


def series_data(it, titles, queries, refresh=False):
    return load_series('tv:' + it['id'], queries, titles, refresh,
                       [s['n'] for s in it.get('seasons', []) if s['n'] > 0][:30])


def tv_show(tid, refresh=False):
    if not need_key():
        end_dir(HANDLE, succeeded=False)
        return
    it, titles, queries = tv_context(tid)
    data = series_data(it, titles, queries, refresh)
    titles_view()
    xbmcplugin.setPluginCategory(HANDLE, it['title'])
    xbmcplugin.setContent(HANDLE, 'seasons')
    known = set()
    for s in it.get('seasons', []):
        n = s['n']
        files = data['seasons'].get(str(n), [])
        if n == 0 and not files:
            continue
        known.add(str(n))
        eps = len({f['e'] for f in files})
        label = '%s%s' % (s['name'], ' (%s)' % s['year'] if s['year'] else '')
        label = ('%s - [B]%s[/B]  [COLOR gray]%d/%d dílů[/COLOR]' % (label, langs_summary(files), eps, s['episodes'])
                 if files else '[COLOR gray]%s  · není na Webshare[/COLOR]' % label)
        art = item_art(it)
        if s['poster']:
            art.update({'poster': s['poster'], 'thumb': s['poster']})
        add_dir(label, art=art, info={'title': s['name'], 'tvshowtitle': it['title'], 'plot': s['plot'] or it['plot'],
                                      'season': n, 'mediatype': 'season', 'year': s['year']},
                action='season', id=tid, s=n)
    for s in sorted((s for s in data['seasons'] if s not in known), key=int):   # série, které TMDB nezná
        add_dir('%s. série - [B]%s[/B]' % (s, langs_summary(data['seasons'][s])), art=item_art(it),
                info={'title': '%s. série' % s, 'tvshowtitle': it['title'], 'season': int(s), 'mediatype': 'season'},
                action='season', id=tid, s=s)
    if data['other']:
        add_dir('Ostatní soubory  [COLOR gray](%d)[/COLOR]' % len(data['other']), art=item_art(it),
                action='season', id=tid, s='other')
    add_dir('[I]Obnovit výsledky z Webshare[/I]', 'DefaultAddonsUpdates.png', action='tv', id=tid, refresh='1')
    end_dir(HANDLE, cacheToDisc=False)


def tv_season(tid, season):
    it, titles, queries = tv_context(tid)
    data = series_data(it, titles, queries)
    if season == 'other':
        xbmcplugin.setPluginCategory(HANDLE, '%s – ostatní' % it['title'])
        xbmcplugin.setContent(HANDLE, 'videos')
        for f in sort_described([describe(f, None) for f in data['other']]):
            add_file(f['f'], poster=it.get('poster'))
        end_dir(HANDLE)
        return
    n = int(season)
    try:
        eps = tm().season(tid, n)
    except T.TmdbError as e:
        log('TMDB série: %s' % e)
        eps = []
    finally:
        tm().save()
    by_ep = {}
    for f in data['seasons'].get(str(n), []):
        by_ep.setdefault(f['e'], []).append(f)
    titles_view()
    xbmcplugin.setPluginCategory(HANDLE, '%s – %d. série' % (it['title'], n))
    xbmcplugin.setContent(HANDLE, 'episodes')
    listed = set()
    for ep in eps + [{'e': e, 'name': '', 'plot': '', 'still': '', 'aired': '', 'rating': 0}
                     for e in sorted(by_ep) if e not in {x['e'] for x in eps}]:
        e = ep['e']
        if e in listed:
            continue
        listed.add(e)
        files = by_ep.get(e, [])
        name = ep['name'] or 'Díl %d' % e
        code = 'S%02dE%02d' % (n, e)
        if files:
            label = '[B]%s[/B]  %s - [B]%s[/B]' % (code, name, langs_summary(files))
        else:   # při výběru se díl ještě dohledá cíleně, takže jen šedě, ne „není“
            label = '[COLOR gray][B]%s[/B]  %s  · hledat při výběru[/COLOR]' % (code, name)
        li = xbmcgui.ListItem(label)
        if ep.get('rating'):
            li.setLabel2('%.1f' % ep['rating'])
        art = item_art(it)
        if ep['still']:
            art['thumb'] = ep['still']
        li.setArt(art)
        head = '[B]%s – %d. série, %d. díl[/B]%s' % (
            it['title'], n, e, ' · vysíláno %s' % '.'.join(reversed(ep['aired'].split('-'))) if ep['aired'] else '')
        set_info(li, {'title': label, 'tvshowtitle': it['title'], 'season': n, 'episode': e,
                      'plot': head + ('\n\n' + ep['plot'] if ep['plot'] else ''),
                      'aired': ep['aired'], 'rating': ep.get('rating'), 'mediatype': 'episode'})
        li.setProperty('IsPlayable', 'true')
        li.addContextMenuItems([('Přehrát – vybrat titulky', 'PlayMedia(%s)' % build_url(
            action='pick_ep', id=tid, s=n, e=e, subs='ask'))])
        xbmcplugin.addDirectoryItem(HANDLE, build_url(action='pick_ep', id=tid, s=n, e=e), li, False)
    sort_methods()
    end_dir(HANDLE, cacheToDisc=False)


def pick_episode(tid, season, episode, subs_mode=None):
    go, warned = vip_gate()
    if not go:
        return resolve_fail()
    it, titles, queries = tv_context(tid)
    data = series_data(it, titles, queries)
    s, e = int(season), int(episode)
    code = 'S%02dE%02d' % (s, e)
    files = [f for f in data['seasons'].get(str(s), []) if f['e'] == e]
    # vždy ještě cíleně „Název S01E05“ – najde i HD verze, které obecné hledání seriálu nezachytí
    seen = {f['ident'] for f in files}
    for f in ws_collect(api(), [('%s %s' % (t, code), 0) for t in queries[:2]], titles):
        if f['ident'] not in seen and parse_episode(f['name']) == (s, e):
            seen.add(f['ident'])
            files.append(f)
    if not files:
        xbmcgui.Dialog().ok('Webshare', 'Díl %s %s jsem na Webshare nenašel.' % (it['title'], code))
        return resolve_fail()
    d = choose_stream(files, '%s %s - %s' % (it['title'], code, langs_summary(files)), it.get('poster'))
    if not d:
        return resolve_fail()
    info = {'title': '%s %s' % (it['title'], code), 'tvshowtitle': it['title'], 'season': s, 'episode': e,
            'year': it.get('year'), 'imdb': it.get('imdb'), 'mediatype': 'episode'}
    play_file(d['f'], info, item_art(it), subs_mode=subs_mode, title=it['title'], season=s, episode=e,
              show=it['title'], vip_checked=warned, details=d,
              watched={'key': 'tv:' + tid, 'type': 'tv', 'id': tid, 'title': it['title'],
                       'poster': it.get('poster'), 's': s, 'e': e})


# ---------- přímo na Webshare (bez katalogu) ----------
def add_file(f, poster=''):
    q = quality_of(f['name'])
    badge = '[COLOR %s][B]%s[/B][/COLOR]' % (QUALITY_COLORS[q], q if q != 'Neznámá' else '???')
    lang = lang_of(f['name'])
    li = xbmcgui.ListItem('[COLOR %s][B]%s[/B][/COLOR] - %s  [B]%s[/B]  [COLOR gray]%s[/COLOR]' % (
        LANG_COLORS[lang], LANG_SHORT[lang], badge, human_size(f['size']), f['name']))
    li.setProperty('IsPlayable', 'true')
    img = poster or f.get('img')
    if img:
        li.setArt({'thumb': f.get('img') or img, 'poster': img, 'icon': img})
    set_info(li, {'title': f['name'], 'plot': 'Soubor: %s\nVelikost: %s\nHlasy na Webshare: +%d / -%d' % (
        f['name'], human_size(f['size']), f.get('positive', 0), f.get('negative', 0)), 'mediatype': 'video'})
    url = build_url(action='play', ident=f['ident'], name=f['name'])
    li.addContextMenuItems([
        ('Přehrát – vybrat titulky', 'PlayMedia(%s)' % build_url(action='play', ident=f['ident'], name=f['name'],
                                                                 subs='ask')),
        ('Přehrát bez titulků', 'PlayMedia(%s)' % build_url(action='play', ident=f['ident'], name=f['name'],
                                                            subs='off')),
    ])
    xbmcplugin.addDirectoryItem(HANDLE, url, li, False)


def ws_search(query=None, page=0):
    if not query:
        query = ask('Hledat přímo na Webshare')
        if not query:
            end_dir(HANDLE, succeeded=False)
            return
    page = int(page or 0)
    limit = setting_int('limit', 50)
    if not page:
        add_history('ws', query)
    total, files = fetch(api(), query, limit, page * limit)
    xbmcplugin.setPluginCategory(HANDLE, 'Webshare: %s' % query)
    xbmcplugin.setContent(HANDLE, 'videos')
    for d in sort_described([describe(f, None) for f in files]):
        add_file(d['f'])
    if (page + 1) * limit < total:
        add_dir('[B]Další stránka >>[/B]', action='ws_search', q=query, page=page + 1)
    if not files:
        notify('Nic nenalezeno', error=False)
    end_dir(HANDLE, cacheToDisc=False)


NAME_STOP = CZ_T | DUB_T | TIT_T | SK_T | EN_T | SCENE_T | {
    '1080p', '1080i', '720p', '2160p', '480p', '576p', '4k', 'hd', 'fhd', 'uhd', 'sd', 'hdr', 'hdr10', 'dv',
    'novinka', 'film', 'cely', 'remux', 'hdtv', 'dvdrip', 'xvid', 'divx', 'avc', 'dd5', 'ac3', 'dts', 'eng'}


def guess_title(name):
    """Z názvu souboru odhadne název a rok: „Madden (2026) CZ dab.mkv“ → („Madden“, „2026“),
    „Ranč Duttonových S01E07 1080p“ → („Ranč Duttonových“, None)."""
    base = re.sub(r'^\s*[\[(][^\])]*[\])]\s*', '', os.path.splitext(name)[0])
    year = None
    m = None
    for rx in EP_PATTERNS:
        m = rx.search(base)
        if m:
            break
    if m:
        base = base[:m.start()]
    else:
        y = re.search(r'(?<!\d)(19\d\d|20\d\d)(?!\d)', base)
        if y and y.start() > 0:
            year, base = y.group(1), base[:y.start()]
    words = []
    for w in re.sub(r'[._]+', ' ', base).split():
        if words and tokens(w) & NAME_STOP:
            break
        words.append(w)
    title = re.split(r'\s[-–]\s|,|\(|\[|/', ' '.join(words))[0].strip(' -–()[]')
    return title, year


def ws_recent(kind='movie', page=0):
    """Nejnovější streamy: filmy / seriály, které se právě nahrály na Webshare – přiřazené k TMDB
    (plakát, popis, hodnocení, jazyky) a vykreslené jako ostatní seznamy."""
    if not need_key():
        end_dir(HANDLE, succeeded=False)
        return
    page = int(page or 0)
    xbmcplugin.setPluginCategory(HANDLE, 'Nejnovější streamy')
    ws = api()
    # prázdný dotaz Webshare nepodporuje; „cz“ seřazené od nejnovějších = čerstvě nahrané české soubory
    guesses, seen, total = [], set(), 0
    for p in (page * 2, page * 2 + 1):
        total, files = fetch(ws, 'cz', 100, p * 100, sort='recent')
        for f in files:
            if (kind == 'tv') != (parse_episode(f['name'])[0] is not None):
                continue
            t, y = guess_title(f['name'])
            key = norm_text(t)
            if len(key) >= 2 and key not in seen:
                seen.add(key)
                guesses.append((t, y))
    tm_kind = 'tv' if kind == 'tv' else 'movie'

    def one(g):
        try:
            return tm().find(tm_kind, g[0], g[1])
        except T.TmdbError as e:
            log('TMDB %s: %s' % (g[0], e))
            return None
    with ThreadPoolExecutor(max_workers=8) as ex:
        found = list(ex.map(one, guesses[:40]))
    tm().save()
    items, ids = [], set()
    for it in found:
        if it and it['id'] not in ids:
            ids.add(it['id'])
            items.append(it)
    render_items(items, hide_unavailable=True)
    if (page * 2 + 2) * 100 < total and page < 10:
        add_dir('[B]Další >>[/B]', action='ws_recent', kind=kind, page=page + 1)
    if not items:
        notify('Nic nového jsem nenašel', error=False)
    end_dir(HANDLE, cacheToDisc=False)


# ---------- titulky ----------
def find_subtitles(ws, video_name, title='', season=None, episode=None):
    """Hledá titulky na Webshare a seřadí je podle shody s názvem videa."""
    base = os.path.splitext(video_name)[0]
    queries = []
    for q in (base, re.sub(r'[._]+', ' ', base), title):
        if q and q not in queries:
            queries.append(q)
    found = {}
    for q in queries[:3]:
        try:
            _, files = ws.search(q, '', 50, 0, category='')
        except WebshareError:
            continue
        for f in files:
            if f['name'].lower().endswith(SUB_EXT) and not f['password']:
                found[f['ident']] = f
    vt = tokens(base)
    langs = [l.strip().lower() for l in (setting('subs_lang') or 'cz,cze,cs,czech,sk').split(',') if l.strip()]
    results = []
    for f in found.values():
        sb = os.path.splitext(f['name'])[0]
        if season is not None:
            s, e = parse_episode(f['name'])
            if (s, e) != (int(season), int(episode)):
                continue
        st = tokens(sb)
        score = len(vt & st) / max(1, len(vt))
        if sb.lower() == base.lower():
            score += 1.0
        if st & set(langs):
            score += 0.3
        f['score'] = score
        if score >= 0.3:
            results.append(f)
    results.sort(key=lambda f: -f['score'])
    return results


def download_subtitle(ws, f):
    link = ws.file_link(f['ident'], download_type='file_download')
    req = urllib.request.Request(link, headers={'User-Agent': 'Kodi'})
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    for enc in ('utf-8-sig', 'cp1250', 'iso-8859-2'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('utf-8', 'replace')
    folder = _path('subs', 'x')[:-1]
    for old in os.listdir(folder):  # smaž titulky z minula
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    ext = os.path.splitext(f['name'])[1].lower() or '.srt'
    safe = re.sub(r'[^\w.-]+', '_', os.path.splitext(f['name'])[0])[:80]
    path = os.path.join(folder, '%s.cs%s' % (safe, ext))
    with open(path, 'w', encoding='utf-8') as out:
        out.write(text)
    return path


def pick_subtitles(ws, name, title, season, episode, mode):
    """Vrátí cestu ke staženým titulkům, nebo None."""
    if mode == 'off' or not name:
        return None
    try:
        subs = find_subtitles(ws, name, title, season, episode)
    except Exception as e:
        log('Hledání titulků: %s' % e)
        return None
    if not subs:
        if mode == 'ask':
            notify('Na Webshare jsem titulky nenašel', error=False)
        return None
    if mode == 'ask':
        labels = ['%s  [COLOR gray](shoda %d %%)[/COLOR]' % (s['name'], min(100, s['score'] * 100)) for s in subs[:30]]
        idx = xbmcgui.Dialog().select('Titulky z Webshare', ['Bez titulků'] + labels)
        if idx <= 0:
            return None
        chosen = subs[idx - 1]
    else:
        chosen = subs[0]
    try:
        return download_subtitle(ws, chosen)
    except Exception as e:
        log('Stažení titulků: %s' % e)
        notify('Titulky se nepodařilo stáhnout')
        return None


# ---------- historie, sledované ----------
HIST_LABELS = {'search': ('[Hledat]', 'search'), 'ws': ('[Webshare]', 'ws_search')}


def history():
    hist = load_json('history.json', [])
    xbmcplugin.setPluginCategory(HANDLE, 'Historie hledání')
    for h in hist:
        prefix, action = HIST_LABELS.get(h['type'], HIST_LABELS['search'])
        add_dir('%s %s' % (prefix, h['q']), 'DefaultAddonsSearch.png', action=action, q=h['q'])
    if hist:
        add_dir('[I]Smazat historii[/I]', 'DefaultIconError.png', action='clear_history', folder=False)
    end_dir(HANDLE, cacheToDisc=False)


def add_watched(entry):
    w = [x for x in load_json('watched.json', []) if x.get('key') != entry['key']]
    w.insert(0, entry)
    save_json('watched.json', w[:50])


def watched(kind=None):
    titles_view()
    xbmcplugin.setPluginCategory(HANDLE, 'Naposledy sledované')
    xbmcplugin.setContent(HANDLE, 'tvshows' if kind == 'tv' else 'movies')
    for w in load_json('watched.json', []):
        t = w.get('type')
        if kind and t != kind:
            continue
        art = {'poster': w.get('poster'), 'thumb': w.get('poster'), 'icon': w.get('poster')} if w.get('poster') else None
        if t == 'tv':
            label = w['title'] + ('  [COLOR gray]naposledy S%02dE%02d[/COLOR]' % (w['s'], w['e']) if w.get('s') else '')
            add_dir(label, 'DefaultTVShows.png', art=art, action='tv', id=w['id'])
        elif t == 'movie':
            li = xbmcgui.ListItem(w['title'])
            li.setArt(art or {'icon': 'DefaultMovies.png'})
            li.setProperty('IsPlayable', 'true')
            xbmcplugin.addDirectoryItem(HANDLE, build_url(action='pick', id=w['id']), li, False)
        elif not kind:   # starší záznamy (z verze 2.x) a soubory přímo z Webshare
            add_dir(w.get('show') or w.get('title', '?'), 'DefaultVideo.png', art=art, action='ws_search',
                    q=w.get('show') or w.get('title'))
    end_dir(HANDLE, cacheToDisc=False)


# ---------- diagnostika a účet ----------
def diagnostics():
    """Projde přihlášení, účet, TMDB, hledání, získání odkazu a stažení začátku videa. Výsledek ukáže a uloží."""
    import platform
    rep_ = []
    out = rep_.append
    OK, BAD, WARN = '[COLOR FF43A047]OK[/COLOR]', '[COLOR FFE53935]CHYBA[/COLOR]', '[COLOR FFFB8C00]POZOR[/COLOR]'
    out('Doplněk %s, Kodi %s, %s' % (ADDON.getAddonInfo('version'), xbmc.getInfoLabel('System.BuildVersion'),
                                      platform.platform()))
    dlg = xbmcgui.DialogProgress()
    dlg.create('Diagnostika', 'Testuji…')
    try:
        dlg.update(5, 'TMDB…')
        if not setting('tmdb_key').strip():
            out('%s TMDB: chybí API klíč (katalog nebude fungovat)' % WARN)
        else:
            try:
                items, _ = tm().page('movie/popular', 1, kind='movie')
                out('%s TMDB: %d populárních filmů, např. „%s“' % (OK, len(items), items[0]['title'] if items else '-'))
            except T.TmdbError as e:
                out('%s TMDB: %s' % (BAD, e))
        dlg.update(10, 'Přihlášení…')
        try:
            addon = xbmcaddon.Addon()
            addon.setSetting('token', '')   # vynutí čisté přihlášení
            global _WS
            _WS = None
            ws = api()
            out('%s Přihlášení (token %s…)' % (OK, ws.token[:6]))
        except WebshareError as e:
            out('%s Přihlášení: %s' % (BAD, e))
            u_, p_ = setting('username'), setting('password')
            out('   Jméno: „%s“ (%d znaků)' % (u_, len(u_)))
            out('   Heslo: %d znaků, 1. znak je %s, mezera na začátku/konci: %s' % (
                len(p_), 'velké písmeno' if p_[:1].isupper() else ('malé písmeno' if p_[:1].islower() else
                                                                     'číslo/znak'),
                'ANO' if p_ != p_.strip() else 'ne'))
            if getattr(e, 'code', '') == 'LOGIN_FATAL_1':
                out('   Webshare jméno zná, ale odmítl heslo. Zadej heslo znovu klávesnicí na televizi.')
            return
        dlg.update(25, 'Účet…')
        try:
            u = ws.user_data()
            r = u['raw']
            out('%s Účet %s: vip=%s, vip_days=%s, vip_until=%s' % (
                OK if u['vip'] else WARN, u['username'], r.get('vip', '?'), r.get('vip_days', '?'),
                r.get('vip_until', '?')))
            if not u['vip']:
                out('   Webshare nehlásí VIP. Zkontroluj na webu, že předplatné běží na tomto účtu.')
            vip_status(force=True)
        except WebshareError as e:
            out('%s Údaje o účtu: %s' % (BAD, e))
        dlg.update(40, 'Hledání…')
        try:
            _, files = ws.search('matrix', '', 20, 0)
            files = [f for f in files if f['name'].lower().endswith(VIDEO_EXT) and not f['password']]
            files.sort(key=lambda f: f['size'])
            if not files:
                out('%s Hledání nevrátilo žádné video' % BAD)
                return
            f = files[len(files) // 2]
            out('%s Hledání: %d videí, testuji „%s“ (%s)' % (OK, len(files), f['name'], human_size(f['size'])))
        except WebshareError as e:
            out('%s Hledání: %s' % (BAD, e))
            return
        try:
            d = describe(f, ws.file_info(f['ident']))
            out('%s file_info: %s' % (OK, re.sub(r'\[/?(?:B|COLOR[^\]]*)\]', '', stream_labels(d)[0])))
        except WebshareError as e:
            out('%s file_info: %s' % (BAD, e))
        link = None
        for i, t in enumerate(LINK_TYPES):
            dlg.update(55 + i * 8, 'Odkaz ke stažení (%s)…' % (t or 'bez typu'))
            try:
                l = ws.file_link(f['ident'], t)
                out('%s file_link (%s): %s' % (OK, t or 'bez typu', l[:70]))
                link = link or l
            except WebshareError as e:
                out('%s file_link (%s): %s' % (BAD, t or 'bez typu', e))
                if e.raw:
                    out('   odpověď: %s' % re.sub(r'\s+', ' ', e.raw)[:300])
        if not link:
            return
        if '://free.' in link:
            out('%s Odkaz vede na free server – bez VIP Kodi video nepřehraje.' % WARN)
        dlg.update(85, 'Stahuji začátek videa…')
        try:
            req = urllib.request.Request(link, headers={'User-Agent': PLAYER_UA, 'Range': 'bytes=0-2047'})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=20) as r:
                head = r.read(2048)
                ctype = r.headers.get('Content-Type', '?')
                size = r.headers.get('Content-Range') or r.headers.get('Content-Length', '?')
                status = r.status
            ms = int((time.time() - t0) * 1000)
            sig = head[:4].hex()
            kind = ('MKV' if sig == '1a45dfa3' else 'MP4' if head[4:8] == b'ftyp' else
                    'AVI' if head[:4] == b'RIFF' else 'HTML stránka!' if b'<html' in head.lower() or b'<!doctype' in head.lower()
                    else 'neznámý (%s)' % sig)
            ok = not kind.startswith('HTML')
            out('%s Stažení: HTTP %s, %s, typ %s, %s, odezva %d ms' % (
                OK if ok else BAD, status, ctype, kind, size, ms))
            if not ok:
                txt = re.sub(r'<[^>]+>', ' ', head.decode('utf-8', 'replace'))
                out('   stránka říká: %s' % re.sub(r'\s+', ' ', txt)[:300])
        except Exception as e:
            out('%s Stažení videa: %s' % (BAD, e))
    finally:
        dlg.close()
        tm().save()
        text = '\n'.join(rep_)
        try:
            with open(_path('diagnostika.txt'), 'w', encoding='utf-8') as fh:
                fh.write(re.sub(r'\[/?COLOR[^\]]*\]', '', text))
        except Exception:
            pass
        log('DIAGNOSTIKA\n' + text)
        xbmcgui.Dialog().textviewer('Diagnostika – vyfoť a pošli mi', text)


def account():
    u = api().user_data()
    vip_status(force=True)
    if u['vip']:
        vip = '[COLOR FF43A047]VIP aktivní[/COLOR] – zbývá %s dní%s' % (
            u['vip_days'], ' (do %s)' % u['vip_until'] if u['vip_until'] else '')
    else:
        vip = ('[COLOR FFE53935]Webshare hlásí účet bez VIP[/COLOR]%s – přehrávání nebude fungovat.\n'
               'Zkontroluj na webshare.cz, že je předplatné aktivní na tomto účtu.'
               % (' (vypršelo %s)' % u['vip_until'] if u['vip_until'] else ''))
    xbmcgui.Dialog().ok('Webshare účet', 'Uživatel: %s\n%s' % (u['username'], vip))


# ---------- hledání: klávesnice a hlas ----------
def search_hub():
    xbmcplugin.setPluginCategory(HANDLE, 'Hledat')
    add_dir('[B]Hledat klávesnicí[/B]', 'DefaultAddonsSearch.png', action='search')
    add_dir('[B]Hlasové hledání[/B]', 'DefaultAddonsSearch.png', action='voice_search')
    add_dir('[B]Hledat z telefonu[/B] (psaní i hlas)', 'DefaultNetwork.png', action='phone_help', folder=False)
    recent = [h for h in load_json('history.json', []) if h['type'] == 'search'][:10]
    for h in recent:
        add_dir('[COLOR gray][Hledat][/COLOR] %s' % h['q'], 'DefaultFolder.png', action='search', q=h['q'])
    end_dir(HANDLE, cacheToDisc=False)


def voice_search():
    """Otevře klávesnici a hned spustí rozpoznávání řeči (Android, Kodi potřebuje oprávnění k mikrofonu).
    Na zařízeních bez rozpoznávání řeči zůstane obyčejná klávesnice."""
    import threading
    kb = xbmc.Keyboard('', 'Řekni název filmu nebo seriálu…')
    timer = threading.Timer(0.8, lambda: xbmc.executebuiltin('Action(VoiceRecognizer)'))
    timer.start()
    kb.doModal()
    timer.cancel()
    text = kb.getText().strip() if kb.isConfirmed() else ''
    if not text:
        end_dir(HANDLE, succeeded=False)
        return
    search(text)


KEYMAP_FILE = 'special://profile/keymaps/pvscinema.xml'
KEYMAP_KEYS = ['f3', 'f', 's']     # volba v nastavení: F3, Ctrl+F, S


def install_keymap():
    key = KEYMAP_KEYS[min(setting_int('search_key', 0), len(KEYMAP_KEYS) - 1)]
    tag = '<f mod="ctrl">' if key == 'f' else '<%s>' % key
    end = '</f>' if key == 'f' else '</%s>' % key
    plugin = 'plugin://plugin.video.pvscinema/'
    xml = '''<keymap>
  <global>
    <keyboard>
      %(tag)sActivateWindow(Videos,%(p)s?action=search,return)%(end)s
      <f4>ActivateWindow(Videos,%(p)s?action=voice_search,return)</f4>
    </keyboard>
  </global>
  <VirtualKeyboard>
    <keyboard>
      <f4>VoiceRecognizer</f4>
    </keyboard>
  </VirtualKeyboard>
</keymap>
''' % {'tag': tag, 'end': end, 'p': plugin}
    path = xbmcvfs.translatePath(KEYMAP_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(xml)
    xbmc.executebuiltin('Action(reloadkeymaps)')
    xbmcgui.Dialog().ok('Klávesové zkratky', '%s = hledání klávesnicí\nF4 = hlasové hledání (i uvnitř klávesnice)\n\nZkratky fungují kdekoli v Kodi.'
                        % {'f3': 'F3', 'f': 'Ctrl+F', 's': 'S'}[key])


def remove_keymap():
    path = xbmcvfs.translatePath(KEYMAP_FILE)
    if os.path.exists(path):
        os.remove(path)
        xbmc.executebuiltin('Action(reloadkeymaps)')
    notify('Klávesové zkratky odebrány', error=False)


# ---------- menu (po vzoru Stream Cinema) ----------
def root():
    adult_lock(go_home=False)     # návrat do hlavního menu sekci 18+ zamkne
    vip = vip_status() if setting('username') else None
    if vip is False:
        add_dir('[COLOR FFE53935][B]Účet nemá VIP – přehrávání nebude fungovat[/B][/COLOR]', 'DefaultIconError.png',
                action='account', folder=False)
    elif vip:
        win = xbmcgui.Window(10000)
        if not win.getProperty('wsh_vip_text'):     # uloženo starší verzí bez počtu dní
            vip_status(force=True)
        text = win.getProperty('wsh_vip_text')
        try:
            days = int(win.getProperty('wsh_vip_days') or 99)
        except ValueError:
            days = 99
        if text:
            color = 'FFE53935' if days <= 3 else ('FFFB8C00' if days <= 7 else 'FF43A047')
            add_dir('[COLOR %s][B]Webshare %s[/B][/COLOR]' % (color, text), 'DefaultUser.png',
                    action='account', folder=False)
    latest = update_available()
    if latest:
        add_dir('[COLOR FFFDD835][B]Je dostupná nová verze %s – klikni pro aktualizaci[/B][/COLOR]' % latest,
                'DefaultAddonsUpdates.png', action='update', folder=False)
    if not setting('username') or not setting('tmdb_key').strip():
        add_dir('[COLOR FFFDD835][B]Jak začít: založení účtů Webshare a TMDB[/B][/COLOR]', 'DefaultIconInfo.png',
                action='help', folder=False)
    if not setting('tmdb_key').strip():
        add_dir('[COLOR FFFDD835][B]Nastav klíč TMDB (katalog filmů a seriálů)[/B][/COLOR]', 'DefaultIconWarning.png',
                action='tmdb_help', folder=False)
    add_dir('Hledat', 'DefaultAddonsSearch.png', action='search_hub')
    add_dir('Filmy', 'DefaultMovies.png', action='menu', kind='movie')
    add_dir('Seriály', 'DefaultTVShows.png', action='menu', kind='tv')
    add_dir('Pro děti', 'DefaultAddonGame.png', action='kids')
    if xmas_season():
        add_dir('[COLOR FFE53935][B]Vánoce %d[/B][/COLOR]' % xmas_year(), art=xmas_art(), action='xmas')
    add_dir('Naposledy sledované', 'DefaultRecentlyAddedMovies.png', action='watched')
    add_dir('Historie hledání', 'DefaultFolder.png', action='history')
    add_dir('Přímo na Webshare', 'DefaultNetwork.png', action='ws_search')
    add_dir('Můj účet', 'DefaultUser.png', action='account', folder=False)
    add_dir('Diagnostika (když nejde přehrávání)', 'DefaultAddonHelper.png', action='diagnostics', folder=False)
    add_dir('Nastavení', 'DefaultAddonProgram.png', action='settings', folder=False)
    add_dir('Návod: jak začít (účty Webshare a TMDB)', 'DefaultIconInfo.png', action='help', folder=False)
    end_dir(HANDLE)
    show_news()
    if latest:
        offer_update(latest)


def xmas_menu():
    y = xmas_year()
    xbmcplugin.setPluginCategory(HANDLE, 'Vánoce %d' % y)
    art = xmas_art()
    add_dir('[B]Vánoční filmy – nejoblíbenější[/B]', art=art, action='list', kind='movie', name='xmas')
    add_dir('Vánoční novinky %d–%d' % (y - 1, y), art=art, action='list', kind='movie', name='xmas_new')
    add_dir('Pohádky a animované pro děti', art=art, action='list', kind='movie', name='xmas_kids')
    add_dir('Vánoční komedie', art=art, action='list', kind='movie', name='xmas_comedy')
    add_dir('Romantické vánoční filmy', art=art, action='list', kind='movie', name='xmas_romance')
    add_dir('Nejlépe hodnocené vánoční filmy', art=art, action='list', kind='movie', name='xmas_top')
    add_dir('České a slovenské vánoční filmy', art=art, action='list', kind='movie', name='xmas_cz')
    add_dir('České a slovenské pohádky', art=art, action='list', kind='movie', name='cz_fairy')
    add_dir('Vánoční seriály a speciály', art=art, action='list', kind='tv', name='xmas')
    end_dir(HANDLE)


def kind_menu(kind):
    tv = kind == 'tv'
    icon = 'DefaultTVShows.png' if tv else 'DefaultMovies.png'
    xbmcplugin.setPluginCategory(HANDLE, 'Seriály' if tv else 'Filmy')
    add_dir('Hledat', 'DefaultAddonsSearch.png', action='search')
    add_dir('Tipy na dnes', icon, action='list', kind=kind, name='tips')
    add_dir('Naposled sledované', 'DefaultRecentlyAddedMovies.png', action='watched', kind=kind)
    add_dir('Novinky', icon, action='list', kind=kind, name='news')
    add_dir('Novinky dabované', icon, action='list', kind=kind, name='news', dub='1')
    add_dir('Novinky dle žánru', 'DefaultGenre.png', action='genres', kind=kind, target='news')
    add_dir('Právě sledované', icon, action='list', kind=kind, name='trending_day')
    add_dir('Populární', icon, action='list', kind=kind, name='popular')
    add_dir('Trendy', icon, action='list', kind=kind, name='trending')
    add_dir('Nejlépe hodnocené', 'DefaultFavourites.png', action='list', kind=kind, name='top')
    add_dir('Nejnovější streamy', 'DefaultNetwork.png', action='ws_recent', kind=kind)
    add_dir('Kids', 'DefaultAddonGame.png', action='list', kind=kind, name='kids')
    add_dir('Podle žánru', 'DefaultGenre.png', action='genres', kind=kind, target='genre')
    add_dir('Podle roku', 'DefaultYear.png', action='years', kind=kind)
    add_dir('Podle hodnocení', 'DefaultFavourites.png', action='ratings', kind=kind)
    end_dir(HANDLE)


def kids_menu():
    xbmcplugin.setPluginCategory(HANDLE, 'Pro děti')
    add_dir('Filmy pro děti', 'DefaultMovies.png', action='list', kind='movie', name='kids')
    add_dir('Animované filmy', 'DefaultMovies.png', action='list', kind='movie', name='genre', genre=16)
    add_dir('Rodinné filmy', 'DefaultMovies.png', action='list', kind='movie', name='genre', genre=10751)
    add_dir('Seriály pro děti', 'DefaultTVShows.png', action='list', kind='tv', name='kids')
    add_dir('Animované seriály', 'DefaultTVShows.png', action='list', kind='tv', name='genre', genre=16)
    end_dir(HANDLE)


def genres_menu(kind, target):
    xbmcplugin.setPluginCategory(HANDLE, 'Žánry')
    for gid, gname in (T.TV_GENRES if kind == 'tv' else T.MOVIE_GENRES):
        add_dir(gname, 'DefaultGenre.png', action='list', kind=kind, name=target, genre=gid)
    end_dir(HANDLE)


def years_menu(kind):
    xbmcplugin.setPluginCategory(HANDLE, 'Podle roku')
    this_year = time.localtime().tm_year
    for y in range(this_year, this_year - 40, -1):
        add_dir(str(y), 'DefaultYear.png', action='list', kind=kind, name='year', year=y)
    end_dir(HANDLE)


RATING_RANGES = [(8, 10), (7, 7.9), (6, 6.9), (5, 5.9), (0, 4.9)]


def ratings_menu(kind):
    xbmcplugin.setPluginCategory(HANDLE, 'Podle hodnocení')
    for lo, hi in RATING_RANGES:
        add_dir('[B]%s[/B]' % ('%g a víc' % lo if hi == 10 else '%g – %g' % (lo, hi)), 'DefaultFavourites.png',
                action='list', kind=kind, name='rating', rmin=lo, rmax=hi)
    end_dir(HANDLE)


def show_help():
    """Návod pro nové uživatele (resources/navod.txt – stejný text je i vedle ZIPu)."""
    path = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo('path')), 'resources', 'navod.txt')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
    except OSError:
        text = 'Návod se nepodařilo načíst.'
    xbmcgui.Dialog().textviewer('PvS Cinema – jak začít', text)


def phone_help():
    if setting('phone_remote') == 'false':
        xbmcgui.Dialog().ok('Hledat z telefonu', 'Funkce je vypnutá. Zapni ji v Nastavení → Zobrazení a ovládání '
                                                 'a restartuj Kodi.')
        return
    url = 'http://%s:%s' % (xbmc.getIPAddress(), setting('phone_port') or '8765')
    xbmcgui.Dialog().ok('Hledat z telefonu',
                        'Na telefonu ve stejné Wi-Fi otevři: [B][COLOR FF12A8E0]%s[/COLOR][/B]\n'
                        'Napiš nebo nadiktuj název (🎤 na klávesnici telefonu) a dej Poslat.\n'
                        'Otevřená klávesnice v TV = text se vloží do ní.' % url)


def tmdb_help():
    if need_key():
        notify('Klíč TMDB uložen', error=False)
        xbmc.executebuiltin('Container.Refresh')


def router():
    p = PARAMS
    a = p.get('action')
    try:
        if a is None:
            root()
        elif a == 'menu':
            kind_menu('tv' if p['kind'] in ('tv', 'series') else 'movie')
        elif a == 'kids':
            kids_menu()
        elif a == 'xmas':
            xmas_menu()
        elif a == 'update':
            do_update()
        elif a == 'list':
            catalog(p['kind'], p['name'], p.get('page'), p.get('dub') == '1', genre=p.get('genre', ''),
                    year=p.get('year', ''), rmin=p.get('rmin', ''), rmax=p.get('rmax', ''))
        elif a == 'genres':
            genres_menu(p['kind'], p.get('target', 'genre'))
        elif a == 'years':
            years_menu(p['kind'])
        elif a == 'ratings':
            ratings_menu(p['kind'])
        elif a in ('search', 'csfd_all_search', 'csfd_movie_search', 'csfd_series_search'):
            search(p.get('q'))     # csfd_* = staré odkazy (klávesové zkratky, Home Assistant)
        elif a == 'adult':
            adult_menu()
        elif a == 'adult_search':
            adult_search(p.get('q'), p.get('page'), p.get('sort', ''))
        elif a == 'adult_lock':
            adult_lock()
        elif a == 'adult_clear':
            save_json('adult_history.json', [])
            notify('Historie 18+ smazána', error=False)
            xbmc.executebuiltin('Container.Refresh')
        elif a == 'search_hub':
            search_hub()
        elif a == 'voice_search':
            voice_search()
        elif a == 'pick':
            pick_movie(p['id'], p.get('subs'))
        elif a == 'tv':
            tv_show(p['id'], p.get('refresh') == '1')
        elif a == 'season':
            tv_season(p['id'], p['s'])
        elif a == 'pick_ep':
            pick_episode(p['id'], p['s'], p['e'], p.get('subs'))
        elif a in ('ws_search', 'movie', 'ws_menu'):
            ws_search(p.get('q'), p.get('page'))
        elif a == 'ws_recent':
            ws_recent(p.get('kind', 'movie'), p.get('page'))
        elif a == 'play':
            play_raw(p['ident'], p.get('name', ''), p.get('subs'))
        elif a == 'watched':
            watched(p.get('kind'))
        elif a == 'history':
            history()
        elif a == 'clear_history':
            save_json('history.json', [])
            notify('Historie smazána', error=False)
            xbmc.executebuiltin('Container.Refresh')
        elif a == 'install_keymap':
            install_keymap()
        elif a == 'remove_keymap':
            remove_keymap()
        elif a == 'diagnostics':
            diagnostics()
        elif a == 'account':
            account()
        elif a == 'tmdb_help':
            tmdb_help()
        elif a == 'phone_help':
            phone_help()
        elif a == 'help':
            show_help()
        elif a == 'settings':
            ADDON.openSettings()
        else:
            root()
    except (WebshareError, T.TmdbError) as e:
        notify(str(e))
        if a in ('play', 'pick', 'pick_ep'):
            resolve_fail()
        elif a not in ('account', 'settings', 'update', 'clear_history', 'adult_lock', 'adult_clear', 'install_keymap', 'remove_keymap', 'diagnostics',
                       'tmdb_help'):
            end_dir(HANDLE, succeeded=False)


if __name__ == '__main__':
    router()
