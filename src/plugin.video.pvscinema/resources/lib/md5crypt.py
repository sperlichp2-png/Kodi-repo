# Čistá Python implementace md5crypt ($1$), kterou Webshare používá pro hash hesla.
import hashlib

_ITOA64 = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


def _to64(v, n):
    out = ''
    while n > 0:
        out += _ITOA64[v & 0x3f]
        v >>= 6
        n -= 1
    return out


def md5crypt(password, salt, magic='$1$'):
    pw = password.encode('utf-8') if isinstance(password, str) else password
    if salt.startswith(magic):
        salt = salt[len(magic):]
    salt = salt.split('$', 1)[0][:8]
    s = salt.encode('utf-8')

    ctx = pw + magic.encode() + s
    final = hashlib.md5(pw + s + pw).digest()
    pl = len(pw)
    while pl > 0:
        ctx += final[:min(16, pl)]
        pl -= 16
    i = len(pw)
    while i:
        ctx += b'\x00' if i & 1 else pw[:1]
        i >>= 1
    final = hashlib.md5(ctx).digest()

    for i in range(1000):
        c = pw if i & 1 else final
        if i % 3:
            c += s
        if i % 7:
            c += pw
        c += final if i & 1 else pw
        final = hashlib.md5(c).digest()

    f = final
    out = (_to64((f[0] << 16) | (f[6] << 8) | f[12], 4) +
           _to64((f[1] << 16) | (f[7] << 8) | f[13], 4) +
           _to64((f[2] << 16) | (f[8] << 8) | f[14], 4) +
           _to64((f[3] << 16) | (f[9] << 8) | f[15], 4) +
           _to64((f[4] << 16) | (f[10] << 8) | f[5], 4) +
           _to64(f[11], 2))
    return magic + salt + '$' + out
