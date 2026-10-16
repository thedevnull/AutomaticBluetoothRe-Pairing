#!/usr/bin/env python3
"""
Remove and re-pair a Bluetooth mouse on Windows, macOS or Linux.

Default target: "Bluetooth Mouse M336/M337/M535" (Logitech)

Requirements per platform
-------------------------
Windows : Python 3.9+ and
          pip install winrt-runtime winrt-Windows.Foundation \
                      winrt-Windows.Foundation.Collections \
                      winrt-Windows.Devices.Enumeration \
                      winrt-Windows.Devices.Bluetooth
macOS   : Homebrew package "blueutil"   ->  brew install blueutil
Linux   : BlueZ (bluetoothctl)          ->  sudo apt install bluez

Usage
-----
    python repair_bt_mouse_crossplatform.py
    python repair_bt_mouse_crossplatform.py --name "Bluetooth Mouse"
    python repair_bt_mouse_crossplatform.py --mac AA:BB:CC:DD:EE:FF
    python repair_bt_mouse_crossplatform.py --scan-time 60
"""

import argparse
import asyncio
import json
import platform
import re
import shutil
import subprocess
import sys
import time

DEFAULT_NAME = "Bluetooth Mouse M336/M337/M535"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def name_matches(candidate, wanted):
    """True if `candidate` looks like the wanted mouse.

    The advertised name is often just "Bluetooth Mouse M336" (or M337/M535),
    so we also accept the shared prefix plus any of the listed model numbers.
    """
    c, w = (candidate or "").lower(), wanted.lower()
    if not c:
        return False
    if w in c or c in w:
        return True
    models = re.findall(r"m\d{3}", w)
    prefix = re.sub(r"\s*m\d{3}.*$", "", w).strip()
    return bool(prefix) and prefix in c and any(m in c for m in models)


def norm_mac(mac):
    return re.sub(r"[^0-9a-f]", "", mac.lower())


def indent(text):
    return "    " + text.replace("\n", "\n    ") if text else ""


