"""Rank execution agents by whole-term overlap with their routing summaries."""

import re
from typing import TypedDict

from .roster import AgentRecord

_TERMS = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+|\w+(?:-\w+)*")
_FILLER = frozenset(
    "a an and about are can did do does for from get has have i in is it me my "
    "of on or please so that the their them they this to us was we what with you".split()
)


class SearchMatch(AgentRecord):
    matched_terms: list[str]


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in _TERMS.findall(text.casefold()):
        if "@" in token or any(char.isdigit() for char in token):
            terms.add(token)
        else:
            terms.update(token.split("-"))
    return terms - _FILLER


def search_agents(query: str, records: list[AgentRecord]) -> list[SearchMatch]:
    """Return up to three positive matches, with name as the stable tie-breaker."""
    query_terms = _terms(query)
    if not query_terms:
        return []

    matches: list[SearchMatch] = []
    for record in records:
        matched_terms = sorted(query_terms & _terms(record["summary"] or record["name"]))
        if matched_terms:
            matches.append({
                "name": record["name"],
                "summary": record["summary"],
                "matched_terms": matched_terms,
            })

    matches.sort(key=lambda match: (-len(match["matched_terms"]), match["name"]))
    return matches[:3]
