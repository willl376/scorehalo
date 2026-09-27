"""Audition OMR output as audio: MusicXML -> MIDI -> WAV -> speakers/TV.

Purpose: let a human judge a transcription by ear, and let pitch analysis of
the real recording be diffed against what we produced (see `analyze`).

Chain:
  .musicxml  -o .mid   via MuseScore (headless, retried, fresh output path)
  .mid       -> .wav    via fluidsynth + a General MIDI soundfont
  .wav       -> audio   via pw-play (PipeWire), so it follows the real default sink
"""

import os
import shutil
import subprocess
import sys
import tempfile

MS_KEY = "MSCORE_BIN"


def find_musescore(explicit=None):
    """Locate the MuseScore binary: explicit, $MSCORE_BIN, PATH, or known opt dirs."""
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


def musicxml_to_midi(mxl_path, out_mid, musescore_bin=None, attempts=3):
    """Convert MusicXML/MXL to MIDI via headless MuseScore.

    Mirrors compare.render_score_musescore: a stale/identical output path can
    make MuseScore fail spuriously, so a fresh path is required and we retry.
    """
    bin_ = find_musescore(musescore_bin)
    if not bin_:
        raise SystemExit(
            "MuseScore binary not found; pass --musescore, set $MSCORE_BIN, "
            "or install MuseScore"
        )
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    out_mid = os.path.abspath(out_mid)
    if os.path.exists(out_mid):
        os.remove(out_mid)
    last = ""
    for _ in range(attempts):
        r = subprocess.run(
            [bin_, "-o", out_mid, os.path.abspath(mxl_path)],
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if os.path.exists(out_mid):
            return out_mid
        last = f"rc={r.returncode}: {(r.stderr or '')[-200:]}"
    raise SystemExit(f"MuseScore MIDI export failed after {attempts} attempts ({last})")


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
):
    """MusicXML -> MIDI -> WAV, optionally play it. Returns (mid, wav)."""
    tmp = work_dir or tempfile.mkdtemp(prefix="scorehalo-hear-")
    os.makedirs(tmp, exist_ok=True)
    base = os.path.splitext(os.path.basename(mxl_path))[0]
    mid = musicxml_to_midi(mxl_path, os.path.join(tmp, base + ".mid"), musescore_bin)
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
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
