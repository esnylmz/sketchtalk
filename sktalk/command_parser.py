"""Maps a final Vosk transcript to an intent (label, delete, undo, export, pause)."""

from dataclasses import dataclass
from difflib import SequenceMatcher

import config

_FUZZY_MATCH_RATIO = 0.8  # catches near-miss ASR output like "delet"/"dilate" for "delete"

# specific mis-hearings observed for "delete" that aren't spelling-close enough
# for the fuzzy ratio above to catch (Vosk's small model sometimes substitutes
# an unrelated-looking word) - add more here as they show up in "DEBUG heard" logs
_DELETE_ALIASES = {"delete", "believe", "lead", "why", "really"}

# same idea, but for words that are also plausible on their own (narration,
# counting) - only treated as "delete" when followed by something else
# ("three this"), never as a bare single-word utterance
_DELETE_ALIASES_NEEDS_FOLLOWUP = {"three"}

# specific mis-hearings observed for a bare label keyword that aren't
# spelling-close enough for a fuzzy ratio to catch - checked both as the
# whole utterance and as the single word left over after an article/"with"
# (see the fallback at the bottom of parse()), so "the his" resolves the
# same way "his" alone does
_LABEL_ALIASES = {
    "wow": "cloud", "crowd": "cloud",
    "there": "user", "sir": "user",
    "thurber": "server", "over": "server", "however": "server",
    "maze": "database", "vase": "database", "base": "database",
    "far": "start", "heart": "start", "thought": "start", "hard": "start", "current": "start",
    "i know": "final", "fighting on": "final", "find them": "final",
    "the decision": "decision", "vision": "decision", "seizure": "decision",
    "susan": "decision", "season": "decision",
    # "his" was deliberately left out - it's spelling-close to "this" (a
    # deictic word said constantly for pointing/connect), so a mis-heard
    # "this" landing here would wrongly relabel a shape as "database"
}

# specific mis-hearings observed for "stop" that aren't spelling-close enough
# for the fuzzy ratio below to catch - fine to be generous here since pause
# only actually fires combined with the stop-pose hand gesture, so a stray
# unrelated word alone can't trigger it
# "huh" was removed - it's a common filler/backchannel said constantly while
# thinking mid-presentation, and even gated behind the stop-pose it triggered
# pauses too often to be worth keeping
_STOP_ALIASES = {"shop", "trump", "rob", "up", "yup", "bob", "what", "pop", "tough", "help"}

# a short utterance ("who will stop") that merely contains a stop-like word
# still counts, not just a bare "stop" - same reasoning as above, the hand
# gesture gate makes this safe to be generous about. Capped in length so a
# long, unrelated sentence that happens to contain a near-miss word doesn't
# also count
_MAX_PAUSE_SENTENCE_WORDS = 6


def _is_pause_phrase(text):
    words = text.split()
    if not words or len(words) > _MAX_PAUSE_SENTENCE_WORDS:
        return False
    # never swallow a sentence that also contains a deictic word ("what is
    # this", "put that stuff there") - losing that pointing reference is
    # worse than missing a stop here, since the exact same word (e.g. "that")
    # is used constantly for pointing/delete
    if any(config.DEICTIC_ALIASES.get(w, w) in config.DEICTIC_WORDS for w in words):
        return False
    return any(
        w in config.PAUSE_WORDS or w in _STOP_ALIASES
        or SequenceMatcher(None, w, "stop").ratio() >= _FUZZY_MATCH_RATIO
        for w in words
    )


@dataclass
class Intent:
    kind: str  # "LABEL" | "DELETE" | "UNDO" | "EXPORT" | "PAUSE" | None
    payload: str = ""


