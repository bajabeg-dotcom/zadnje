PA800_SOUNDS_FULL = {
    (121, 0, 0): "Acoustic Piano GM",
    (121, 3, 0): "Grand Piano",
    (121, 10, 0): "Grand Piano RX",

    (121, 0, 1): "Bright Piano GM",
    (121, 5, 1): "Bright Piano RX",

    (121, 0, 2): "E.Grand Piano GM",
    (121, 2, 2): "M1 Piano",

    (121, 0, 4): "E. Piano 1 GM",
    (121, 10, 4): "Tine E.Piano RX",

    (121, 0, 5): "E. Piano 2 GM",

    (121, 0, 6): "Harpsichord GM",
    (121, 0, 7): "Clav GM",

    (121, 0, 16): "Drawbars Org. GM",
    (121, 4, 16): "Jazz Organ",
    (121, 0, 17): "Perc. Organ GM",
    (121, 0, 18): "Rock Organ GM",
    (121, 0, 19): "Church Organ GM",

    (121, 0, 21): "Accordion GM",
    (121, 7, 21): "Arabic Accordion",
    (121, 8, 21): "French Musette",
    (121, 0, 22): "Harmonica GM",
    (121, 0, 23): "Tango Accord. GM",

    (121, 0, 24): "Nylon Guitar GM",
    (121, 2, 24): "Spanish Guitar",
    (121, 7, 24): "Nylon Guitar DNC",

    (121, 0, 25): "Steel Guitar GM",
    (121, 2, 25): "Mandolin",

    (121, 0, 26): "Jazz Guitar GM",
    (121, 0, 27): "Clean Guitar GM",
    (121, 4, 27): "Funky Wah RX",
    (121, 0, 28): "Muted Guitar GM",
    (121, 0, 29): "Overdrive Gtr GM",
    (121, 0, 30): "Distortion GtrGM",

    (121, 0, 32): "Acoustic Bass GM",
    (121, 0, 33): "Finger Bass GM",
    (121, 5, 33): "Finger Bass RX",
    (121, 0, 34): "Picked E.Bass GM",
    (121, 0, 35): "Fretless Bass GM",
    (121, 0, 36): "Slap Bass 1 GM",
    (121, 0, 38): "Synth Bass 1 GM",

    (121, 0, 40): "Violin GM",
    (121, 0, 41): "Viola GM",
    (121, 0, 42): "Cello GM",
    (121, 0, 43): "Contrabass GM",
    (121, 0, 44): "Tremolo Str. GM",
    (121, 0, 45): "Pizzicato Str.GM",
    (121, 0, 48): "Strings Ens.1 GM",
    (121, 7, 48): "Concert Str.RX",
    (121, 0, 49): "Strings Ens.2 GM",

    (121, 0, 52): "Choir Aahs GM",
    (121, 0, 53): "Voice Oohs GM",
    (121, 0, 54): "Synth Voice GM",

    (121, 0, 56): "Trumpet GM",
    (121, 3, 56): "Trumpet DNC",
    (121, 0, 57): "Trombone GM",
    (121, 1, 57): "Trombone DNC",
    (121, 0, 58): "Tuba GM",
    (121, 0, 60): "French Horn GM",
    (121, 0, 61): "Brass Section GM",

    (121, 0, 64): "Soprano Sax GM",
    (121, 0, 65): "Alto Sax GM",
    (121, 1, 65): "Alto Sax DNC",
    (121, 0, 66): "Tenor Sax GM",
    (121, 1, 66): "Tenor Sax DNC",
    (121, 0, 67): "Baritone Sax GM",

    (121, 0, 68): "Oboe GM",
    (121, 0, 71): "Clarinet GM",
    (121, 1, 71): "Clarinet DNC",
    (121, 0, 72): "Piccolo GM",
    (121, 0, 73): "Flute GM",
    (121, 1, 73): "Flute DNC",
    (121, 0, 75): "Pan Flute GM",

    (121, 0, 80): "Lead Square GM",
    (121, 0, 81): "Lead Saw GM",
    (121, 0, 88): "New Age Pad GM",
    (121, 0, 89): "Warm Pad GM",
    (121, 0, 90): "Polysynth GM",
    (121, 0, 91): "Choir Pad GM",
    (121, 0, 94): "Halo Pad GM",
    (121, 0, 95): "Sweep Pad GM",

    (121, 0, 104): "Sitar GM",
    (121, 0, 105): "Banjo GM",
    (121, 0, 106): "Shamisen GM",
    (121, 0, 107): "Koto GM",
    (121, 0, 108): "Kalimba GM",
    (121, 0, 109): "Bag Pipes GM",
    (121, 0, 110): "Fiddle GM",
    (121, 0, 111): "Shanai GM",
}


def get_sound_name(cc00: int, cc32: int, pc: int) -> str:
    key = (cc00, cc32, pc)
    if key in PA800_SOUNDS_FULL:
        return PA800_SOUNDS_FULL[key]
    return PA800_SOUNDS_FULL.get((121, 0, pc), f"Prog {pc}")


def build_sound_list():
    result = []
    seen = set()
    for (cc00, cc32, pc), name in sorted(PA800_SOUNDS_FULL.items(), key=lambda x: (x[0][2], x[0][1])):
        display = f"{pc:03d}/{cc32:02d}  {name}"
        if display not in seen:
            result.append((cc00, cc32, pc, display))
            seen.add(display)
    return result