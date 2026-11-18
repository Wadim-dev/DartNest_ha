"""Sound effects for the speakers: a sound for every dart (the better the dart, the bigger the
sound) and a sound for big moments (180, ton-forty, bust, checkout, match …).

The sounds are made here once (plain Python, no files to download) and saved as WAV in the
media folder autodarts/toene, so the speakers and phones can play them like the caller's sounds.
A new set of sounds gets a new VERSION, older files are removed.
"""

from __future__ import annotations

import array
import math
from pathlib import Path
import random
import wave

VERSION = 1
RATE = 44100
ORDNER = ("autodarts", "toene")

# sound of a dart, from its field (M, S1…S20, D1…D20, T1…T20, S25, D25)
DART_TOENE = ("dart_miss", "dart_s", "dart_s_hoch", "dart_d", "dart_t", "dart_t20", "dart_bull", "dart_bullseye")
# sounds of a visit: (lead in front of the voice, bed under the voice)
MOMENTE = {
    "match": ("m_match", "m_jubel_lang"),
    "checkout": ("m_checkout", "m_jubel"),
    "180": ("m_180", "m_jubel_lang"),
    "ton_forty": ("m_140", "m_jubel"),
    "ton": ("m_100", None),
    "bust": ("m_bust", None),
    "no_score": ("m_noscore", None),
}
RISER = "m_riser"  # two big trebles in a visit: the third dart can make it (180 …)
HERZ = "m_herz"  # the next player is on a finish: heartbeat
ALLE = (*DART_TOENE, *sorted({k for m in MOMENTE.values() for k in m if k}), RISER, HERZ)


def dart_ton(feld: str) -> str:
    """Name of the sound of a dart: the better the dart, the bigger the sound."""
    f = str(feld or "M").upper()
    if f in ("M", "", "S0"):
        return "dart_miss"
    if f == "D25":
        return "dart_bullseye"
    if f == "S25":
        return "dart_bull"
    try:
        zahl = int(f[1:])
    except ValueError:
        return "dart_s"
    if f[0] == "T":
        return "dart_t20" if zahl >= 19 else "dart_t"
    if f[0] == "D":
        return "dart_d"
    return "dart_s_hoch" if zahl >= 15 else "dart_s"


def riser(darts: list[str]) -> bool:
    """After two darts of a visit: both in the treble 20 or 19 (the third one can make 180 / 171 / 177 …)."""
    return len(darts) == 2 and all(str(d).upper() in ("T20", "T19") for d in darts)


def moment(score: int, bust: bool, checkout: bool, match: bool, darts: int) -> str | None:
    """Key of the sound for a finished visit (see MOMENTE), or None for an ordinary one."""
    if match:
        return "match"
    if checkout:
        return "checkout"
    if bust:
        return "bust"
    if score == 180:
        return "180"
    if score >= 140:
        return "ton_forty"
    if score >= 100:
        return "ton"
    if score == 0 and darts >= 3:
        return "no_score"
    return None


# ---------- making the sounds ----------

def _leer(sek: float) -> list[float]:
    return [0.0] * int(RATE * sek)


def _dazu(ziel: list[float], quelle: list[float], ab: float = 0.0, laut: float = 1.0) -> list[float]:
    start = int(RATE * ab)
    if len(ziel) < start + len(quelle):
        ziel.extend([0.0] * (start + len(quelle) - len(ziel)))
    for i, v in enumerate(quelle):
        ziel[start + i] += v * laut
    return ziel


def _huelle(n: int, an: float, ab: float, halten: float = 0.0) -> list[float]:
    """Envelope: rise in `an` seconds, hold, then fall exponentially over `ab` seconds."""
    a, h = max(1, int(RATE * an)), int(RATE * halten)
    out = []
    for i in range(n):
        if i < a:
            out.append(i / a)
        elif i < a + h:
            out.append(1.0)
        else:
            out.append(math.exp(-(i - a - h) / (RATE * ab / 5)))
    return out


def _ton(frei: float, sek: float, form: str = "sinus", bis: float | None = None, an: float = 0.005,
         ab: float | None = None, halten: float = 0.0, vibrato: float = 0.0) -> list[float]:
    """One tone; `bis` glides the pitch, `form` sinus/dreieck/saege/glocke."""
    n = int(RATE * sek)
    huelle = _huelle(n, an, ab if ab is not None else sek, halten)
    out, phase = [], 0.0
    for i in range(n):
        t = i / n
        f = frei * ((bis / frei) ** t) if bis else frei
        if vibrato:
            f *= 1 + 0.012 * math.sin(2 * math.pi * vibrato * i / RATE)
        phase += 2 * math.pi * f / RATE
        if form == "dreieck":
            v = 2 / math.pi * math.asin(math.sin(phase))
        elif form == "saege":
            # a few harmonics: a soft brass sound instead of a harsh saw
            v = sum(math.sin(phase * k) / k for k in range(1, 7)) * 0.55
        elif form == "glocke":
            v = math.sin(phase) + 0.35 * math.sin(phase * 2.76) * math.exp(-6 * i / RATE) + 0.2 * math.sin(phase * 5.4) * math.exp(-12 * i / RATE)
        else:
            v = math.sin(phase)
        out.append(v * huelle[i])
    return out


