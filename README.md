# lg-monitor-pbp

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

| Part | Notes |
|---|---|
| USB to RS-232 adapter | Tested with a Prolific PL2303 (`067b:2303`) |
| **Null-modem** adapter | **Required.** The LG manual says "use a crossed (reverse) cable". A straight gender changer produces complete silence: the monitor answers nothing at all |
| LG service cable | DB9 male to 3.5 mm jack (4-pole) |
| Monitor | RS-232C control enabled, set ID 1 (the default) |

The serial port is not enough on its own, and the failure mode of a missing
null-modem is indistinguishable from a broken adapter: no reply, no error. If you
get silence, suspect the crossover first.

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
