"""BLUETTI V2 encrypted-BLE client for MicroPython (Tufty 2350).

The link is not plain Modbus. Auth is a mutually-signed ECDH:

  1. device -> 2A2A 01 04 <nonce4> <sum>          (challenge)
       u_iv = md5(reverse(nonce)); u_key = u_iv XOR LOCAL_AES_KEY
       reply 2A2A 02 04 <u_iv[8:12]> <sum>
  2. device -> AES-CBC(u_key,u_iv) blob, type 04  (peer secp256r1 pubkey + sig)
       generate a keypair, ECDSA-sign (our pubkey || u_iv) with PRIVATE_KEY_L1,
       reply 2A2A 05 80 <our pubkey><sig>, AES-wrapped
  3. device -> AES blob type 06                   (accepted)
       secure_aes_key = ECDH(our priv, peer pubkey) = shared X coord
  4. Modbus 01 03 <addr><count> <crc>, AES-CBC'd with the secure key.

Frame checksum is a plain additive 16-bit sum of the body (not a CRC). Keys are
the vendor's own (bluetti-official/bluetti-bluetooth-lib), also published in
Patrick762/bluetti-bt-lib and nhurman/bluetti_mqtt.

The device drops the link ~1.1s after connect if auth is not finished, so the
ephemeral keypair and the ECDSA signing nonce are precomputed before connecting
(see prepare_keys) and the one remaining scalar mult (ECDH) is cooperative.
"""

import asyncio
import aioble
import bluetooth
import hashlib
import cryptolib
import os
import struct
import time

from secp256r1 import N, GX, GY, inv, mul, pub_from_priv, _add, _dbl, _to_affine

SVC = bluetooth.UUID(0xFF00)
FF01 = bluetooth.UUID(0xFF01)
FF02 = bluetooth.UUID(0xFF02)

LOCAL_AES_KEY = bytes((0x45, 0x9F, 0xC5, 0x35, 0x80, 0x89, 0x41, 0xF1,
                       0x70, 0x91, 0xE0, 0x99, 0x3E, 0xE3, 0xE9, 0x3D))
PRIVATE_KEY_L1 = 0x4F19A16E3E87BDD9BD24D3E5495B88041511943CBC8B969ADE9641D0F56AF337
BLOCK = 16

# Holding registers (Elite/portable V2 map). DC input is the solar/PV input.
REGISTERS = (
    ("soc", 102),
    ("dc_out", 140),
    ("ac_out", 142),
    ("dc_in", 144),
    ("ac_in", 146),
    ("mins", 104),
    ("ac_on", 2011),      # AC output switch state
    ("dc_on", 2012),      # DC output switch state
    ("eco_dc", 2014),     # eco cutoff enabled on the DC output
    ("eco_ac", 2017),     # eco cutoff enabled on the AC output
)

# Writeable control registers (Modbus function 6). Switching these physically
# turns the station's outputs on and off.
CTRL_AC = 2011
CTRL_DC = 2012


def _sum16(b):
    return (sum(b) & 0xFFFF).to_bytes(2, "big")


