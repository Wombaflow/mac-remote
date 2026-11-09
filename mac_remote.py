#!/usr/bin/env python3
"""
Mac Remote — use your iPhone as a trackpad (and optional gyro air-mouse) for your Mac.
Minimal, zero dependencies (Python 3 + openssl, both ship with macOS dev tools).

Run on the Mac:   python3 mac_remote.py
Then open the printed address in Safari on the iPhone and enter the PIN.
"""
import base64, ctypes, hashlib, hmac, json, math, os, secrets, socket, ssl, struct
import subprocess, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get("PORT", "8080"))
TLS_PORT = int(os.environ.get("TLS_PORT", "8443"))
UK = os.environ.get("LANG", "").lower().startswith(("uk", "ru")) or os.environ.get("MR_LANG") == "uk"

def tr(en, uk): return uk if UK else en

MOCK = sys.platform != "darwin" or bool(os.environ.get("MOCK"))
def load_pin():
    if os.environ.get("PIN"): return os.environ["PIN"]
    d = os.path.expanduser("~/.mac-remote"); os.makedirs(d, exist_ok=True); p = d + "/pin"
    try:
        v = open(p).read().strip()
        if len(v) == 4 and v.isdigit(): return v
    except Exception: pass
    v = "".join(secrets.choice("0123456789") for _ in range(4))
    with open(p, "w") as f: f.write(v)
    os.chmod(p, 0o600)
    return v

PIN = load_pin()
WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ───────────────────────── macOS input (CoreGraphics via ctypes) ─────────────────────────
class MockMouse:
    def __init__(self): self.log = []
    def ensure_access(self): return True
    def move(self, dx, dy): print("move", dx, dy, flush=True)
    def click(self, b): print("click", b, flush=True)
    def button(self, down): print("drag", "down" if down else "up", flush=True)
    def scroll(self, dx, dy): print("scroll", dx, dy, flush=True)
    def key(self, code): print("key", code, flush=True)
    def text(self, s): print("text", s, flush=True)
    def center(self): print("center", flush=True)