# --------------------------------------------------------------------------
# Linux (BlueZ)
# --------------------------------------------------------------------------
class LinuxBackend:
    DEVICE_RE = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})\s+(.*)$")

    def __init__(self):
        if not shutil.which("bluetoothctl"):
            sys.exit("bluetoothctl not found. Install BlueZ: sudo apt install bluez")

    def _ctl(self, *args, timeout=30):
        try:
            r = subprocess.run(["bluetoothctl", *args], capture_output=True,
                               text=True, timeout=timeout)
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return 124, "timeout"

    def _devices(self):
        _, out = self._ctl("devices")
        devs = {}
        for line in out.splitlines():
            m = self.DEVICE_RE.match(line.strip())
            if m:
                devs[m.group(1).upper()] = m.group(2).strip()
        return devs

    def prepare(self):
        self._ctl("power", "on")

    def find_known(self, name, mac):
        return [(m, n) for m, n in self._devices().items()
                if (mac and norm_mac(m) == norm_mac(mac))
                or (not mac and name_matches(n, name))]

    def remove(self, ids):
        for mac in ids:
            print(f"[*] Disconnecting/removing {mac} ...")
            self._ctl("disconnect", mac)
            print(indent(self._ctl("remove", mac)[1]))

    def discover(self, name, mac, timeout):
        scan = subprocess.Popen(
            ["bluetoothctl", "--timeout", str(timeout), "scan", "on"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        found = None
        deadline = time.time() + timeout
        try:
            while time.time() < deadline and not found:
                time.sleep(2)
                for m, n in self._devices().items():
                    if (mac and norm_mac(m) == norm_mac(mac)) or \
                       (not mac and name_matches(n, name)):
                        found = (m, n)
                        break
        finally:
            scan.terminate()
            try:
                scan.wait(timeout=5)
            except subprocess.TimeoutExpired:
                scan.kill()
            self._ctl("scan", "off")
        return found

    def pair(self, mac):
        _, out = self._ctl("pair", mac, timeout=40)
        print(indent(out))
        if "Pairing successful" not in out and "AlreadyExists" not in out:
            return False
        self._ctl("trust", mac)
        _, out = self._ctl("connect", mac)
        print(indent(out))
        return "Connection successful" in out


# --------------------------------------------------------------------------
# macOS (blueutil)
# --------------------------------------------------------------------------
class MacBackend:
    def __init__(self):
        if not shutil.which("blueutil"):
            sys.exit("blueutil not found. Install it with: brew install blueutil")

    def _run(self, *args, timeout=30):
        try:
            r = subprocess.run(["blueutil", *args], capture_output=True,
                               text=True, timeout=timeout)
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return 124, "timeout"

    def _json(self, *args, timeout=30):
        _, out = self._run(*args, "--format", "json", timeout=timeout)
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return []

    @staticmethod
    def _match(d, name, mac):
        if mac:
            return norm_mac(d.get("address", "")) == norm_mac(mac)
        return name_matches(d.get("name"), name)

    def prepare(self):
        self._run("--power", "1")

    def find_known(self, name, mac):
        return [(d["address"], d.get("name") or "?")
                for d in self._json("--paired") if self._match(d, name, mac)]

    def remove(self, ids):
        for addr in ids:
            print(f"[*] Disconnecting/unpairing {addr} ...")
            self._run("--disconnect", addr)
            print(indent(self._run("--unpair", addr)[1]))

    def discover(self, name, mac, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            secs = max(3, min(8, int(deadline - time.time())))
            for d in self._json("--inquiry", str(secs), timeout=secs + 20):
                if self._match(d, name, mac):
                    return d["address"], d.get("name") or "?"
        return None

    def pair(self, addr):
        rc, out = self._run("--pair", addr, timeout=40)
        print(indent(out))
        if rc != 0:
            return False
        rc, out = self._run("--connect", addr)
        print(indent(out))
        return rc == 0


# --------------------------------------------------------------------------
# Windows (WinRT via the "winrt" Python projection)
# --------------------------------------------------------------------------
class WindowsBackend:
    # Bluetooth Classic and Bluetooth LE protocol GUIDs
    AQS = ('(System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}" '
           'OR System.Devices.Aep.ProtocolId:="{bb7bb05e-5972-42b5-94fc-76eaa7084d49}")')

    def __init__(self):
        try:
            from winrt.windows.devices import enumeration as en
        except ImportError:
            sys.exit(
                "Missing WinRT packages. Install them with:\n"
                "  pip install winrt-runtime winrt-Windows.Foundation "
                "winrt-Windows.Foundation.Collections "
                "winrt-Windows.Devices.Enumeration winrt-Windows.Devices.Bluetooth")
        self.en = en
        self.infos = {}

    # Names the "kind-aware" overload may have in the Python projection
    # (dir() does not list WinRT static methods, so we probe with getattr).
    KIND_FN_CANDIDATES = (
        "find_all_async_with_kind_aqs_filter_and_additional_properties",
        "find_all_async_with_kind_and_additional_properties",
    )

    def _run_find(self, fn, *args):
        async def go():
            return await fn(*args)
        return list(asyncio.run(go()))

    def _enumerate_aep(self):
        """Preferred: association-endpoint enumeration (paired + nearby)."""
        di = self.en.DeviceInformation
        kind = self.en.DeviceInformationKind.ASSOCIATION_ENDPOINT
        props = ["System.Devices.Aep.IsPresent"]
        for name in self.KIND_FN_CANDIDATES:
            fn = getattr(di, name, None)
            if fn is None:
                continue
            try:
                return self._run_find(fn, self.AQS, props, kind)
            except TypeError:
                continue
        return None

    def _enumerate_bluetooth_selectors(self):
        """Fallback: classic Bluetooth + BLE device selectors."""
        try:
            from winrt.windows.devices import bluetooth as bt
        except ImportError:
            sys.exit("Missing package. Install it with:\n"
                     "  pip install winrt-Windows.Devices.Bluetooth")
        di = self.en.DeviceInformation
        found = {}
        for cls in (bt.BluetoothDevice, bt.BluetoothLEDevice):
            for paired in (True, False):
                try:
                    aqs = cls.get_device_selector_from_pairing_state(paired)
                    for info in self._run_find(di.find_all_async, aqs):
                        found.setdefault(info.id, info)
                except Exception as e:  # keep going with the other selectors
                    print(f"    (selector {cls.__name__}/paired={paired} failed: {e})")
        return list(found.values())

    def _enumerate(self):
        infos = self._enumerate_aep()
        if infos is None:
            infos = self._enumerate_bluetooth_selectors()
        self.infos = {i.id: i for i in infos}
        return infos

    def _match(self, info, name, mac):
        if mac:
            return norm_mac(mac) in norm_mac(info.id)
        return name_matches(info.name, name)

    def prepare(self):
        pass  # Windows manages the adapter itself; make sure Bluetooth is ON.

    def find_known(self, name, mac):
        return [(i.id, i.name) for i in self._enumerate()
                if i.pairing.is_paired and self._match(i, name, mac)]

    def _is_present(self, info):
        """True if Windows currently sees the device in range (None if unknown)."""
        try:
            val = info.properties["System.Devices.Aep.IsPresent"]
            return bool(val)
        except Exception:
            return None

    @staticmethod
    async def _await(op):
        return await op

    def remove(self, ids):
        ok = {self.en.DeviceUnpairingResultStatus.UNPAIRED,
              self.en.DeviceUnpairingResultStatus.ALREADY_UNPAIRED}
        for dev_id in ids:
            info = self.infos[dev_id]
            print(f"[*] Unpairing {info.name} ...")
            res = asyncio.run(self._await(info.pairing.unpair_async()))
            print(f"    Result: {res.status.name}"
                  + ("" if res.status in ok else "  (failed)"))
        # Give Windows time to drop the old bond / cached entry
        print("[*] Waiting for Windows to forget the old device ...")
        time.sleep(4)

    def discover(self, name, mac, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i in self._enumerate():
                if not self._match(i, name, mac):
                    continue
                # Ignore stale cache entries: the mouse must really be in range
                if self._is_present(i) is False:
                    continue
                return i.id, i.name
            time.sleep(1)
        return None

    def _pair_custom(self, info):
        """Pair using our own ceremony handler (accepts confirm / PIN prompts)."""
        kinds_cls = self.en.DevicePairingKinds
        kinds = 0
        for n in ("NONE", "CONFIRM_ONLY", "DISPLAY_PIN", "PROVIDE_PIN",
                  "CONFIRM_PIN_MATCH"):
            kinds |= int(getattr(kinds_cls, n, 0))
        provide_pin = getattr(kinds_cls, "PROVIDE_PIN", None)

        def on_requested(sender, args):
            try:
                if provide_pin is not None and args.pairing_kind == provide_pin:
                    args.accept("0000")   # legacy default PIN
                else:
                    args.accept()
            except Exception as e:
                print(f"    (ceremony handler error: {e})")

        custom = info.pairing.custom
        token = custom.add_pairing_requested(on_requested)
        try:
            return asyncio.run(self._await(custom.pair_async(kinds_cls(kinds))))
        finally:
            try:
                custom.remove_pairing_requested(token)
            except Exception:
                pass

    def pair(self, dev_id):
        ok = {self.en.DevicePairingResultStatus.PAIRED,
              self.en.DevicePairingResultStatus.ALREADY_PAIRED}
        attempts = 3
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                print(f"    Retry {attempt}/{attempts} (keep the mouse in pairing mode) ...")
                time.sleep(3)
            self._enumerate()
            info = self.infos.get(dev_id)
            if info is None:
                print("    Device not visible anymore; is it still in pairing mode?")
                continue
            # A leftover pairing record makes new pairing fail: remove it first
            if info.pairing.is_paired:
                print("    Stale pairing record found; unpairing it first ...")
                asyncio.run(self._await(info.pairing.unpair_async()))
                time.sleep(4)
                self._enumerate()
                info = self.infos.get(dev_id)
                if info is None:
                    print("    Device not visible after unpair; keep pairing mode on.")
                    continue
            if attempt == 1:
                # Custom pairing is the most reliable way to pair classic mice
                print("    Using custom pairing (auto-accepting any confirmation/PIN) ...")
                res = self._pair_custom(info)
            else:
                print("    Using standard pairing ...")
                res = asyncio.run(self._await(info.pairing.pair_async()))
            print(f"    Result: {res.status.name}")
            if res.status in ok:
                return True
        # Last resort: open Windows' own Bluetooth page
        try:
            import os
            os.startfile("ms-settings:bluetooth")
            print("    Opened Windows Bluetooth settings: click 'Add device' > 'Bluetooth'.")
        except Exception:
            pass
        print("    Tip: also remove the mouse manually in Settings > Bluetooth & devices,")
        print("    then run this script again right after the LED starts blinking.")
        return False

# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def get_backend():
    system = platform.system()
    if system == "Windows":
        return WindowsBackend()
    if system == "Darwin":
        return MacBackend()
    if system == "Linux":
        return LinuxBackend()
    sys.exit(f"Unsupported platform: {system}")


def main():
    ap = argparse.ArgumentParser(description="Remove and re-pair a Bluetooth mouse.")
    ap.add_argument("--name", default=DEFAULT_NAME, help="device name to look for")
    ap.add_argument("--mac", help="device MAC address (skips name matching)")
    ap.add_argument("--scan-time", type=int, default=45, help="seconds to scan")
    args = ap.parse_args()

    be = get_backend()
    print(f"[*] Platform: {platform.system()}")
    be.prepare()

    # 1. Remove the existing pairing
    known = be.find_known(args.name, args.mac)
    if known:
        for dev_id, n in known:
            print(f"[*] Found paired device: {n}  ({dev_id})")
        be.remove([d for d, _ in known])
    else:
        print("[!] No existing paired device found; continuing to pairing.")

    time.sleep(1)

    # 2. Scan for the mouse in pairing mode
    print(f"[*] Scanning up to {args.scan_time}s. Put the mouse in pairing mode now")
    print("    (hold the Connect button on the bottom until the LED blinks fast).")
    found = be.discover(args.name, args.mac, args.scan_time)
    if not found:
        sys.exit("[x] Mouse not found. Make sure it is in pairing mode and try again.")
    dev_id, dev_name = found
    print(f"[+] Found: {dev_name}")

    # 3. Pair
    print("[*] Pairing ...")
    if be.pair(dev_id):
        print("[+] Done! The mouse is paired.")
    else:
        sys.exit("[x] Pairing failed. Try again with the mouse in pairing mode.")


if __name__ == "__main__":
    main()
