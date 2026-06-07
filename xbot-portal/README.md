# X-Bot Portal (:8009)

Systemd launcher for the LAN dashboard in the checked-out X-Bot project.

This service intentionally does not duplicate the X-Bot app code. It starts:

```bash
/home/<user>/x-bot/.venv/bin/python -m uvicorn dashboard.api:app --host 0.0.0.0 --port 8009
```

The X-Bot checkout should exist at `/home/<user>/x-bot`, with its `.venv` already built.
