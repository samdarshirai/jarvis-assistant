import re

YES = {"yes", "yeah", "confirm", "do it"}
NO = {"no", "cancel", "stop"}


def match_confirmation(text: str) -> bool | None:
    """True/False only when the whole utterance is one decision word; anything else is a new request (None).
    Plain code on purpose: the LLM never decides whether the user confirmed."""
    t = " ".join(re.sub(r"[^\w\s]", "", text.lower()).split())
    if t in YES:
        return True
    if t in NO:
        return False
    return None
