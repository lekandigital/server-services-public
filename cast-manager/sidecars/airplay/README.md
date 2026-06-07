# Cast Manager AirPlay Sidecar

This sidecar uses `pyatv` to discover AirPlay receivers, pair when required, and ask a receiver to play the HTTP/HLS URL prepared by Cast Manager.

```bash
cd sidecars/airplay
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn airplay_sidecar:app --host 127.0.0.1 --port 8765
```

Cast Manager talks to it with:

```bash
AIRPLAY_SIDECAR_URL=http://127.0.0.1:8765
```

Limitations:

- DRM-protected streams are not supported.
- Screen mirroring is not part of sender mode.
- Pairing and commands such as seek/status vary by receiver model and tvOS version.
