# Klient pro TMDB (themoviedb.org) – katalog, české názvy, popisy, plakáty a hodnocení.
# Potřebuje vlastní bezplatný API klíč (v3 „API Key“ nebo v4 „Read Access Token“).
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://api.themoviedb.org/3/'
IMG = 'https://image.tmdb.org/t/p/'
LANG = 'cs-CZ'

MOVIE_GENRES = [
    (28, 'Akční'), (12, 'Dobrodružný'), (16, 'Animovaný'), (35, 'Komedie'), (80, 'Krimi'),
    (99, 'Dokumentární'), (18, 'Drama'), (10751, 'Rodinný'), (14, 'Fantasy'), (36, 'Historický'),
    (27, 'Horor'), (10402, 'Hudební'), (9648, 'Mysteriózní'), (10749, 'Romantický'), (878, 'Sci-Fi'),
    (53, 'Thriller'), (10752, 'Válečný'), (37, 'Western'), (10770, 'TV film'),
]
TV_GENRES = [
    (10759, 'Akční a dobrodružný'), (16, 'Animovaný'), (35, 'Komedie'), (80, 'Krimi'), (99, 'Dokumentární'),
    (18, 'Drama'), (10751, 'Rodinný'), (10762, 'Pro děti'), (9648, 'Mysteriózní'), (10764, 'Reality'),
    (10765, 'Sci-Fi a fantasy'), (10768, 'Válečný a politický'), (37, 'Western'), (10766, 'Telenovela'),
]
GENRE_NAMES = dict(MOVIE_GENRES + TV_GENRES)


class TmdbError(Exception):
    pass


class Tmdb:
    def __init__(self, key, cache_file=None, ttl=12 * 3600, adult=False):
        self.key = (key or '').strip()
        self.adult = bool(adult)
        self.cache_file = cache_file
        self.ttl = ttl
        self._cache = None

    # --- cache ---
    def _load(self):
        if self._cache is None:
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self._cache = json.load(f)
            except Exception:
                self._cache = {}
        return self._cache

    def save(self):
        if self._cache is None or not self.cache_file:
            return
        now = time.time()
        c = {k: v for k, v in self._cache.items() if now - v['ts'] < self.ttl}
        if len(c) > 400:
            for k in sorted(c, key=lambda k: c[k]['ts'])[:len(c) - 400]:
                del c[k]
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(c, f, ensure_ascii=False)
        except Exception:
            pass

    def get(self, path, **params):
        if not self.key:
            raise TmdbError('Chybí API klíč TMDB')
        params.setdefault('language', LANG)
        if path.startswith(('search/', 'discover/')):
            params['include_adult'] = 'true' if self.adult else 'false'
        q = urllib.parse.urlencode(sorted((k, v) for k, v in params.items() if v not in (None, '')))
        ck = path + '?' + q
        cache = self._load()
        hit = cache.get(ck)
        if hit and time.time() - hit['ts'] < self.ttl:
            return hit['data']
        headers = {'Accept': 'application/json'}
        url = API + path + '?' + q
        if self.key.startswith('eyJ'):         # v4 Read Access Token
            headers['Authorization'] = 'Bearer ' + self.key
        else:                                   # v3 API Key
            url += '&api_key=' + urllib.parse.quote(self.key)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                data = json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise TmdbError('TMDB odmítl API klíč – zkontroluj ho v nastavení')
            raise TmdbError('TMDB chyba %s' % e.code)
        except Exception as e:
            raise TmdbError('TMDB nedostupné: %s' % e)
        cache[ck] = {'ts': time.time(), 'data': data}
        return data

    # --- seznamy ---
    def page(self, path, page=1, kind=None, **params):
        """Vrátí (položky, počet stránek)."""
        d = self.get(path, page=page, **params)
        items = [norm(r, kind) for r in d.get('results', [])]
        return [i for i in items if i], int(d.get('total_pages') or 1)

    def search(self, query, page=1):
        return self.page('search/multi', page, query=query, include_adult='false')

    def find(self, kind, query, year=None):
        """Nejlepší shoda pro název (a rok) – pro přiřazení souboru k filmu/seriálu."""
        p = {'query': query, 'include_adult': 'false'}
        if year:
            p['primary_release_year' if kind == 'movie' else 'first_air_date_year'] = year
        items, _ = self.page('search/' + kind, 1, kind=kind, **p)
        if not items and year:
            items, _ = self.page('search/' + kind, 1, kind=kind, query=query, include_adult='false')
        return items[0] if items else None

    def details(self, kind, tid):
        d = self.get('%s/%s' % (kind, tid), append_to_response='external_ids,alternative_titles')
        item = norm(d, kind)
        item['genres'] = [g['name'] for g in d.get('genres', [])]
        item['imdb'] = (d.get('external_ids') or {}).get('imdb_id') or d.get('imdb_id') or ''
        item['runtime'] = d.get('runtime') or ((d.get('episode_run_time') or [0])[0])
        alts = (d.get('alternative_titles') or {}).get('titles') or (d.get('alternative_titles') or {}).get('results') or []
        item['alt'] = [a['title'] for a in alts if a.get('iso_3166_1') in ('CZ', 'SK', 'US', 'GB')][:3]
        if kind == 'tv':
            item['seasons'] = [{'n': s['season_number'], 'name': s.get('name') or '%d. série' % s['season_number'],
                                'episodes': s.get('episode_count') or 0, 'poster': img(s.get('poster_path')),
                                'year': (s.get('air_date') or '')[:4], 'plot': s.get('overview') or ''}
                               for s in d.get('seasons', [])]
        return item

    def season(self, tid, n):
        d = self.get('tv/%s/season/%s' % (tid, n))
        return [{'e': e['episode_number'], 'name': e.get('name') or '', 'plot': e.get('overview') or '',
                 'still': img(e.get('still_path'), 'w300'), 'aired': e.get('air_date') or '',
                 'rating': e.get('vote_average') or 0}
                for e in d.get('episodes', [])]


def img(path, size='w500'):
    return IMG + size + path if path else ''


def norm(r, kind=None):
    kind = kind or r.get('media_type')
    if kind not in ('movie', 'tv'):
        return None
    title = r.get('title') or r.get('name') or ''
    orig = r.get('original_title') or r.get('original_name') or ''
    date = r.get('release_date') or r.get('first_air_date') or ''
    return {
        'id': str(r['id']), 'type': kind, 'title': title, 'original': orig,
        'orig_lang': r.get('original_language') or '', 'year': date[:4], 'date': date,
        'plot': r.get('overview') or '', 'poster': img(r.get('poster_path')),
        'fanart': img(r.get('backdrop_path'), 'w1280'),
        'rating': round(float(r.get('vote_average') or 0), 1), 'votes': int(r.get('vote_count') or 0),
        'genres': [GENRE_NAMES[g] for g in r.get('genre_ids', []) if g in GENRE_NAMES],
    }
