#!/usr/bin/env python3
"""Switch an LG 43UD79 between display scenarios, by intent.

A profile says WHAT you want, never HOW it is done:

    [profiles.full]
    layout = "single"
    input  = "hdmi-4"

    [profiles.pbp]
    layout  = "pbp"
    arrange = "vertical"
    top     = "hdmi-3"
    bottom  = "hdmi-4"

The monitor's serial commands (`kn`, `xb`, `xc`, `kp`), the compositor modes,
the positions and the re-probing are derived from that plus the physical wiring
declared once in `[links]`.

Configuration lives in $XDG_CONFIG_HOME/lg-mode/profiles.toml
(~/.config/lg-mode/profiles.toml), overridable with --config or
LG_MODE_CONFIG. The repository ships profiles.example.toml as a template.

Hardware facts this was built against (measured, see project memory):
  * only the rear HDMI cable reaches 4K60; the USB-C hub path tops out at 4K30;
  * PBP re-publishes a synthetic EDID per window, so panel size cannot be read
    from it while a split layout is active;
  * re-enabling PBP makes the sub window inherit the main input, so both window
    inputs are always set explicitly;
  * niri caches the mode list per connector and does not re-probe on its own;
  * DMS re-applies its own output profile during the EDID churn, so the final
    state is applied and re-checked until it holds.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import serial

SERIAL_PORT = "/dev/ttyUSB0"
BAUD = 9600
SET_ID = 1
LAPTOP_OUTPUT = "eDP-1"

# Monitor input names -> serial data byte (43UD79 manual, input select table).
INPUT_CODES = {
    "hdmi-1": "90",
    "hdmi-2": "91",
    "hdmi-3": "92",
    "hdmi-4": "93",
    "displayport": "C0",
    "dp": "C0",
    "usb-c": "E0",
    "usbc": "E0",
}

# (layout, arrange/corner) -> `kn` data byte.
LAYOUT_CODES = {
    ("single", "none"): "00",
    ("pbp", "horizontal"): "01",
    ("pbp", "vertical"): "02",
    ("pip", "top-left"): "03",
    ("pip", "top-right"): "04",
    ("pip", "bottom-left"): "05",
    ("pip", "bottom-right"): "06",
}

PIP_SIZES = {"small": "00", "medium": "01", "large": "02"}

SERIAL_ATTEMPTS = 3
NIRI_ATTEMPTS = 2
CONVERGE_PASSES = 4
HALF_MIN_REFRESH_MHZ = 50_000


class Failure(RuntimeError):
    pass


@dataclass
class Ack:
    status: str
    data: str

    @property
    def ok(self) -> bool:
        return self.status == "OK"


@dataclass
class Plan:
    """Everything needed to reach one profile, already translated."""

    name: str
    monitor: list[tuple[str, str, str]]  # (command, data, description)
    inputs: list[str]  # monitor inputs taking part, in layout order
    outputs: list[str]  # compositor outputs taking part, same order
    arrange: str
    pip_size: str | None = None

    @property
    def participants(self) -> list[str]:
        return self.outputs


# --------------------------------------------------------------------------
# serial
# --------------------------------------------------------------------------


def serial_send(command: str, data: str) -> Ack:
    frame = f"{command} {SET_ID:02X} {data}\r".encode("ascii")
    last = Ack("NO", "")
    for attempt in range(1, SERIAL_ATTEMPTS + 1):
        with serial.Serial(
            SERIAL_PORT, BAUD, timeout=1.5, bytesize=8, parity="N", stopbits=1
        ) as port:
            port.reset_input_buffer()
            port.write(frame)
            raw = port.read_until(b"\r").decode("ascii", errors="replace").strip()
        if len(raw) >= 7 and raw[5:7] in ("OK", "NG"):
            return Ack(raw[5:7], raw[7:])
        last = Ack("NO", raw)
        if attempt < SERIAL_ATTEMPTS:
            time.sleep(0.4)
    return last


def monitor(command: str, data: str, what: str) -> None:
    ack = serial_send(command, data)
    print(f"  monitor: {command} {SET_ID:02X} {data}  ({what}) -> {ack.status} {ack.data}".rstrip())
    if not ack.ok:
        raise Failure(f"el monitor rechazó {command} {data} para «{what}»: {ack.status} {ack.data}".strip())


# --------------------------------------------------------------------------
# compositor
# --------------------------------------------------------------------------


def niri(*args: str) -> str:
    result = subprocess.run(["niri", "msg", *args], capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise Failure(f"niri msg {' '.join(args)} falló: {output}")
    return output


def outputs() -> dict:
    result = subprocess.run(["niri", "msg", "-j", "outputs"], capture_output=True, text=True)
    if result.returncode != 0:
        raise Failure(f"no se pudo leer el estado de niri: {result.stderr.strip()}")
    return json.loads(result.stdout)


def logical(name: str, state: dict | None = None) -> dict | None:
    state = state if state is not None else outputs()
    return (state.get(name) or {}).get("logical")


def modes_of(name: str, state: dict) -> list[dict]:
    return list((state.get(name) or {}).get("modes") or [])


def preferred_mode(name: str, state: dict) -> dict | None:
    """The monitor's declared preferred timing, with a sane fallback."""
    modes = modes_of(name, state)
    for mode in modes:
        if mode.get("is_preferred"):
            return mode
    if not modes:
        return None
    return max(modes, key=lambda m: (m["width"] * m["height"], m["refresh_rate"]))


