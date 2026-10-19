"""Switchable breakpoints for the long pipeline runs.

The reason this exists
----------------------
Almost every hard bug in this project has been a *data-shape* bug found at a
seam between two stages: the clef that homr wrote in a second ``<attributes>``
block, the meter that contradicted its own bar lengths, the staff that a brace
put in the wrong part. Reading the code does not reveal those. You have to see
the value at the moment it crosses the seam, in the run that actually
reproduced it.

A debugger is the wrong tool here for three reasons. The stages are separate
processes (homr is a CLI subprocess), runs take minutes of CPU, and the
interesting bugs show up in the *aggregate* -- every bar of every system, not
one stack frame. So instead of suspending execution, a breakpoint here
**records and prints what it saw**, and the whole run's history can be reviewed
afterwards or written to JSON.

Two properties make it safe to leave in the tree permanently:

* **Zero cost when off.** One module-level boolean, checked before any work.
  No allocation, no formatting, no locking on the hot path.
* **Environment-driven, not import-driven.** homr is a separate process, so a
  Python-level ``enable()`` call cannot reach it. ``SCOREHALO_BP`` is read in
  the child, which is what lets one shell command instrument both sides.

Using it
--------
In code, anywhere a value crosses a seam::

    from .breakpoints import bp

    bp("emit.part_parsed", part=part, clefs=clef_by_staff, meters=meters)

Any keyword arguments are context: whatever you would want to look at when the
bug fires. They are captured by reference only when the breakpoint is armed, so
passing a large object costs nothing while disabled.

Turn them on for a run, from the shell, in both processes at once::

    SCOREHALO_BP=emit.part_parsed,emit.before_write \\
    SCOREHALO_BP_ACTION=print homr -f img.png --output_dir out

Other variables, all optional:

======================  =====================================================
``SCOREHALO_BP``        Comma-separated labels to arm. ``*`` arms every
                        breakpoint, and a label may itself be a glob --
                        ``homr.*`` arms every label with that prefix. Unset
                        means "everything off".
``SCOREHALO_BP_ACTION`` ``print`` (default) dump at once, ``pdb`` drop into a
                        debugger, ``raise`` raise :class:`BreakpointStop` so a
                        caller can catch it and keep running, ``count`` record
                        silently and only report later.
``SCOREHALO_BP_MATCH``  Substring filter. Only labels containing it fire --
                        use ``parse`` to narrow ``emit.part_parsed`` without
                        listing every neighbour.
``SCOREHALO_BP_SKIP``   Integer. Ignore the first *n* hits of each label, then
                        arm. For bugs that only appear on the fourth system.
``SCOREHALO_BP_TIMES``  Integer. Fire at most *n* times per label. Default
                        unlimited, because a bug in a loop usually wants to be
                        seen on every iteration, not once.
``SCOREHALO_BP_WIDTH``  Truncate each rendered context value. Default 400.
======================  =====================================================

Arming at runtime
-----------------
For when you are already inside a Python REPL and do not want to restart::

    from scorehalo.breakpoints import enable, disable, trace, report
    enable("emit.part_parsed")      # also arms everything if given "*"
    disable()                        # or disable("emit.part_parsed")

    trace()      # every hit this session, as a list of records
    report()     # the same, formatted for a terminal

The distinction that matters: :func:`enable` changes what the *rest of this
process* does, and does not survive into a subprocess. For homr, set the
environment variable in the parent instead -- ``engine.run_homr`` inherits
``os.environ``, so exporting it before the run reaches the child unchanged.

Why not just use ``breakpoint()``
---------------------------------
``breakpoint()`` needs a TTY, stops the run at the first hit, and cannot reach
a subprocess. Every bug this was built for wanted the opposite: *do not stop,
record all of it, and let me look at the whole picture*. ``SCOREHALO_BP_ACTION=pdb``
is still there when a genuine interactive stop is what you want.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import threading
import time

__all__ = [
    "BreakpointStop",
    "bp",
    "disable",
    "dump",
    "enable",
    "enabled",
    "hits",
    "refresh",
    "report",
    "reset",
    "trace",
    "would_fire",
]

ENV_LABELS = "SCOREHALO_BP"
ENV_ACTION = "SCOREHALO_BP_ACTION"
ENV_MATCH = "SCOREHALO_BP_MATCH"
ENV_SKIP = "SCOREHALO_BP_SKIP"
ENV_TIMES = "SCOREHALO_BP_TIMES"
ENV_WIDTH = "SCOREHALO_BP_WIDTH"

_DEFAULT_WIDTH = 400


class BreakpointStop(RuntimeError):
    """Raised at a breakpoint when ``SCOREHALO_BP_ACTION=raise``.

    Carries the label and context so a caller can inspect the state that
    triggered it, decide whether it is the bug they are hunting, and continue.
    A caller that does not catch it will abort the run -- which is the point of
    ``pdb`` being the only other stopping action.
    """

    def __init__(self, label: str, context: dict, hits: int) -> None:
        super().__init__(f"breakpoint {label!r} hit #{hits}")
        self.label = label
        self.context = context
        self.hits = hits


class _Config:
    """Parsed environment. Rebuilt only when the environment actually changes."""

    def __init__(self) -> None:
        self.signature: tuple | None = None
        self.labels: set[str] = set()
        #: Compiled label globs. Empty unless the environment named one.
        #: Initialised here as well as in read() because armed() reads it, and
        #: a fresh _Config must be safe to consult before its first read().
        self.patterns: list = []
        self.wildcard = False
        self.action = "print"
        self.match = ""
        self.skip = 0
        self.times: int | None = None
        self.width = _DEFAULT_WIDTH
        self.parse_error: str | None = None

    def read(self) -> None:
        """Re-parse the environment. A no-op when nothing relevant changed.

        Note that this only reads; applying runtime overrides is refresh()'s
        job, so that a re-read can never stomp a label enable()() just set.
        """
        raw_labels = os.environ.get(ENV_LABELS)
        action = os.environ.get(ENV_ACTION, "print").strip().lower()
        match = os.environ.get(ENV_MATCH, "")
        skip = os.environ.get(ENV_SKIP, "0").strip() or "0"
        times = os.environ.get(ENV_TIMES, "").strip()
        width = os.environ.get(ENV_WIDTH, str(_DEFAULT_WIDTH)).strip()

        signature = (raw_labels, action, match, skip, times, width)
        if signature == self.signature:
            return
        self.signature = signature

        self.wildcard = False
        self.labels = set()
        self.patterns = []
        self.parse_error = None
        for piece in (raw_labels or "").split(","):
            name = piece.strip()
            if not name:
                continue
            if name == "*":
                self.wildcard = True
            elif "*" in name or "?" in name:
                # ``homr.*`` has to mean "every homr breakpoint". Treating the
                # star as literal was the first thing this module got wrong in
                # real use: I armed ``SCOREHALO_BP='homr.*'``, nothing fired,
                # and the run looked like a clean no-op rather than a mistake.
                # Patterns are compiled once here, not per hit.
                self.patterns.append(re.compile(fnmatch.translate(name)))
            else:
                self.labels.add(name)

        self.action = action
        self.match = match
        self.skip = self._int(skip, 0)
        self.times = self._int(times, None)
        self.width = self._int(width, _DEFAULT_WIDTH)

    @staticmethod
    def _int(text: str, fallback: int | None) -> int | None:
        try:
            value = int(text)
        except (TypeError, ValueError):
            return fallback
        return value if value >= 0 else fallback

    @property
    def armed(self) -> bool:
        # patterns count: a glob-only configuration arms real breakpoints, and
        # leaving them out here made every glob a silent no-op.
        return bool(self.wildcard or self.labels or self.patterns)


_CONFIG = _Config()
_LOCK = threading.Lock()

# Runtime overrides applied by enable()/disable(), which take precedence over
# the environment. None means "defer to the environment".
_RUNTIME_LABELS: set[str] | None = None
_RUNTIME_WILDCARD: bool | None = None

_TRACE: list[dict] = []
_HITS: dict[str, int] = {}


def refresh() -> None:
    """Re-read the environment and re-apply runtime overrides.

    Called by :func:`enable` and :func:`disable`, and deliberately *not* called
    from the hot path. A configuration that was already armed is not re-read on
    every hit, so changing ``SCOREHALO_BP`` in a parent process does not affect
    a child that is already running -- call this explicitly after changing it.
    """
    _CONFIG.read()
    labels = _RUNTIME_LABELS
    wildcard = _RUNTIME_WILDCARD
    if labels is not None:
        _CONFIG.labels = set(labels)
    if wildcard is not None:
        _CONFIG.wildcard = wildcard


def _selected(label: str) -> bool:
    if _CONFIG.wildcard or label in _CONFIG.labels:
        return True
    for pattern in _CONFIG.patterns:
        if pattern.match(label):
            return True
    return False


def _matches_filter(label: str) -> bool:
    if not _CONFIG.match:
        return True
    return _CONFIG.match in label


def enable(*labels: str) -> None:
    """Arm breakpoints in this process until :func:`disable` is called.

    ``enable()`` with no arguments arms every breakpoint, like ``*`` in the
    environment. ``enable("a", "b")`` arms exactly those two, adding to whatever
    the environment already armed rather than replacing it -- enabling one
    breakpoint mid-investigation should not silently disarm its neighbours.

    Runtime state is not inherited by a subprocess. Export ``SCOREHALO_BP`` as
    well if homr should fire too.
    """
    global _RUNTIME_LABELS, _RUNTIME_WILDCARD
    with _LOCK:
        runtime = _runtime_set()
        if not labels or "*" in labels:
            _RUNTIME_WILDCARD = True
        runtime.update(label for label in labels if label != "*")
        _sync(runtime)


def disable(*labels: str) -> None:
    """Disarm everything, or just the named labels."""
    global _RUNTIME_LABELS, _RUNTIME_WILDCARD
    with _LOCK:
        if not labels or "*" in labels:
            _RUNTIME_LABELS = set()
            _RUNTIME_WILDCARD = False
            _sync(_RUNTIME_LABELS)
            return
        # Disarm only the named labels, leaving their neighbours armed.
        _RUNTIME_LABELS = _runtime_set() - set(labels)
        _sync(_RUNTIME_LABELS)


def _runtime_set() -> set[str]:
    """The armed labels as runtime code sees them, seeded from the environment.

    Runtime state is a single set, not a patch to be re-applied on every read:
    once enable() or disable() has run, this set *is* the truth and the
    environment is only consulted for action/match/skip/times/width.
    """
    global _RUNTIME_LABELS
    if _RUNTIME_LABELS is None:
        _CONFIG.read()
        _RUNTIME_LABELS = set(_CONFIG.labels)
    return _RUNTIME_LABELS


def _sync(labels: set[str]) -> None:
    _CONFIG.labels = set(labels)
    _CONFIG.wildcard = bool(_RUNTIME_WILDCARD)


def enabled(label: str) -> bool:
    """True when ``label`` is currently armed, ignoring skip/limit/filters."""
    return _selected(label)


def would_fire(label: str) -> bool:
    """True when ``label`` passes every gate for the *next* hit: armed, matches
    ``SCOREHALO_BP_MATCH``, and not already past ``SCOREHALO_BP_TIMES``.

    Use this to check a breakpoint is reachable without consuming a hit, which
    is the question you actually want answered before a three-minute run.
    """
    if not _selected(label) or not _matches_filter(label):
        return False
    if _CONFIG.times is not None and _HITS.get(label, 0) >= _CONFIG.times:
        return False
    return True


def reset() -> None:
    """Drop the trace and the per-label hit counters. Keeps the configuration."""
    global _TRACE, _HITS
    with _LOCK:
        _TRACE = []
        _HITS = {}


def bp(label: str, **context: object) -> bool:
    """Breakpoint. Returns True if it fired.

    Disabled path is one attribute read and a bool test -- no set construction,
    no dict, no lock. Context kwargs are already collected by the caller's
    frame but never touched here, so a ``bp()`` in a note loop costs one
    comparison while off.

    ``label`` should name the seam, not the symptom: ``emit.part_parsed`` will
    still be worth firing at for the next bug, ``emit.wrong_clef`` will not.
    """
    if not _CONFIG.armed:
        return False

    if not _selected(label) or not _matches_filter(label):
        return False

    with _LOCK:
        hits = _HITS.get(label, 0) + 1
        _HITS[label] = hits
        if hits <= _CONFIG.skip:
            return False
        if _CONFIG.times is not None and hits > _CONFIG.times:
            return False
        action = _CONFIG.action
        if action != "count":
            _TRACE.append({
                "label": label,
                "hits": hits,
                "pid": os.getpid(),
                "time": time.time(),
                "context": context,
            })

    if action == "raise":
        raise BreakpointStop(label, context, hits)
    if action == "pdb":
        _emit(_TRACE[-1])
        # A debugger that raises is indistinguishable from the bug being
        # hunted, so this is guarded here rather than inside _debugger: the
        # failure has to be swallowed at the call site to protect the run.
        try:
            _debugger(sys._getframe().f_back)
        except Exception as exc:
            sys.stderr.write("  [bp] debugger unavailable: %r\n" % (exc,))
    elif action != "count":
        _emit(_TRACE[-1])
    return True


def _debugger(frame) -> None:
    """Suspend into pdb at ``frame``.

    Suspends rather than raising: the whole point of this module is that the
    run continues and you see every hit, and ``SCOREHALO_BP_ACTION=pdb`` is the
    one mode where a human, not a log, drives the rest.
    """
    import pdb

    pdb.Pdb(stdin=sys.stdin, stdout=sys.stdout).set_trace(frame)


#: Sequences longer than this are abbreviated with a count of what was elided,
#: because "38 more" tells you something a bare "..." cannot.
_ABBREVIATE_AT = 12


def _abbreviate(value: object) -> object:
    """Shorten a long sequence or mapping, saying how much was dropped.

    Shared by the stderr emitter and :func:`report` so the two never drift --
    an elided list that reads one way on the console and another in the report
    is worse than either.
    """
    if isinstance(value, (list, tuple)) and len(value) > _ABBREVIATE_AT:
        dropped = len(value) - _ABBREVIATE_AT
        return list(value[:_ABBREVIATE_AT]) + ["... %d more" % dropped]
    if isinstance(value, dict) and len(value) > _ABBREVIATE_AT:
        keys = list(value)[:_ABBREVIATE_AT]
        shown = {key: value[key] for key in keys}
        shown["..."] = "%d more keys" % (len(value) - _ABBREVIATE_AT)
        return shown
    return value


def _render(value: object, width: int) -> str:
    """``repr`` capped at ``width``, keeping the elision count visible.

    A plain head-truncation replaced a list's "38 more" marker with "...", so
    the reader could see that something was cut but not how much. Truncating in
    the middle keeps both ends: the shape at the front, the count at the back.
    """
    text = repr(value)
    if len(text) <= width:
        return text
    if width <= 5:
        return text[:width]
    marker = " ..."
    keep = width - len(marker)
    head = keep * 2 // 3
    tail = keep - head
    if tail <= 0:
        return text[:keep] + marker
    return text[:head] + marker + text[-tail:]


def _emit(record: dict) -> None:
    label = record["label"]
    context = record["context"]
    stream = sys.stderr
    stream.write("  [bp] %s  (hit %d, pid %d)\n" % (label, record["hits"], record["pid"]))
    if not context:
        stream.write("        (no context)\n")
        return
    width = _CONFIG.width
    for key, value in context.items():
        shown = _abbreviate(value)
        stream.write("        %-18s %s\n" % (key + ":", _render(shown, width)))
    stream.flush()


def trace() -> list[dict]:
    """Every recorded hit this process, oldest first."""
    return list(_TRACE)


def hits() -> dict[str, int]:
    """Per-label hit counts, including hits that were skipped or over the limit."""
    return dict(_HITS)


def report(labels: list[str] | None = None) -> str:
    """Format the trace for a terminal, optionally filtered to ``labels``."""
    rows = _TRACE
    if labels:
        wanted = set(labels)
        rows = [row for row in rows if row["label"] in wanted]
    lines: list[str] = []
    if not rows:
        lines.append("(no breakpoint hits recorded)")
        return "\n".join(lines)
    lines.append("%d breakpoint hit(s)" % len(rows))
    for row in rows:
        lines.append("")
        lines.append("[bp] %s  (hit %d, pid %d)" % (row["label"], row["hits"], row["pid"]))
        for key, value in row["context"].items():
            shown = _abbreviate(value)
            lines.append("     %-18s %s" % (key + ":", _render(shown, _CONFIG.width)))
    return "\n".join(lines)


def dump(path: str) -> str:
    """Write the trace to ``path`` as JSON. Returns the path.

    JSON rather than a text report because these records routinely contain
    ``Fraction`` and ``ET.Element`` values that only ``repr`` can render; the
    text form is for eyes, this one is for ``jq`` and diffing two runs.
    """
    payload = {
        "trace": _TRACE,
        "hits": _HITS,
        "pid": os.getpid(),
        "command": " ".join(sys.argv),
    }
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=repr)
        handle.write("\n")
    return path


# Read the environment once, here, at import.
#
# The hot path checks ``_CONFIG.armed`` and returns False when nothing is armed,
# deliberately without re-reading the environment on every hit. That left the
# first hit reading a config that had never been populated: SCOREHALO_BP=...
# was a silent no-op and every instrumented run looked like a clean pass. A
# debugging tool that does nothing is worse than no tool, because it is trusted.
refresh()