def _rauschen(sek: float, tief: float = 0.0, hoch: float = 1.0, zufall: random.Random | None = None) -> list[float]:
    """Noise between two simple filters: `tief` (0…1, removes the bass) and `hoch` (0…1, keeps the treble)."""
    z = zufall or random.Random(7)
    out, lp, hp_alt, hp = [], 0.0, 0.0, 0.0
    for _ in range(int(RATE * sek)):
        x = z.uniform(-1, 1)
        lp += hoch * (x - lp)
        hp = (1 - tief) * (hp + lp - hp_alt)
        hp_alt = lp
        out.append(hp)
    return out


def _formen(werte: list[float], huelle: list[float]) -> list[float]:
    return [v * huelle[i] if i < len(huelle) else 0.0 for i, v in enumerate(werte)]


def _einschlag() -> list[float]:
    """The thud of a dart in the board: short noise plus a falling low tone."""
    n = _rauschen(0.09, tief=0.2, hoch=0.35)
    s = _formen(n, _huelle(len(n), 0.001, 0.06))
    return _dazu(s, _ton(170, 0.14, bis=70, an=0.001, ab=0.12), 0, 0.9)


def _publikum(sek: float, laut: float = 1.0, saat: int = 3) -> list[float]:
    """Cheering crowd: band-limited noise that swells and moves a little."""
    z = random.Random(saat)
    n = _rauschen(sek, tief=0.55, hoch=0.45, zufall=z)
    huelle = _huelle(len(n), 0.35, sek * 0.6, sek * 0.25)
    out = []
    for i, v in enumerate(n):
        welle = 0.75 + 0.25 * math.sin(2 * math.pi * 3.1 * i / RATE + math.sin(2 * math.pi * 0.7 * i / RATE))
        out.append(v * huelle[i] * welle * laut)
    # some whistles in the crowd
    for _ in range(int(sek * 1.5)):
        f = z.uniform(1500, 2600)
        _dazu(out, _ton(f, 0.35, bis=f * z.uniform(1.05, 1.25), an=0.05, ab=0.3), z.uniform(0.2, max(0.3, sek - 0.6)), 0.06 * laut)
    return out


def _akkord(frequenzen: list[float], sek: float, form: str = "saege", an: float = 0.02, ab: float | None = None,
            halten: float = 0.0) -> list[float]:
    out: list[float] = []
    for f in frequenzen:
        _dazu(out, _ton(f, sek, form, an=an, ab=ab, halten=halten, vibrato=5.5), 0, 1 / len(frequenzen))
    return out


def _noten(folge: list[tuple[float, float, float]], form: str = "glocke", laut: float = 1.0) -> list[float]:
    """(frequency, start, length) after each other."""
    out: list[float] = []
    for f, ab, lang in folge:
        _dazu(out, _ton(f, lang, form, ab=lang), ab, laut)
    return out


C5, E5, G5, A5 = 523.25, 659.25, 783.99, 880.0
C6, E6, G6, A6, C7, E7 = 1046.5, 1318.5, 1568.0, 1760.0, 2093.0, 2637.0
C4, E4, G4, BB3 = 261.63, 329.63, 392.0, 233.08