class MacMouse:
    def __init__(self):
        class P(ctypes.Structure): _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]
        class Z(ctypes.Structure): _fields_ = [("w", ctypes.c_double), ("h", ctypes.c_double)]
        class R(ctypes.Structure): _fields_ = [("o", P), ("s", Z)]
        self.P = P
        L = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        C = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        v, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32
        L.CGEventCreate.restype = v; L.CGEventCreate.argtypes = [v]
        L.CGEventGetLocation.restype = P; L.CGEventGetLocation.argtypes = [v]
        L.CGEventCreateMouseEvent.restype = v; L.CGEventCreateMouseEvent.argtypes = [v, u32, P, u32]
        L.CGEventPost.argtypes = [u32, v]
        L.CGEventSetIntegerValueField.argtypes = [v, u32, ctypes.c_int64]
        L.CGEventCreateScrollWheelEvent2.restype = v
        L.CGEventCreateScrollWheelEvent2.argtypes = [v, u32, u32, i32, i32, i32]
        L.CGEventCreateKeyboardEvent.restype = v
        L.CGEventCreateKeyboardEvent.argtypes = [v, ctypes.c_uint16, ctypes.c_bool]
        L.CGEventKeyboardSetUnicodeString.argtypes = [v, ctypes.c_ulong, ctypes.POINTER(ctypes.c_uint16)]
        L.CGGetActiveDisplayList.argtypes = [u32, ctypes.POINTER(u32), ctypes.POINTER(u32)]
        L.CGDisplayBounds.restype = R; L.CGDisplayBounds.argtypes = [u32]
        C.CFRelease.argtypes = [v]
        self.L, self.C = L, C
        self.dragging = False
        self.last_click = (0.0, "", 0, 0.0, 0.0)  # time, button, count, x, y
        self.pos = None
        self.lock = threading.Lock()

    def ensure_access(self):
        try:
            self.L.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(self.L.AXIsProcessTrusted())
        except Exception:
            return True

    def _post(self, ev, tap=0):
        self.L.CGEventPost(tap, ev); self.C.CFRelease(ev)

    def _cur(self):
        e = self.L.CGEventCreate(None); p = self.L.CGEventGetLocation(e); self.C.CFRelease(e)
        return p.x, p.y

    def _bounds(self):
        n = ctypes.c_uint32(0); ids = (ctypes.c_uint32 * 16)()
        self.L.CGGetActiveDisplayList(16, ids, ctypes.byref(n))
        x0 = y0 = 1e9; x1 = y1 = -1e9
        for i in range(n.value):
            r = self.L.CGDisplayBounds(ids[i])
            x0 = min(x0, r.o.x); y0 = min(y0, r.o.y)
            x1 = max(x1, r.o.x + r.s.w); y1 = max(y1, r.o.y + r.s.h)
        if x1 < 0: x0, y0, x1, y1 = 0, 0, 1440, 900
        return x0, y0, x1 - 1, y1 - 1

    def _mouse(self, typ, x, y, button=0, clicks=None, dx=0, dy=0):
        ev = self.L.CGEventCreateMouseEvent(None, typ, self.P(x, y), button)
        if clicks: self.L.CGEventSetIntegerValueField(ev, 1, clicks)
        if dx or dy:
            self.L.CGEventSetIntegerValueField(ev, 4, int(dx)); self.L.CGEventSetIntegerValueField(ev, 5, int(dy))
        self._post(ev)

    def move(self, dx, dy):
        with self.lock:
            cx, cy = self._cur()
            if self.pos is None or math.hypot(self.pos[0] - cx, self.pos[1] - cy) > 3:
                self.pos = (cx, cy)  # the user moved the real mouse
            x0, y0, x1, y1 = self._bounds()
            nx = min(max(self.pos[0] + dx, x0), x1); ny = min(max(self.pos[1] + dy, y0), y1)
            self.pos = (nx, ny)
            self._mouse(6 if self.dragging else 5, nx, ny, 0, None, dx, dy)

    def _where(self):
        return self.pos if self.pos else self._cur()

    def click(self, b):
        with self.lock:
            x, y = self._cur(); self.pos = (x, y)
            now = time.time(); t, lb, n, lx, ly = self.last_click
            n = n + 1 if (now - t < 0.45 and lb == b and math.hypot(x - lx, y - ly) < 6 and n < 3) else 1
            self.last_click = (now, b, n, x, y)
            d, u, btn = (1, 2, 0) if b == "l" else (3, 4, 1)
            self._mouse(d, x, y, btn, n); self._mouse(u, x, y, btn, n)

    def button(self, down):
        with self.lock:
            x, y = self._cur(); self.pos = (x, y)
            self.dragging = bool(down)
            self._mouse(1 if down else 2, x, y, 0, 1)

    def scroll(self, dx, dy):
        ev = self.L.CGEventCreateScrollWheelEvent2(None, 0, 2, int(round(dy)), int(round(dx)), 0)
        self._post(ev)

    def key(self, code):
        for down in (True, False):
            self._post(self.L.CGEventCreateKeyboardEvent(None, code, down))

    def text(self, s):
        for ch in s[:200]:
            if ch == "\n": self.key(36); continue
            u = ch.encode("utf-16-le"); n = len(u) // 2
            arr = (ctypes.c_uint16 * n).from_buffer_copy(u)
            for down in (True, False):
                ev = self.L.CGEventCreateKeyboardEvent(None, 0, down)
                self.L.CGEventKeyboardSetUnicodeString(ev, n, arr)
                self._post(ev)

    def center(self):
        with self.lock:
            x0, y0, x1, y1 = self._bounds()
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            self.pos = (cx, cy)
            self._mouse(6 if self.dragging else 5, cx, cy)


mouse = MockMouse() if MOCK else MacMouse()

KEYS = {"left": 123, "right": 124, "down": 125, "up": 126, "space": 49, "esc": 53,
        "enter": 36, "back": 51, "tab": 48}

