# Mac Remote

Turn your iPhone into a minimal trackpad for your Mac — with an optional **gyro air-mouse** mode.
No app to install on the phone, no third-party packages on the Mac: one Python file.

- **Trackpad gestures** on a pure-black, distraction-free screen (glow, trail and ripple feedback)
- **Optional gyro mode** — tilt the phone to move the cursor, scroll with your thumb, one-handed
- **Edge scroll** — drag a finger along either screen edge to scroll without two fingers
- **Typing** — a text field that types straight into whatever is focused on the Mac
- Works over your local Wi-Fi, protected by a 4-digit PIN

## Quick start

On the Mac (Python 3 ships with Xcode Command Line Tools; macOS will offer to install them on first run):

```bash
python3 mac_remote.py
```

1. Allow it to control the Mac: **System Settings → Privacy & Security → Accessibility** → enable your terminal app (Terminal / iTerm), then restart the script.
2. On the iPhone (same Wi-Fi), open the `http://<mac-ip>:8080` address printed in the terminal.
3. Enter the PIN printed in the terminal. It is generated once and stored in `~/.mac-remote/pin`; the phone remembers it.

Tip: in Safari tap **Share → Add to Home Screen** to get a fullscreen app without browser bars (use the `http://` address).

## Gestures

| Gesture | Action |
| --- | --- |
| One finger swipe | Move the cursor (faster swipe = further) |
| Tap | Left click (double tap = double click) |
| Touch and hold ~0.4 s, then move | Drag |
| Two-finger swipe | Scroll |
| Two-finger tap | Right click |
| One finger along the left/right screen edge | Scroll |
| ⌨︎ button | Text input, finger/gyro speed, invert axes, right-click button |

## Gyro mode (optional)

Tap **◎** to toggle it. iOS only exposes motion sensors on **HTTPS**, so the button switches to `https://<mac-ip>:8443`.
The server creates a self-signed certificate in `~/.mac-remote/` on first run; confirm Safari's certificate warning once
(*Show Details → visit this website*), enter the PIN again and allow *Motion & Orientation Access*.

With gyro on, tilt the phone left/right to move horizontally and tilt it away/towards you to move vertically.
One finger now scrolls, tap clicks, and hold-and-tilt drags. If an axis feels reversed, use **Invert** in the ⌨︎ panel;
the gyro speed slider is there too. Hold the phone at a natural reading angle (not perfectly vertical) for best results.

## How it works

`mac_remote.py` runs a tiny HTTP(S) + WebSocket server (standard library only). The page served to the phone sends
gesture and sensor events over a WebSocket, and the Mac posts them as real mouse/keyboard events through
CoreGraphics (via `ctypes`). `caffeinate` keeps the Mac awake while the script runs.

## Security notes

- Anyone on your Wi-Fi who knows the PIN can control your Mac. Use it on networks you trust, not on public Wi-Fi.
- The PIN is checked on every connection; after 5 wrong attempts further attempts are blocked for 60 seconds.
- The plain `http://` address is unencrypted (the PIN is sent in clear text on your LAN). The `https://` address is
  encrypted but uses a self-signed certificate.
- Stop the script (Ctrl+C) when you are not using it.

## Options

Environment variables: `PORT` (HTTP, default `8080`), `TLS_PORT` (HTTPS, default `8443`), `PIN` (override the stored PIN),
`MR_LANG=uk` (Ukrainian terminal messages; the web UI follows the phone language).

## Troubleshooting

- **Nothing happens when I touch the screen** — Accessibility permission is missing (see Quick start, step 1). Restart the script after granting it.
- **Page doesn't load** — the phone and Mac must be on the same Wi-Fi (guest networks often isolate devices). The script prints every IP of the Mac; Wi-Fi is usually `192.168.x.x`. Type the full `http://…:8080` address.
- **Gyro button does nothing / asks again** — allow motion access: *Settings → Apps → Safari → Motion & Orientation Access*; Safari may ask again after a page reload.
- **Wrong PIN message right after a restart** — wait a minute (rate limiting) and re-enter; the PIN itself is stored in `~/.mac-remote/pin`.

## License

MIT
