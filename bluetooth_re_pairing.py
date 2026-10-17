#!/usr/bin/env python3
"""
Bluetooth device control for Windows, macOS and Linux.

  python bluetooth_re-pairing.py
      Scans for 10 seconds, lets you pick a device, removes it and pairs it
      again, then saves the device so --service can handle it later.

  python bluetooth_re-pairing.py --service
      Removes and re-pairs every saved device, with no menu and no prompts.

Requirements per platform
-------------------------
Windows : Python 3.9+ and
          pip install winrt-runtime winrt-Windows.Foundation ^
                      winrt-Windows.Foundation.Collections ^
                      winrt-Windows.Devices.Enumeration ^
                      winrt-Windows.Devices.Bluetooth
macOS   : brew install blueutil
Linux   : sudo apt install bluez

Saved devices live in a per-OS config file (printed when a device is saved),
so run the program once in each OS you dual-boot.
"""

import argparse
import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime



def host_env():
    """Environment for system programs such as bluetoothctl.

    Frozen builds (PyInstaller / AppImage) point LD_LIBRARY_PATH at the
    bundled libraries, which can break the host's own binaries.
    """
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


SCAN_TIME = 10        # seconds spent listing devices (interactive mode)
PAIR_TIMEOUT = 45     # seconds to wait for the device to appear in pairing mode
APP_NAME = "bt_device_control"

MAC_AT_END_RE = re.compile(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})\s*$")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def norm_mac(mac):
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


def fmt_mac(mac):
    n = norm_mac(mac).upper()
    if len(n) != 12:
        return mac
    return ":".join(n[i:i + 2] for i in range(0, 12, 2))


def mac_from_id(dev_id):
    """Windows ids look like 'Bluetooth#Bluetooth<adapter>-<device mac>'."""
    m = MAC_AT_END_RE.search(dev_id or "")
    return m.group(1).upper() if m else None


def clean_name(name):
    """Drop unprintable/replacement characters and surrounding spaces."""
    return "".join(ch for ch in (name or "")
                   if ch.isprintable() and ch != "\ufffd").strip()


def indent(text):
    return "    " + text.replace("\n", "\n    ") if text else ""


# --------------------------------------------------------------------------
# Saved devices
# --------------------------------------------------------------------------
def config_path():
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif system == "Darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME, "devices.json")


def load_devices():
    path = config_path()
    try:
        with open(path, encoding="utf-8") as f:
            return list(json.load(f).get("devices", []))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        print(f"[!] Could not read {path}: {e}")
        return []


def write_devices(devices):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"devices": devices}, f, indent=2, ensure_ascii=False)
    return path


def save_device(mac, name):
    devices = load_devices()
    entry = {
        "name": name,
        "mac": fmt_mac(mac),
        "last_paired": datetime.now().isoformat(timespec="seconds"),
    }
    for i, d in enumerate(devices):
        if norm_mac(d.get("mac")) == norm_mac(mac):
            devices[i] = entry
            break
    else:
        devices.append(entry)
    return write_devices(devices)