def handle(m):
    t = m.get("t")
    if t == "m": mouse.move(float(m["x"]), float(m["y"]))
    elif t == "s": mouse.scroll(float(m.get("x", 0)), float(m.get("y", 0)))
    elif t == "c": mouse.click("r" if m.get("b") == "r" else "l")
    elif t == "d": mouse.button(bool(m.get("s")))
    elif t == "k" and m.get("k") in KEYS: mouse.key(KEYS[m["k"]])
    elif t == "x" and isinstance(m.get("v"), str): mouse.text(m["v"])
    elif t == "r": mouse.center()

# ───────────────────────────────────── HTTP + WebSocket ─────────────────────────────────────
fails = []
fails_lock = threading.Lock()

def pin_ok(p):
    now = time.time()
    with fails_lock:
        fails[:] = [f for f in fails if now - f < 60]
        if len(fails) >= 5: return False
        ok = hmac.compare_digest(p.encode(), PIN.encode())
        if not ok: fails.append(now)
        return ok


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            return self.ws(parse_qs(u.query).get("pin", [""])[0])
        body = PAGE.replace("%TLSPORT%", str(TLS_PORT)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def ws(self, pin):
        if not pin_ok(pin):
            self.send_response(403); self.send_header("Content-Length", "0"); self.send_header("Connection", "close")
            self.end_headers(); self.close_connection = True; return
        key = self.headers.get("Sec-WebSocket-Key", "").encode()
        acc = base64.b64encode(hashlib.sha1(key + WS_GUID).digest()).decode()
        self.send_response(101)
        self.send_header("Upgrade", "websocket"); self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", acc); self.end_headers()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.close_connection = True
        print(tr("📱 iPhone connected", "📱 iPhone підключено"), flush=True)
        try:
            while True:
                h = self.rfile.read(2)
                if len(h) < 2: break
                op = h[0] & 15; ln = h[1] & 127
                if ln == 126: ln = struct.unpack(">H", self.rfile.read(2))[0]
                elif ln == 127: ln = struct.unpack(">Q", self.rfile.read(8))[0]
                mask = self.rfile.read(4) if h[1] & 128 else b""
                data = self.rfile.read(ln)
                if mask: data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
                if op == 8: break
                if op == 9: self.wfile.write(bytes([0x8A, len(data)]) + data); continue
                if op == 1:
                    try: handle(json.loads(data))
                    except Exception as e: print("err:", e, flush=True)
        except Exception:
            pass
        finally:
            if getattr(mouse, "dragging", False): mouse.button(False)
            print(tr("📱 disconnected", "📱 відключено"), flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass  # dropped connections are normal


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]; s.close(); return ip
    except Exception:
        return "127.0.0.1"


def all_ips():
    import re
    ips = []
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
        ips = [m for m in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out) if not m.startswith(("127.", "169.254."))]
    except Exception:
        pass
    ips = ips or [local_ip()]
    ips.sort(key=lambda i: (not i.startswith(("192.168.", "172.16.", "172.17.", "172.18.")), i))
    return ips


def make_cert(ips, host):
    d = os.path.expanduser("~/.mac-remote"); os.makedirs(d, exist_ok=True)
    tag = hashlib.md5(",".join(ips).encode()).hexdigest()[:8]
    cert, key, conf = f"{d}/cert-{tag}.pem", f"{d}/key-{tag}.pem", f"{d}/openssl-{tag}.cnf"
    if not (os.path.exists(cert) and os.path.exists(key)):
        san = ",".join("IP:" + i for i in ips) + f",DNS:{host}"
        with open(conf, "w") as f:
            f.write(f"[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=Mac Remote\n[v3]\nsubjectAltName={san}\n")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                        "-days", "825", "-config", conf], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chmod(key, 0o600)
    return cert, key


