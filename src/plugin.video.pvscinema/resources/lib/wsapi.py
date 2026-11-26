# Klient pro API Webshare.cz (https://webshare.cz/api/)
import hashlib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .md5crypt import md5crypt

API = 'https://webshare.cz/api/'
HEADERS = {
    'Accept': 'text/xml; charset=UTF-8',
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
    'User-Agent': 'Kodi plugin.video.pvscinema/1.0',
}


def days_left(vip_until, vip_days=0):
    """Kolik celých dní VIP zbývá (jako počítá Webshare) podle data konce, jinak podle vip_days."""
    import datetime
    try:
        end = datetime.datetime.strptime((vip_until or '')[:19], '%Y-%m-%d %H:%M:%S')
        return max(0, int((end - datetime.datetime.now()).total_seconds() // 86400))
    except (ValueError, TypeError):
        return vip_days


def days_word(n):
    return 'den' if n == 1 else ('dny' if 2 <= n <= 4 else 'dní')


def vip_text(u):
    """„VIP: zbývá 29 dní (do 18. 12. 2026)“ – krátký popis pro menu a oznámení."""
    n = u.get('days_left', 0)
    until = u.get('vip_until') or ''
    try:
        y, m, d = until[:10].split('-')
        until = ' (do %d. %d. %s)' % (int(d), int(m), y)
    except ValueError:
        until = ''
    return 'VIP: zbývá %d %s%s' % (n, days_word(n), until)


class WebshareError(Exception):
    def __init__(self, message, code='', raw=''):
        Exception.__init__(self, message)
        self.code = code
        self.raw = raw


class Webshare:
    def __init__(self, token='', device_uuid=''):
        self.token = token or ''
        self.device_uuid = device_uuid
        self.last_raw = ''

    def _call(self, endpoint, data):
        if self.token and 'wst' not in data:
            data['wst'] = self.token
        body = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(API + endpoint + '/', data=body, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
        except Exception as e:
            raise WebshareError('Chyba spojení s Webshare: %s' % e)
        self.last_raw = raw.decode('utf-8', 'replace')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise WebshareError('Webshare vrátil neočekávanou odpověď', raw=self.last_raw[:500])
        if root.findtext('status') != 'OK':
            code = root.findtext('code') or ''
            msg = root.findtext('message') or code or 'Neznámá chyba'
            raise WebshareError('%s%s' % (msg, ' (%s)' % code if code and code != msg else ''),
                                code=code, raw=self.last_raw[:500])
        return root

    # --- přihlášení ---
    def login(self, username, password):
        self.token = ''
        username = (username or '').strip()
        password = password or ''
        salt = self._call('salt', {'username_or_email': username}).findtext('salt')
        if not salt:
            raise WebshareError('Uživatel nenalezen')
        pw_hash = hashlib.sha1(md5crypt(password, salt).encode('utf-8')).hexdigest()
        # digest se počítá z hashe hesla, ne z hesla v otevřené podobě
        digest = hashlib.md5((username + ':Webshare:' + pw_hash).encode('utf-8')).hexdigest()
        root = self._call('login', {
            'username_or_email': username,
            'password': pw_hash,
            'digest': digest,
            'keep_logged_in': 1,
        })
        self.token = root.findtext('token') or ''
        if not self.token:
            raise WebshareError('Přihlášení selhalo')
        return self.token

    def user_data(self):
        root = self._call('user_data', {})
        raw = {child.tag: (child.text or '').strip() for child in root}

        def num(k):
            try:
                return int(float(raw.get(k) or 0))
            except ValueError:
                return 0
        vip = raw.get('vip') in ('1', 'true', 'yes') or num('vip_days') > 0 or num('vip_hours') > 0 \
            or num('vip_minutes') > 0
        return {
            'username': raw.get('username', ''),
            'vip': vip,
            'vip_days': raw.get('vip_days') or '0',
            'vip_until': raw.get('vip_until', ''),
            'days_left': days_left(raw.get('vip_until', ''), num('vip_days')) if vip else 0,
            'raw': raw,
        }

    def is_logged_in(self):
        if not self.token:
            return False
        try:
            self.user_data()
            return True
        except WebshareError:
            return False

    # --- vyhledávání a přehrávání ---
    def search(self, what, sort='', limit=50, offset=0, category='video'):
        root = self._call('search', {
            'what': what,
            'category': category,
            'sort': sort,
            'limit': limit,
            'offset': offset,
        })
        files = []
        for f in root.findall('file'):
            files.append({
                'ident': f.findtext('ident') or '',
                'name': f.findtext('name') or '',
                'size': int(f.findtext('size') or 0),
                'img': f.findtext('img') or '',
                'positive': int(f.findtext('positive_votes') or 0),
                'negative': int(f.findtext('negative_votes') or 0),
                'password': f.findtext('password') == '1',
            })
        total = int(root.findtext('total') or 0)
        return total, files

    def file_info(self, ident):
        """Technické údaje o videu: délka, bitrate, rozlišení, kodeky, audio stopy (jazyk, kanály)."""
        root = self._call('file_info', {'ident': ident})

        def i(node, tag):
            try:
                return int(float(node.findtext(tag) or 0))
            except ValueError:
                return 0
        video = [{'format': (s.findtext('format') or '').strip(), 'width': i(s, 'width'), 'height': i(s, 'height'),
                  'bitrate': i(s, 'bitrate'), 'fps': i(s, 'fps')} for s in root.findall('video/stream')]
        audio = [{'format': (s.findtext('format') or '').strip(), 'channels': i(s, 'channels'),
                  'bitrate': i(s, 'bitrate'), 'lang': (s.findtext('language') or '').strip().lower()}
                 for s in root.findall('audio/stream')]
        return {
            'length': i(root, 'length'), 'bitrate': i(root, 'bitrate'), 'format': root.findtext('format') or '',
            'width': i(root, 'width'), 'height': i(root, 'height'), 'video': video, 'audio': audio,
            'stripe': root.findtext('stripe') or '', 'available': root.findtext('available') != '0',
        }

    def file_link(self, ident, download_type='video_stream'):
        data = {'ident': ident, 'force_https': 1}
        if download_type:
            data['download_type'] = download_type
        if self.device_uuid:
            data['device_uuid'] = self.device_uuid
        root = self._call('file_link', data)
        link = root.findtext('link')
        if not link:
            raise WebshareError('Webshare nevrátil odkaz na soubor', raw=self.last_raw[:500])
        return link
