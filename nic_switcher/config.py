"""Config persistence — presets, selected NIC, DHCP settings."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


def _config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    d = Path(base) / "NICSwitcher"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_PATH = _config_dir() / "config.json"


@dataclass
class Preset:
    name: str
    ip: str
    prefix: int = 24
    gateway: str = ""
    dns1: str = ""
    dns2: str = ""
    # MAC override for this preset, 12 hex chars (e.g. 'AABBCCDDEEFF') or one
    # of two sentinels interpreted by nic.apply_preset:
    #   ""           — don't touch MAC (fastest path, no adapter restart)
    #   "restore"    — remove any override, restore hardware MAC
    #   "<12 hex>"   — apply this override
    mac: str = ""

    @property
    def subnet_mask(self) -> str:
        bits = 0xFFFFFFFF ^ ((1 << (32 - self.prefix)) - 1)
        return ".".join(str((bits >> (8 * i)) & 0xFF) for i in (3, 2, 1, 0))


@dataclass
class DhcpConfig:
    # Empty = prefer bundled dhcpsrv.exe (via sys._MEIPASS). User can override
    # in the DHCP settings dialog to point at an existing install.
    exe_path: str = ""
    bind_ip: str = ""
    range_start: str = ""
    range_end: str = ""
    subnet_mask: str = "255.255.255.0"
    gateway: str = ""
    dns: str = "8.8.8.8"
    lease_seconds: int = 86400


@dataclass
class AppConfig:
    selected_nic: Optional[str] = None
    presets: list[Preset] = field(default_factory=list)
    dhcp: DhcpConfig = field(default_factory=DhcpConfig)
    auto_start: bool = False
    # On first run we register the app to launch at Windows login. Once
    # we've done that (or the user turned it off), we never auto-touch
    # the registry value again — they own that toggle from then on.
    boot_setup_done: bool = False

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.presets = _default_presets()
            cfg.save()
            return cfg
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # Filter unknown keys so a config written by a future version
            # with new Preset fields doesn't wipe the user's presets here.
            preset_fields = set(Preset.__dataclass_fields__)
            dhcp_fields = set(DhcpConfig.__dataclass_fields__)
            return cls(
                selected_nic=data.get("selected_nic"),
                presets=[
                    Preset(**{k: v for k, v in p.items() if k in preset_fields})
                    for p in data.get("presets", [])
                ],
                dhcp=DhcpConfig(**{
                    k: v for k, v in data.get("dhcp", {}).items()
                    if k in dhcp_fields
                }),
                auto_start=data.get("auto_start", False),
            )
        except Exception:
            # Preserve the corrupt file alongside so the user can recover presets
            # instead of silently losing them on next save.
            try:
                backup = CONFIG_PATH.with_suffix(".json.corrupt")
                CONFIG_PATH.rename(backup)
            except Exception:
                pass
            return cls(presets=_default_presets())

    def save(self) -> None:
        data = {
            "selected_nic": self.selected_nic,
            "presets": [asdict(p) for p in self.presets],
            "dhcp": asdict(self.dhcp),
            "auto_start": self.auto_start,
        }
        # Atomic write: tmp file + os.replace. Never leaves a half-written config.
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)


def _default_presets() -> list[Preset]:
    return [
        Preset(name="Connect NZ", ip="10.17.75.240", prefix=24, gateway="10.17.75.1"),
        Preset(name="DHCP", ip="", prefix=0),
    ]
