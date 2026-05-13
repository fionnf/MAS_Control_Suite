#!/usr/bin/env python3
"""
Alicat probe using the 'alicat' package (async API).

Usage examples:
  # Auto-detect port and read unit 'A' (default)
  python scripts/alicat_probe.py

  # Explicit port and unit
  python scripts/alicat_probe.py --port /dev/cu.usbserial-XXXX --unit A

  # Scan units A..Z on a given port
  python scripts/alicat_probe.py --port /dev/cu.usbserial-XXXX --scan-units

  # Try a setpoint write (will send then read)
  python scripts/alicat_probe.py --port /dev/cu.usbserial-XXXX --test-setpoint 0.5
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import sys
from typing import Optional

try:
    # primary import location used by the package README
    from alicat.driver import FlowController
except Exception:
    FlowController = None

try:
    # BASIS controller if probing a BASIS device
    from alicat.basis import BASISController
except Exception:
    BASISController = None

import serial.tools.list_ports


DEFAULT_UNIT = "A"
DEFAULT_TIMEOUT = 2.0
DEFAULT_BAUD = None  # allow library defaults unless user supplies


def list_serial_ports() -> list[str]:
    return [p.device for p in serial.tools.list_ports.comports()]


async def try_open_and_get(controller_cls, address: str, unit: str, timeout: float, baud: Optional[int]):
    """
    Try to instantiate and use an async controller class.
    We attempt several common constructor kwarg names to be tolerant of small API differences.
    Returns (success: bool, message: str, reading: dict|None)
    """
    candidate_kwargs = [
        {"address": address, "unit": unit},
        {"address": address, "unit": unit, "baudrate": baud} if baud else {"address": address, "unit": unit},
        {"address": address, "unit_id": unit},
        {"port": address, "unit": unit},
        {"address": address},  # if unit specified later
    ]

    last_exc = None
    for kw in candidate_kwargs:
        # remove None values
        kw = {k: v for k, v in kw.items() if v is not None}
        try:
            # If the controller class is an async context manager, use "async with"
            if hasattr(controller_cls, "__aenter__") or inspect.isasyncgenfunction(controller_cls):
                # Try calling with kw; this may raise TypeError for unsupported kwargs
                ctrl = controller_cls(**kw)
                async with ctrl as c:
                    try:
                        # Some clients expose .get(); others expose .get_reading() ...
                        coro = None
                        for name in ("get", "get_reading", "read"):
                            if hasattr(c, name):
                                coro = getattr(c, name)
                                break
                        if coro is None:
                            return False, f"No recognized read method on controller instance ({controller_cls})", None

                        # call the method with timeout
                        reading = await asyncio.wait_for(coro(), timeout=timeout)
                        return True, f"Opened with kwargs {kw}", reading
                    except asyncio.TimeoutError:
                        return False, f"Timeout waiting for reading after opening (kwargs {kw})", None
            else:
                # Some older variants might not be async context managers (unlikely for 0.9.0),
                # attempt to construct then call async methods if available.
                ctrl = controller_cls(**kw)
                if hasattr(ctrl, "open") and inspect.iscoroutinefunction(ctrl.open):
                    await ctrl.open()
                # attempt read
                coro = None
                for name in ("get", "get_reading", "read"):
                    if hasattr(ctrl, name):
                        coro = getattr(ctrl, name)
                        break
                if coro is None:
                    return False, f"No recognized read method on controller instance ({controller_cls})", None
                reading = await asyncio.wait_for(coro(), timeout=timeout)
                # close if close exists
                if hasattr(ctrl, "close") and inspect.iscoroutinefunction(ctrl.close):
                    await ctrl.close()
                return True, f"Opened (non-asyncctx) with kwargs {kw}", reading
        except TypeError as exc:
            # constructor didn't accept those kwargs — try next signature
            last_exc = exc
            continue
        except Exception as exc:
            # other errors (IO, parse, protocol) — report and stop trying more signatures
            return False, f"Error while opening/reading with kwargs {kw}: {exc}", None

    return False, f"All constructor attempts failed. Last error: {last_exc}", None


async def probe_port(port: str, unit: str, timeout: float, baud: Optional[int], scan_units: bool, test_setpoint: Optional[float], test_gas: Optional[str]):
    print(f"Probing port: {port!s}")
    # Prefer FlowController if available
    controllers_tried = []
    if FlowController is not None:
        controllers_tried.append(("FlowController", FlowController))
    if BASISController is not None:
        controllers_tried.append(("BASISController", BASISController))

    if not controllers_tried:
        print("alicat package is installed but no FlowController/BASISController classes were importable.")
        print("Inspect the package layout or upgrade/downgrade to a compatible version.")
        return 1

    # If scan_units requested, iterate A..Z; otherwise just probe the provided unit
    units = [unit] if not scan_units else [chr(ord("A") + i) for i in range(26)]

    for ctrl_name, ctrl_cls in controllers_tried:
        print(f"\nTrying controller type: {ctrl_name}")
        for u in units:
            success, msg, reading = await try_open_and_get(ctrl_cls, port, u, timeout, baud)
            print(f"Unit {u}: {msg}")
            if reading:
                print("  Reading:", reading)
                # optionally run test commands (setpoint/gas) if supported
                if test_setpoint is not None:
                    # Re-open in a context to send setpoint (attempt common method names)
                    try:
                        # create instance with tolerant kwargs like before
                        # we try the simplest constructor first
                        inst_kw = {"address": port, "unit": u}
                        inst_kw = {k: v for k, v in inst_kw.items() if v is not None}
                        async with ctrl_cls(**inst_kw) as c:
                            # prefer set_flow_rate, set_setpoint, or setpoint write
                            if hasattr(c, "set_flow_rate"):
                                await getattr(c, "set_flow_rate")(test_setpoint)
                                print(f"  Sent set_flow_rate {test_setpoint}")
                            elif hasattr(c, "set_setpoint"):
                                await getattr(c, "set_setpoint")(test_setpoint)
                                print(f"  Sent set_setpoint {test_setpoint}")
                            else:
                                # try generic write if available
                                if hasattr(c, "write"):
                                    # format as Alicat string: <unit><value>\r
                                    cmd = f"{u}{test_setpoint:.4f}\r"
                                    maybe = getattr(c, "write")
                                    if inspect.iscoroutinefunction(maybe):
                                        await maybe(cmd.encode())
                                    else:
                                        maybe(cmd.encode())
                                    print(f"  Wrote raw setpoint command: {cmd!r}")
                            # read back a measurement after a short delay
                            await asyncio.sleep(0.2)
                            for name in ("get","get_reading"):
                                if hasattr(c, name):
                                    res = await getattr(c, name)()
                                    print("  Post-setpoint read:", res)
                                    break
                    except Exception as exc:
                        print("  Error while attempting setpoint test:", exc)

                if test_gas is not None:
                    try:
                        inst_kw = {"address": port, "unit": u}
                        inst_kw = {k: v for k, v in inst_kw.items() if v is not None}
                        async with ctrl_cls(**inst_kw) as c:
                            # set_gas or set_gas_by_index name variants
                            if hasattr(c, "set_gas"):
                                await getattr(c, "set_gas")(test_gas)
                                print(f"  Sent set_gas {test_gas!r}")
                            elif hasattr(c, "set_gas_by_index"):
                                await getattr(c, "set_gas_by_index")(int(test_gas))
                                print(f"  Sent set_gas_by_index {test_gas}")
                            else:
                                print("  No known gas-set method on controller instance.")
                    except Exception as exc:
                        print("  Error while attempting gas test:", exc)

                # If we are not scanning multiple units, exit after first successful read
                if not scan_units:
                    return 0
            # continue scanning units
    # If we reach here with no successful read:
    print("\nNo successful reading found using the alicat high-level API.")
    print("If you still have connection problems, try the low-level serial probe script or confirm cabling/adapter type.")
    return 2


def pick_port(preferred: Optional[str] = None) -> Optional[str]:
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    if preferred:
        for p in ports:
            if p.device == preferred:
                return preferred
    # look for likely USB/tty ports
    for p in ports:
        d = p.device.lower()
        desc = (p.description or "").lower()
        manu = (p.manufacturer or "").lower()
        if "alicat" in desc or "alicat" in manu:
            return p.device
    for p in ports:
        if p.device.startswith("/dev/cu.") or "usb" in (p.description or "").lower():
            return p.device
    return ports[0].device


def main(argv=None):
    parser = argparse.ArgumentParser(description="Alicat probe using the 'alicat' package")
    parser.add_argument("--port", "-p", help="Serial port or TCP address (e.g. /dev/cu.usb..., 192.168.1.100:23)")
    parser.add_argument("--unit", "-u", default=DEFAULT_UNIT, help="Device unit ID (A..Z). Use --scan-units to try A..Z.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baud rate (passed to driver if supported).")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Operation timeout (seconds)")
    parser.add_argument("--scan-units", action="store_true", help="Scan unit IDs A..Z and report responses")
    parser.add_argument("--test-setpoint", type=float, help="Send a setpoint after opening (e.g. 0.5)")
    parser.add_argument("--test-gas", help="Send a set-gas command after opening (name or index)")
    args = parser.parse_args(argv)

    port = args.port or pick_port(None)
    if not port:
        print("No serial ports found.")
        return 3

    print("Available ports:", ", ".join(list_serial_ports()))
    print("Using port:", port)
    # run the async probe
    return asyncio.run(probe_port(port, args.unit, args.timeout, args.baud, args.scan_units, args.test_setpoint, args.test_gas))


if __name__ == "__main__":
    sys.exit(main())