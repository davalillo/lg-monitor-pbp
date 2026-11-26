# lg-monitor-pbp

**English** · [Español](README.es.md)

![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platform: Linux / Wayland](https://img.shields.io/badge/platform-Linux%20%7C%20Wayland-informational.svg)
![Compositor: niri](https://img.shields.io/badge/compositor-niri-informational.svg)
![Tested on: LG 43UD79-B](https://img.shields.io/badge/tested%20on-LG%2043UD79--B-brightgreen.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

Switch an LG monitor between **Picture-by-Picture (PBP)** and **full screen** with
one command, and make the compositor follow.

Switching a big LG panel between a split layout and a single full-screen input is
a monitor-side operation. DDC/CI does not expose it, so every existing Linux
monitor tool (including `ddcutil`) can adjust brightness and input but cannot
rearrange the panel. The only channel that can is the monitor's RS-232C port.

This tool drives that port and then reconfigures the compositor to match: modes,
enabled outputs and positions.

```
$ lg-mode full
perfil «full»: single con hdmi-4
  monitor: kn 01 00  (pantalla completa) -> OK  00
  monitor: xb 01 93  (entrada hdmi-4)    -> OK  93
  niri: HDMI-A-5 3840x2160 @ (2048,1080) (estable)
  niri: DP-3 apagada (estable)
  guardia: 20s vigilados, 0 corrección(es)
```

## Why this exists

On a 4K panel used as two stacked halves, going full screen is a daily move —
watching something, presenting, gaming. Doing it from the monitor's own menu
takes a dozen presses on a remote. There is no software path because PBP is not a
VCP feature: it lives only behind the serial protocol, and the serial protocol is
write-only, so no tool can even read which layout you are in.

## Hardware

### Device under control

- **Monitor:** LG 43UD79 (verified on a 43UD79-B, 4K, firmware 3.48).
- **Control interface:** RS-232C over LG's proprietary 3.5 mm jack (4-contact TRRS).
- **Set ID:** `01`. With any other ID the monitor ignores every frame silently.
- **Goal:** drive the PBP/PIP mode over RS-232 and switch between the two-input
  split and a single full-screen 4K input.

### Cables and adapters

| Component | Model / reference | Role |
|---|---|---|
| LG original RS-232 cable | **EAD62707902** | LG-specific cable: 3.5 mm 4-contact TRRS jack on one end, DB9 **male** on the other |
| USB-to-serial adapter | **Prolific PL2303** (`067b:2303`) | USB to RS-232. This is the one in the verified assembly |
| USB-to-serial adapter, alternative | **FTDI FT232R** (`0403:6001`) | Second adapter used during early testing; not part of the verified chain |
| DB9 gender changer | **Female-female**, straight-through | Joins the USB adapter's DB9 male to the LG cable's DB9 male. **It crosses nothing** |
| Null-modem adapter | DB9, crossed | **The part that makes it work.** Crosses TX/RX (pins 2 and 3) |

No level shifting is involved: the LG service jack expects real RS-232 levels and
the USB adapter produces them. A TTL adapter would not work here.

### Physical connection

```text
PC (CachyOS)
    │
    └── USB
         │
         └── USB-RS232 adapter (PL2303)
              │ DB9 male
              │
              └── DB9 female-female gender changer   (straight-through)
                   │
                   └── NULL-MODEM adapter            (crosses TX/RX)  ← required
                        │
                        └── LG cable EAD62707902
                             │ TRRS 3.5 mm jack
                             │
                             └── LG 43UD79 (SERVICE / RS-232C)
```

### Serial parameters

| Parameter | Value |
|---|---|
| Device | `/dev/ttyUSB0` (`/dev/serial/by-id/usb-067b_2303-if00-port0` for the verified adapter) |
| Baud rate | 9600 |
| Data bits | 8 |
| Parity | none |
| Stop bits | 1 |
| Flow control | none |

### Pin mapping: what is known and what is not

The monitor manual requires a *crossed / reverse* cable. The pinout of the LG
`EAD62707902` cable is not publicly documented, and it was never confirmed whether
that cable crosses TX/RX internally.

That question is now settled **empirically**, which is stronger than a datasheet:

- With only the straight female-female coupler in the chain, the monitor never
  answered a single frame: no reply, no error, nothing in the kernel log.
- Adding the null-modem adapter produced replies immediately and every command
  since has been acknowledged.

Since those two configurations differ only by the crossover, the USB adapter and
the LG cable together did **not** cross TX/RX: the crossover has to be added
externally for this monitor.

A loopback test was also run — bridging the jack's tip and first ring returns the
transmitted bytes. That proves the adapter's TX/RX pair and the continuity of the
chain, but on its own it does not prove the role of each signal, which is why the
null-modem conclusion rests on the before/after comparison above.

### Video paths

Which cable feeds which input matters, because the two paths are not equivalent:

| Cable | Monitor input | Maximum |
|---|---|---|
| Laptop's rear HDMI | HDMI-4 (upper half) | **3840x2160 @ 60 Hz** |
| USB-C hub | HDMI-3 (lower half) | 3840x2160 @ 30 Hz (HDMI 1.4 link) |

```text
            ┌──────────────── laptop ────────────────┐
            │                                        │
       rear HDMI                              USB-C hub
            │                                        │
            │ HDMI 2.0 → 4K60                        │ HDMI 1.4 → 4K30
            ▼                                        ▼
   ┌──────────────── LG 43UD79-B ─────────────────────────┐
   │   HDMI-4  (upper window)      HDMI-3  (lower window) │
   └──────────────────────────────────────────────────────┘
```

This is why `full` always targets the rear HDMI input: reaching 4K60 through the
hub is a hardware impossibility, not a configuration mistake. The `links` table in
the configuration encodes exactly this wiring.

### Monitor settings used

- **RS-232C control enabled, set ID = 1** (factory default).
- `HDMI ULTRA HD Deep Color` was set to **On** along the way. It made no measurable
  difference on the hub path, and the rear HDMI negotiated 4K60 regardless, so
  treat it as unrelated rather than required.

### Commissioning procedure

If nothing replies, work through this in order. Each step isolates one link.

```bash
# 1. Is the adapter there?
ls -l /dev/serial/by-id/
dmesg | tail -5

# 2. Does the cable reach the monitor at all?  Bridge the jack's tip and first
#    ring (a paperclip will do) and send anything: returning bytes prove the
#    whole chain, TX and RX included, works.

# 3. Does the monitor parse frames?  Any reply proves it, even a rejection:
#    "a 01 NG ff" means the link is fine and the command was understood.
python3 lg_serial_probe.py --command ka --data ff

# 4. Full probe: link status plus one idempotent command.
python3 lg_serial_probe.py
```

**The most likely failure**: no null-modem in the chain. The symptom is complete
silence — no reply, no error, nothing in the kernel log — which is
indistinguishable from a dead adapter or a wrong set ID. Check the crossover first.

## Install

```bash
git clone https://github.com/davalillo/lg-monitor-pbp.git
cd lg-monitor-pbp

# pyserial is the only dependency
python3 -m pip install --user pyserial

# make the command available
ln -s "$PWD/lg_mode.py" ~/.local/bin/lg-mode

# create your configuration from the annotated template
mkdir -p ~/.config/lg-mode
cp profiles.example.toml ~/.config/lg-mode/profiles.toml
```

## Configure

Everything lives in `~/.config/lg-mode/profiles.toml` (respects
`$XDG_CONFIG_HOME`; override with `--config` or `$LG_MODE_CONFIG`).

The configuration has exactly two kinds of content: **which cable goes where**,
and **which layout you want**. It never contains monitor protocol bytes.

```toml
[links]
"HDMI-A-5" = "hdmi-4"   # compositor output -> monitor input
"DP-3"     = "hdmi-3"

[profiles.full]
layout = "single"
input  = "hdmi-4"

[profiles.pbp]
layout  = "pbp"
arrange = "vertical"
top     = "hdmi-3"
bottom  = "hdmi-4"
```

Supported intents:

| Layout | Keys |
|---|---|
| `single` | `input` |
| `pbp` | `arrange = vertical` with `top` / `bottom`, or `arrange = horizontal` with `left` / `right` |
| `pip` | `base`, `overlay`, `corner` (four corners), `size` (`small`/`medium`/`large`) |

The program derives from that the serial frames (`kn`, `xb`, `xc`, `kp`), the
modes (from the EDID the monitor publishes *after* the switch), the output
positions and the re-probing. Adding a scenario is five lines of TOML, not code.

## Usage

```bash
lg-mode full          # apply the profile named "full"
lg-mode pbp
lg-mode list          # profiles found in the configuration
lg-mode status        # what the compositor believes, plus a warning
lg-mode doctor        # re-assert the last applied profile (fix drift)
lg-mode full --dry-run
lg-mode full --config ./other.toml
```

## How the round trip is made safe

Four real problems showed up while building this, and each is handled:

1. **Re-enabling PBP makes the sub window inherit the main input.** A naive
   `PBP on` + `input` sequence silently loses one of your two sources. Both
   window inputs are therefore always set explicitly.
2. **Compositors do not re-probe a connector after an EDID change.** They keep a
   stale mode list. Outputs are toggled off/on and every property is set
   explicitly: `on`, `scale`, `mode`, `position`.
3. **Desktop shells re-apply their own output profile during the churn.** A
   single pass is not enough, so the tool converges (apply, wait, verify, repeat)
   and then watches for 20 s, correcting anything that comes back.
4. **An output left to automatic placement can land off-screen.** If the desktop
   re-enables a disabled output while the panel is in single-input mode, the
   positions overlap and the compositor moves it past the visible edge, taking
   your workspaces with it. The tool asserts the positions declared by your
   desktop configuration and keeps unused outputs off, so nothing is ever
   auto-placed.

## Verified behaviour on an LG 43UD79-B

Measured, not assumed:

- `kn 00` = PBP off, `kn 01` = side by side, `kn 02` = stacked. The values are
  not intuitive; `01` is not "PBP on".
- `xb` sets the main window's input, `xc` sets the sub window's.
- 4K60 only negotiates on a direct HDMI link. The same monitor reached through a
  USB-C hub topped out at 4K30 — an HDMI 1.4 link, not a monitor limit.
- **In PBP the monitor publishes a synthetic EDID per window**: its image size
  field claimed 60x34 cm on a 43" panel, and contradicted its own timing aspect.
  Never derive model, size or DPI from the EDID while a split layout is active.
- The serial port is write-only on this firmware. There is no command that
  reports the current layout or input, so the monitor's state cannot be verified
  — only the acknowledgements and the compositor state can.

## Limitations

- Tested only with **niri** on Wayland. The compositor layer is deliberately small
  and isolated (`niri()`, `apply_output()`, `converge()`) to make porting to
  another compositor straightforward.
- Tested only on an **LG 43UD79-B**. The protocol and the command table come from
  the LG family manual, and other models are reported to implement subsets, so
  check the acknowledgements before trusting a profile.
- `ma` (main/sub swap) is a **toggle**, not a value, so it is never sent. If your
  two inputs are in the wrong windows, fix the pairing once from the monitor menu.
- The monitor must be reachable over serial; the tool does not fall back to
  DDC/CI, because DDC/CI cannot arrange the panel at all.

## Not affiliated with LG

Unofficial project, not endorsed by or affiliated with LG Electronics. The LG
manual used during development is copyrighted and is deliberately not included in
this repository.

## License

MIT. See [LICENSE](LICENSE).
