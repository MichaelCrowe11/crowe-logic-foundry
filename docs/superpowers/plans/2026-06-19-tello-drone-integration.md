# Tello Drone Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `crowe-logic` agent a controllable Tello drone as a sensor — flight primitives plus frame capture — reusing the existing `analyze_image` vision path and `crowe-farm-automation` for analysis/reporting.

**Architecture:** One new module `tools/tello.py` exposing `tello_*` functions (each returns a JSON string). A fresh connection is disarmed; `tello_arm()` gates flight; movements are capped; `tello_land()`/`tello_emergency()` always work; auto-land on low battery. The actual `djitellopy.Tello` is constructed behind `_new_backend()` and frames are written behind `_save_frame()`, so ~90% of the module is unit-tested against a fake backend with no hardware. Tools register conditionally via `register(target)` (no-op unless the `drone` extra is installed), mirroring `tools/notebook.py`.

**Tech Stack:** Python 3.9+, `djitellopy` (optional `drone` extra), `Pillow` (frame write), `httpx` (farm POST, already a base dep), `pytest` + `unittest.mock`.

## Global Constraints

- **Python 3.9 compatible** — no `X | Y` runtime annotations; use `Optional`/`List`/`Set` from `typing`. (Repo carries 3.9 compat in `tools/registry.py`.)
- **Every tool returns a JSON string** via `json.dumps`; **no exception escapes** to the agent loop (catch and return an `{"error": ...}` JSON).
- **Docstrings drive the schema** — every tool function has `:param:`, `:return:`, `:rtype:` (same as `tools/vision.py`).
- **No emojis** in any user-facing string (Crowe design rule).
- **`djitellopy` is the optional `drone` extra** — `tools/tello.py` MUST import cleanly when it is absent; flight tools return a clear error and `register()` returns `[]`.
- **Tests use no hardware and no network** — patch `tools.tello._new_backend`, `tools.tello._save_frame`, and `httpx`.
- **Run tests via the foundry venv:** `.venv/bin/python -m pytest ...` (the `.zshrc` PATH hook does not fire in non-interactive tool calls — per repo CLAUDE.md).
- **Branch:** `feat/tello-drone-integration` (already created; spec committed there).

---

### Task 1: Module scaffold + connection/state tools

**Files:**
- Create: `tools/tello.py`
- Test: `tests/test_tello.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - Module constants `MAX_STEP_CM`, `MAX_ROTATE_DEG`, `MAX_ALT_CM`, `MIN_BATTERY_PCT`, `CAPTURE_DIR`, `FARM_API_URL`, `FARM_OBS_ENDPOINT`.
  - `_SESSION` dict `{"drone", "armed", "flying"}`, `_HAVE_DJITELLO` bool.
  - Helpers `_err(message, **extra) -> str`, `_ok(**fields) -> str`, `_new_backend()`.
  - Tools `tello_connect() -> str`, `tello_status() -> str`, `tello_disconnect() -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tello.py
"""Tests for tools.tello — drone flight + capture, against a fake backend."""

import json
import pytest
import tools.tello as tello


class FakeTello:
    """Minimal stand-in for djitellopy.Tello — records calls, no hardware."""

    def __init__(self):
        self.battery = 80
        self.height = 0
        self.calls = []

    def connect(self): self.calls.append("connect")
    def streamon(self): self.calls.append("streamon")
    def streamoff(self): self.calls.append("streamoff")
    def end(self): self.calls.append("end")
    def get_battery(self): return self.battery
    def get_height(self): return self.height
    def get_temperature(self): return 25
    def takeoff(self): self.calls.append("takeoff")
    def land(self): self.calls.append("land")
    def emergency(self): self.calls.append("emergency")
    def move_forward(self, cm): self.calls.append(("move_forward", cm))
    def move_back(self, cm): self.calls.append(("move_back", cm))
    def move_left(self, cm): self.calls.append(("move_left", cm))
    def move_right(self, cm): self.calls.append(("move_right", cm))
    def move_up(self, cm): self.calls.append(("move_up", cm))
    def move_down(self, cm): self.calls.append(("move_down", cm))
    def rotate_clockwise(self, d): self.calls.append(("cw", d))
    def rotate_counter_clockwise(self, d): self.calls.append(("ccw", d))

    def get_frame_read(self):
        class _R:
            frame = "FAKEFRAME"
        return _R()


