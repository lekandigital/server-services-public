import asyncio
import json
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

try:
    import pyatv
    from pyatv.const import Protocol
except Exception as exc:  # pragma: no cover - exercised on systems without pyatv
    pyatv = None
    Protocol = None
    IMPORT_ERROR = str(exc)
else:
    IMPORT_ERROR = ""


app = FastAPI(title="Cast Manager AirPlay Sidecar")
CONFIG_CACHE: Dict[str, Any] = {}
PAIRINGS: Dict[str, Any] = {}


class DeviceRequest(BaseModel):
    deviceId: Optional[str] = None
    host: Optional[str] = None
    credentials: Optional[Any] = None


class PlayRequest(DeviceRequest):
    url: str
    title: Optional[str] = None


class PairFinishRequest(DeviceRequest):
    pin: str


class SeekRequest(DeviceRequest):
    seconds: float


def _require_pyatv() -> None:
    if pyatv is None:
        raise HTTPException(status_code=503, detail=f"pyatv is not installed or failed to import: {IMPORT_ERROR}")


def _proto_name(protocol: Any) -> str:
    return getattr(protocol, "name", str(protocol)).lower()


def _protocol_from_name(name: str) -> Any:
    normalized = str(name).lower()
    for proto in (Protocol.AirPlay, Protocol.MRP, Protocol.RAOP, Protocol.Companion, Protocol.DMAP):
        if _proto_name(proto) == normalized:
            return proto
    return None


def _device_id(conf: Any) -> str:
    return str(getattr(conf, "identifier", None) or getattr(conf, "address", "") or getattr(conf, "name", ""))


def _credentials_dict(conf: Any) -> Dict[str, str]:
    creds: Dict[str, str] = {}
    for service in getattr(conf, "services", []):
        value = getattr(service, "credentials", None)
        if value:
            creds[_proto_name(service.protocol)] = str(value)
    return creds


def _apply_credentials(conf: Any, credentials: Optional[Any]) -> None:
    if not credentials:
        return
    if isinstance(credentials, str):
        try:
            credentials = json.loads(credentials)
        except json.JSONDecodeError:
            credentials = {"airplay": credentials}
    if not isinstance(credentials, dict):
        return
    for service in getattr(conf, "services", []):
        value = credentials.get(_proto_name(service.protocol))
        if value:
            service.credentials = value


def _device_to_json(conf: Any) -> Dict[str, Any]:
    services = list(getattr(conf, "services", []))
    protocols = [_proto_name(s.protocol) for s in services]
    paired = any(getattr(s, "credentials", None) for s in services)
    return {
        "id": _device_id(conf),
        "name": getattr(conf, "name", None) or _device_id(conf),
        "host": str(getattr(conf, "address", "") or ""),
        "model": str(getattr(getattr(conf, "device_info", None), "model", "") or ""),
        "paired": paired,
        "protocols": protocols,
    }


async def _scan(device_id: Optional[str] = None, host: Optional[str] = None, timeout: int = 5) -> List[Any]:
    _require_pyatv()
    loop = asyncio.get_running_loop()
    kwargs: Dict[str, Any] = {"timeout": timeout}
    if host:
        kwargs["hosts"] = [host]
    if device_id and not host:
        kwargs["identifier"] = device_id
    try:
        kwargs["protocol"] = {Protocol.AirPlay, Protocol.MRP, Protocol.RAOP, Protocol.Companion}
    except Exception:
        pass
    devices = await asyncio.wait_for(pyatv.scan(loop, **kwargs), timeout=timeout + 3)
    for conf in devices:
        CONFIG_CACHE[_device_id(conf)] = conf
    return devices


async def _find_config(device_id: Optional[str], host: Optional[str], credentials: Optional[Any] = None) -> Any:
    conf = CONFIG_CACHE.get(device_id or "")
    if conf is None:
        devices = await _scan(device_id=device_id, host=host, timeout=6)
        if device_id:
            conf = next((d for d in devices if _device_id(d) == device_id), None)
        if conf is None and host:
            conf = next((d for d in devices if str(getattr(d, "address", "")) == host), None)
        if conf is None and devices:
            conf = devices[0]
    if conf is None:
        raise HTTPException(status_code=404, detail="AirPlay device was not found")
    _apply_credentials(conf, credentials)
    return conf


