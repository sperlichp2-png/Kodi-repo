# Webshare Hledání – služba „Hledat z telefonu“.
# V domácí síti běží malá webová stránka (http://<IP televize>:8765). Na telefonu do ní napíšeš nebo
# nadiktuješ (mikrofon na klávesnici telefonu) název:
#  - když je v Kodi otevřená klávesnice, text se vloží přímo do ní (JSON-RPC Input.SendText),
#  - jinak se otevře hledání v doplňku s výsledky na televizi.
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = 'plugin.video.pvscinema'
PLUGIN = 'plugin://%s/' % ADDON_ID


def setting(key):
    return xbmcaddon.Addon(ADDON_ID).getSetting(key)


def log(msg):
    xbmc.log('[Webshare Hledání – telefon] %s' % msg, xbmc.LOGINFO)


def recent():
    path = os.path.join(xbmcvfs.translatePath(xbmcaddon.Addon(ADDON_ID).getAddonInfo('profile')), 'history.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return [h['q'] for h in json.load(f) if h.get('type') in ('search', 'csfd_all')][:12]
    except Exception:
        return []


def keyboard_open():
    return xbmc.getCondVisibility('Window.IsVisible(virtualkeyboard)')


def send(text, mode):
    """mode: auto | search | type. Vrátí popis, co se stalo."""
    text = (text or '').strip()
    if not text:
        return 'Prázdný text'
    if mode == 'type' or (mode == 'auto' and keyboard_open()):
        req = {'jsonrpc': '2.0', 'method': 'Input.SendText', 'params': {'text': text, 'done': True}, 'id': 1}
        res = xbmc.executeJSONRPC(json.dumps(req))
        log('Text do klávesnice: %s → %s' % (text, res))
        return 'Vloženo do klávesnice v televizi'
    url = PLUGIN + '?' + urllib.parse.urlencode({'action': 'search', 'q': text})
    xbmc.executebuiltin('ActivateWindow(Videos,"%s",return)' % url)
    log('Hledám z telefonu: %s' % text)
    return 'Hledám na televizi: %s' % text


PAGE = '''<!doctype html><html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PvS Cinema</title>
<style>
:root{--bg:#10171f;--card:#1b2631;--fg:#eef3f7;--muted:#8fa3b5;--acc:#12a8e0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.4 system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:4px 0 16px}
form{display:flex;gap:8px}
input{flex:1;min-width:0;font-size:20px;padding:14px;border-radius:12px;border:1px solid #2c3b49;background:var(--card);color:var(--fg)}
button{font-size:18px;padding:14px 16px;border:0;border-radius:12px;background:var(--acc);color:#fff;font-weight:600}
.row{display:flex;gap:8px;margin-top:10px}
.row button{flex:1;background:var(--card);border:1px solid #2c3b49;font-weight:500;font-size:16px}
.row button.on{border-color:var(--acc);color:var(--acc)}
#st{margin:14px 2px;color:var(--muted);min-height:1.4em}
h2{font-size:15px;color:var(--muted);font-weight:500;margin:22px 0 8px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips button{background:var(--card);border:1px solid #2c3b49;font-weight:400;font-size:15px;padding:10px 12px}
.tip{color:var(--muted);font-size:14px;margin-top:22px}
#mic{display:none}
</style></head><body><main>
<h1>PvS Cinema – ovladač</h1>
<form id="f"><input id="q" type="search" placeholder="Film, seriál… nebo diktuj 🎤" autocomplete="off" autofocus>
<button id="mic" type="button" title="Hlasem">🎤</button><button>Poslat</button></form>
<div class="row"><button type="button" data-m="auto" class="on">Automaticky</button>
<button type="button" data-m="search">Hledat</button><button type="button" data-m="type">Do klávesnice</button></div>
<div id="st"></div>
<h2>Naposledy hledané</h2><div class="chips" id="rc">%RECENT%</div>
<p class="tip">Hlasem: klepni do pole a na klávesnici telefonu stiskni mikrofon 🎤.<br>
„Automaticky“ = když je na televizi otevřená klávesnice, text se vloží do ní, jinak se spustí hledání.</p>
</main><script>
var mode='auto',q=document.getElementById('q'),st=document.getElementById('st');
document.querySelectorAll('.row button').forEach(function(b){b.onclick=function(){
 mode=b.dataset.m;document.querySelectorAll('.row button').forEach(function(x){x.classList.toggle('on',x===b)});q.focus();}});
function go(t){if(!t)return;st.textContent='Posílám…';
 fetch('/send',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
  body:'q='+encodeURIComponent(t)+'&mode='+mode}).then(function(r){return r.json()})
 .then(function(j){st.textContent='✔ '+j.msg;q.value='';}).catch(function(){st.textContent='Televize neodpovídá';});}
document.getElementById('f').onsubmit=function(e){e.preventDefault();go(q.value.trim());};
document.getElementById('rc').onclick=function(e){if(e.target.tagName==='BUTTON')go(e.target.textContent);};
var SR=window.SpeechRecognition||window.webkitSpeechRecognition;
if(SR&&window.isSecureContext){var m=document.getElementById('mic');m.style.display='block';m.onclick=function(){
 var r=new SR();r.lang='cs-CZ';r.onresult=function(e){q.value=e.results[0][0].transcript;go(q.value);};
 st.textContent='Poslouchám…';r.start();};}
</script></body></html>'''


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


class Handler(BaseHTTPRequestHandler):
    def _out(self, code, body, ctype):
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.split('?')[0] != '/':
            return self._out(404, 'Nenalezeno', 'text/plain; charset=utf-8')
        chips = ''.join('<button type="button">%s</button>' % esc(r) for r in recent()) or \
            '<span style="color:#8fa3b5">zatím nic</span>'
        self._out(200, PAGE.replace('%RECENT%', chips), 'text/html; charset=utf-8')

    def do_POST(self):
        if self.path != '/send':
            return self._out(404, '{}', 'application/json')
        n = min(int(self.headers.get('Content-Length') or 0), 4096)
        p = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode('utf-8', 'replace')))
        try:
            msg = send(p.get('q', '')[:200], p.get('mode', 'auto'))
        except Exception as e:
            log('Chyba: %s' % e)
            msg = 'Chyba: %s' % e
        self._out(200, json.dumps({'msg': msg}, ensure_ascii=False), 'application/json; charset=utf-8')

    def log_message(self, *a):
        pass


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def vip_notify(mon):
    """Po startu Kodi ukáže oznámení, kolik dní zbývá do konce Webshare VIP."""
    if setting('vip_notify') == 'false' or not setting('username'):
        return
    if mon.waitForAbort(8):        # ať Kodi nejdřív dokreslí domovskou obrazovku
        return
    from resources.lib.wsapi import Webshare, vip_text
    addon = xbmcaddon.Addon(ADDON_ID)
    ws = Webshare(addon.getSetting('token'), addon.getSetting('device_uuid'))
    try:
        if not ws.is_logged_in():
            ws.login(addon.getSetting('username'), addon.getSetting('password'))
            addon.setSetting('token', ws.token)
        u = ws.user_data()
    except Exception as e:
        log('VIP při startu: %s' % e)
        return
    win = xbmcgui.Window(10000)
    win.setProperty('wsh_vip', '1' if u['vip'] else '0')
    win.setProperty('wsh_vip_text', vip_text(u) if u['vip'] else '')
    win.setProperty('wsh_vip_days', str(u.get('days_left', 0)))
    win.setProperty('wsh_vip_ts', str(__import__('time').time()))
    dlg = xbmcgui.Dialog()
    icon = xbmcaddon.Addon(ADDON_ID).getAddonInfo('icon')
    if u['vip']:
        days = u.get('days_left', 0)
        dlg.notification('PvS Cinema', vip_text(u),
                         xbmcgui.NOTIFICATION_WARNING if days <= 7 else icon, 8000, False)   # False = bez zvuku
    else:
        dlg.notification('PvS Cinema', 'Webshare: účet nemá VIP – přehrávání nebude fungovat',
                         xbmcgui.NOTIFICATION_ERROR, 10000, False)
    log('VIP při startu: %s' % (vip_text(u) if u['vip'] else 'bez VIP'))


def main():
    mon = xbmc.Monitor()
    threading.Thread(target=vip_notify, args=(mon,), daemon=True).start()
    if setting('phone_remote') == 'false':
        while not mon.waitForAbort(1):
            pass
        return
    try:
        port = int(setting('phone_port') or 8765)
    except ValueError:
        port = 8765
    try:
        srv = Server(('0.0.0.0', port), Handler)
    except OSError as e:
        log('Nejde spustit na portu %d: %s' % (port, e))
        return
    t = threading.Thread(target=srv.serve_forever)
    t.daemon = True
    t.start()
    log('Běží na http://%s:%d' % (xbmc.getIPAddress(), port))
    while not mon.abortRequested():
        if mon.waitForAbort(1):
            break
    srv.shutdown()
    srv.server_close()


if __name__ == '__main__':
    main()
