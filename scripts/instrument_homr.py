"""Instrument a byte-identical copy of the INSTALLED homr with breakpoints.

Makes three copies byte-identical to
``.venv/lib/python3.12/site-packages/homr`` and then adds breakpoint calls to the
three functions that decide the time signature. The venv is never modified: the
instrumented tree is shadowed in via ``PYTHONPATH`` for one run, so the resulting
MusicXML should match what homr already produced, and the log should say why.

Deliberately asserts the copy is identical before editing, because the whole
reason this script exists is that I once patched a tree that nothing imported.
"""

import filecmp
import os
import shutil
import subprocess
import sys

#: repo root, derived from this file's location so the script runs from any
#: checkout instead of one machine's home directory.
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLED = os.path.join(REPO, ".venv/lib/python3.12/site-packages")
TARGET = "/tmp/homr-instr"

EDITS = [
    # (anchor, replacement)
    ("""def find_division_and_time_signature_nominator(voice: list[SymbolChord]) -> tuple[int, Fraction]:
    durations = [Fraction(1, 4)]""",
     """def find_division_and_time_signature_nominator(voice: list[SymbolChord]) -> tuple[int, Fraction]:
    import os as _os
    if _os.environ.get("SCOREHALO_BP"):
        from scorehalo.breakpoints import bp as _bp
        _bp("homr.nominator_input", chords=len(voice),
            positions=sorted({x.position for c in voice for x in c.symbols}),
            two_position_chords=sum(
                1 for c in voice
                if len({x.position for x in c.symbols
                        if x.rhythm.startswith(("note", "rest"))}) > 1))
    durations = [Fraction(1, 4)]"""),
    ("""    nominator: Fraction = np.median(measure_duration)  # type: ignore

    return find_common_division(durations), nominator""",
     """    nominator: Fraction = np.median(measure_duration)  # type: ignore

    import os as _os
    if _os.environ.get("SCOREHALO_BP"):
        from scorehalo.breakpoints import bp as _bp
        _bp("homr.nominator_out", nominator=str(nominator),
            quarters=str(nominator * 4),
            bars=[str(x * 4) for x in measure_duration])

    return find_common_division(durations), nominator"""),
    ("""    denominator = model_time_signature.rhythm.split("/")[1]
    attributes.add_child(time)
    beats = max(int(state.nominator * int(denominator)), 1)""",
     """    denominator = model_time_signature.rhythm.split("/")[1]
    attributes.add_child(time)
    beats = max(int(state.nominator * int(denominator)), 1)
    import os as _os
    if _os.environ.get("SCOREHALO_BP"):
        from scorehalo.breakpoints import bp as _bp
        _bp("homr.time_signature", token=model_time_signature.rhythm,
            state_nominator=str(state.nominator), denominator=denominator,
            beats=beats)"""),
]


def main():
    if os.path.isdir(TARGET):
        shutil.rmtree(TARGET)
    os.makedirs(TARGET)
    shutil.copytree(os.path.join(INSTALLED, "homr"), os.path.join(TARGET, "homr"))

    source = os.path.join(INSTALLED, "homr")
    copy = os.path.join(TARGET, "homr")
    # Only .py files: cmpfiles reports subdirectories as mismatches, and a
    # __pycache__ difference is not what this guard is for.
    names = [n for n in sorted(os.listdir(source)) if n.endswith(".py")]
    match, mismatch, errors = filecmp.cmpfiles(source, copy, names, shallow=False)
    if mismatch or errors:
        raise SystemExit("copy is not identical; refusing to patch: %s %s" % (mismatch, errors))
    print("copy verified byte-identical to installed (%d modules)" % len(match))

    path = os.path.join(copy, "music_xml_generator.py")
    text = open(path).read()
    for anchor, replacement in EDITS:
        if anchor not in text:
            raise SystemExit("anchor not found -- installed homr has changed:\n" + anchor[:90])
        text = text.replace(anchor, replacement, 1)
    open(path, "w").write(text)
    print("added %d breakpoints" % len(EDITS))

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([TARGET, os.path.join(REPO, "pysrc")])
    env["SCOREHALO_BP"] = "homr.*"
    env["SCOREHALO_BP_ACTION"] = "print"
    env["SCOREHALO_BP_WIDTH"] = "200"

    crops = sys.argv[1:] or [
        os.path.join(REPO, "data/out-system/homr/p0020/p0020.sys1_in.png"),
        os.path.join(REPO, "data/out-system/homr/p0020/p0020.sys2_in.png"),
        os.path.join(REPO, "data/out-system/homr/p0020/p0020.sys3_in.png"),
    ]
    work = "/tmp/bp-run"
    os.makedirs(work, exist_ok=True)
    for crop in crops:
        base = os.path.basename(crop)
        staged = os.path.join(work, base)
        shutil.copy(crop, staged)
        print("\n" + "=" * 72)
        print("RUN", base)
        print("=" * 72)
        proc = subprocess.run([os.path.join(REPO, ".venv/bin/homr"), staged],
                              capture_output=True, text=True, env=env, timeout=900)
        for line in proc.stderr.splitlines():
            if "[bp]" in line or "deviates" in line or "chords:" in line or "positions:" in line \
               or "state_nominator" in line or "beats:" in line or "nominator" in line \
               or "bars:" in line:
                print(line)
        produced = staged[:-4] + ".musicxml"
        print("wrote:", produced, "exists:", os.path.exists(produced))


if __name__ == "__main__":
    main()