"""Deterministic document chunking (§13).

Turns a policy document into retrievable chunks without a model. Chunking is
pure text surgery, so it is deterministic and testable offline — and it matters
more than it looks: §13 requires the AI to cite *the exact rule* supporting a
recommendation, and a citation is only as good as the chunk boundary that
created it. A chunk that splits a rule mid-sentence cannot be quoted back at a
reviewer, and a chunk spanning three rules will be cited for the wrong one.

Three properties the splitter guarantees:

  - **Headings survive.** Every chunk carries the section reference it came from,
    which is what `compliance_chunks.section_reference` exists to hold, and the
    heading line stays inside `content` so the section title is findable —
    `search_vector` reads `content` alone.
  - **Paragraphs are not torn.** Units are packed whole when they fit; only a
    single oversized paragraph is split further, by sentence and then by hard wrap.
  - **Overlap is bounded, and so is every chunk.** A carried prefix lets a rule
    that straddles a boundary still be matched, without re-reading the whole
    document into every chunk — and the prefix is budgeted against `max_chars`,
    so carrying context can never be what makes a chunk too large.

Chosen as a fixed, reasoned algorithm rather than a splitter library: the inputs
are policy documents and marketplace rule sets, not code, and a committed
algorithm can be asserted on in tests where a dependency's internal heuristics
cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP = 150

# Markdown headings, plus plain "Section 12.3" / "§14" style references, which is
# how most marketplace policy pages and help-centre documents are titled.
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(\S.*)$")
_SECTION_RE = re.compile(
    r"^\s*(?:section|§)\s+([0-9]+(?:\.[0-9]+)*)\s*[.:]?\s*(.*)$", re.I
)
# A section heading carries a *title*, and the regex above will happily swallow
# an entire sentence as one:
# "Section 3. A textile product must carry a fibre content label." parses as
# heading "Section 3 A textile product must carry a fibre content label."
# A document consisting of one such line then produced no chunks at all, and
# ingestion refused a legitimate single-sentence rule — found in Phase 27 by
# profiling with realistic policy content rather than the tidy fixtures the
# unit tests use.
#
# A title is short and does not end like a sentence. Both are checked: a length
# cap alone rejects a long heading, and terminal punctuation alone rejects a
# title like "Safety requirements." that happens to end in a full stop.
_TITLE_MAX_WORDS = 8
_SENTENCE_END = (".", "!", "?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable piece, in the shape `compliance_chunks` stores."""

    index: int
    content: str
    section_reference: str | None
    token_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "content": self.content,
            "section_reference": self.section_reference,
            "token_count": self.token_count,
        }