@pytest.fixture(autouse=True)
def _reset_session(monkeypatch):
    """Each test starts disconnected, with djitellopy 'available' and a fake backend."""
    tello._SESSION.update({"drone": None, "armed": False, "flying": False})
    monkeypatch.setattr(tello, "_HAVE_DJITELLO", True)
    yield
    tello._SESSION.update({"drone": None, "armed": False, "flying": False})


@pytest.fixture
def fake(monkeypatch):
    drone = FakeTello()
    monkeypatch.setattr(tello, "_new_backend", lambda: drone)
    return drone


class TestConnection:
    def test_connect_returns_battery_and_disarmed(self, fake):
        result = json.loads(tello.tello_connect())
        assert result["connected"] is True
        assert result["armed"] is False
        assert result["battery"] == 80
        assert "connect" in fake.calls and "streamon" in fake.calls

    def test_status_without_connection_errors(self):
        result = json.loads(tello.tello_status())
        assert "error" in result

    def test_status_after_connect_reports_state(self, fake):
        tello.tello_connect()
        result = json.loads(tello.tello_status())
        assert result["battery"] == 80
        assert result["armed"] is False
        assert result["height_cm"] == 0
        assert result["temperature_c"] == 25

    def test_disconnect_clears_session(self, fake):
        tello.tello_connect()
        result = json.loads(tello.tello_disconnect())
        assert result["connected"] is False
        assert tello._SESSION["drone"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.tello'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/tello.py
"""Tello drone tools — flight + frame capture for the crowe-logic agent.

The drone is a sensor: these tools fly it and grab frames; analysis is done by
existing tools (analyze_image) and crowe-farm-automation. Every function returns
a JSON string and never raises into the agent loop.

Safety: a fresh connection is DISARMED. tello_arm() is required before takeoff/
move/rotate. Movements are capped. tello_land()/tello_emergency() always work.
Auto-land on low battery.
"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional, Set

# djitellopy is the optional `drone` extra. Import cleanly without it so the
# base install is unaffected; flight tools error and register() is a no-op.
try:
    from djitellopy import Tello as _Tello  # type: ignore
    _HAVE_DJITELLO = True
except ImportError:  # pragma: no cover - exercised via monkeypatch
    _Tello = None
    _HAVE_DJITELLO = False

MAX_STEP_CM = int(os.environ.get("TELLO_MAX_STEP_CM", "100"))
MAX_ROTATE_DEG = int(os.environ.get("TELLO_MAX_ROTATE_DEG", "180"))
MAX_ALT_CM = int(os.environ.get("TELLO_MAX_ALT_CM", "300"))
MIN_BATTERY_PCT = int(os.environ.get("TELLO_MIN_BATTERY_PCT", "15"))
CAPTURE_DIR = os.environ.get(
    "TELLO_CAPTURE_DIR", os.path.expanduser("~/.crowe/tello-captures")
)
FARM_API_URL = os.environ.get("CROWE_FARM_API_URL", "")
FARM_OBS_ENDPOINT = os.environ.get("CROWE_FARM_OBS_ENDPOINT", "/api/observations")

_SESSION = {"drone": None, "armed": False, "flying": False}


def _err(message: str, **extra) -> str:
    out = {"error": message}
    out.update(extra)
    return json.dumps(out)


def _ok(**fields) -> str:
    return json.dumps(fields)


def _new_backend():
    """Construct a djitellopy Tello. Isolated so tests can patch it."""
    if not _HAVE_DJITELLO:
        raise RuntimeError("djitellopy not installed; install crowe-logic[drone]")
    return _Tello()


def tello_connect() -> str:
    """Connect to the Tello over its WiFi SDK and start the video stream.

    The drone starts DISARMED; call tello_arm() before any flight.

    :return: JSON with connection state and battery percent, or an error.
    :rtype: str
    """
    try:
        drone = _new_backend()
        drone.connect()
        drone.streamon()
        _SESSION["drone"] = drone
        _SESSION["armed"] = False
        _SESSION["flying"] = False
        return _ok(connected=True, armed=False, battery=drone.get_battery())
    except Exception as exc:  # noqa: BLE001
        return _err("connect failed: {}".format(exc))


def tello_status() -> str:
    """Report battery, height, temperature, and armed/flying state.

    :return: JSON status, or an error if no drone is connected.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected; call tello_connect first")
    try:
        battery = drone.get_battery()
        status = {
            "connected": True,
            "armed": _SESSION["armed"],
            "flying": _SESSION["flying"],
            "battery": battery,
            "height_cm": drone.get_height(),
            "temperature_c": drone.get_temperature(),
        }
        return json.dumps(status)
    except Exception as exc:  # noqa: BLE001
        return _err("status failed: {}".format(exc))


def tello_disconnect() -> str:
    """Stop the video stream and release the connection.

    :return: JSON with connected=False (idempotent).
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _ok(connected=False)
    try:
        drone.streamoff()
        drone.end()
    except Exception:  # noqa: BLE001
        pass
    _SESSION["drone"] = None
    _SESSION["armed"] = False
    _SESSION["flying"] = False
    return _ok(connected=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/tello.py tests/test_tello.py
git commit -m "feat(tello): module scaffold + connection/state tools"
```

---

### Task 2: Arming gate + flight primitives with caps

**Files:**
- Modify: `tools/tello.py`
- Modify: `tests/test_tello.py`

**Interfaces:**
- Consumes: `_SESSION`, `_err`, `_ok`, connected fake from Task 1.
- Produces:
  - `tello_arm() -> str`, `tello_disarm() -> str`
  - `tello_takeoff() -> str`, `tello_land() -> str`, `tello_emergency() -> str`
  - `tello_move(direction: str, cm: int) -> str`, `tello_rotate(degrees: int) -> str`
  - `_MOVE_METHODS` mapping {direction -> djitellopy method name}.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tello.py

class TestFlightGating:
    def _connected(self, fake):
        tello.tello_connect()
        return fake

    def test_move_while_disarmed_errors_and_does_not_fly(self, fake):
        self._connected(fake)
        result = json.loads(tello.tello_move("forward", 50))
        assert "error" in result
        assert not any(isinstance(c, tuple) and c[0] == "move_forward" for c in fake.calls)

    def test_arm_then_move_calls_backend(self, fake):
        self._connected(fake)
        assert json.loads(tello.tello_arm())["armed"] is True
        result = json.loads(tello.tello_move("forward", 50))
        assert result["moved"] == "forward" and result["cm"] == 50
        assert ("move_forward", 50) in fake.calls

    def test_move_over_cap_is_rejected(self, fake):
        self._connected(fake)
        tello.tello_arm()
        result = json.loads(tello.tello_move("up", tello.MAX_STEP_CM + 1))
        assert "error" in result and "cap" in result["error"]

    def test_invalid_direction_errors(self, fake):
        self._connected(fake)
        tello.tello_arm()
        assert "error" in json.loads(tello.tello_move("sideways", 10))

    def test_takeoff_requires_arm(self, fake):
        self._connected(fake)
        assert "error" in json.loads(tello.tello_takeoff())
        tello.tello_arm()
        assert json.loads(tello.tello_takeoff())["flying"] is True

    def test_land_works_while_disarmed(self, fake):
        self._connected(fake)
        tello.tello_arm(); tello.tello_takeoff(); tello.tello_disarm()
        result = json.loads(tello.tello_land())
        assert result["flying"] is False and "land" in fake.calls

    def test_emergency_always_available(self, fake):
        self._connected(fake)
        result = json.loads(tello.tello_emergency())
        assert result["emergency"] is True and "emergency" in fake.calls

    def test_rotate_respects_cap_and_direction(self, fake):
        self._connected(fake)
        tello.tello_arm()
        assert json.loads(tello.tello_rotate(90))["rotated"] == 90
        assert ("cw", 90) in fake.calls
        assert json.loads(tello.tello_rotate(-45))["rotated"] == -45
        assert ("ccw", 45) in fake.calls
        assert "error" in json.loads(tello.tello_rotate(tello.MAX_ROTATE_DEG + 1))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tello.py::TestFlightGating -v`
Expected: FAIL — `AttributeError: module 'tools.tello' has no attribute 'tello_arm'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/tello.py (after tello_disconnect)

_MOVE_METHODS = {
    "forward": "move_forward",
    "back": "move_back",
    "left": "move_left",
    "right": "move_right",
    "up": "move_up",
    "down": "move_down",
}


def tello_arm() -> str:
    """Authorize flight. Required before takeoff/move/rotate.

    :return: JSON with armed=True, or an error if not connected.
    :rtype: str
    """
    if _SESSION["drone"] is None:
        return _err("no drone connected; call tello_connect first")
    _SESSION["armed"] = True
    return _ok(armed=True)


def tello_disarm() -> str:
    """Revoke flight authorization. Does not land a flying drone (use tello_land).

    :return: JSON with armed=False.
    :rtype: str
    """
    _SESSION["armed"] = False
    return _ok(armed=False)


def tello_takeoff() -> str:
    """Take off. Requires arm and sufficient battery.

    :return: JSON with flying=True, or an error.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected; call tello_connect first")
    if not _SESSION["armed"]:
        return _err("disarmed; call tello_arm before flying")
    try:
        battery = drone.get_battery()
        if battery <= MIN_BATTERY_PCT:
            return _err("battery too low to take off ({}%)".format(battery))
        drone.takeoff()
        _SESSION["flying"] = True
        return _ok(flying=True, battery=battery)
    except Exception as exc:  # noqa: BLE001
        return _err("takeoff failed: {}".format(exc))


def tello_land() -> str:
    """Land the drone. Always available, even when disarmed.

    :return: JSON with flying=False, or an error.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected")
    try:
        drone.land()
        _SESSION["flying"] = False
        return _ok(flying=False)
    except Exception as exc:  # noqa: BLE001
        return _err("land failed: {}".format(exc))


def tello_emergency() -> str:
    """Cut motors immediately. Always available. Disarms.

    :return: JSON with emergency=True, or an error.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected")
    try:
        drone.emergency()
        _SESSION["flying"] = False
        _SESSION["armed"] = False
        return _ok(emergency=True, flying=False)
    except Exception as exc:  # noqa: BLE001
        return _err("emergency failed: {}".format(exc))


def tello_move(direction: str, cm: int) -> str:
    """Move one direction by a capped distance.

    :param direction: one of forward, back, left, right, up, down.
    :param cm: distance in centimeters (1..TELLO_MAX_STEP_CM).
    :return: JSON result or error.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected; call tello_connect first")
    if not _SESSION["armed"]:
        return _err("disarmed; call tello_arm before flying")
    if direction not in _MOVE_METHODS:
        return _err("invalid direction '{}'; use one of {}".format(
            direction, list(_MOVE_METHODS)))
    try:
        cm = int(cm)
    except (TypeError, ValueError):
        return _err("cm must be an integer")
    if cm < 1:
        return _err("cm must be >= 1")
    if cm > MAX_STEP_CM:
        return _err("cm {} exceeds cap {}; issue a smaller move".format(cm, MAX_STEP_CM))
    try:
        getattr(drone, _MOVE_METHODS[direction])(cm)
        return _ok(moved=direction, cm=cm)
    except Exception as exc:  # noqa: BLE001
        return _err("move failed: {}; consider tello_emergency".format(exc))


def tello_rotate(degrees: int) -> str:
    """Rotate in place; positive=clockwise, capped at TELLO_MAX_ROTATE_DEG.

    :param degrees: signed degrees, non-zero, |degrees| <= cap.
    :return: JSON result or error.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected; call tello_connect first")
    if not _SESSION["armed"]:
        return _err("disarmed; call tello_arm before flying")
    try:
        degrees = int(degrees)
    except (TypeError, ValueError):
        return _err("degrees must be an integer")
    if degrees == 0:
        return _err("degrees must be non-zero")
    if abs(degrees) > MAX_ROTATE_DEG:
        return _err("|degrees| {} exceeds cap {}".format(abs(degrees), MAX_ROTATE_DEG))
    try:
        if degrees > 0:
            drone.rotate_clockwise(degrees)
        else:
            drone.rotate_counter_clockwise(-degrees)
        return _ok(rotated=degrees)
    except Exception as exc:  # noqa: BLE001
        return _err("rotate failed: {}".format(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS (all Task 1 + Task 2 tests)

- [ ] **Step 5: Commit**

```bash
git add tools/tello.py tests/test_tello.py
git commit -m "feat(tello): arming gate + capped flight primitives"
```

---

### Task 3: Low-battery auto-land guard

**Files:**
- Modify: `tools/tello.py`
- Modify: `tests/test_tello.py`

**Interfaces:**
- Consumes: `_SESSION`, `MIN_BATTERY_PCT`, flight tools from Task 2.
- Produces: `_check_guards(drone, battery) -> Optional[str]`; `tello_status` and `tello_move` now invoke it.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tello.py

class TestSafetyGuards:
    def _flying(self, fake):
        tello.tello_connect(); tello.tello_arm(); tello.tello_takeoff()
        return fake

    def test_low_battery_autolands_on_status(self, fake):
        self._flying(fake)
        fake.battery = tello.MIN_BATTERY_PCT  # at/below threshold
        result = json.loads(tello.tello_status())
        assert "guard" in result and "auto-landed" in result["guard"]
        assert "land" in fake.calls
        assert tello._SESSION["flying"] is False

    def test_low_battery_blocks_move(self, fake):
        self._flying(fake)
        fake.battery = 10
        result = json.loads(tello.tello_move("forward", 30))
        assert "error" in result and result.get("guard") is True
        assert not any(isinstance(c, tuple) and c[0] == "move_forward" for c in fake.calls)

    def test_guard_noop_when_healthy(self, fake):
        self._flying(fake)
        fake.battery = 80
        result = json.loads(tello.tello_status())
        assert "guard" not in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tello.py::TestSafetyGuards -v`
Expected: FAIL — `KeyError: 'guard'` / move still executes

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/tello.py (after _new_backend)

def _check_guards(drone, battery) -> Optional[str]:
    """Return a guard-action string if a safety guard fired, else None.

    Low battery while flying triggers an auto-land (emergency if land fails).
    Battery is passed in so callers reuse a single blocking read.
    """
    if _SESSION["flying"] and battery <= MIN_BATTERY_PCT:
        try:
            drone.land()
        except Exception:  # noqa: BLE001
            drone.emergency()
        _SESSION["flying"] = False
        _SESSION["armed"] = False
        return "low battery ({}%) <= {}%: auto-landed".format(battery, MIN_BATTERY_PCT)
    return None
```

```python
# in tello_status, replace the `status = {...}` / `return json.dumps(status)` block:
        battery = drone.get_battery()
        status = {
            "connected": True,
            "armed": _SESSION["armed"],
            "flying": _SESSION["flying"],
            "battery": battery,
            "height_cm": drone.get_height(),
            "temperature_c": drone.get_temperature(),
        }
        guard = _check_guards(drone, battery)
        if guard:
            status["guard"] = guard
        return json.dumps(status)
```

```python
# in tello_move, insert the guard check immediately AFTER the cap check
# (after the `if cm > MAX_STEP_CM:` block) and BEFORE the try/getattr:
    battery = drone.get_battery()
    guard = _check_guards(drone, battery)
    if guard:
        return _err(guard, guard=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS (all prior + TestSafetyGuards)

- [ ] **Step 5: Commit**

```bash
git add tools/tello.py tests/test_tello.py
git commit -m "feat(tello): low-battery auto-land safety guard"
```

---

### Task 4: Frame capture

**Files:**
- Modify: `tools/tello.py`
- Modify: `tests/test_tello.py`

**Interfaces:**
- Consumes: `_SESSION`, `CAPTURE_DIR`, fake `get_frame_read()` from Task 1.
- Produces: `_save_frame(frame, path) -> None`; `tello_capture(label: str = "frame") -> str` returning `{"path", "label", "ts"}`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tello.py

class TestCapture:
    def test_capture_writes_file_and_returns_path(self, fake, tmp_path, monkeypatch):
        tello.tello_connect()
        monkeypatch.setattr(tello, "CAPTURE_DIR", str(tmp_path))
        saved = {}

        def fake_save(frame, path):
            saved["frame"] = frame
            with open(path, "wb") as fh:
                fh.write(b"PNGSTUB")

        monkeypatch.setattr(tello, "_save_frame", fake_save)
        result = json.loads(tello.tello_capture("tray-A"))
        assert result["label"] == "tray-A"
        assert result["path"].endswith(".png")
        assert saved["frame"] == "FAKEFRAME"
        import os as _os
        assert _os.path.exists(result["path"])

    def test_capture_without_connection_errors(self):
        assert "error" in json.loads(tello.tello_capture())

    def test_capture_no_frame_returns_error_no_file(self, fake, monkeypatch):
        tello.tello_connect()

        class _NoFrame:
            frame = None

        monkeypatch.setattr(fake, "get_frame_read", lambda: _NoFrame())
        result = json.loads(tello.tello_capture())
        assert "error" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tello.py::TestCapture -v`
Expected: FAIL — `AttributeError: module 'tools.tello' has no attribute 'tello_capture'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to tools/tello.py (after tello_rotate)

def _save_frame(frame, path) -> None:
    """Write a captured frame (numpy BGR array) to path as PNG.

    Isolated so tests can patch it without numpy/Pillow. Pillow ships with the
    `drone` extra. djitellopy frames are BGR, so reverse the last axis to RGB.
    """
    from PIL import Image  # local import: only needed at real capture time
    Image.fromarray(frame[:, :, ::-1]).save(path)


def tello_capture(label: str = "frame") -> str:
    """Grab the current video frame and save it to disk.

    The returned path feeds directly into analyze_image(path, prompt).

    :param label: short label used in the filename (sanitized).
    :return: JSON with the saved frame path, label, and unix ts; or an error.
    :rtype: str
    """
    drone = _SESSION["drone"]
    if drone is None:
        return _err("no drone connected; call tello_connect first")
    try:
        reader = drone.get_frame_read()
        frame = getattr(reader, "frame", None)
        if frame is None:
            return _err("no video frame available")
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        safe = "".join(c for c in label if c.isalnum() or c in ("-", "_")) or "frame"
        ts = int(time.time())
        path = os.path.join(CAPTURE_DIR, "{}-{}.png".format(safe, ts))
        _save_frame(frame, path)
        return _ok(path=path, label=safe, ts=ts)
    except Exception as exc:  # noqa: BLE001
        return _err("capture failed: {}".format(exc))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS (all prior + TestCapture)

- [ ] **Step 5: Commit**

```bash
git add tools/tello.py tests/test_tello.py
git commit -m "feat(tello): frame capture to disk for analyze_image handoff"
```

---

### Task 5: Farm-automation observation bridge

**Files:**
- Modify: `tools/tello.py`
- Modify: `tests/test_tello.py`

**Interfaces:**
- Consumes: `_err`, `_ok`, `FARM_API_URL`, `FARM_OBS_ENDPOINT`.
- Produces: `tello_log_observation(path: str, kind: str = "observation", notes: str = "") -> str`.

- [ ] **Step 1: Confirm the real farm endpoint**

Read `~/crowe-farm-automation/backend/app/main.py` (and `app/schemas.py`) to find the POST route that accepts an observation / contamination report. If the route differs from `/api/observations`, set the `CROWE_FARM_OBS_ENDPOINT` default in `tools/tello.py` to the real path and adjust the payload keys in Step 3 to match the Pydantic schema. Record the chosen route in the commit message.

- [ ] **Step 2: Write the failing test**

```python
# append to tests/test_tello.py
from unittest.mock import patch, MagicMock


class TestObservationBridge:
    def test_unset_farm_url_errors(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tello, "FARM_API_URL", "")
        f = tmp_path / "frame.png"; f.write_bytes(b"x")
        assert "error" in json.loads(tello.tello_log_observation(str(f)))

    def test_missing_frame_errors(self, monkeypatch):
        monkeypatch.setattr(tello, "FARM_API_URL", "http://farm.test")
        assert "error" in json.loads(tello.tello_log_observation("/nope.png"))

    def test_posts_payload_when_reachable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tello, "FARM_API_URL", "http://farm.test")
        f = tmp_path / "frame.png"; f.write_bytes(b"x")
        resp = MagicMock(status_code=201)
        with patch("httpx.post", return_value=resp) as mock_post:
            result = json.loads(
                tello.tello_log_observation(str(f), kind="contamination", notes="green mold")
            )
        assert result["logged"] is True and result["status"] == 201
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["kind"] == "contamination"
        assert kwargs["json"]["notes"] == "green mold"
        assert kwargs["json"]["source"] == "tello"

    def test_unreachable_farm_degrades_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tello, "FARM_API_URL", "http://farm.test")
        f = tmp_path / "frame.png"; f.write_bytes(b"x")
        with patch("httpx.post", side_effect=Exception("conn refused")):
            result = json.loads(tello.tello_log_observation(str(f)))
        assert "error" in result and "unreachable" in result["error"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tello.py::TestObservationBridge -v`
Expected: FAIL — `AttributeError: ... has no attribute 'tello_log_observation'`

- [ ] **Step 4: Write minimal implementation**

```python
# add to tools/tello.py (after tello_capture)

def tello_log_observation(path: str, kind: str = "observation", notes: str = "") -> str:
    """POST a captured frame to crowe-farm-automation as an observation.

    Degrades gracefully when CROWE_FARM_API_URL is unset or the API is down.

    :param path: path to a captured frame (from tello_capture).
    :param kind: observation kind, e.g. 'contamination' or 'observation'.
    :param notes: free-text notes, e.g. an analyze_image verdict summary.
    :return: JSON result, or a clear error if not configured/unreachable.
    :rtype: str
    """
    if not FARM_API_URL:
        return _err("CROWE_FARM_API_URL not set; cannot log observation")
    if not os.path.exists(path):
        return _err("frame not found: {}".format(path))
    payload = {"kind": kind, "notes": notes, "source": "tello", "frame_path": path}
    try:
        import httpx
        url = FARM_API_URL.rstrip("/") + FARM_OBS_ENDPOINT
        resp = httpx.post(url, json=payload, timeout=10)
        return _ok(logged=True, status=resp.status_code, kind=kind)
    except Exception as exc:  # noqa: BLE001
        return _err("farm API unreachable: {}".format(exc))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS (all prior + TestObservationBridge)

- [ ] **Step 6: Commit**

```bash
git add tools/tello.py tests/test_tello.py
git commit -m "feat(tello): graceful farm-automation observation bridge"
```

---

### Task 6: Conditional registration, system prompt, optional dependency

**Files:**
- Modify: `tools/tello.py`
- Modify: `tools/__init__.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_tello.py`

**Interfaces:**
- Consumes: all `tello_*` tools; `_HAVE_DJITELLO`.
- Produces: `_TOOL_FUNCS`, `_TOOL_NAMES`, `register(target: Set) -> List[str]`, `SYSTEM_PROMPT_ADDENDUM`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_tello.py

class TestRegistration:
    def test_register_noop_without_djitellopy(self, monkeypatch):
        monkeypatch.setattr(tello, "_HAVE_DJITELLO", False)
        target = set()
        assert tello.register(target) == []
        assert target == set()

    def test_register_adds_all_tools_when_available(self, monkeypatch):
        monkeypatch.setattr(tello, "_HAVE_DJITELLO", True)
        target = set()
        names = tello.register(target)
        assert "tello_connect" in names and "tello_capture" in names
        assert len(target) == len(tello._TOOL_FUNCS)

    def test_module_imports_without_djitellopy(self):
        # The module is already imported at top of file; importing must not
        # require djitellopy. Assert the guard constant exists.
        assert hasattr(tello, "_HAVE_DJITELLO")

    def test_system_prompt_addendum_mentions_arm(self):
        assert "tello_arm" in tello.SYSTEM_PROMPT_ADDENDUM
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tello.py::TestRegistration -v`
Expected: FAIL — `AttributeError: ... has no attribute 'register'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to the END of tools/tello.py

_TOOL_FUNCS = (
    tello_connect,
    tello_status,
    tello_disconnect,
    tello_arm,
    tello_disarm,
    tello_takeoff,
    tello_land,
    tello_emergency,
    tello_move,
    tello_rotate,
    tello_capture,
    tello_log_observation,
)
_TOOL_NAMES = [fn.__name__ for fn in _TOOL_FUNCS]


def register(target: Set) -> List[str]:
    """Add the tello_* tools to a user-functions set when djitellopy is present.

    No-op (returns []) when the `drone` extra is not installed, so the tools
    never surface where they cannot work.

    :param target: the set of user-facing tool functions to mutate in place.
    :return: the names of the tools that were registered.
    :rtype: list
    """
    if not _HAVE_DJITELLO:
        return []
    for fn in _TOOL_FUNCS:
        target.add(fn)
    return list(_TOOL_NAMES)


SYSTEM_PROMPT_ADDENDUM = """\
## Tello drone — flight safety contract

A Tello drone is available. Treat it as a sensor you fly deliberately.

- Call tello_connect first, then tello_arm before any takeoff/move/rotate.
- Fly one bounded step at a time; call tello_status between moves and read the
  battery. Distances are capped (per-step and rotation).
- tello_capture saves a frame to disk; pass its path to analyze_image to assess
  trays, fruiting blocks, or contamination, then tello_log_observation to record
  it to the farm system.
- If anything is uncertain or a command errors, call tello_land, or
  tello_emergency to cut motors. Both work even when disarmed.
"""
```

```python
# add to the END of tools/__init__.py (after the _notebook.register block)

# Tello drone tools. Registered only when the `drone` extra (djitellopy) is
# installed; a silent no-op otherwise, so the tools never surface where they
# cannot work.
from tools import tello as _tello  # noqa: E402

_tello.register(user_functions)
```

```toml
# add to pyproject.toml under [project.optional-dependencies]
drone = [
    "djitellopy>=2.5",
    "Pillow>=10.0",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS (all classes)

- [ ] **Step 5: Verify the package still imports with the drone extra absent**

Run: `.venv/bin/python -c "import tools; import tools.tello; print('import OK', tools.tello.register(set()))"`
Expected: prints `import OK []` (djitellopy not installed in the base venv, so register is a no-op and nothing raises)

- [ ] **Step 6: Run the full tello suite once more for a clean baseline**

Run: `.venv/bin/python -m pytest tests/test_tello.py -v`
Expected: PASS — all tests green

- [ ] **Step 7: Commit**

```bash
git add tools/tello.py tools/__init__.py pyproject.toml tests/test_tello.py
git commit -m "feat(tello): conditional registration + system prompt + drone extra"
```

---

## Hardware Verification (manual — after a real Tello arrives)

Not part of CI. Run once with a genuine Ryze/DJI Tello (or Tello EDU) in a clear, open space, props clear of people:

1. `pip install -e ".[drone]"` in the foundry venv.
2. Join the drone's WiFi.
3. In the CLI agent: connect, status (confirm battery), arm, takeoff, one `tello_move("up", 50)`, `tello_capture("test")`, pass the path to `analyze_image`, then land.
4. Confirm: disarmed state blocks movement; `tello_emergency` cuts motors; low battery auto-lands.

Document results in a short runbook under `docs/` and update the spec status to "implemented".

## Self-Review

- **Spec coverage:** connection/state (T1), arming gate + capped primitives (T2), low-battery auto-land guard (T3), capture→analyze_image handoff (T4), farm-automation bridge (T5), conditional registration + system prompt + optional dep (T6), manual hardware verification (final section). Outdoor/GPS, swarm, SLAM, Cortex UI all explicitly out of scope per spec. Covered.
- **Placeholder scan:** all steps contain real code/commands; the one open value (farm endpoint) is resolved by T5/Step 1 against the live farm app before coding. No TBD/TODO.
- **Type consistency:** `_SESSION` keys, `_MOVE_METHODS`, `_check_guards(drone, battery)`, `_save_frame(frame, path)`, `register(target)->List[str]`, and all `tello_*` signatures are consistent across tasks and tests.