def main():
    ips = all_ips()
    srv = Server(("0.0.0.0", PORT), H)
    tls_ok = False
    try:
        cert, key = make_cert(ips, socket.gethostname())
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(cert, key)
        tsrv = Server(("0.0.0.0", TLS_PORT), H)
        tsrv.socket = ctx.wrap_socket(tsrv.socket, server_side=True)
        threading.Thread(target=tsrv.serve_forever, daemon=True).start(); tls_ok = True
    except Exception as e:
        print(tr("  (HTTPS not started, gyro unavailable:", "  (HTTPS не запущено, гіроскоп недоступний:"), e, ")")
    if not MOCK:
        try: subprocess.Popen(["caffeinate", "-di", "-w", str(os.getpid())])
        except Exception: pass
    print("\n  MAC REMOTE\n")
    print(tr("  Open in Safari on your iPhone (same Wi-Fi, usually 192.168.x.x):", "  Відкрий у Safari на iPhone (Wi-Fi зазвичай 192.168.x.x):"))
    for i in ips: print(f"     http://{i}:{PORT}")
    print(f"\n  PIN: {PIN}   " + tr("(permanent, stored in ~/.mac-remote/pin)", "(постійний)"))
    if tls_ok: print(tr(f"  Gyro (optional): the ◎ button switches to https://…:{TLS_PORT} (iOS requires HTTPS for motion sensors)\n", f"  Гіроскоп (опційно): кнопка ◎ в додатку сама перейде на https://…:{TLS_PORT}\n"))
    if not mouse.ensure_access():
        print(tr("  ⚠️  Grant access: System Settings → Privacy & Security → Accessibility →", "  ⚠️  Дай доступ: Системні налаштування → Конфіденційність і безпека →"))
        print(tr("     enable Terminal (or iTerm), then restart this script.\n", "     Доступність (Accessibility) → увімкни Terminal, потім перезапусти скрипт.\n"))
    print(tr("  Tip: Safari → Share → Add to Home Screen gives a fullscreen app (use the http:// address).", "  Порада: у Safari → Поділитися → «На початковий екран» — буде повноекранний додаток."))
    print(tr("  Ctrl+C to stop.\n", "  Ctrl+C — зупинити.\n"), flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: print(tr("\nBye!", "\nБувай!"))


PAGE = r"""<!doctype html>
<html lang="uk"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black">
<meta name="apple-mobile-web-app-title" content="Mac"><meta name="theme-color" content="#000">
<title>Mac</title>
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none}
html,body{margin:0;height:100%;background:#000;color:#fff;font:16px -apple-system,system-ui,sans-serif;overflow:hidden;overscroll-behavior:none;position:fixed;inset:0;width:100%}
#pad{position:fixed;inset:0;touch-action:none;background:#000;transition:background .12s}
#pad.drag{background:#14102b}
#fx{position:fixed;inset:0;width:100%;height:100%;pointer-events:none}
.edge{--c:#2b2b35;position:fixed;top:24%;bottom:24%;width:12px;pointer-events:none;background-image:linear-gradient(var(--c) 2px,transparent 2px);background-size:100% 14px;background-repeat:repeat-y;-webkit-mask-image:linear-gradient(transparent,#000 28%,#000 72%,transparent);mask-image:linear-gradient(transparent,#000 28%,#000 72%,transparent);transition:--c .2s,opacity .2s;opacity:.9}
.edge.act{--c:#9a7cff}
#el{left:6px}#er{right:6px}
#hint0{position:fixed;left:0;right:0;top:44%;text-align:center;color:#fff;opacity:.3;font-size:13px;letter-spacing:.03em;line-height:2;pointer-events:none;transition:opacity .7s}
#hint0.off{opacity:0}
#dot{position:fixed;top:max(env(safe-area-inset-top),14px);left:16px;width:8px;height:8px;border-radius:50%;background:#ff453a;opacity:.9;pointer-events:none}
#dot.on{background:#30d158;opacity:.45}
.fab{backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);position:fixed;bottom:max(env(safe-area-inset-bottom),14px);width:46px;height:46px;border-radius:50%;border:1px solid #26262e;background:rgba(26,26,32,.78);color:#8e8e99;font-size:20px;display:flex;align-items:center;justify-content:center;padding:0}
#kb{right:14px}#gy{right:70px}#gy.on{background:#7c5cff;color:#fff}
.chips{display:flex;gap:8px;align-items:center}.chip{flex:1;border:0;border-radius:12px;padding:11px 0;background:#1c1c21;color:#8e8e99;font:inherit;font-size:14px}.chip.on{background:#2b2350;color:#fff}
.lbl{font-size:12px;color:#666;margin:-4px 0 -6px}
#sheet{position:fixed;left:0;right:0;bottom:0;padding:12px 14px max(env(safe-area-inset-bottom),12px);background:#111114;border-top:1px solid #222;display:none;gap:12px;flex-direction:column}
#sheet.open{display:flex}
#txt{width:100%;font:inherit;color:#fff;background:#1c1c21;border:0;border-radius:12px;padding:13px 14px;outline:0;-webkit-user-select:text;user-select:text}
#spd{width:100%;accent-color:#7c5cff}
#login{position:fixed;inset:0;background:#000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px}
#pin{width:200px;font:600 34px -apple-system,system-ui,sans-serif;letter-spacing:.45em;text-indent:.45em;text-align:center;color:#fff;background:transparent;border:0;border-bottom:2px solid #333;outline:0;padding:8px;-webkit-user-select:text;user-select:text}
#pin.bad{border-color:#ff453a}
#hint{color:#666;font-size:13px;min-height:1em}
</style></head><body>
<div id="pad"></div><div class="edge" id="el"></div><div class="edge" id="er"></div><canvas id="fx"></canvas><div id="hint0" data-t="hint"></div><div id="dot"></div>
<button id="gy" class="fab" aria-label="gyro">◎</button><button id="kb" class="fab" aria-label="keyboard">⌨︎</button>
<div id="sheet"><input id="txt" type="text" data-p="text" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false" enterkeyhint="send"><div class="lbl" data-t="spd"></div><input id="spd" type="range" min="0.4" max="3" step="0.1">
<div class="lbl" data-t="gspd"></div><input id="gs" type="range" min="0.3" max="3" step="0.1">
<div class="chips"><button class="chip" id="ix" data-t="ix"></button><button class="chip" id="iy" data-t="iy"></button><button class="chip" id="rc" data-t="rc"></button></div></div>
<div id="login"><input id="pin" type="tel" inputmode="numeric" maxlength="4" placeholder="····" autocomplete="off"><div id="hint"></div></div>
<script>
const $=s=>document.querySelector(s);
const I18N={en:{hint:'Swipe — move<br>Tap — click · Hold — drag<br>Edge — scroll',text:'Type to Mac',spd:'Finger speed',gspd:'Gyro speed',ix:'Invert ↔︎',iy:'Invert ↕︎',rc:'Right click',bad:'Wrong PIN or Mac unreachable',deny:'Safari denied motion access: Settings → Apps → Safari → Motion & Orientation Access'},
 uk:{hint:'Проведи — курсор<br>Тап — клік · Утримуй — перетягти<br>Край — скрол',text:'Текст на Mac',spd:'Швидкість пальця',gspd:'Швидкість гіро',ix:'Інверт ↔︎',iy:'Інверт ↕︎',rc:'ПКМ',bad:'Невірний PIN або Mac недоступний',deny:'Safari не дав доступ до руху: Налаштування → Додатки → Safari → Доступ до руху й орієнтації'}};
const LNG=/^(uk|ru)/i.test(navigator.language||'')?'uk':'en',TX=I18N[LNG];
document.documentElement.lang=LNG;
document.querySelectorAll('[data-t]').forEach(e=>e.innerHTML=TX[e.dataset.t]);
document.querySelectorAll('[data-p]').forEach(e=>e.placeholder=TX[e.dataset.p]);
let spd=1.2;try{spd=+localStorage.getItem('spd')||1.2}catch(e){}
$('#spd').value=spd;$('#spd').oninput=e=>{spd=+e.target.value;try{localStorage.setItem('spd',spd)}catch(_){}};
let ax=0,ay=0;
let G={on:false,pend:false,ix:false,iy:false,gs:1};
try{Object.assign(G,JSON.parse(localStorage.getItem('g')||'{}'))}catch(e){}
const saveG=()=>{try{localStorage.setItem('g',JSON.stringify({on:G.on,ix:G.ix,iy:G.iy,gs:G.gs}))}catch(e){}};
G.pend=false;
if(/[?&]g=1/.test(location.search)){G.on=true;saveG()}
if(G.on&&window.isSecureContext)G.pend=true;else if(G.on&&!window.isSecureContext){G.on=false}
function paintG(){$('#gy').classList.toggle('on',G.on);$('#ix').classList.toggle('on',G.ix);$('#iy').classList.toggle('on',G.iy);$('#gs').value=G.gs}
paintG();
$('#gs').oninput=e=>{G.gs=+e.target.value;saveG()};
$('#ix').onclick=()=>{G.ix=!G.ix;paintG();saveG()};$('#iy').onclick=()=>{G.iy=!G.iy;paintG();saveG()};
$('#rc').onclick=()=>send({t:'c',b:'r'});
let motionBound=false;
async function askMotion(){
  try{
    const RP=(typeof DeviceOrientationEvent!=='undefined'&&DeviceOrientationEvent.requestPermission)||(typeof DeviceMotionEvent!=='undefined'&&DeviceMotionEvent.requestPermission);
    if(typeof RP==='function'){
      const r=await RP.call(typeof DeviceOrientationEvent!=='undefined'&&DeviceOrientationEvent.requestPermission?DeviceOrientationEvent:DeviceMotionEvent);
      if(r!=='granted'){G.on=false;G.pend=false;saveG();paintG();alert(TX.deny);return}
    }
    if(!motionBound){addEventListener('deviceorientation',onOri);motionBound=true}
    G.pend=false;
  }catch(e){G.on=false;G.pend=false;paintG()}
}
$('#gy').onclick=()=>{
  if(G.on){G.on=false;G.pend=false;saveG();paintG();return}
  if(!window.isSecureContext){location.href='https://'+location.hostname+':%TLSPORT%/?g=1';return}
  G.on=true;saveG();paintG();askMotion();
};
// Relative motion from tilt angles: gamma (left/right) -> X, beta (towards/away) -> Y
let lb=null,lg=null,fx=0,fy=0;
function onOri(e){
  if(e.beta==null||e.gamma==null)return;
  if(!G.on||!ok){lb=null;fx=fy=0;return}
  if(lb===null){lb=e.beta;lg=e.gamma;return}
  const db=e.beta-lb,dg=e.gamma-lg;lb=e.beta;lg=e.gamma;
  if(Math.abs(db)>40||Math.abs(dg)>40)return;          // axis jump — ignore
  fx=fx*.35+dg*.65;fy=fy*.35+db*.65;                     // light smoothing
  const m=Math.hypot(fx,fy);if(m<0.06){return}           // dead zone against hand jitter
  const k=22*G.gs*(1+Math.min(m*0.35,1.5));
  ax+=fx*k*(G.ix?-1:1);ay+=fy*k*(G.iy?-1:1);queue();
}
let ws=null,ok=false,pin='';
const send=o=>{try{fxHook(o)}catch(e){}if(ws&&ws.readyState===1)ws.send(JSON.stringify(o))};
function connect(){
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws?pin='+encodeURIComponent(pin));
  ws.onopen=()=>{ok=true;$('#dot').classList.add('on');$('#login').style.display='none';try{localStorage.setItem('pin',pin)}catch(e){}};
  ws.onclose=()=>{$('#dot').classList.remove('on');
    if(ok){setTimeout(connect,700)}
    else{try{localStorage.removeItem('pin')}catch(e){}$('#login').style.display='flex';$('#pin').classList.add('bad');$('#hint').textContent=TX.bad;$('#pin').value=''}};
}
$('#pin').addEventListener('input',e=>{$('#pin').classList.remove('bad');$('#hint').textContent='';
  if(e.target.value.length===4){pin=e.target.value;connect()}});
try{const p=localStorage.getItem('pin');if(p){pin=p;connect()}else $('#pin').focus()}catch(e){}
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&ok&&(!ws||ws.readyState>1))connect()});

// ── gestures ──
const pad=$('#pad');let T=null,sx=0,sy=0,raf=0,lt=0;
const ctr=t=>{let x=0,y=0;for(const p of t){x+=p.clientX;y+=p.clientY}return{x:x/t.length,y:y/t.length}};
function flush(){raf=0;
  if(ax||ay){send({t:'m',x:+ax.toFixed(2),y:+ay.toFixed(2)});ax=ay=0}
  if(sx||sy){const ix=Math.trunc(sx),iy=Math.trunc(sy);if(ix||iy){send({t:'s',x:ix,y:iy});sx-=ix;sy-=iy}}}
const queue=()=>{if(!raf)raf=requestAnimationFrame(flush)};
pad.addEventListener('touchstart',e=>{e.preventDefault();closeSheet();const c=ctr(e.touches);
  if(!T){T={t0:Date.now(),x:c.x,y:c.y,lx:c.x,ly:c.y,moved:false,max:e.touches.length,drag:false,edge:e.touches.length===1&&(c.x>innerWidth-56||c.x<56)&&c.y<innerHeight-130};lt=performance.now();
    T.timer=setTimeout(()=>{if(T&&!T.moved&&T.max===1&&!T.edge){T.drag=true;send({t:'d',s:1});pad.classList.add('drag');if(navigator.vibrate)navigator.vibrate(8)}},380)}
  else{T.max=Math.max(T.max,e.touches.length);clearTimeout(T.timer);T.lx=c.x;T.ly=c.y}},{passive:false});
pad.addEventListener('touchmove',e=>{e.preventDefault();if(!T)return;const c=ctr(e.touches);
  const dx=c.x-T.lx,dy=c.y-T.ly;T.lx=c.x;T.ly=c.y;
  const now=performance.now(),dt=Math.max(now-lt,1);lt=now;
  if(!T.moved&&Math.hypot(c.x-T.x,c.y-T.y)>7){T.moved=true;clearTimeout(T.timer)}
  if(!T.moved&&!T.drag)return;
  if(e.touches.length>=2||T.edge||(G.on&&!T.drag)){const v=Math.hypot(dx,dy)/dt;const k=e.touches.length>=2?1.6:1.6+Math.min(v,2);sx+=dx*k;sy+=dy*k}
  else if(G.on){}
  else{const v=Math.hypot(dx,dy)/dt;const g=spd*(1.1+Math.min(v*0.9,2.6));ax+=dx*g;ay+=dy*g}
  queue()},{passive:false});
const endT=e=>{e.preventDefault();if(G.on&&G.pend)askMotion();if(!T)return;
  if(e.touches.length>0){const c=ctr(e.touches);T.lx=c.x;T.ly=c.y;return}
  clearTimeout(T.timer);flush();
  if(T.drag)send({t:'d',s:0});
  else if(!T.moved&&Date.now()-T.t0<300)send({t:'c',b:T.max>=2?'r':'l'});
  T=null;pad.classList.remove('drag')};
pad.addEventListener('touchend',endT,{passive:false});pad.addEventListener('touchcancel',endT,{passive:false});


// ── visual feedback ──
const cv=$('#fx'),cx=cv.getContext('2d');let W=0,H=0;
function fit(){const d=Math.min(devicePixelRatio||1,3);W=innerWidth;H=innerHeight;cv.width=W*d;cv.height=H*d;cx.setTransform(d,0,0,d,0,0)}
fit();addEventListener('resize',fit);
const FX={pts:new Map(),trail:[],rip:[],drag:false,last:{x:0,y:0},run:false,tick:0,sc:0};
function frame(now){
  cx.clearRect(0,0,W,H);
  const tr=FX.trail;while(tr.length&&now-tr[0].t>340)tr.shift();
  const col=FX.drag?'167,139,250':'124,92,255';
  for(let i=1;i<tr.length;i++){const a=tr[i-1],b=tr[i],al=Math.max(0,1-(now-b.t)/340);
    cx.strokeStyle='rgba('+col+','+(al*.6)+')';cx.lineWidth=2+al*7;cx.lineCap='round';cx.beginPath();cx.moveTo(a.x,a.y);cx.lineTo(b.x,b.y);cx.stroke()}
  for(const p of FX.pts.values()){const r=FX.drag?70:50;const g=cx.createRadialGradient(p.x,p.y,0,p.x,p.y,r);
    g.addColorStop(0,'rgba('+col+','+(FX.drag?.5:.3)+')');g.addColorStop(1,'rgba('+col+',0)');
    cx.fillStyle=g;cx.beginPath();cx.arc(p.x,p.y,r,0,7);cx.fill();
    if(FX.drag){cx.strokeStyle='rgba(190,170,255,.55)';cx.lineWidth=2;cx.beginPath();cx.arc(p.x,p.y,26+Math.sin(now/130)*3,0,7);cx.stroke()}}
  FX.rip=FX.rip.filter(r=>now-r.t<430);
  for(const r of FX.rip){const k=(now-r.t)/430,e=1-Math.pow(1-k,3);cx.strokeStyle='rgba('+r.c+','+((1-k)*.85)+')';cx.lineWidth=2.5*(1-k)+.5;cx.beginPath();cx.arc(r.x,r.y,8+e*r.r,0,7);cx.stroke()}
  if(FX.pts.size||tr.length||FX.rip.length)requestAnimationFrame(frame);else FX.run=false;
}
const kick=()=>{if(!FX.run){FX.run=true;requestAnimationFrame(frame)}};
const nearEdge=x=>x>innerWidth-56||x<56;
function upd(e,move){
  FX.pts.clear();for(const t of e.touches)FX.pts.set(t.identifier,{x:t.clientX,y:t.clientY});
  const t0=e.touches[0];
  if(t0){FX.last={x:t0.clientX,y:t0.clientY};
    if(move&&e.touches.length===1&&!G.on&&!nearEdge(t0.clientX))FX.trail.push({x:t0.clientX,y:t0.clientY,t:performance.now()})}
  kick()}
pad.addEventListener('touchstart',e=>{upd(e,false);
  const h=$('#hint0');if(!h.classList.contains('off')){h.classList.add('off');try{localStorage.setItem('seen','1')}catch(_){}}
  if(e.touches.length===1&&nearEdge(e.touches[0].clientX))(e.touches[0].clientX<56?$('#el'):$('#er')).classList.add('act')},{passive:true});
pad.addEventListener('touchmove',e=>upd(e,true),{passive:true});
const fxEnd=e=>{upd(e,false);if(!e.touches.length){FX.drag=false;$('#el').classList.remove('act');$('#er').classList.remove('act')}};
pad.addEventListener('touchend',fxEnd,{passive:true});pad.addEventListener('touchcancel',fxEnd,{passive:true});
try{if(localStorage.getItem('seen'))$('#hint0').classList.add('off')}catch(_){}
let actT=0;
function fxHook(o){
  const now=performance.now();
  if(o.t==='c'){const r=o.b==='r';FX.rip.push({x:FX.last.x,y:FX.last.y,t:now,r:r?64:46,c:r?'255,159,10':'190,170,255'});kick()}
  else if(o.t==='d'){FX.drag=!!o.s;if(o.s)FX.rip.push({x:FX.last.x,y:FX.last.y,t:now,r:80,c:'167,139,250'});kick()}
  else if(o.t==='s'){FX.tick+=o.y;const p=(FX.tick%14)+'px';
    for(const id of ['#el','#er']){const el=$(id);el.style.backgroundPositionY=p;el.classList.add('act')}
    clearTimeout(actT);actT=setTimeout(()=>{if(!T||!T.edge){$('#el').classList.remove('act');$('#er').classList.remove('act')}},220)}
}

// ── keyboard ──
const sheet=$('#sheet'),txt=$('#txt');
function closeSheet(){if(sheet.classList.contains('open')){sheet.classList.remove('open');txt.blur()}}
$('#kb').onclick=()=>{if(sheet.classList.contains('open'))closeSheet();else{sheet.classList.add('open');txt.focus()}};
txt.addEventListener('input',e=>{const v=txt.value;if(v){send({t:'x',v});txt.value=''}});
txt.addEventListener('keydown',e=>{if(e.key==='Backspace'&&!txt.value)send({t:'k',k:'back'});else if(e.key==='Enter'){e.preventDefault();send({t:'k',k:'enter'})}});
</script></body></html>
"""

if __name__ == "__main__":
    main()