async def _connect(req: DeviceRequest) -> Any:
    conf = await _find_config(req.deviceId, req.host, req.credentials)
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(pyatv.connect(conf, loop), timeout=12)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": pyatv is not None, "pyatv": pyatv is not None, "error": IMPORT_ERROR or None}


@app.post("/scan")
async def scan() -> Dict[str, Any]:
    devices = await _scan(timeout=6)
    return {"devices": [_device_to_json(conf) for conf in devices]}


@app.post("/pair/start")
async def pair_start(req: DeviceRequest) -> Dict[str, Any]:
    _require_pyatv()
    conf = await _find_config(req.deviceId, req.host)
    service = conf.get_service(Protocol.AirPlay)
    if service is None:
        raise HTTPException(status_code=400, detail="Device does not advertise AirPlay pairing")
    pairing = await pyatv.pair(conf, Protocol.AirPlay, asyncio.get_running_loop())
    await asyncio.wait_for(pairing.begin(), timeout=12)
    key = _device_id(conf)
    PAIRINGS[key] = pairing
    return {
        "success": True,
        "deviceId": key,
        "deviceProvidesPin": bool(pairing.device_provides_pin),
        "message": "Enter the PIN shown by the AirPlay receiver." if pairing.device_provides_pin else "Enter the displayed PIN on the AirPlay receiver.",
    }


@app.post("/pair/finish")
async def pair_finish(req: PairFinishRequest) -> Dict[str, Any]:
    key = req.deviceId or ""
    pairing = PAIRINGS.get(key)
    if pairing is None:
        raise HTTPException(status_code=404, detail="No pairing session for this device")
    try:
        pairing.pin(int(req.pin))
        await asyncio.wait_for(pairing.finish(), timeout=15)
        if not pairing.has_paired:
            raise HTTPException(status_code=400, detail="Pairing did not complete")
        credentials = _credentials_dict(pairing.service.config if hasattr(pairing.service, "config") else CONFIG_CACHE.get(key))
        if not credentials and getattr(pairing, "service", None) is not None:
            credentials[_proto_name(pairing.service.protocol)] = str(pairing.service.credentials)
        return {"success": True, "deviceId": key, "credentials": credentials}
    finally:
        try:
            await pairing.close()
        finally:
            PAIRINGS.pop(key, None)


@app.post("/play")
async def play(req: PlayRequest) -> Dict[str, Any]:
    atv = await _connect(req)
    try:
        await asyncio.wait_for(atv.stream.play_url(req.url), timeout=20)
        return {"success": True, "state": "playing"}
    finally:
        atv.close()


@app.post("/pause")
async def pause(req: DeviceRequest) -> Dict[str, Any]:
    atv = await _connect(req)
    try:
        await asyncio.wait_for(atv.remote_control.pause(), timeout=8)
        return {"success": True, "state": "paused"}
    finally:
        atv.close()


@app.post("/resume")
async def resume(req: DeviceRequest) -> Dict[str, Any]:
    atv = await _connect(req)
    try:
        await asyncio.wait_for(atv.remote_control.play(), timeout=8)
        return {"success": True, "state": "playing"}
    finally:
        atv.close()


@app.post("/stop")
async def stop(req: DeviceRequest) -> Dict[str, Any]:
    atv = await _connect(req)
    try:
        await asyncio.wait_for(atv.remote_control.stop(), timeout=8)
        return {"success": True, "state": "idle"}
    finally:
        atv.close()


@app.post("/seek")
async def seek(req: SeekRequest) -> Dict[str, Any]:
    atv = await _connect(req)
    try:
        if not hasattr(atv.remote_control, "set_position"):
            raise HTTPException(status_code=501, detail="This AirPlay device does not expose seek control")
        await asyncio.wait_for(atv.remote_control.set_position(req.seconds), timeout=8)
        return {"success": True, "state": "playing", "currentTime": req.seconds}
    finally:
        atv.close()


@app.post("/status")
async def status(req: DeviceRequest) -> Dict[str, Any]:
    atv = await _connect(req)
    try:
        playing = await asyncio.wait_for(atv.metadata.playing(), timeout=8)
        state = str(getattr(playing, "device_state", "") or "unknown").lower()
        return {
            "success": True,
            "state": "paused" if "pause" in state else "playing" if "play" in state else "unknown",
            "title": getattr(playing, "title", None) or "",
            "currentTime": float(getattr(playing, "position", 0) or 0),
            "duration": float(getattr(playing, "total_time", 0) or 0),
        }
    finally:
        atv.close()
