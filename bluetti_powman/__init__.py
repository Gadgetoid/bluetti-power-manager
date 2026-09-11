"""BLUETTI power dashboard for the Tufty 2350.

Reimplements the BLUETTI Elite 200 V2 front-panel display: a segmented charge
ring, big state-of-charge readout, remaining charge time, and the four power
readouts (DC/AC in/out), pulled live over the encrypted BLE link. While the unit
is charging the ring animates, filling one segment at a time from the current
level up to full.

Buttons: A toggles the AC output, B toggles the DC output, C refreshes now,
up/down cycle the colour theme. A and B physically switch the station's outputs.

Runs standalone on the Tufty: pure-Python secp256r1 (see secp256r1.py) plus the
firmware's hashlib/cryptolib. No companion app or vendor library on-device.
"""

import os
import sys

# Standalone bootstrap for finding app assets and modules
APP_DIRS = (
    "/system/apps/bluetti_powman",
    "/apps/bluetti_powman",
    "/bluetti_powman",
)

APP_DIR = APP_DIRS[0]
for app_dir in APP_DIRS:
    try:
        os.stat(app_dir)
        APP_DIR = app_dir
        break
    except OSError:
        pass

try:
    os.chdir(APP_DIR)
    sys.path.insert(0, APP_DIR)
except OSError:
    pass

import asyncio
import gc
import secrets

import ui
from bluetti import read, prepare_keys, CTRL_AC, CTRL_DC

# The BLUETTI to talk to. Find it with a scan (its advert name starts "Elite").
secrets.require("BLUETTI_DEVICE")

BLUETTI_DEVICE = secrets.BLUETTI_DEVICE

REFRESH_MS = 20000     # re-read the device this often
STEP_MS = 350          # charge-ring animation step

state = {
    "data": {"soc": 0, "dc_in": 0, "dc_out": 0, "ac_in": 0, "ac_out": 0, "mins": 0,
             "ac_on": 0, "dc_on": 0},
    "have": False,
    "head": None,
    "busy": False,     # a connection is in flight
    "step": 0,         # last charge-animation step
}

pending = []                   # (register, value) writes queued by the buttons
wake = asyncio.ThreadSafeFlag()  # nudges the reader to connect right now


def _toggle(register, key):
    """Queue an output switch and ask the reader to go now."""
    if not state["have"]:
        return
    pending.append((register, 0 if state["data"].get(key) else 1))
    wake.set()


def _charging(d):
    return (d.get("dc_in", 0) + d.get("ac_in", 0)) > 0


async def _reader(keys):
    while True:
        try:
            writes = pending[:]
            del pending[:]
            state["busy"] = True
            data = await read(BLUETTI_DEVICE, keys, writes=writes)
        except Exception as e:      # noqa: BLE001 - a poll must never end the app
            data = None
            sys.print_exception(e)
        state["busy"] = False
        # Reclaim the connection's churn before the next one, so a long run
        # does not fragment the heap into a MemoryError.
        gc.collect()
        if data:
            # Merge rather than replace: a read can come back partial if the
            # link drops midway (more likely when a write goes first), and a
            # missing register should keep its last known value.
            state["data"].update(data)
            state["have"] = True
        # sleep until the refresh is due, or until a button asks for sooner
        try:
            await asyncio.wait_for_ms(wake.wait(), REFRESH_MS)
        except asyncio.TimeoutError:
            pass


async def _renderer():
    while True:
        try:
            _animate()
            _draw()
            _buttons()
        except Exception as e:      # noqa: BLE001 - keep the screen alive
            sys.print_exception(e)
            await asyncio.sleep_ms(200)
        await asyncio.sleep_ms(30)


def _animate():
    """While charging, fill one slice at a time from the level up to full."""
    data = state["data"]
    if not _charging(data):
        state["head"] = None
        return
    now = badge.ticks
    if now - state["step"] <= STEP_MS:
        return
    state["step"] = now
    base = round(data.get("soc", 0) / 100 * ui.NSEG)
    head = state["head"]
    state["head"] = base if head is None else head + 1
    if state["head"] > ui.NSEG:
        state["head"] = base


def _draw():
    if state["have"]:
        ui.render(state["data"], state["head"], busy=state["busy"])
    else:
        ui.render_connecting()
    display.update()
    badge.poll()


def _buttons():
    if badge.pressed(BUTTON_A):
        _toggle(CTRL_AC, "ac_on")
    elif badge.pressed(BUTTON_B):
        _toggle(CTRL_DC, "dc_on")
    elif badge.pressed(BUTTON_C):
        wake.set()                 # refresh now
    elif badge.pressed(BUTTON_UP):
        ui.cycle_theme(-1)
    elif badge.pressed(BUTTON_DOWN):
        ui.cycle_theme(1)


async def main():
    state["step"] = badge.ticks
    keys = prepare_keys()          # ephemeral keypair + signing nonce, before connecting
    await asyncio.gather(_reader(keys), _renderer())


asyncio.run(main())