def _machen(name: str) -> list[float]:  # one recipe per sound
    if name == "dart_miss":
        s = _ton(900, 0.55, "sinus", bis=240, an=0.01, ab=0.5)
        return _dazu(s, _einschlag(), 0.5, 0.5)
    if name == "dart_s":
        return _einschlag()
    if name == "dart_s_hoch":
        return _dazu(_einschlag(), _ton(E6, 0.25, "glocke"), 0.03, 0.35)
    if name == "dart_d":
        return _dazu(_einschlag(), _noten([(E6, 0, 0.22), (A6, 0.09, 0.35)], laut=0.4), 0.03)
    if name == "dart_t":
        return _dazu(_einschlag(), _noten([(C6, 0, 0.2), (E6, 0.07, 0.2), (G6, 0.14, 0.45)], laut=0.4), 0.03)
    if name == "dart_t20":
        s = _dazu(_einschlag(), _noten([(C6, 0, 0.2), (E6, 0.06, 0.2), (G6, 0.12, 0.2), (C7, 0.18, 0.7)], laut=0.42), 0.03)
        return _dazu(s, _noten([(E7, 0.3, 0.5), (C7 * 1.5, 0.38, 0.5)], laut=0.12), 0)
    if name == "dart_bull":
        s = _dazu(_einschlag(), _ton(60, 0.6, bis=42, an=0.005, ab=0.5), 0, 0.8)
        return _dazu(s, _ton(G6, 0.4, "glocke"), 0.04, 0.3)
    if name == "dart_bullseye":
        s = _dazu(_einschlag(), _ton(55, 0.9, bis=38, an=0.005, ab=0.8), 0, 0.9)
        return _dazu(s, _akkord([C6, E6, G6, C7], 0.9, "glocke", an=0.08, ab=0.8), 0.06, 0.55)
    if name == "m_180":
        s = _noten([(G4, 0, 0.18), (C5, 0.15, 0.18), (E5, 0.3, 0.18)], "saege", 0.45)
        return _dazu(s, _akkord([C5, E5, G5, C6], 1.1, an=0.02, ab=0.9, halten=0.35), 0.45, 0.55)
    if name == "m_140":
        s = _noten([(G4, 0, 0.16), (C5, 0.13, 0.16)], "saege", 0.45)
        return _dazu(s, _akkord([C5, E5, G5], 0.8, an=0.02, ab=0.7, halten=0.2), 0.27, 0.5)
    if name == "m_100":
        return _akkord([G4, C5, E5], 0.6, an=0.02, ab=0.5, halten=0.1)
    if name == "m_checkout":
        s = _noten([(C5, 0, 0.14), (E5, 0.11, 0.14), (G5, 0.22, 0.14), (C6, 0.33, 0.14)], "saege", 0.4)
        return _dazu(s, _akkord([C5, E5, G5, C6], 1.0, an=0.02, ab=0.8, halten=0.3), 0.45, 0.55)
    if name == "m_match":
        s = _noten([(G4, 0, 0.14), (G4, 0.14, 0.14), (G4, 0.28, 0.14), (C5, 0.42, 0.5)], "saege", 0.45)
        s = _dazu(s, _akkord([E5, G5, C6], 0.6, an=0.02, ab=0.5), 0.9, 0.45)
        return _dazu(s, _akkord([C5, E5, G5, C6, E6], 1.4, an=0.02, ab=1.1, halten=0.4), 1.35, 0.55)
    if name == "m_bust":
        b = _ton(98, 0.6, "saege", an=0.005, ab=0.6, halten=0.4)
        return _dazu(b, _ton(104, 0.6, "saege", an=0.005, ab=0.6, halten=0.4), 0, 0.8)
    if name == "m_noscore":
        return _noten([(G4, 0, 0.32), (G4 * 0.944, 0.3, 0.32), (G4 * 0.891, 0.6, 0.32), (G4 * 0.84, 0.9, 0.9)], "saege", 0.45)
    if name == RISER:
        s = _formen(_rauschen(1.0, tief=0.6, hoch=0.5), [min(1.0, (i / (RATE * 1.0)) ** 2) for i in range(RATE)])
        _dazu(s, _ton(220, 1.0, "saege", bis=880, an=0.6, ab=1.0, halten=0.3), 0, 0.5)
        return _dazu(s, _ton(330, 1.0, "saege", bis=1320, an=0.6, ab=1.0, halten=0.3), 0, 0.3)
    if name == HERZ:
        s = []
        for ab, laut in ((0.05, 1.0), (0.3, 0.7), (0.95, 1.0), (1.2, 0.7)):
            _dazu(s, _ton(62, 0.22, bis=45, an=0.004, ab=0.2), ab, laut)
        return _dazu(s, _leer(0.2), 1.4)
    if name == "m_jubel":
        return _publikum(2.4, 0.9)
    if name == "m_jubel_lang":
        return _publikum(3.6, 1.0, 5)
    raise KeyError(name)


def _schreiben(pfad: Path, werte: list[float]) -> None:
    spitze = max((abs(v) for v in werte), default=0.0) or 1.0
    k = 0.8 / spitze  # same loudness for every sound, never clipped
    daten = array.array("h", (int(max(-1.0, min(1.0, v * k)) * 32767) for v in werte))
    tmp = pfad.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(daten.tobytes())
    tmp.replace(pfad)


def datei(name: str) -> str:
    return f"{name}_v{VERSION}.wav"


def bereitstellen(basis: Path) -> Path:
    """Make the sounds that are missing (in the executor); returns their folder."""
    ordner = basis.joinpath(*ORDNER)
    ordner.mkdir(parents=True, exist_ok=True)
    gueltig = {datei(n) for n in ALLE}
    for alt in ordner.glob("*.wav"):
        if alt.name not in gueltig:
            alt.unlink(missing_ok=True)  # sounds of an older version
    for name in ALLE:
        ziel = ordner / datei(name)
        if not ziel.is_file():
            _schreiben(ziel, _machen(name))
    return ordner