def mode_string(mode: dict) -> str:
    return f"{mode['width']}x{mode['height']}@{mode['refresh_rate'] / 1000:.3f}"


def refresh_output(name: str, rounds: int = 4) -> None:
    """Force a fresh EDID read; niri keeps the stale mode list otherwise."""
    for round_number in range(1, rounds + 1):
        state = outputs()
        if preferred_mode(name, state):
            return
        print(f"  niri: {name} sin modos utilizables, re-sondeando ({round_number}/{rounds})")
        niri("output", name, "off")
        time.sleep(1.5)
        niri("output", name, "on")
        time.sleep(2.5)
    raise Failure(f"{name} no publicó ningún modo tras re-sondear")


def apply_output(name: str, mode: str, position: tuple[int, int] | None) -> bool:
    width, height = (int(value) for value in mode.split("@")[0].split("x"))
    for _ in range(NIRI_ATTEMPTS):
        niri("output", name, "on")
        niri("output", name, "scale", "1")
        try:
            niri("output", name, "mode", mode)
        except Failure:
            pass
        if position is not None:
            niri("output", name, "position", "set", str(position[0]), str(position[1]))
        time.sleep(1.5)
        current = logical(name)
        if current and (current["width"], current["height"]) == (width, height):
            return True
    return False


def matches(desired: dict[str, tuple[str, tuple[int, int] | None] | None]) -> bool:
    state = outputs()
    for name, target in desired.items():
        current = logical(name, state)
        if target is None:
            if current:
                return False
            continue
        mode, position = target
        width, height = (int(value) for value in mode.split("@")[0].split("x"))
        if not current or (current["width"], current["height"]) != (width, height):
            return False
        if position is not None and (current["x"], current["y"]) != position:
            return False
    return True


def converge(
    desired: dict[str, tuple[str, tuple[int, int] | None] | None],
    passes: int = CONVERGE_PASSES,
) -> None:
    """Apply, wait and re-check: DMS re-applies its profile during EDID churn."""
    for attempt in range(1, passes + 1):
        for name, target in desired.items():
            if target is None:
                niri("output", name, "off")
            else:
                mode, position = target
                apply_output(name, mode, position)
        time.sleep(2.5)
        if matches(desired):
            for name, target in desired.items():
                if target is None:
                    print(f"  niri: {name} apagada (estable)")
                else:
                    mode, position = target
                    where = f" @ ({position[0]},{position[1]})" if position else " (posición según DMS)"
                    print(f"  niri: {name} {mode.split('@')[0]}{where} (estable)")
            return
        print(f"  niri: el compositor reaplicó su configuración, recolocando ({attempt}/{passes})")
    raise Failure("la disposición no se estabiliza: algo la reaplica constantemente")


