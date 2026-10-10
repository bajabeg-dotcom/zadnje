PA800_DRUM_KITS = {
    (120, 0, 0): "Standard Kit GM",
    (120, 0, 1): "Standard Kit RX2",
    (120, 0, 2): "Standard Kit RX3",
    (120, 0, 3): "Ambient Kit RX",
    (120, 0, 4): "Pop Std. Kit RX",
    (120, 0, 5): "Standard Kit RX1",
    (120, 0, 8): "Room Kit GM",
    (120, 0, 9): "HipHop Kit 1",
    (120, 0, 10): "Jungle Kit",
    (120, 0, 11): "Techno Kit 1",
    (120, 0, 16): "Power Kit GM",
    (120, 0, 24): "Electro Kit GM",
    (120, 0, 25): "Analog Kit GM",
    (120, 0, 32): "Jazz Kit GM",
    (120, 0, 40): "Brush Kit GM",
    (120, 0, 48): "Orchestra Kit GM",
    (120, 0, 56): "SFX Kit GM",
    (120, 0, 58): "Synth Kit",
    (120, 0, 64): "Percussion Kit",
    (120, 0, 72): "Hip Hop Kit RX",
    (120, 0, 73): "Techno Kit RX",
    (120, 0, 74): "Dance Kit RX",
    (120, 0, 117): "Arabian Kit 2",
    (120, 0, 118): "Turkish Kit",
    (120, 0, 119): "Oriental PercKit",
}


def get_drum_name(cc00: int, cc32: int, pc: int) -> str:
    return PA800_DRUM_KITS.get((cc00, cc32, pc), f"Drum Kit {pc}")


def build_drum_list():
    return [
        (cc00, cc32, pc, f"{pc:03d}  {name}")
        for (cc00, cc32, pc), name in sorted(PA800_DRUM_KITS.items(), key=lambda x: x[0][2])
    ]