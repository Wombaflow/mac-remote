# Contributing

Thanks for your interest in Mac Remote! The project is intentionally small: **one Python file, zero dependencies**.
Please keep it that way — contributions that add third-party packages or a build step are unlikely to be accepted.

## Development setup

You can work on the server and the web UI on any OS. On non-macOS systems the server logs input events instead of
injecting them:

```bash
MOCK=1 PORT=8080 TLS_PORT=8443 python3 mac_remote.py
```

Open `http://localhost:8080` in a browser (use device emulation for touch). Real cursor control needs a Mac and the
Accessibility permission for your terminal.

## Guidelines

- Python 3.8+ compatible, standard library only.
- The web UI lives in the `PAGE` string inside `mac_remote.py`; keep it dependency-free (no CDN, no frameworks).
- Keep the UI minimal and fast; test on a real iPhone when you touch gestures or the gyro code.
- Add user-visible strings for both languages (`I18N` in the page and `tr()` in the server).
- Update `CHANGELOG.md` under an *Unreleased* heading and the README if behaviour changes.
- Run `python3 -m py_compile mac_remote.py` before opening a pull request; CI runs the same check plus a smoke test.

## Reporting bugs

Open an issue with your macOS version, iPhone/iOS version, what you expected and what happened.
For security problems please follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