def state_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "lg-mode" / "last-profile"


def remember(profile: str) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile + "\n")


def recall() -> str | None:
    path = state_path()
    return path.read_text().strip() if path.exists() else None


def guard(desired: dict, seconds: float, interval: float = 3.0) -> None:
    """Keep the state after the switch: the desktop re-applies its own profile.

    Anything that comes back gets corrected, so nothing is left to niri's
    automatic placement and no workspace can land on an invisible output.
    """
    if seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    catches = 0
    while time.monotonic() < deadline:
        time.sleep(interval)
        if matches(desired):
            continue
        catches += 1
        print(f"  guardia: el compositor desvió el estado; recolocando (corrección {catches})")
        for name, target in desired.items():
            if target is None:
                niri("output", name, "off")
            else:
                mode, position = target
                apply_output(name, mode, position)
    print(f"  guardia: {seconds:.0f}s vigilados, {catches} corrección(es)")


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get("LG_MODE_CONFIG")
    if from_env:
        return Path(from_env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "lg-mode" / "profiles.toml"


def load_config(path: Path) -> dict:
    if not path.exists():
        raise Failure(
            f"no encuentro la configuración en {path}\n"
            "  copia profiles.example.toml del repositorio a esa ruta, o usa --config"
        )
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    links = data.get("links") or {}
    if not links:
        raise Failure("la configuración no declara [links]: sin el cableado no puedo deducir nada")
    for output, monitor_input in links.items():
        if str(monitor_input).lower() not in INPUT_CODES:
            valid = ", ".join(sorted(INPUT_CODES))
            raise Failure(f"entrada desconocida «{monitor_input}» en [links]; válidas: {valid}")

    profiles = data.get("profiles") or {}
    if not profiles:
        raise Failure("la configuración no declara ningún [profiles.<nombre>]")

    for name, profile in profiles.items():
        layout = str(profile.get("layout", "")).lower()
        if layout not in {"single", "pbp", "pip"}:
            raise Failure(f"perfil «{name}»: layout «{layout}» no soportado (single, pbp, pip)")
    return data


def resolve_plan(name: str, config: dict) -> Plan:
    profiles = config["profiles"]
    if name not in profiles:
        raise Failure(f"no existe el perfil «{name}»; disponibles: {', '.join(sorted(profiles))}")
    profile = profiles[name]
    links = {str(k): str(v).lower() for k, v in config["links"].items()}
    by_input = {monitor_input: output for output, monitor_input in links.items()}

    layout = str(profile["layout"]).lower()
    monitor_commands: list[tuple[str, str, str]] = []
    inputs: list[str] = []

    if layout == "single":
        input_name = str(profile.get("input", "")).lower()
        if input_name not in INPUT_CODES:
            raise Failure(f"perfil «{name}»: input «{input_name}» desconocido")
        outputs_involved = [by_input.get(input_name)]
        if not outputs_involved[0]:
            raise Failure(f"perfil «{name}»: ninguna salida declara la entrada «{input_name}» en [links]")
        monitor_commands = [
            ("kn", LAYOUT_CODES[("single", "none")], "pantalla completa"),
            ("xb", INPUT_CODES[input_name], f"entrada {input_name}"),
        ]
        inputs = [input_name]
        arrange = "single"

    elif layout == "pbp":
        arrange = str(profile.get("arrange", "vertical")).lower()
        if arrange == "vertical":
            first, second = profile.get("top"), profile.get("bottom")
        elif arrange == "horizontal":
            first, second = profile.get("left"), profile.get("right")
        else:
            raise Failure(f"perfil «{name}»: arrange «{arrange}» no válido (vertical, horizontal)")
        if not first or not second:
            keys = "top/bottom" if arrange == "vertical" else "left/right"
            raise Failure(f"perfil «{name}»: faltan las dos ventanas ({keys})")
        first, second = str(first).lower(), str(second).lower()
        for value in (first, second):
            if value not in INPUT_CODES:
                raise Failure(f"perfil «{name}»: entrada «{value}» desconocida")
        for value in (first, second):
            if not by_input.get(value):
                raise Failure(f"perfil «{name}»: ninguna salida declara la entrada «{value}» en [links]")
        monitor_commands = [
            ("kn", LAYOUT_CODES[("pbp", arrange)], f"PBP {arrange}"),
            ("xb", INPUT_CODES[first], f"ventana 1 = {first}"),
            ("xc", INPUT_CODES[second], f"ventana 2 = {second}"),
        ]
        inputs = [first, second]

    else:  # pip
        corner = str(profile.get("corner", "bottom-right")).lower()
        if ("pip", corner) not in LAYOUT_CODES:
            raise Failure(f"perfil «{name}»: corner «{corner}» no válido")
        base = str(profile.get("base", "")).lower()
        overlay = str(profile.get("overlay", "")).lower()
        size = str(profile.get("size", "medium")).lower()
        if size not in PIP_SIZES:
            raise Failure(f"perfil «{name}»: size «{size}» no válido (small, medium, large)")
        for value in (base, overlay):
            if value not in INPUT_CODES:
                raise Failure(f"perfil «{name}»: entrada «{value}» desconocida")
            if not by_input.get(value):
                raise Failure(f"perfil «{name}»: ninguna salida declara la entrada «{value}» en [links]")
        monitor_commands = [
            ("kn", LAYOUT_CODES[("pip", corner)], f"PIP {corner}"),
            ("xb", INPUT_CODES[base], f"base = {base}"),
            ("xc", INPUT_CODES[overlay], f"solapa = {overlay}"),
            ("kp", PIP_SIZES[size], f"tamaño {size}"),
        ]
        inputs = [base, overlay]
        arrange = corner
        return Plan(name, monitor_commands, inputs, [by_input[v] for v in inputs], arrange, size)

    return Plan(name, monitor_commands, inputs, [by_input[v] for v in inputs], arrange)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


DMS_PROFILE = Path.home() / ".config/niri/dms/outputs.kdl"


def declared_positions(path: Path | None = None) -> dict[str, tuple[int, int] | None]:
    """Positions declared by the desktop's own display configuration.

    Taken from DMS: it owns them and re-applies them. Reusing its values means
    we assert the same layout instead of leaving niri free to auto-place an
    output that overlapped — which is how a workspace ended up at x=5888.
    """
    target = path or DMS_PROFILE
    if not target.exists():
        return {}
    try:
        text = target.read_text()
    except OSError:
        return {}
    result: dict[str, tuple[int, int] | None] = {}
    for match in re.finditer(r'output "([^"]+)"\s*\{(.*?)\n\}', text, re.DOTALL):
        name, body = match.group(1), match.group(2)
        if re.search(r"^\s*off\s*$", body, re.MULTILINE):
            result[name] = None
            continue
        position = re.search(r"position\s+x=(-?\d+)\s+y=(-?\d+)", body)
        result[name] = (int(position.group(1)), int(position.group(2))) if position else None
    return result


def block_origin(state: dict, total_height: int, anchor: str) -> tuple[int, int]:
    laptop = logical(LAPTOP_OUTPUT, state) or {"x": 0, "y": 0, "width": 1920, "height": 1080}
    x = laptop["x"] + laptop["width"]
    if anchor == "top":
        y = laptop["y"]
    elif anchor == "bottom":
        y = laptop["y"] + laptop["height"] - total_height
    else:
        y = laptop["y"] + laptop["height"] // 2 - total_height // 2
    return x, y


def build_desired(
    plan: Plan, config: dict, state: dict, pre_state: dict | None = None
) -> dict[str, tuple[str, tuple[int, int] | None] | None]:
    """Modes always; positions only when the configuration asks for them.

    Positions belong to the desktop's own display configuration (DMS writes them
    and re-applies them). Fighting that would make the layout oscillate, so by
    default the tool changes modes and on/off state and leaves positions alone.
    """
    modes = {}
    for output in plan.participants:
        mode = preferred_mode(output, state)
        if not mode:
            raise Failure(f"{output} no publica ningún modo utilizable")
        modes[output] = mode

    layout_config = config.get("layout", {})
    manage_positions = "origin" in layout_config
    positions_source = Path(layout_config.get("positions_from", str(DMS_PROFILE))).expanduser()
    declared = {} if manage_positions else declared_positions(positions_source)
    anchor = str(layout_config.get("anchor", "center")).lower()
    heights = [modes[o]["height"] for o in plan.participants]
    widths = [modes[o]["width"] for o in plan.participants]

    if manage_positions:
        origin = tuple(int(v) for v in layout_config["origin"])
    else:
        origin = None

    desired: dict[str, tuple[str, tuple[int, int] | None] | None] = {}

    def where(name: str) -> tuple[int, int] | None:
        if origin is not None:
            return origin
        return declared.get(name)

    if plan.arrange == "single":
        desired[plan.participants[0]] = (mode_string(modes[plan.participants[0]]), where(plan.participants[0]))
        if len(plan.participants) > 1:
            # PIP overlay: the monitor composites it; the output just needs a signal.
            desired[plan.participants[1]] = (
                mode_string(modes[plan.participants[1]]),
                where(plan.participants[1]),
            )
    elif plan.arrange == "vertical":
        desired[plan.participants[0]] = (mode_string(modes[plan.participants[0]]), where(plan.participants[0]))
        desired[plan.participants[1]] = (
            mode_string(modes[plan.participants[1]]),
            (origin[0], origin[1] + heights[0]) if origin else declared.get(plan.participants[1]),
        )
    else:  # horizontal
        desired[plan.participants[0]] = (mode_string(modes[plan.participants[0]]), where(plan.participants[0]))
        desired[plan.participants[1]] = (
            mode_string(modes[plan.participants[1]]),
            (origin[0] + widths[0], origin[1]) if origin else declared.get(plan.participants[1]),
        )

    for output in config["links"]:
        if output not in desired:
            desired[output] = None
    return desired


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def check_staleness(plan: Plan) -> None:
    """Detect a window that kept the EDID of a previous layout."""
    state = outputs()
    if len(plan.participants) == 2:
        first, second = (preferred_mode(name, state) for name in plan.participants)
        if not first or not second:
            for name in plan.participants:
                refresh_output(name)
            return
        if (first["width"], first["height"]) != (second["width"], second["height"]):
            print("  aviso: las dos ventanas no anuncian el mismo tamaño; re-sondeando")
            for name in plan.participants:
                refresh_output(name)
        return

    mode = preferred_mode(plan.participants[0], state)
    if mode and mode["width"] >= 3000 and mode["height"] <= 1200:
        print(f"  aviso: {plan.participants[0]} sigue anunciando una media pantalla; re-sondeando")
        refresh_output(plan.participants[0])


def warn_about_link(plan: Plan, desired: dict) -> None:
    for output, target in desired.items():
        if target is None:
            continue
        mode, _ = target
        refresh = float(mode.split("@")[1])
        if refresh + 0.01 < HALF_MIN_REFRESH_MHZ / 1000:
            print(
                f"  aviso: {output} solo alcanza {refresh:.3f} Hz en este escenario "
                "(el hub USB-C topa en 4K30; la HDMI trasera da 4K60)"
            )


def run_profile(name: str, config: dict, dry_run: bool) -> int:
    plan = resolve_plan(name, config)
    print(f"perfil «{plan.name}»: {plan.arrange} con {', '.join(plan.inputs)}")
    pre_state = outputs()

    if dry_run:
        for command, data, what in plan.monitor:
            print(f"  [dry-run] monitor: {command} {SET_ID:02X} {data}  ({what})")
        try:
            desired = build_desired(plan, config, pre_state, pre_state)
        except Failure as error:
            print(f"  [dry-run] geometría no deducible desde el estado actual: {error}")
            print("  [dry-run] el modo real se conoce después de que el monitor cambie")
            return 0
        if plan.arrange == "single":
            print("  [dry-run] el modo real se relee tras el cambio (aquí aún está partido)")
        for output, target in desired.items():
            if target is None:
                print(f"  [dry-run] niri: {output} off")
            else:
                mode, position = target
                where = f" @ ({position[0]},{position[1]})" if position else " (posición según DMS)"
                print(f"  [dry-run] niri: {output} {mode}{where}")
        warn_about_link(plan, desired)
        return 0

    for command, data, what in plan.monitor:
        monitor(command, data, what)
    time.sleep(2.5)
    check_staleness(plan)
    desired = build_desired(plan, config, outputs(), pre_state)
    warn_about_link(plan, desired)
    converge(desired)
    remember(plan.name)
    guard(desired, float(config.get("layout", {}).get("guard_seconds", 20)))
    return 0


def list_profiles(config: dict) -> int:
    for name in sorted(config["profiles"]):
        profile = config["profiles"][name]
        layout = profile.get("layout")
        detail = ", ".join(f"{k}={v}" for k, v in profile.items() if k != "layout")
        print(f"{name:12} {layout:8} {detail}")
    return 0


def do_doctor(config: dict, dry_run: bool) -> int:
    name = recall()
    if not name:
        raise Failure("no sé qué perfil se aplicó la última vez; ejecuta un perfil y luego doctor")
    if name not in config["profiles"]:
        raise Failure(f"el último perfil «{name}» ya no existe en la configuración")
    plan = resolve_plan(name, config)
    print(f"doctor: revisando el escenario «{name}» ({plan.arrange} con {', '.join(plan.inputs)})")
    state = outputs()
    desired = build_desired(plan, config, state, state)
    if dry_run:
        for output, target in desired.items():
            if target is None:
                print(f"  [dry-run] niri: {output} off")
            else:
                mode, position = target
                where = f" @ ({position[0]},{position[1]})" if position else " (sin posición declarada)"
                print(f"  [dry-run] niri: {output} {mode}{where}")
        return 0
    if matches(desired):
        print("  todo coherente, nada que corregir")
        return 0
    print("  desviación detectada, recolocando")
    converge(desired)
    guard(desired, float(config.get("layout", {}).get("guard_seconds", 20)))
    return 0


def show_status(_: dict) -> int:
    state = outputs()
    for name in sorted(state):
        entry = state[name]
        current = entry.get("logical") or {}
        mode = preferred_mode(name, state)
        if current:
            refresh = f" (preferido {mode_string(mode)})" if mode else ""
            print(
                f"{name:9} activa    {current['width']}x{current['height']} "
                f"@ ({current['x']},{current['y']}) scale {current['scale']}{refresh}"
            )
        else:
            print(f"{name:9} apagada")
    print("\nnota: el monitor no informa de su PBP ni de su entrada; esto describe el compositor.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cambia el escenario del monitor LG. Los perfiles se leen de la configuración."
    )
    parser.add_argument("profile", help="nombre del perfil, o «list» / «status»")
    parser.add_argument("--config", help="ruta alternativa a profiles.toml")
    parser.add_argument("--dry-run", action="store_true", help="imprime los pasos sin tocar nada")
    args = parser.parse_args()

    try:
        path = config_path(args.config)
        config = load_config(path)
        print(f"config: {path}")
        if args.profile == "list":
            return list_profiles(config)
        if args.profile == "status":
            return show_status(config)
        if args.profile == "doctor":
            return do_doctor(config, args.dry_run)
        return run_profile(args.profile, config, args.dry_run)
    except Failure as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except serial.SerialException as error:
        print(f"ERROR de puerto serie: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
