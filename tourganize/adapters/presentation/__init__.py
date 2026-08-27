"""``PresentationSurface`` adapters, and the one thing both of F07's share.

Filled by F07 (terminal, scripted), F25 (web).

The two surfaces draw the *same* :class:`~tourganize.language.act_renderer.RenderedAct`, and
they have to draw it the same way. A Golden Conversation captured through the Scripted Surface
(F11) is only evidence about what a person saw in the terminal if the two agree on what a line
looks like, so the shaping of a Rendered Act into display lines lives here — in the one place
both adapters reach — rather than twice, drifting.

It is text shaping and nothing else. The *wording* comes from the Message Catalogue and has
already happened by the time a :class:`~tourganize.language.act_renderer.RenderedAct` exists;
the *layout* — panes, scrolling, prefixes — is each surface's own business. Logical order
throughout: bidi shaping is applied at the terminal boundary and nowhere else (F10), and never
here, because these lines are also what a transcript file is compared against.
"""

from __future__ import annotations

from typing import Final

from tourganize.language.act_renderer import OptionRow, RenderedAct

__all__ = [
    "CELL_SEPARATOR",
    "FILTER_NOTE_MARKER",
    "option_row_line",
    "rendered_lines",
]

#: What separates the fields of one Option Slate row. A pipe rather than whitespace so that a
#: value containing spaces cannot be mistaken for two columns when a transcript is read back.
CELL_SEPARATOR: Final = " | "

#: Prefixes a Filter Note. A Filter Note is a field name, never a reason (F06), so the marker
#: has to carry the "this one does not satisfy it" part on its own — and it has to be visible,
#: because soft filtering that nobody can see is indistinguishable from no filtering at all.
FILTER_NOTE_MARKER: Final = "!"


def option_row_line(row: OptionRow) -> str:
    """One numbered Option Slate row as a single line: number, id, price, cells, notes.

    The order is fixed rather than configurable. Which *facts* appear is the Display Profile's
    decision and is already settled in ``row.cells``; this is only the frame around them.
    """
    parts = [f"{row.number}. {row.option_id}"]
    if row.price is not None:
        parts.append(row.price)
    parts += [f"{label}: {value}" for label, value in row.cells]
    parts += [f"{FILTER_NOTE_MARKER}{note}" for note in row.filter_notes]
    return CELL_SEPARATOR.join(parts)


def rendered_lines(rendered: RenderedAct) -> tuple[str, ...]:
    """A Rendered Act as the lines a surface shows: heading first, option table last.

    An empty heading is dropped rather than shown as a blank line. The Act Renderer never
    produces one — a key nobody declares renders a visible marker — so this is a guard against
    a future renderer, not a case that arises today.
    """
    lines = [rendered.heading] if rendered.heading else []
    lines += rendered.lines
    lines += [option_row_line(row) for row in rendered.option_rows]
    return tuple(lines)
