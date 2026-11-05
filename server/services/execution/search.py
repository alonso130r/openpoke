"""Rank execution agents by whole-term overlap with their names and summaries."""

import re
from collections import Counter
from typing import TypedDict

from .roster import AgentRecord

_TERMS = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+|\w+(?:-\w+)*")
_FILLER = frozenset(
    "a an and about are can did do does for from get has have i in is it me my "
    "of on or please so that the their them they this to us was we what with you".split()
)


class SearchMatch(AgentRecord):
    matched_terms: list[str]


_COMMON_TO_DISTINCTIVE_RATIO = 5


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in _TERMS.findall(text.casefold()):
        if "@" in token or any(char.isdigit() for char in token):
            terms.add(token)
        else:
            terms.update(token.split("-"))
    return terms - _FILLER


def search_agents(query: str, records: list[AgentRecord]) -> list[SearchMatch]:
    """Return the top three positive matches and all ties at the cutoff."""
    query_terms = _terms(query)
    if not query_terms:
        return []

    matches: list[SearchMatch] = []
    for record in records:
        worker_terms = _terms(record["name"]) | _terms(record["summary"] or "")
        matched_terms = sorted(query_terms & worker_terms)
        if matched_terms:
            matches.append({
                "name": record["name"],
                "summary": record["summary"],
                "matched_terms": matched_terms,
            })

    frequencies = Counter(term for match in matches for term in match["matched_terms"])
    if frequencies:
        broadest = max(frequencies.values())
        distinctive = {
            term for term, count in frequencies.items()
            if count * _COMMON_TO_DISTINCTIVE_RATIO <= broadest
        }
        if distinctive:
            strongest = max(len(match["matched_terms"]) for match in matches)
            matches = [
                match for match in matches
                if len(match["matched_terms"]) == strongest
                or distinctive.intersection(match["matched_terms"])
            ]

    matches.sort(key=lambda match: (-len(match["matched_terms"]), match["name"]))
    if len(matches) <= 3:
        return matches
    cutoff = len(matches[2]["matched_terms"])
    return [match for match in matches if len(match["matched_terms"]) >= cutoff]