def _modbus_crc(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def prepare_keys():
    """Precompute the ephemeral keypair and ECDSA signing nonce (before connecting)."""
    my_priv = int.from_bytes(os.urandom(32), "big") % (N - 1) + 1
    my_pub = pub_from_priv(my_priv)
    k = int.from_bytes(os.urandom(32), "big") % (N - 1) + 1
    rx, ry = mul(k, GX, GY)
    return {"priv": my_priv, "pub": my_pub, "sig_r": rx % N, "sig_kinv": inv(k, N)}


async def _ecdh_shared_x(priv, peer_xy):
    # Cooperative scalar mult: yields to the BLE stack so the link survives.
    x = int.from_bytes(peer_xy[:32], "big")
    y = int.from_bytes(peer_xy[32:], "big")
    rx, ry, rz = 0, 0, 0
    ax, ay, az = x, y, 1
    k = priv
    i = 0
    while k:
        if k & 1:
            rx, ry, rz = _add(rx, ry, rz, ax, ay, az)
        ax, ay, az = _dbl(ax, ay, az)
        k >>= 1
        i += 1
        if i % 8 == 0:
            await asyncio.sleep_ms(0)
    sx, sy = _to_affine(rx, ry, rz)
    return sx.to_bytes(32, "big")


class _Session:
    def __init__(self, keys):
        self.u_key = None
        self.u_iv = None
        self.secure = None
        self.peer = None
        self.keys = keys

    def key_iv(self):
        if self.secure is None:
            return self.u_key, self.u_iv
        return self.secure, None

    def encrypt(self, data, key, iv):
        header = len(data).to_bytes(2, "big")
        if iv is None:
            seed = os.urandom(4)
            iv = hashlib.md5(seed).digest()
            header += seed
        pad = (BLOCK - len(data) % BLOCK) % BLOCK
        return header + cryptolib.aes(key, 2, iv).encrypt(data + bytes(pad))

    def decrypt(self, data, key, iv):
        dlen = (data[0] << 8) + data[1]
        if iv is None:
            iv = hashlib.md5(data[2:6]).digest()
            ct = data[6:]
        else:
            ct = data[2:]
        return cryptolib.aes(key, 2, iv).decrypt(ct)[:dlen]

    def challenge_reply(self, frame):
        nonce = frame[4:8]
        self.u_iv = hashlib.md5(bytes(reversed(nonce))).digest()
        self.u_key = bytes(a ^ b for a, b in zip(self.u_iv, LOCAL_AES_KEY))
        body = b"\x02\x04" + self.u_iv[8:12]
        return b"**" + body + _sum16(body)

    def pubkey_reply(self, plain):
        data = plain[2:-2][2:]                  # 128 bytes: 64 peer pubkey + 64 sig
        self.peer = bytes(data[:64])
        z = int.from_bytes(hashlib.sha256(self.keys["pub"] + self.u_iv).digest(), "big")
        r = self.keys["sig_r"]
        s = (self.keys["sig_kinv"] * (z + r * PRIVATE_KEY_L1)) % N
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        body = b"\x05\x80" + self.keys["pub"] + sig
        return self.encrypt(b"**" + body + _sum16(body), self.u_key, self.u_iv)

    async def accept(self):
        self.secure = await _ecdh_shared_x(self.keys["priv"], self.peer)


async def _find(target, ms):
    async with aioble.scan(ms, interval_us=30000, window_us=30000, active=True) as scanner:
        async for r in scanner:
            if r.device.addr_hex() == target and r.connectable:
                return aioble.Device(r.device.addr_type, r.device.addr)
    return None


async def _handshake(ff01, ff02, session, deadline_ms):
    buf = bytearray()
    replied = False
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < deadline_ms:
        try:
            data = bytes(await ff01.notified(timeout_ms=2000))
        except asyncio.TimeoutError:
            continue
        if data[:2] == b"**":
            if data[2] == 0x01:
                await ff02.write(session.challenge_reply(data), response=True)
            continue
        if session.u_key is None:
            continue
        buf.extend(data)
        key, iv = session.key_iv()
        dlen = (buf[0] << 8) + buf[1]
        need = (2 if iv is not None else 6) + ((dlen + BLOCK - 1) // BLOCK) * BLOCK
        if len(buf) < need:
            continue
        plain = session.decrypt(bytes(buf[:need]), key, iv)
        buf[:] = buf[need:]
        if plain[:2] != b"**":
            continue
        if plain[2] == 0x04 and not replied:
            await ff02.write(session.pubkey_reply(plain), response=True)
            replied = True
        elif plain[2] == 0x06:
            await session.accept()
            return True
    return False


async def _read_register(ff01, ff02, session, addr, count=1):
    cmd = bytearray(b"\x01\x03" + struct.pack(">HH", addr, count))
    cmd += struct.pack("<H", _modbus_crc(cmd))
    await ff02.write(session.encrypt(bytes(cmd), session.secure, None), response=True)
    buf = bytearray()
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 4000:
        try:
            buf.extend(bytes(await ff01.notified(timeout_ms=2000)))
        except asyncio.TimeoutError:
            continue
        if len(buf) < 2:
            continue
        dlen = (buf[0] << 8) + buf[1]
        need = 6 + ((dlen + BLOCK - 1) // BLOCK) * BLOCK
        if len(buf) < need:
            continue
        plain = session.decrypt(bytes(buf[:need]), session.secure, None)
        return int.from_bytes(plain[3:3 + 2 * count], "big")
    return None


async def _write_register(ff01, ff02, session, addr, value):
    """Modbus function 6, single register. The device echoes the request back."""
    cmd = bytearray(b"\x01\x06" + struct.pack(">HH", addr, value))
    cmd += struct.pack("<H", _modbus_crc(cmd))
    await ff02.write(session.encrypt(bytes(cmd), session.secure, None), response=True)
    buf = bytearray()
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 4000:
        try:
            buf.extend(bytes(await ff01.notified(timeout_ms=2000)))
        except asyncio.TimeoutError:
            continue
        if len(buf) < 2:
            continue
        dlen = (buf[0] << 8) + buf[1]
        need = 6 + ((dlen + BLOCK - 1) // BLOCK) * BLOCK
        if len(buf) < need:
            continue
        echo = session.decrypt(bytes(buf[:need]), session.secure, None)
        return len(echo) >= 6 and echo[1] == 0x06
    return False


async def _attempt(target, keys, writes):
    """One connect-handshake-read cycle. Returns a dict, or None."""
    dev = await _find(target, 15000)
    if dev is None:
        return None
    try:
        conn = await dev.connect(timeout_ms=20000)
    except Exception:
        dev._connection = None
        return None
    try:
        async with conn:
            svc = None
            async for s in conn.services():
                if s.uuid == SVC:
                    svc = s
            if svc is None:
                return None
            chars = {}
            async for c in svc.characteristics():
                chars[c.uuid] = c
            ff01, ff02 = chars[FF01], chars[FF02]
            try:
                await conn.exchange_mtu(400)
            except Exception:
                pass
            await ff01.subscribe(notify=True)
            session = _Session(keys)
            if not await _handshake(ff01, ff02, session, 12000):
                return None
            for addr, value in (writes or ()):
                await _write_register(ff01, ff02, session, addr, value)
            out = {}
            for name, addr in REGISTERS:
                value = await _read_register(ff01, ff02, session, addr)
                if value is not None:
                    out[name] = value
                elif conn._conn_handle is None:
                    break                # link gone; keep what we have
            return out or None           # may be partial - the caller merges
    finally:
        dev._connection = None           # aioble keeps a stale one otherwise


async def read(target, keys, attempts=4, writes=None):
    """Connect, handshake, apply `writes`, then read all registers.

    `writes` is an iterable of (register, value) applied before the read-back,
    so the returned dict reflects the new state. Returns a dict or None.

    Every attempt is wrapped: this link times out, refuses and drops
    mid-sequence as a matter of course, and `async with conn` can raise on the
    way out too.
    """
    for _ in range(attempts):
        try:
            result = await _attempt(target, keys, writes)
        except Exception:
            result = None
        if result:
            return result
        await asyncio.sleep_ms(500)
    return None