def parse(text):
    text = text.strip().lower()
    if not text:
        return Intent(kind=None)

    if _is_pause_phrase(text):
        return Intent(kind="PAUSE")

    if text in ("undo", "undo last"):
        return Intent(kind="UNDO")

    if text in ("export diagram", "export"):
        return Intent(kind="EXPORT")

    words = text.split()
    # a leading article ("the delete this") is skipped when looking for the
    # delete trigger word, since Vosk sometimes prefixes it with one
    trigger_idx = 1 if words and words[0] in ("the", "a", "an") and len(words) > 1 else 0
    if words and trigger_idx < len(words):
        trigger = words[trigger_idx]
        if (trigger in _DELETE_ALIASES
                or (trigger in _DELETE_ALIASES_NEEDS_FOLLOWUP and len(words) > trigger_idx + 1)
                or SequenceMatcher(None, trigger, "delete").ratio() >= _FUZZY_MATCH_RATIO):
            target = " ".join(words[trigger_idx + 1:])
            return Intent(kind="DELETE", payload=target)

    # the keyword-grammar recognizer sometimes returns just the bare word
    # ("user", "cloud", ...) with no article, since that's all it's allowed
    # to output - treat that directly as a label
    if text in config.LABEL_KEYWORDS:
        return Intent(kind="LABEL", payload=text)

    if text in _LABEL_ALIASES:
        return Intent(kind="LABEL", payload=_LABEL_ALIASES[text])

    # plural of a bare keyword ("users", "servers", "databases", ...) -
    # normalize down to the singular so it still gets the right icon
    if text.endswith("s") and text[:-1] in config.LABEL_KEYWORDS:
        return Intent(kind="LABEL", payload=text[:-1])

    # near-miss ASR output for the node-type keywords specifically ("decisions",
    # "the decision") - checked one at a time against a fixed target, not a
    # loop over every keyword, so it can never cross-match "action"/"decision"
    # against each other or against an unrelated keyword
    for node_word in config.NODE_TYPE_KEYWORDS:
        if SequenceMatcher(None, text, node_word).ratio() >= _FUZZY_MATCH_RATIO:
            return Intent(kind="LABEL", payload=node_word)

    # same idea for the UML start/end terminal keywords
    for terminal_word in config.TERMINAL_KEYWORDS:
        if SequenceMatcher(None, text, terminal_word).ratio() >= _FUZZY_MATCH_RATIO:
            return Intent(kind="LABEL", payload=terminal_word)

    # same idea for "cloud"/"server" - fixed single-word targets, not a loop
    # over every keyword. "he server" is spelling-close enough to "server"
    # for this ratio; "however" is not, so it lives in _LABEL_ALIASES above
    # ("wow"/"crowd" are also aliases - too far for the ratio alone)
    if SequenceMatcher(None, text, "cloud").ratio() >= _FUZZY_MATCH_RATIO:
        return Intent(kind="LABEL", payload="cloud")
    if SequenceMatcher(None, text, "server").ratio() >= _FUZZY_MATCH_RATIO:
        return Intent(kind="LABEL", payload="server")

    # filler + keyword ("he server", "um cloud") - Vosk often prefixes a
    # short junk word; take the first known label keyword in the utterance
    for w in words:
        if w in config.LABEL_KEYWORDS:
            return Intent(kind="LABEL", payload=w)
        if w in _LABEL_ALIASES:
            return Intent(kind="LABEL", payload=_LABEL_ALIASES[w])

    # narrating while working ("here will be... the cloud", "with database")
    # means the actual label often isn't at the start of the utterance - take
    # the LAST "the/a/an/with X" in the sentence instead of requiring it up
    # front, so the word right before it (the drawing narration) doesn't get
    # glued onto the label or block it from matching at all
    last_article_idx = None
    for i, w in enumerate(words):
        if w in ("the", "a", "an", "with"):
            last_article_idx = i
    if last_article_idx is not None and last_article_idx < len(words) - 1:
        payload = " ".join(words[last_article_idx + 1:])
        # a single trailing word might itself be a known mis-hearing
        # ("the his" -> "his" -> database) - a multi-word payload is left
        # alone since that's a real custom label, not a keyword near-miss
        payload = _LABEL_ALIASES.get(payload, payload)
        return Intent(kind="LABEL", payload=payload)

    return Intent(kind=None)
