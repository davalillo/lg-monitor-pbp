# Contributing

Contributions are welcome, especially **hardware reports**: this project was
measured on exactly one monitor, an LG 43UD79-B.

Reports in English or Spanish are both fine.

## Reporting a new monitor model

Open a **Model compatibility report** issue. The form asks for the one thing that
cannot be guessed: what your monitor answers.

Two facts shape every report, so check them before anything else:

1. **A null-modem (crossed) adapter is mandatory.** Without it the monitor answers
   nothing at all, and that silence is indistinguishable from a dead adapter or a
   wrong set ID. A straight female-female coupler is not enough.
2. **A `NG` reply is a result, not a failure.** It proves the link works and the
   monitor parsed the frame; it just does not implement that command. Both `OK` and
   `NG` answers are equally valuable.

### What to capture

Start with commands that cannot change anything visible.

```bash
# Link check: any reply proves the chain, even a rejection
python3 lg_serial_probe.py --command ka --data ff
# -> "a 01 NG ff"  is a perfectly good result

# Idempotent write: sets mute off, safe to repeat
python3 lg_serial_probe.py --command ke --data 01

# The layout commands, one at a time, noting each answer
python3 lg_serial_probe.py --command kn --data 02   # stacked PBP
python3 lg_serial_probe.py --command xb --data 92   # main window input
python3 lg_serial_probe.py --command xc --data 93   # sub window input

# Full probe: link status plus one idempotent command
python3 lg_serial_probe.py
```

Keep the **exact bytes** in the report. `kn 01 01 -> "n 01 OK 01"` is evidence;
"PBP works" is not.

Be aware that some of these change the monitor's state, and that the serial port
cannot report the current layout, so you cannot read your way back. The monitor
menu is the way to restore something you did not want.

### Useful extras

- The monitor's own menu often shows model, firmware and set ID.
- `niri msg outputs` (or the equivalent) shows what the compositor sees, which is
  what the tool has to reconcile with.
- `journalctl --user -b | grep niri` often explains surprising layout changes: see
  the "How the round trip is made safe" section of the README.

## Contributing code

- Python 3.11 or newer. `tomllib` from the standard library is used deliberately,
  so the only runtime dependency is `pyserial`.
- Run the same checks CI runs:

  ```bash
  uvx ruff check .
  python -m compileall -q lg_mode.py lg_serial_probe.py
  python - <<'PY'
  from pathlib import Path
  import lg_mode
  config = lg_mode.load_config(Path("profiles.example.toml"))
  for name in config["profiles"]:
      plan = lg_mode.resolve_plan(name, config)
      print(name, len(plan.monitor), plan.outputs)
  PY
  ```

- **Keep profiles free of protocol detail.** A profile declares intent (`layout`,
  windows, corner, size) and the wiring; the translation into `kn`/`xb`/`xc`/`kp`
  belongs in the code. A pull request that asks users to write monitor bytes in
  their configuration will be questioned.
- **Keep the compositor layer isolated.** `niri()`, `apply_output()` and
  `converge()` are the only places that know about the compositor; porting to
  another one should not require touching the profile logic.
- **Do not fight the desktop shell.** Positions belong to the user's own display
  configuration. The tool reads them from there instead of inventing them, and
  watches for a while after a switch because that configuration gets re-applied.
- Document measured behaviour as measured. If something is unverified, say so in
  the code comment or the README rather than smoothing it over.

## Documentation

`README.md` is English and `README.es.md` is Spanish. They are kept in parity: a
change to one is expected in the other. Commands, configuration keys, device
paths and hardware references are not translated.