def forget_device(mac):
    """Remove a device from the saved list (does not touch the Bluetooth pairing)."""
    devices = [d for d in load_devices() if norm_mac(d.get("mac")) != norm_mac(mac)]
    return write_devices(devices)

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
                               text=True, timeout=timeout, env=host_env())
            return r.returncode, (r.stdout + r.stderr).strip()
        except subprocess.TimeoutExpired:
            return 124, "timeout"

    def _parse(self, out):
        devs = {}
        for line in out.splitlines():
            m = self.DEVICE_RE.match(line.strip())
            if m:
                devs[m.group(1).upper()] = m.group(2).strip()
        return devs

    def _devices(self):
        return self._parse(self._ctl("devices")[1])

    def _paired_macs(self):
        for cmd in (("paired-devices",), ("devices", "Paired")):
            found = self._parse(self._ctl(*cmd)[1])
            if found:
                return set(found)
        return set()

    def prepare(self):
        self._ctl("power", "on")

    def list_devices(self, timeout):
        scan = subprocess.Popen(
            ["bluetoothctl", "--timeout", str(timeout), "scan", "on"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=host_env())
        try:
            scan.wait(timeout=timeout + 5)
        except subprocess.TimeoutExpired:
            scan.terminate()
        self._ctl("scan", "off")
        paired = self._paired_macs()
        out = []
        for mac, name in self._devices().items():
            if norm_mac(name) == norm_mac(mac):   # unnamed device
                continue
            out.append({"mac": mac, "name": name, "paired": mac in paired})
        return out

    def is_connected(self, mac):
        _, out = self._ctl("info", mac)
        m = re.search(r"Connected:\s*(yes|no)", out)
        return bool(m and m.group(1) == "yes")

    def find_known(self, mac):
        return [(m, n) for m, n in self._devices().items()
                if norm_mac(m) == norm_mac(mac)]

    def remove(self, ids):
        for mac in ids:
            print(f"[*] Disconnecting/removing {mac} ...")
            self._ctl("disconnect", mac)
            print(indent(self._ctl("remove", mac)[1]))

    def discover(self, mac, timeout):
        scan = subprocess.Popen(
            ["bluetoothctl", "--timeout", str(timeout), "scan", "on"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=host_env())
        found = None
        deadline = time.time() + timeout
        try:
            while time.time() < deadline and not found:
                time.sleep(2)
                for m, n in self._devices().items():
                    if norm_mac(m) == norm_mac(mac):
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

    def prepare(self):
        self._run("--power", "1")

    def list_devices(self, timeout):
        devices = {}
        for d in self._json("--paired"):
            devices[norm_mac(d["address"])] = {
                "mac": d["address"], "name": d.get("name") or "", "paired": True}
        for d in self._json("--inquiry", str(timeout), timeout=timeout + 20):
            key = norm_mac(d["address"])
            if key in devices:
                devices[key]["name"] = devices[key]["name"] or d.get("name") or ""
            else:
                devices[key] = {"mac": d["address"], "name": d.get("name") or "",
                                "paired": False}
        return [d for d in devices.values() if d["name"]]

    def is_connected(self, mac):
        rc, out = self._run("--is-connected", mac)
        return rc == 0 and out.strip() == "1"

    def find_known(self, mac):
        return [(d["address"], d.get("name") or "?") for d in self._json("--paired")
                if norm_mac(d["address"]) == norm_mac(mac)]

    def remove(self, ids):
        for addr in ids:
            print(f"[*] Disconnecting/unpairing {addr} ...")
            self._run("--disconnect", addr)
            print(indent(self._run("--unpair", addr)[1]))

    def discover(self, mac, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            secs = max(3, min(8, int(deadline - time.time())))
            for d in self._json("--inquiry", str(secs), timeout=secs + 20):
                if norm_mac(d["address"]) == norm_mac(mac):
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

    # Names the "kind-aware" overload may have in the Python projection
    # (dir() does not list WinRT static methods, so we probe with getattr).
    KIND_FN_CANDIDATES = (
        "find_all_async_with_kind_aqs_filter_and_additional_properties",
        "find_all_async_with_kind_and_additional_properties",
    )

    open_settings_on_failure = True   # service mode turns this off

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
        self._enum_time = 0.0

    # ---- enumeration -----------------------------------------------------
    def _run_find(self, fn, *args):
        async def go():
            return await fn(*args)
        return list(asyncio.run(go()))

    def _enumerate_aep(self):
        """Preferred: association-endpoint enumeration (paired + nearby)."""
        di = self.en.DeviceInformation
        kind = self.en.DeviceInformationKind.ASSOCIATION_ENDPOINT
        props = ["System.Devices.Aep.IsPresent", "System.Devices.Aep.IsConnected"]
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

    def _enumerate(self, max_age=0):
        """Enumerate devices; reuse the last result if it is younger than max_age s."""
        if max_age and self.infos and time.time() - self._enum_time < max_age:
            return list(self.infos.values())
        infos = self._enumerate_aep()
        if infos is None:
            infos = self._enumerate_bluetooth_selectors()
        self.infos = {i.id: i for i in infos}
        self._enum_time = time.time()
        return infos

    def _is_present(self, info):
        """True if Windows currently sees the device in range (None if unknown)."""
        try:
            return bool(info.properties["System.Devices.Aep.IsPresent"])
        except Exception:
            return None

    @staticmethod
    def _same_device(info, mac):
        return norm_mac(mac_from_id(info.id)) == norm_mac(mac)

    @staticmethod
    async def _await(op):
        return await op

    # ---- backend interface ----------------------------------------------
    def prepare(self):
        pass  # Windows manages the adapter itself; make sure Bluetooth is ON.

    def list_devices(self, timeout):
        deadline = time.time() + timeout
        seen = {}
        while True:
            for i in self._enumerate():
                mac = mac_from_id(i.id)
                if not mac or not i.name:
                    continue
                paired = bool(i.pairing.is_paired)
                if self._is_present(i) is False and not paired:
                    continue   # stale cache entry of a device that is not around
                seen[norm_mac(mac)] = {"mac": mac, "name": i.name, "paired": paired}
            if time.time() >= deadline:
                break
        return list(seen.values())

    def is_connected(self, mac):
        """True / False, or None when Windows does not report the state."""
        for i in self._enumerate():
            if i.pairing.is_paired and self._same_device(i, mac):
                try:
                    return bool(i.properties["System.Devices.Aep.IsConnected"])
                except Exception:
                    return None
        return False

    def find_known(self, mac):
        return [(i.id, i.name) for i in self._enumerate(max_age=60)
                if i.pairing.is_paired and self._same_device(i, mac)]

    def remove(self, ids):
        ok = {self.en.DeviceUnpairingResultStatus.UNPAIRED,
              self.en.DeviceUnpairingResultStatus.ALREADY_UNPAIRED}
        for dev_id in ids:
            info = self.infos[dev_id]
            print(f"[*] Unpairing {info.name} ...")
            res = asyncio.run(self._await(info.pairing.unpair_async()))
            print(f"    Result: {res.status.name}"
                  + ("" if res.status in ok else "  (failed)"))

    def discover(self, mac, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for i in self._enumerate():
                if not self._same_device(i, mac):
                    continue
                # Ignore stale cache entries: the device must really be in range
                if self._is_present(i) is False:
                    continue
                return i.id, i.name
            time.sleep(0.5)
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
                print(f"    Retry {attempt}/{attempts} (keep the device in pairing mode) ...")
                time.sleep(3)
            if attempt > 1 or dev_id not in self.infos:
                self._enumerate()
            info = self.infos.get(dev_id)
            if info is None:
                print("    Device not visible anymore; is it still in pairing mode?")
                continue
            # A leftover pairing record makes new pairing fail: remove it first
            if info.pairing.is_paired:
                print("    Stale pairing record found; unpairing it first ...")
                asyncio.run(self._await(info.pairing.unpair_async()))
                time.sleep(2)
                self._enumerate()
                info = self.infos.get(dev_id)
                if info is None:
                    print("    Device not visible after unpair; keep pairing mode on.")
                    continue
            if attempt == 1:
                # Custom pairing is the most reliable way to pair classic devices
                print("    Using custom pairing (auto-accepting any confirmation/PIN) ...")
                res = self._pair_custom(info)
            else:
                print("    Using standard pairing ...")
                res = asyncio.run(self._await(info.pairing.pair_async()))
            print(f"    Result: {res.status.name}")
            if res.status in ok:
                return True
        if self.open_settings_on_failure:
            try:
                os.startfile("ms-settings:bluetooth")
                print("    Opened Windows Bluetooth settings: Add device > Bluetooth.")
            except Exception:
                pass
        print("    Tip: also remove the device manually in Settings > Bluetooth & devices,")
        print("    then run again right after the LED starts blinking.")
        return False


# --------------------------------------------------------------------------
# Main flow
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


def repair_device(be, mac, name):
    """Remove the device (if present) and pair it again. Returns True on success."""
    name = clean_name(name) or fmt_mac(mac)
    print(f"\n=== {name} ({fmt_mac(mac)}) ===")
    print("[*] Put the device in pairing mode NOW (hold its pairing/Connect button")
    print("    until the LED blinks fast). The scan starts right after the removal.")
    t_start = t = time.time()

    def lap(label):
        nonlocal t
        now = time.time()
        print(f"    [{label}: {now - t:.1f}s]")
        t = now

    known = be.find_known(mac)
    if known:
        be.remove([dev_id for dev_id, _ in known])
    else:
        print("[!] Not paired on this system; going straight to pairing.")
    lap("remove")

    print(f"[*] Scanning for the device (up to {PAIR_TIMEOUT}s) ...")
    found = be.discover(mac, PAIR_TIMEOUT)
    lap("scan")
    if not found:
        print("[x] Device not found. Make sure it is in pairing mode and try again.")
        return False
    dev_id, dev_name = found
    print(f"[+] Found: {dev_name}")

    print("[*] Pairing ...")
    ok = be.pair(dev_id)
    lap("pair")
    print(f"[+] Done! The device is paired. (total {time.time() - t_start:.1f}s)"
          if ok else "[x] Pairing failed.")
    return ok


def choose_device(be):
    """Scan, list, and let the user pick one. Returns a device dict or None."""
    while True:
        print(f"[*] Scanning for {SCAN_TIME}s ...")
        print("    (new devices must be in pairing mode to show up)")
        devices = be.list_devices(SCAN_TIME)
        for d in devices:
            d["name"] = clean_name(d["name"])
        devices = [d for d in devices if re.search(r"\w", d["name"])]
        devices.sort(key=lambda d: (not d["paired"], d["name"].lower()))

        if devices:
            print("\nDevices found:")
            for n, d in enumerate(devices, 1):
                tag = "paired" if d["paired"] else "new"
                print(f"  {n:>2}. {d['name']}  [{fmt_mac(d['mac'])}]  ({tag})")
        else:
            print("\nNo devices found.")

        try:
            choice = input("\nNumber to select, r to rescan, q to quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice == "q":
            return None
        if choice == "r" or not choice:
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(devices):
            return devices[int(choice) - 1]
        print("Invalid choice.\n")


def run_interactive(be):
    device = choose_device(be)
    if device is None:
        print("Nothing selected.")
        return 0
    if not repair_device(be, device["mac"], device["name"]):
        return 1
    path = save_device(device["mac"], device["name"])
    print(f"[+] Device saved in {path}")
    return 0


def run_service(be):
    devices = load_devices()
    if not devices:
        print(f"[x] No saved devices in {config_path()}.")
        print("    Run without --service first to add one.")
        return 1

    # No pop-ups in unattended mode
    be.open_settings_on_failure = False

    results = []
    for d in devices:
        ok = repair_device(be, d["mac"], d.get("name") or d["mac"])
        if ok:
            save_device(d["mac"], d.get("name") or d["mac"])   # refresh timestamp
        results.append((d.get("name") or d["mac"], ok))

    print("\nSummary:")
    for name, ok in results:
        print(f"  [{'OK' if ok else 'FAILED'}] {name}")
    return 0 if all(ok for _, ok in results) else 1


def main():
    ap = argparse.ArgumentParser(
        description="Bluetooth device control: remove and re-pair devices.")
    ap.add_argument("--service", action="store_true",
                    help="re-pair every saved device, without menus or prompts")
    args = ap.parse_args()

    be = get_backend()
    print(f"[*] Platform: {platform.system()}")
    be.prepare()
    return run_service(be) if args.service else run_interactive(be)


if __name__ == "__main__":
    sys.exit(main())
