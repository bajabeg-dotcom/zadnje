NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

CHANNEL_NAMES = {
    0: "Ch1-Melody",
    1: "Ch2-Bass",
    2: "Ch3-Chords",
    3: "Ch4-Strings",
    4: "Ch5-Brass",
    5: "Ch6-Sax",
    6: "Ch7-Guitar",
    7: "Ch8-Synth",
    8: "Ch9-Pad",
    9: "Drums",
    10: "Ch11-Acc1",
    11: "Ch12-Acc2",
    12: "Ch13-Acc3",
    13: "Ch14-Acc4",
    14: "Ch15-FX",
    15: "Ch16-Extra",
}

INSTRUMENT_CATEGORIES = {
    "piano": list(range(0, 8)),
    "organ": list(range(16, 24)),
    "accordion": [21, 22, 23],
    "guitar": list(range(24, 32)),
    "bass": list(range(32, 40)),
    "strings": list(range(40, 52)),
    "choir": list(range(52, 56)),
    "brass": list(range(56, 64)),
    "sax": list(range(64, 68)),
    "woodwind": list(range(68, 80)),
    "synth": list(range(80, 104)),
    "ethnic": list(range(104, 112)),
    "fx": list(range(112, 128)),
}

VELOCITY_PROFILES = {
    "piano": {"base": 80, "min": 60, "max": 110, "rand": 12},
    "organ": {"base": 85, "min": 70, "max": 110, "rand": 8},
    "accordion": {"base": 80, "min": 65, "max": 105, "rand": 10},
    "guitar": {"base": 75, "min": 55, "max": 100, "rand": 14},
    "bass": {"base": 90, "min": 75, "max": 110, "rand": 8},
    "strings": {"base": 70, "min": 55, "max": 95, "rand": 10},
    "choir": {"base": 72, "min": 58, "max": 95, "rand": 8},
    "brass": {"base": 85, "min": 65, "max": 115, "rand": 12},
    "sax": {"base": 80, "min": 60, "max": 110, "rand": 14},
    "woodwind": {"base": 65, "min": 50, "max": 90, "rand": 10},
    "synth": {"base": 78, "min": 60, "max": 105, "rand": 10},
    "ethnic": {"base": 75, "min": 55, "max": 100, "rand": 12},
    "drums": {"base": 90, "min": 70, "max": 120, "rand": 18},
    "other": {"base": 75, "min": 55, "max": 100, "rand": 12},
}


def note_name(n: int) -> str:
    n = max(0, min(127, n))
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def get_instrument_category(program: int) -> str:
    for cat, pcs in INSTRUMENT_CATEGORIES.items():
        if program in pcs:
            return cat
    return "other"


def get_velocity_profile(category: str):
    return VELOCITY_PROFILES.get(category, VELOCITY_PROFILES["other"])


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def transpose_note(note: int, semitones: int) -> int:
    return clamp(note + semitones, 0, 127)