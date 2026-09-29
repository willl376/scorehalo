"""Audition OMR output as audio: MusicXML -> MIDI -> WAV -> speakers/TV.

Purpose: let a human judge a transcription by ear, and let pitch analysis of
the real recording be diffed against what we produced (see `analyze`).

Listening is the fastest error-finder we have: a wrong note or a dropped beat
is obvious in four seconds of audio and invisible in a wall of MusicXML. It is
also the thing a non-musician actually wants out of an OMR tool, so it belongs
in the product, not just in a notebook.

Chain:
  .musicxml  -o .mid   via an ENGRAVER (see ENGRAVERS below)
  .mid       -> .wav    via fluidsynth + a General MIDI soundfont
  .wav       -> audio   via pw-play (PipeWire), so it follows the real default sink

ENGRAVERS, in order of preference
  musescore  MuseScore headless. The most faithful importer we have.
  lilypond   musicxml2ly + `lilypond`. Free, scriptable, and *second*
             opinion: a file MuseScore imports silently can still be broken
             (see the measure-arithmetic and slur defects), and LilyPond
             complains out loud. LilyPond is a hard dependency anyway -- the
             `emit` path already uses it -- so this fallback costs nothing.

  `--engraver` forces one. Default is `auto`: try MuseScore, fall back to
  LilyPond. The fallback is the point; a missing MuseScore must not mean a
  user cannot hear their own transcription.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

MS_KEY = "MSCORE_BIN"


def find_musescore(explicit=None, strict=False):
    """Locate the MuseScore binary: explicit, $MSCORE_BIN, PATH, or known opt dirs.

    An `explicit` path that does not exist is never silently replaced by a
    different binary on PATH -- if someone types `--musescore /wrong/path` they
    mean it, and quietly using another engraver produces output they did not
    ask for with no warning. (Found this while testing --engraver.) By default
    we report that and return None, so `auto` can fall back to LilyPond;
    `strict=True` makes it fatal, which is right when MuseScore was demanded.
    """
    if explicit:
        if os.path.exists(explicit) and os.access(explicit, os.X_OK):
            return explicit
        msg = f"the MuseScore path given does not exist or is not runnable: {explicit}"
        if strict:
            raise SystemExit(msg)
        print(f"hear: {msg}", file=sys.stderr)
        return None
    for cand in (
        explicit,
        os.environ.get(MS_KEY),
        shutil.which("mscore4portable"),
        shutil.which("mscore"),
        os.path.expanduser("~/.local/opt/musescore/bin/mscore4portable"),
    ):
        if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def find_soundfont(explicit=None):
    """Locate a General MIDI soundfont for fluidsynth."""
    for cand in (
        explicit,
        os.environ.get("SCOREHALO_SOUNDFONT"),
        "/usr/share/sounds/sf2/FluidR3_GM.sf2",
        "/usr/share/sounds/sf2/default-GM.sf2",
    ):
        if cand and os.path.exists(cand):
            return cand
    return None


def musicxml_to_midi_lilypond(mxl_path, out_mid, timeout=300):
    """Convert MusicXML to MIDI with musicxml2ly + LilyPond. No GUI involved.

    Two traps, both learned the hard way, both of which produce a *successful*
    run with no output rather than an error:

    1. musicxml2ly writes the `\\midi` block COMMENTED OUT, and LilyPond only
       writes a .midi when that block is live. Un-comment it with a
       line-anchored regex -- matching a literal like `%  \\midi {\\tempo 4 = 100 }`
       silently matches nothing once the converter's spacing shifts, and you
       get no MIDI and no complaint.
    2. The block must stay INSIDE the `\\score`. A `\\midi{}` at the top level is
       accepted and ignored. So: edit in place, never append at the end.
    """
    for tool in ("musicxml2ly", "lilypond"):
        if not shutil.which(tool):
            raise SystemExit(
                f"{tool} not found on PATH; install lilypond (which ships musicxml2ly)"
            )
    out_mid = os.path.abspath(out_mid)
    work = tempfile.mkdtemp(prefix="scorehalo-ly-")
    base = os.path.splitext(os.path.basename(mxl_path))[0]
    ly = os.path.join(work, base + ".ly")
    mid = os.path.join(work, base + ".midi")
    try:
        r = subprocess.run(
            ["musicxml2ly", "-o", ly, os.path.abspath(mxl_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0 or not os.path.exists(ly):
            # musicxml2ly is a Python script and CRASHES (uncaught TypeError in
            # group_tuplets) on homr's damaged tuplet data rather than exiting
            # cleanly. Surface the actual exception, not just the rc.
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            raise SystemExit(
                "musicxml2ly failed on this score (its own converter, not our bug):\n  "
                + "\n  ".join(tail[-4:])
            )

        with open(ly) as fh:
            src = fh.read()
        uncommented, n = re.subn(r"(?m)^([ \t]*)%[ \t]*(\\midi\b.*)$", r"\1\2", src)
        if n == 0 and not re.search(r"\\midi\b", uncommented):
            raise SystemExit(
                "no \\midi block in the converted score; cannot emit MIDI"
            )
        with open(ly, "w") as fh:
            fh.write(uncommented)

        subprocess.run(
            ["lilypond", "-o", os.path.join(work, base), ly],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if not os.path.exists(mid):
            raise SystemExit("lilypond produced no MIDI (see its warnings above)")
        shutil.move(mid, out_mid)
        return out_mid
    finally:
        shutil.rmtree(work, ignore_errors=True)


def musicxml_to_midi(mxl_path, out_mid, musescore_bin=None, attempts=3,
                     engraver="auto"):
    """Convert MusicXML/MXL to MIDI using the best available engraver.

    `auto` prefers MuseScore and falls back to LilyPond. The fallback matters:
    MuseScore here is a 146MB portable app that has already cost us one silent
    rc=40 import-dialog hang, and it can wedge headless mode indefinitely.
    """
    out_mid = os.path.abspath(out_mid)
    if engraver == "lilypond":
        return musicxml_to_midi_lilypond(mxl_path, out_mid)

    # strict only when MuseScore was explicitly demanded: in `auto` a bad path
    # should degrade to LilyPond rather than abort the whole audition.
    bin_ = find_musescore(musescore_bin, strict=(engraver == "musescore"))
    if not bin_:
        if engraver == "musescore":
            raise SystemExit(
                "MuseScore binary not found; pass --musescore, set $MSCORE_BIN, "
                "or use --engraver lilypond"
            )
        return musicxml_to_midi_lilypond(mxl_path, out_mid)

    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.path.exists(out_mid):
        os.remove(out_mid)
    last = ""
    for _ in range(attempts):
        try:
            r = subprocess.run(
                [bin_, "-o", out_mid, os.path.abspath(mxl_path)],
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
        except subprocess.TimeoutExpired:
            # MuseScore can hang headless forever; that is exactly why the
            # LilyPond fallback exists, so stop retrying and take it.
            last = "timed out after 300s (headless MuseScore hang)"
            break
        if os.path.exists(out_mid):
            return out_mid
        last = f"rc={r.returncode}: {(r.stderr or '')[-200:]}"
    if engraver == "musescore":
        raise SystemExit(f"MuseScore MIDI export failed after {attempts} attempts ({last})")
    print(
        f"hear: MuseScore could not render this ({last});\n"
        f"      trying LilyPond instead -- a failure there means the SCORE is "
        f"damaged, not that the tool is missing.",
        file=sys.stderr,
    )
    return musicxml_to_midi_lilypond(mxl_path, out_mid)


def midi_to_wav(mid_path, out_wav, soundfont=None, sample_rate=44100):
    """Synthesize a MIDI file to WAV with fluidsynth."""
    sf = find_soundfont(soundfont)
    if not sf:
        raise SystemExit(
            "no General MIDI soundfont found; pass --soundfont or set "
            "$SCOREHALO_SOUNDFONT"
        )
    fs = shutil.which("fluidsynth")
    if not fs:
        raise SystemExit("fluidsynth not installed")
    out_wav = os.path.abspath(out_wav)
    if os.path.exists(out_wav):
        os.remove(out_wav)
    r = subprocess.run(
        [
            fs,
            "-ni",
            "-g", "1.0",  # gain
            "-r", str(sample_rate),
            "-F", out_wav,
            sf,
            os.path.abspath(mid_path),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if not os.path.exists(out_wav):
        raise SystemExit(f"fluidsynth failed (rc={r.returncode}): {r.stderr[-300:]}")
    return out_wav


def active_sink():
    """Describe the current default audio sink.

    Playback follows the PipeWire default, so this is what a listener will
    actually hear. Caveat worth surfacing: a sink can be *routed* yet silent
    (e.g. a DP->HDMI adapter with no audio return). That is a hardware fact no
    tool call here can confirm, so callers should not over-claim.
    """
    wpctl = shutil.which("wpctl")
    if not wpctl:
        return {"name": "unknown", "node": None}
    r = subprocess.run(
        [wpctl, "inspect", "@DEFAULT_AUDIO_SINK@"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    name, node = "unknown", None
    for line in r.stdout.splitlines():
        if "node.description" in line and "=" in line:
            name = line.split("=", 1)[1].strip().strip('"')
        if "node.name" in line and "=" in line:
            node = line.split("=", 1)[1].strip().strip('"')
    return {"name": name, "node": node}


def play(wav_path, player="pw-play", extra_args=()):
    """Play a WAV on the current default PipeWire sink (e.g. the TV)."""
    p = shutil.which(player)
    if not p:
        raise SystemExit(f"{player} not found; install pipewire-utils")
    return subprocess.run([p, *extra_args, os.path.abspath(wav_path)]).returncode


def audition(
    mxl_path,
    work_dir=None,
    musescore_bin=None,
    soundfont=None,
    player="pw-play",
    keep=False,
    play_it=True,
    quiet=False,
    engraver="auto",
):
    """MusicXML -> MIDI -> WAV, optionally play it. Returns (mid, wav)."""
    tmp = work_dir or tempfile.mkdtemp(prefix="scorehalo-hear-")
    os.makedirs(tmp, exist_ok=True)
    base = os.path.splitext(os.path.basename(mxl_path))[0]
    mid = musicxml_to_midi(
        mxl_path, os.path.join(tmp, base + ".mid"), musescore_bin, engraver=engraver
    )
    wav = midi_to_wav(mid, os.path.join(tmp, base + ".wav"), soundfont)
    if not quiet:
        dur = os.path.getsize(wav) / (44100 * 2 * 2)  # 16-bit stereo
        print(f"midi: {mid}")
        print(f"wav:  {wav}  (~{dur:.0f}s)")
    if play_it:
        sink = active_sink()
        if not quiet:
            print(f"default sink: {sink['name']}")
            print("playing (routed there -- it will only be audible if that device is connected)...")
        play(wav, player=player)
    if not keep and work_dir is None and not play_it:
        shutil.rmtree(tmp, ignore_errors=True)
    return mid, wav


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(
        prog="scorehalo hear", description="audition a MusicXML/MXL score as audio"
    )
    ap.add_argument("score", help=".musicxml or .mxl to play")
    ap.add_argument("--musescore", help="path to MuseScore binary")
    ap.add_argument("--soundfont", help="path to a GM soundfont (.sf2)")
    ap.add_argument("--player", default="pw-play", help="playback command")
    ap.add_argument("--no-play", action="store_true", help="render only, do not play")
    ap.add_argument("--keep", action="store_true", help="keep temp dir")
    ap.add_argument("--out", help="directory for the .mid/.wav (default: temp)")
    ap.add_argument(
        "--engraver",
        choices=("auto", "musescore", "lilypond"),
        default="auto",
        help="which importer to use (default: auto = MuseScore, else LilyPond)",
    )
    args = ap.parse_args(argv)
    if not os.path.exists(args.score):
        raise SystemExit(f"no such score: {args.score}")
    audition(
        args.score,
        work_dir=args.out,
        musescore_bin=args.musescore,
        soundfont=args.soundfont,
        player=args.player,
        keep=args.keep,
        play_it=not args.no_play,
        engraver=args.engraver,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
