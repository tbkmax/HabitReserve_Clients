MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


def parse_hotkey(sequence: str) -> tuple[int, int]:
    pieces = [piece.strip().upper() for piece in sequence.split("+") if piece.strip()]
    if not pieces:
        raise ValueError("Shortcut cannot be empty")
    modifiers = MOD_NOREPEAT
    aliases = {"CTRL": MOD_CONTROL, "CONTROL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT, "WIN": MOD_WIN, "META": MOD_WIN}
    for piece in pieces[:-1]:
        if piece not in aliases:
            raise ValueError(f"Unsupported modifier: {piece}")
        modifiers |= aliases[piece]
    key = pieces[-1]
    if len(key) == 1 and key.isalnum():
        return modifiers, ord(key)
    function_keys = {f"F{number}": 0x6F + number for number in range(1, 13)}
    if key in function_keys:
        return modifiers, function_keys[key]
    raise ValueError("Use a letter, number, or F1-F12 as the shortcut key")
