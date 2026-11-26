# lg-monitor-pbp

[English](README.md) · **Español**

![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)
![Platform: Linux / Wayland](https://img.shields.io/badge/platform-Linux%20%7C%20Wayland-informational.svg)
![Compositor: niri](https://img.shields.io/badge/compositor-niri-informational.svg)
![Tested on: LG 43UD79-B](https://img.shields.io/badge/tested%20on-LG%2043UD79--B-brightgreen.svg)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

Cambia un monitor LG entre **Picture-by-Picture (PBP)** y **pantalla completa**
con un solo comando, y haz que el compositor lo acompañe.

Cambiar un panel LG grande entre una disposición partida y una única entrada a
pantalla completa es una operación del propio monitor. DDC/CI no la expone, así
que cualquier herramienta de monitor para Linux (incluida `ddcutil`) puede ajustar
brillo y entrada, pero no reorganizar el panel. El único canal capaz de hacerlo es
el puerto RS-232C del monitor.

Esta herramienta maneja ese puerto y después reconfigura el compositor para que
coincida: modos, salidas activas y posiciones.

```
$ lg-mode full
perfil «full»: single con hdmi-4
  monitor: kn 01 00  (pantalla completa) -> OK  00
  monitor: xb 01 93  (entrada hdmi-4)    -> OK  93
  niri: HDMI-A-5 3840x2160 @ (2048,1080) (estable)
  niri: DP-3 apagada (estable)
  guardia: 20s vigilados, 0 corrección(es)
```

## Por qué existe

En un panel 4K usado como dos mitades apiladas, pasar a pantalla completa es una
acción diaria: ver algo, presentar, jugar. Hacerlo desde el menú del monitor
supone una docena de pulsaciones en el mando. No hay vía por software porque PBP
no es una función VCP: vive únicamente detrás del protocolo serie, y ese protocolo
es de solo escritura, así que ninguna herramienta puede siquiera leer en qué
disposición estás.

## Hardware

### Equipo controlado

- **Monitor:** LG 43UD79 (verificado en un 43UD79-B, 4K, firmware 3.48).
- **Interfaz de control:** RS-232C mediante el conector propietario de 3,5 mm de LG
  (TRRS de 4 contactos).
- **Set ID:** `01`. Con cualquier otro ID el monitor ignora todas las tramas en
  silencio.
- **Objetivo:** controlar por RS-232 el modo PBP/PIP y alternar entre la
  configuración de doble entrada y una única entrada a pantalla completa 4K.

### Cables y adaptadores

| Componente | Modelo / referencia | Función |
|---|---|---|
| Cable RS-232 original LG | **EAD62707902** | Cable específico de LG: jack TRRS de 3,5 mm de 4 contactos en un extremo y DB9 **macho** en el otro |
| Adaptador USB-serie | **Prolific PL2303** (`067b:2303`) | USB a RS-232. Es el del montaje verificado |
| Adaptador USB-serie alternativo | **FTDI FT232R** (`0403:6001`) | Segundo adaptador usado en las pruebas iniciales; no forma parte de la cadena verificada |
| Cambiador de género DB9 | **Hembra-hembra**, conexión directa | Une el DB9 macho del adaptador USB con el DB9 macho del cable LG. **No cruza nada** |
| Adaptador null-modem | DB9, cruzado | **La pieza que lo hace funcionar.** Cruza TX/RX (pines 2 y 3) |

No hay conversión de niveles: el jack de servicio del LG espera niveles RS-232
reales y el adaptador USB los entrega. Un adaptador TTL no serviría aquí.

### Conexión física

```text
PC (CachyOS)
    │
    └── USB
         │
         └── Adaptador USB-RS232 (PL2303)
              │ DB9 macho
              │
              └── Cambiador de género DB9 hembra-hembra   (conexión directa)
                   │
                   └── Adaptador NULL-MODEM               (cruza TX/RX)  ← imprescindible
                        │
                        └── Cable LG EAD62707902
                             │ Jack TRRS 3,5 mm
                             │
                             └── LG 43UD79 (SERVICE / RS-232C)
```

### Parámetros serie

| Parámetro | Valor |
|---|---|
| Dispositivo | `/dev/ttyUSB0` (`/dev/serial/by-id/usb-067b_2303-if00-port0` para el adaptador verificado) |
| Velocidad | 9600 |
| Bits de datos | 8 |
| Paridad | ninguna |
| Bits de parada | 1 |
| Control de flujo | desactivado |

### Correspondencia de pines: lo que se sabe y lo que no

El manual del monitor exige un cable cruzado (*crossed / reverse*). El pinout del
cable LG `EAD62707902` no está documentado públicamente, y nunca se confirmó si
ese cable cruza TX/RX internamente.

Esa cuestión quedó resuelta **empíricamente**, lo cual vale más que un datasheet:

- Con solo el cambiador recto hembra-hembra en la cadena, el monitor no respondió a
  una sola trama: ni respuesta, ni error, ni nada en el log del kernel.
- Añadiendo el adaptador null-modem, las respuestas llegaron de inmediato y todos
  los comandos posteriores han sido confirmados.

Como esas dos configuraciones solo difieren en el cruce, el adaptador USB y el
cable LG **juntos no cruzaban TX/RX**: el cruce hay que añadirlo externamente para
este monitor.

También se hizo una prueba de loopback: puentear la punta y el primer anillo del
jack devuelve los bytes transmitidos. Eso demuestra el par TX/RX del adaptador y la
continuidad de la cadena, pero por sí solo no demuestra la función de cada señal;
por eso la conclusión sobre el null-modem se apoya en la comparación antes/después.

### Caminos de vídeo

Qué cable alimenta cada entrada importa, porque los dos caminos no son
equivalentes:

| Cable | Entrada del monitor | Máximo |
|---|---|---|
| HDMI trasera del portátil | HDMI-4 (mitad superior) | **3840x2160 @ 60 Hz** |
| Hub USB-C | HDMI-3 (mitad inferior) | 3840x2160 @ 30 Hz (enlace HDMI 1.4) |

```text
            ┌──────────────── portátil ──────────────┐
            │                                        │
       HDMI trasera                           hub USB-C
            │                                        │
            │ HDMI 2.0 → 4K60                        │ HDMI 1.4 → 4K30
            ▼                                        ▼
   ┌──────────────── LG 43UD79-B ─────────────────────────┐
   │   HDMI-4  (ventana superior)  HDMI-3  (ventana inferior) │
   └──────────────────────────────────────────────────────┘
```

Por eso `full` apunta siempre a la entrada de la HDMI trasera: alcanzar 4K60 a
través del hub es una imposibilidad física, no un error de configuración. La tabla
`links` de la configuración refleja exactamente este cableado.

### Ajustes del monitor

- **Control RS-232C activado, set ID = 1** (valor de fábrica).
- `HDMI ULTRA HD Deep Color` se puso en **On** por el camino. No produjo ninguna
  diferencia medible en el camino del hub, y la HDMI trasera negoció 4K60 igual,
  así que considéralo no relacionado en lugar de necesario.

### Procedimiento de puesta en marcha

Si no responde nada, sigue estos pasos en orden. Cada uno aísla un eslabón.

```bash
# 1. ¿Está el adaptador?
ls -l /dev/serial/by-id/
dmesg | tail -5

# 2. ¿Llega el cable hasta el monitor?  Puentea la punta y el primer anillo del
#    jack (un clip sirve) y envía cualquier cosa: los bytes que vuelven demuestran
#    que toda la cadena, TX y RX incluidos, funciona.

# 3. ¿Parsea tramas el monitor?  Cualquier respuesta lo demuestra, incluso un
#    rechazo: "a 01 NG ff" significa que el enlace está bien y el comando se
#    entendió.
python3 lg_serial_probe.py --command ka --data ff

# 4. Sonda completa: estado del enlace más un comando idempotente.
python3 lg_serial_probe.py
```

**El fallo más probable**: que falte el null-modem en la cadena. El síntoma es
silencio total — ni respuesta, ni error, ni nada en el log del kernel —, que es
indistinguible de un adaptador muerto o de un set ID equivocado. Revisa el cruce
primero.

## Instalación

```bash
git clone https://github.com/davalillo/lg-monitor-pbp.git
cd lg-monitor-pbp

# pyserial es la única dependencia
python3 -m pip install --user pyserial

# deja el comando disponible
ln -s "$PWD/lg_mode.py" ~/.local/bin/lg-mode

# crea tu configuración a partir de la plantilla comentada
mkdir -p ~/.config/lg-mode
cp profiles.example.toml ~/.config/lg-mode/profiles.toml
```

## Configuración

Todo vive en `~/.config/lg-mode/profiles.toml` (respeta `$XDG_CONFIG_HOME`; se
puede cambiar con `--config` o `$LG_MODE_CONFIG`).

La configuración tiene exactamente dos tipos de contenido: **qué cable va dónde**
y **qué disposición quieres**. Nunca contiene bytes del protocolo del monitor.

```toml
[links]
"HDMI-A-5" = "hdmi-4"   # salida del compositor -> entrada del monitor
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

Intenciones admitidas:

| Disposición | Claves |
|---|---|
| `single` | `input` |
| `pbp` | `arrange = vertical` con `top` / `bottom`, o `arrange = horizontal` con `left` / `right` |
| `pip` | `base`, `overlay`, `corner` (las cuatro esquinas), `size` (`small`/`medium`/`large`) |

A partir de eso el programa deduce las tramas serie (`kn`, `xb`, `xc`, `kp`), los
modos (del EDID que el monitor publica *después* del cambio), las posiciones de las
salidas y el re-sondeo. Añadir un escenario son cinco líneas de TOML, no código.

## Uso

```bash
lg-mode full          # aplica el perfil llamado "full"
lg-mode pbp
lg-mode list          # perfiles encontrados en la configuración
lg-mode status        # lo que cree el compositor, más un aviso
lg-mode doctor        # reafirma el último perfil aplicado (corrige desviaciones)
lg-mode full --dry-run
lg-mode full --config ./other.toml
```

## Cómo se hace seguro el viaje de ida y vuelta

Durante el desarrollo aparecieron cuatro problemas reales, y los cuatro están
resueltos:

1. **Al reactivar PBP, la ventana secundaria hereda la entrada de la principal.**
   Una secuencia ingenua de `PBP on` + `entrada` pierde una de tus dos fuentes en
   silencio. Por eso las entradas de las dos ventanas se fijan siempre de forma
   explícita.
2. **Los compositores no re-sondean un conector tras un cambio de EDID.** Se
   quedan con una lista de modos obsoleta. Las salidas se apagan y encienden, y
   cada propiedad se fija explícitamente: `on`, `scale`, `mode`, `position`.
3. **Los shells de escritorio reaplican su propio perfil de salidas durante el
   cambio.** Una sola pasada no basta, así que la herramienta converge (aplica,
   espera, verifica, repite) y después vigila 20 s corrigiendo lo que reaparezca.
4. **Una salida librada a la colocación automática puede acabar fuera de pantalla.**
   Si el escritorio reactiva una salida deshabilitada mientras el panel está en
   modo de entrada única, las posiciones se solapan y el compositor la mueve más
   allá del borde visible, llevándose tus workspaces con ella. La herramienta
   afirma las posiciones declaradas por tu configuración de escritorio y mantiene
   apagadas las salidas que no se usan, así que nada queda en colocación
   automática.

## Comportamiento verificado en un LG 43UD79-B

Medido, no supuesto:

- `kn 00` = PBP apagado, `kn 01` = lado a lado, `kn 02` = apilado. Los valores no
  son intuitivos: `01` no es «PBP activado».
- `xb` fija la entrada de la ventana principal y `xc` la de la secundaria.
- 4K60 solo se negocia por un enlace HDMI directo. El mismo monitor a través de un
  hub USB-C se quedó en 4K30: es un enlace HDMI 1.4, no un límite del monitor.
- **En PBP el monitor publica un EDID sintético por ventana**: su campo de tamaño
  de imagen declaraba 60x34 cm en un panel de 43", y contradecía la relación de
  aspecto de su propio timing. Nunca deduzcas modelo, tamaño o DPI del EDID
  mientras haya una disposición partida activa.
- En este firmware el puerto serie es de solo escritura. No hay ningún comando que
  informe de la disposición o la entrada actual, así que el estado del monitor no
  se puede verificar: solo las confirmaciones y el estado del compositor.

## Limitaciones

- Probado solo con **niri** en Wayland. La capa del compositor es deliberadamente
  pequeña y aislada (`niri()`, `apply_output()`, `converge()`) para facilitar el
  port a otro compositor.
- Probado solo en un **LG 43UD79-B**. El protocolo y la tabla de comandos vienen del
  manual de la familia LG, y se sabe que otros modelos implementan subconjuntos,
  así que revisa las confirmaciones antes de fiarte de un perfil.
- `ma` (intercambio principal/secundaria) es un **conmutador**, no un valor, así
  que nunca se envía. Si tus dos entradas están en ventanas equivocadas, corrige el
  emparejamiento una vez desde el menú del monitor.
- El monitor tiene que ser accesible por serie; la herramienta no recurre a
  DDC/CI, porque DDC/CI no puede reorganizar el panel en absoluto.

## Sin relación con LG

Proyecto no oficial, no respaldado por LG Electronics ni afiliado a la empresa. El
manual de LG usado durante el desarrollo está protegido por derechos de autor y se
ha excluido deliberadamente de este repositorio.

## Licencia

MIT. Ver [LICENSE](LICENSE).
