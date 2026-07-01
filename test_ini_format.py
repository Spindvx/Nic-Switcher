"""Find the correct dhcpsrv.ini index scheme by testing both.

Runs dhcpsrv briefly with each format and inspects the log to see whether the
adapter was actually bound (vs 'found but not used').
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

EXE = Path(r"C:\Users\spind\OneDrive\Documents\Hobbies\NIC Switcher\vendor\dhcpsrv\dhcpsrv.exe")
RUNTIME = Path.home() / "AppData" / "Roaming" / "NICSwitcher"
INI = RUNTIME / "dhcpsrv.ini"
LOG = RUNTIME / "dhcpsrv.log"

BIND_IP = "192.168.50.75"  # from user's current Ethernet
POOL_START = "192.168.50.100"
POOL_END_OCTET = "200"
MASK = "255.255.255.0"

FORMATS = {
    "1-indexed (current)": f"""
[SETTINGS]
IPBIND_1={BIND_IP}
IPPOOL_1={POOL_START}-{POOL_END_OCTET}
AssociateBindsToPools=1
Trace=1
TraceFile={LOG}

[GENERAL]
SUBNETMASK={MASK}
LEASETIME=3600
NODETYPE=8
""",
    "0-indexed": f"""
[SETTINGS]
IPBIND_0={BIND_IP}
IPPOOL_0={POOL_START}-{POOL_END_OCTET}
AssociateBindsToPools=1
Trace=1
TraceFile={LOG}

[GENERAL]
SUBNETMASK={MASK}
LEASETIME=3600
NODETYPE=8
""",
    "mixed (bind_0, pool_0)": f"""
[SETTINGS]
IPPOOL_0={POOL_START}-{POOL_END_OCTET}
IPBIND_0={BIND_IP}
AssociateBindsToPools=1
Trace=1
TraceFile={LOG}

[GENERAL]
SUBNETMASK={MASK}
LEASETIME=3600
NODETYPE=8
""",
}


def test_format(name: str, content: str) -> tuple[bool, str]:
    # Kill any existing dhcpsrv
    subprocess.run(["taskkill", "/F", "/IM", "dhcpsrv.exe"],
                   capture_output=True, creationflags=0x08000000)
    time.sleep(0.3)

    # Write fresh ini
    INI.write_text(content.strip() + "\n", encoding="utf-8")

    # Clear log
    if LOG.exists():
        LOG.unlink()

    # Launch
    proc = subprocess.Popen(
        [str(EXE), "-runapp", "-ini", str(INI)],
        cwd=str(EXE.parent),
        creationflags=0x08000000,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(4.0)

    # Read log
    try:
        log_text = LOG.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        log_text = "(no log file written)"

    # Kill process
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

    # Analyze log — 'used' (or similar success marker) vs 'not used'
    bound = False
    for line in log_text.splitlines():
        if BIND_IP in line and ("used" in line.lower() and "not used" not in line.lower()):
            bound = True
            break

    return bound, log_text


for name, content in FORMATS.items():
    print(f"\n{'=' * 60}\n{name}\n{'=' * 60}")
    bound, log = test_format(name, content)
    print(f"  BOUND: {bound}")
    print("  --- log ---")
    print(log)

# cleanup
subprocess.run(["taskkill", "/F", "/IM", "dhcpsrv.exe"],
               capture_output=True, creationflags=0x08000000)
