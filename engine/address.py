"""How Clicky speaks to the person using it (CLAUDE.md rule 8): it calls itself "em" and the user "chị".

Every sentence, card, button and Biên bản reason takes the word from here, so the product never mixes "anh" and
"chị" again, and a later setting can change it in one place. Words the user may say ("để anh xem") are input,
not address, and stay in the triggers as they are."""
YOU = "chị"


def you(capital: bool = False) -> str:
    """The word for the user: "chị", or "Chị" at the start of a sentence."""
    return YOU[:1].upper() + YOU[1:] if capital else YOU