def estimate_tokens(text: str) -> int:
    """A four-characters-per-token approximation.

    Deliberately not a tokenizer: `token_count` budgets chunks, it does not bill
    for them, and shipping a tokenizer to buy a decimal place would be a bad trade.
    """
    return max(1, len(text) // 4)


def _parse_heading(line: str) -> str | None:
    """The reference a line opens a section with, or None if it is body text."""
    if match := _HEADING_RE.match(line):
        return match.group(2).strip()
    if match := _SECTION_RE.match(line):
        number, title = match.group(1), match.group(2).strip()
        words = title.split()
        looks_like_a_sentence = len(words) > _TITLE_MAX_WORDS or (
            title.endswith(_SENTENCE_END) and len(words) > 3
        )
        if looks_like_a_sentence:
            # A numbered sentence in body text, not a heading. Treated as
            # content, which is what it is.
            return None
        return f"Section {number} {title}".strip()
    return None


def split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split into `(section_reference, body)` pairs, preserving all text.

    A heading is attached to the section that follows it, so the reference is
    never a chunk of its own and never lost — it labels the content it heads.

    The heading line also *stays in the body*. `section_reference` is a label
    for a citation; it is not searched — `search_vector` is built from `content`
    alone — so dropping the line would make a section title unfindable by the
    query most likely to be asked about it.

    A heading with nothing under it is still dropped: a chunk whose entire
    content is a title matches on every other title too.
    """
    sections: list[tuple[str | None, str]] = []
    current_ref: str | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        reference = _parse_heading(line)
        if reference is None:
            buffer.append(line)
            continue
        if buffer:
            _append_section(sections, current_ref, buffer)
        buffer = [line]
        current_ref = reference

    if buffer:
        _append_section(sections, current_ref, buffer)
    return sections


def _append_section(
    sections: list[tuple[str | None, str]], ref: str | None, buffer: list[str]
) -> None:
    """Append a buffered section if it says anything at all.

    `buffer[0]` is the heading when `ref` is set, because the buffer starts at
    the line that produced the reference — so "everything after the heading" is
    exactly `buffer[1:]`.
    """
    body = "\n".join(buffer)
    if not body.strip():
        return
    if ref is not None and not any(line.strip() for line in buffer[1:]):
        return
    sections.append((ref, body))


def _paragraphs(body: str) -> list[str]:
    """Non-empty paragraphs, with blank-line runs collapsed."""
    return [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]


def _carry_tail(current: str, unit: str, max_chars: int, overlap: int) -> str:
    """Start `unit` with a budgeted tail of `current`, or just `unit`.

    The tail is the overlap — the reason a rule that straddles a boundary is
    still matchable. It is also the reason a chunk can exceed `max_chars` if
    left unbounded, and an oversized chunk is not a small defect: retrieval
    returns what fits and an embedding provider rejects what does not, so the
    overflow would be either silently truncated or a failed ingest.
    """
    if not current or not overlap:
        return unit
    budget = max_chars - len(unit) - 1  # the "\n" join
    if budget < 1:
        return unit
    return f"{current[-min(overlap, budget):]}\n{unit}"


def _split_oversized(unit: str, max_chars: int, overlap: int) -> list[str]:
    """Break a single unit that cannot fit: sentences first, then hard wrap.

    The resulting pieces are *repacked* rather than emitted one per sentence. A
    sentence is far smaller than a chunk, so emitting each on its own would turn
    one long paragraph into dozens of near-empty chunks — small enough to rank
    on their own, too small to hold the rule they were split from. The repack
    stops at `max_chars`, and because every fragment it receives is already
    within that bound it cannot recurse.
    """
    if len(unit) <= max_chars:
        return [unit]

    fragments: list[str] = []
    for sentence in _SENTENCE_RE.split(unit):
        while len(sentence) > max_chars:
            fragments.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if sentence.strip():
            fragments.append(sentence.strip())
    if not fragments:
        return [unit[:max_chars]]

    return _pack(fragments, max_chars=max_chars, overlap=overlap)


def _pack(units: list[str], *, max_chars: int, overlap: int) -> list[str]:
    """Greedily pack units into chunks within `max_chars`, carrying a tail."""
    chunks: list[str] = []
    current = ""

    for unit in units:
        if len(unit) > max_chars:
            # A unit too large for one chunk is split rather than allowed to set a
            # chunk of unbounded size: retrieval must not be handed half a document.
            if current:
                chunks.append(current)
                current = ""
            pieces = _split_oversized(unit, max_chars, overlap)
            chunks.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""
            continue

        candidate = f"{current}\n\n{unit}" if current else unit
        if len(candidate) <= max_chars:
            current = candidate
            continue

        chunks.append(current)
        current = _carry_tail(current, unit, max_chars, overlap)

    if current:
        chunks.append(current)
    return chunks


def chunk_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk `text`, preserving section references across boundaries."""
    if not text or not text.strip():
        return []
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    overlap = max(0, min(overlap, max_chars // 2))

    chunks: list[Chunk] = []
    for section_ref, body in split_sections(text):
        for piece in _pack(_paragraphs(body), max_chars=max_chars, overlap=overlap):
            if not piece.strip():
                continue
            chunks.append(
                Chunk(
                    index=len(chunks),
                    content=piece.strip(),
                    section_reference=section_ref,
                    token_count=estimate_tokens(piece),
                )
            )
    return chunks


def keywords(text: str, *, limit: int = 40) -> list[str]:
    """Distinct lowercase terms, most useful first (by first appearance).

    Stopwords are kept: `must`, `not` and `exceed` are load-bearing in rule text
    such as "must not exceed", so stripping them would change what a match means.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for match in _TOKEN_RE.finditer(text.casefold()):
        term = match.group(0)
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms

