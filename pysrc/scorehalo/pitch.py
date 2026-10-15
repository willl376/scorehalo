"""ScoreHalo pitch: live PyAudio capture + monophonic pitch tracking from a WAV.

Purpose: Wilbur can hum or whistle a melody into a microphone; this records a
clean WAV (crystal-clear 44.1 kHz / 16-bit / mono) and, when asked, turns the
recording into a note sequence we can diff against what ScoreHalo transcribed.
This is the only independent (non-image) ground truth available for the
*melody* line.

Recording (the replaced path): the previous `record_wav` shelled out to
`pw-record` (PipeWire). The recording path is now a direct PyAudio capture with
explicitly configured buffers -- the same parameters that were validated as
crystal-clear:

    CHUNK   = 1024   frames per buffer
    FORMAT  = paInt16 (16-bit, the rock-solid choice for pitch work)
    CHANNELS= 1      mono
    RATE    = 44100  CD quality

Analysis: NSDF (McLeod Pitch Method, 2002) for octave-robust f0, then a sliding
median filter over the f0 track, semitone quantization, and run-length grouping
into notes. Each note carries a `clarity` (peak NSDF, 0..1) so a caller can
tell a confident reading from a marginal one.

Scope, honestly stated:
  * MONOPHONIC analysis only. Chords produce a blurry/garbled track; this cannot
    transcribe polyphony. Humming or whistling one line is the use case.
  * Recording is a plain capture: it does not filter room noise. It reports the
    captured level (and warns on clipping / near-silence) but does not denoise.
    Use `scripts/pitch_listener.py` for a live, noise-guarded readout.
  * Durations are in SECONDS. The transcribed score's durations are quarter-note
    Fractions, so `diff_melody` compares pitch sequences only -- the two clocks
    are not interchangeable without a beat grid.
  * Does not recover *missing* music: a staff homr never read is silent, and
    silence is indistinguishable from "nothing there" here. This verifies the
    notes that survived; it cannot restore the ones that did not.

Usage:
    python -m scorehalo.pitch --help
    python -m scorehalo.pitch                    # capture 5 s -> output_crystal.wav
    python -m scorehalo.pitch out.wav            # capture to out.wav
    python -m scorehalo.pitch --record out.wav --secs 8
    python -m scorehalo.pitch exists.wav         # analyze an existing WAV
    python -m scorehalo.pitch --json exists.wav  # notes as JSON
    python -m scorehalo.pitch --against out.mxl exists.wav   # diff vs a score
    python -m scorehalo.pitch --list-devices
    python -m scorehalo.pitch --selftest         # NSDF on synthetic tones + capture init
"""

import argparse
import json
import os
import sys
import wave

import numpy as np

try:
    import pyaudio
except ImportError:  # pragma: no cover - environment check
    pyaudio = None

# musical constants (analysis)
A4_MIDI = 69
A4_HZ = 440.0
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# capture defaults (the validated crystal-clear configuration)
DEFAULT_CHUNK = 1024
DEFAULT_RATE = 44100
DEFAULT_CHANNELS = 1
DEFAULT_SECS = 5.0
DEFAULT_OUTPUT = "output_crystal.wav"

# analysis defaults
DEFAULT_SILENCE_RMS = 0.01     # absolute silence gate when not adaptive
MIN_SILENCE_RMS = 0.003        # adaptive gate never drops below this
MAX_SILENCE_RMS = 0.05         # ...nor rises above it (would gate real hum)
CLIP_LEVEL = 0.999             # |sample| at/above this counts as clipped


# ---------------------------------------------------------------------------
# Capture (PyAudio) -- the replaced recording path
# ---------------------------------------------------------------------------

def _pa():
    if pyaudio is None:
        raise SystemExit(
            "pyaudio is not installed. Run:  pip install pyaudio\n"
            "(needs the portaudio19-dev system packages on Debian/Ubuntu)"
        )
    return pyaudio


def list_devices():
    """Print every input device PyAudio can see. Returns the list."""
    pa = _pa()
    p = pa.PyAudio()
    try:
        n = p.get_device_count()
        for i in range(n):
            info = p.get_device_info_by_index(i)
            inch = int(info.get("maxInputChannels", 0))
            if inch == 0:
                continue
            print(f"  [{i}] {info['name']}  ({inch} in, sr {info['defaultSampleRate']:.0f})")
        if n == 0:
            print("  (no devices reported)")
        return [p.get_device_info_by_index(i) for i in range(n)]
    finally:
        p.terminate()


def _pcm_levels(frames, sampwidth):
    """Level stats for a list of raw PCM byte buffers -> dict or None.

    Returns peak (0..1), rms (0..1) and the fraction of samples at/above
    CLIP_LEVEL. Only 16-bit PCM is decoded; other widths return None rather
    than a wrong number.
    """
    if not frames or sampwidth != 2:
        return None
    a = np.frombuffer(b"".join(frames), dtype="<i2").astype(np.float32) / 32768.0
    if a.size == 0:
        return None
    peak = float(np.max(np.abs(a)))
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    clipped = float(np.mean(np.abs(a) >= CLIP_LEVEL))
    return {"peak": peak, "rms": rms, "clipped": clipped, "samples": int(a.size)}


def _report_level(levels):
    """Print a one-line capture-level verdict. Returns 'ok'|'clip'|'quiet'."""
    if levels is None:
        return "ok"
    peak, rms, clip = levels["peak"], levels["rms"], levels["clipped"]
    if clip > 0.001:
        print(f"level: CLIPPING ({clip*100:.1f}% of samples at full scale; "
              f"peak {peak:.3f}) -- lower the input gain")
        return "clip"
    if peak < 0.02:
        print(f"level: very quiet (peak {peak:.3f}, rms {rms:.4f}) -- raise the "
              "input gain or move closer to the mic")
        return "quiet"
    print(f"level: OK (peak {peak:.3f}, rms {rms:.4f})")
    return "ok"


def record_wav(path, secs=DEFAULT_SECS, rate=DEFAULT_RATE, channels=DEFAULT_CHANNELS,
               chunk=DEFAULT_CHUNK, fmt=None, device=None, meter=False):
    """Record `secs` seconds of mono audio to a WAV via PyAudio.

    Parameters mirror the validated crystal-clear configuration: 16-bit mono at
    44.1 kHz read in 1024-frame buffers. `meter=True` prints a live peak over
    the capture; either way the captured level is reported and warned about.
    Ctrl+C stops early and still writes what was captured. Returns the absolute
    output path.
    """
    pa = _pa()
    if fmt is None:
        fmt = pa.paInt16

    p = pa.PyAudio()
    frames = []
    interrupted = False
    sampwidth = 2
    try:
        sampwidth = p.get_sample_size(fmt)
        stream = p.open(format=fmt,
                        channels=channels,
                        rate=rate,
                        input=True,
                        frames_per_buffer=chunk,
                        input_device_index=device)
        try:
            n_buffers = max(1, int(round(rate / chunk * secs)))
            for i in range(n_buffers):
                try:
                    data = stream.read(chunk, exception_on_overflow=False)
                except KeyboardInterrupt:
                    interrupted = True
                    break
                frames.append(data)
                if meter and sampwidth == 2 and i % 8 == 0:
                    buf = np.frombuffer(data, dtype="<i2")
                    pk = float(np.max(np.abs(buf))) / 32768.0 if buf.size else 0.0
                    bar = "#" * min(40, int(pk * 40))
                    sys.stderr.write(f"\r  |{bar:<40}| {pk:0.3f}")
                    sys.stderr.flush()
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        p.terminate()
    if meter:
        sys.stderr.write("\n")

    levels = _pcm_levels(frames, sampwidth)
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))
    if interrupted:
        captured = len(b"".join(frames)) / max(1, sampwidth * channels) / rate
        print(f"capture interrupted at {captured:.1f}s -- wrote what was captured")
    _report_level(levels)
    return path


# ---------------------------------------------------------------------------
# Analysis (NSDF) -- preserved from the previous pitch.py, then hardened
# ---------------------------------------------------------------------------

def load_wav_mono(path):
    """Read a WAV file -> (float32 mono samples in [-1,1], sample_rate)."""
    with wave.open(os.path.abspath(path)) as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(n)
    if width == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif width == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise SystemExit(f"unsupported WAV sample width: {width*8} bit")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a.astype(np.float32), sr


def _nsdf(x, max_tau):
    """Normalized square difference function. Returns nsdf[tau] for tau 0..max_tau."""
    n = len(x)
    sq = x.astype(np.float64) ** 2
    c = np.concatenate(([0.0], np.cumsum(sq)))  # c[k] = sum_{i<k} sq[i]
    taus = np.arange(0, max_tau + 1)
    # m(tau) = sum_{j<n-tau} x[j]^2 + sum_{j=tau..n-1} x[j]^2
    m = c[n - taus] + (c[n] - c[taus])
    # r(tau) = sum_{j<n-tau} x[j]*x[j+tau], via FFT autocorrelation
    nfft = 1 << int(np.ceil(np.log2(2 * n)))
    X = np.fft.rfft(x, nfft)
    r = np.fft.irfft(np.abs(X) ** 2, nfft)[: max_tau + 1]
    denom = m.copy()
    denom[denom < 1e-12] = 1e-12
    nsdf = 2.0 * r / denom
    nsdf[0] = 1.0
    return nsdf


def _nsdf_peak(frame, sr, fmin, fmax, thresh, silence_rms):
    """One NSDF peak-pick. Returns (f0_hz, clarity) with f0=0.0 when unvoiced.

    `clarity` is the NSDF value at the chosen peak (roughly 0..1); it is the
    pitch-tracker's own confidence and is carried through to each note.
    """
    if len(frame) < 256:
        return 0.0, 0.0
    x = frame - np.mean(frame)
    rms = float(np.sqrt(np.mean(x**2)))
    if rms < silence_rms:  # silence / noise floor
        return 0.0, 0.0
    tau_min = max(2, int(sr / fmax))
    tau_max = min(len(x) - 1, int(sr / fmin))
    if tau_max <= tau_min:
        return 0.0, 0.0
    nsdf = _nsdf(x, tau_max)
    seg = nsdf[tau_min : tau_max + 1]
    if len(seg) == 0 or np.max(seg) <= 0:
        return 0.0, 0.0
    # local maxima
    peaks = np.where((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]))[0] + 1
    if len(peaks) == 0:
        return 0.0, 0.0
    gmax = float(np.max(seg[peaks]))
    # first prominent peak (>= thresh*global max) avoids picking the octave-below
    cand = [p for p in peaks if seg[p] >= thresh * gmax]
    if not cand:
        cand = list(peaks)
    p = cand[0]
    tau = p + tau_min
    clarity = float(seg[p])
    # parabolic interpolation around the peak for sub-sample precision
    if 0 < tau < len(nsdf) - 1:
        y0, y1, y2 = nsdf[tau - 1], nsdf[tau], nsdf[tau + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            tau = tau + 0.5 * (y0 - y2) / denom
    f0 = sr / tau
    if not (fmin <= f0 <= fmax):
        return 0.0, 0.0
    return f0, clarity


def detect_f0(frame, sr, fmin=65.0, fmax=1400.0, thresh=0.90, silence_rms=DEFAULT_SILENCE_RMS):
    """Estimate f0 (Hz) of one frame via NSDF peak picking. Returns 0.0 if unvoiced."""
    return _nsdf_peak(frame, sr, fmin, fmax, thresh, silence_rms)[0]


def hz_to_midi(f):
    return A4_MIDI + 12.0 * np.log2(f / A4_HZ)


def midi_to_name(m):
    m = int(round(m))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def estimate_noise_floor(samples, sr, frame=2048, hop=1024, pct=10.0):
    """Estimate the room-noise RMS as a low percentile of frame RMS values.

    The quietest frames are almost never a sustained note, so their level is a
    usable floor for an adaptive silence gate. Returns 0.0 for a signal too
    short to frame.
    """
    n = len(samples)
    if n < frame:
        return 0.0
    rmss = []
    for i in range(0, n - frame + 1, hop):
        seg = samples[i : i + frame]
        rmss.append(float(np.sqrt(np.mean(seg.astype(np.float64) ** 2))))
    if not rmss:
        return 0.0
    return float(np.percentile(rmss, pct))


def adaptive_silence_rms(samples, sr):
    """Pick a silence gate from the signal's own noise floor, clamped to sane bounds."""
    floor = estimate_noise_floor(samples, sr)
    gate = floor * 3.0 if floor > 0 else DEFAULT_SILENCE_RMS
    return float(min(MAX_SILENCE_RMS, max(MIN_SILENCE_RMS, gate)))


def _median_filter_f0(f0s, k):
    """Sliding median over the VOICED frames of an f0 track.

    This is the real fix for the old code, which clamped outliers to the
    *global* median (and never even used its `median_k` argument): a single
    wrong median could weld an entire passage to one pitch. A sliding window
    removes isolated octave outliers and centres vibrato while leaving genuine
    note changes intact. Unvoiced frames stay 0.
    """
    if k < 3:
        return f0s
    out = f0s.copy()
    half = k // 2
    n = len(f0s)
    for i in range(n):
        if f0s[i] <= 0:
            continue
        lo, hi = max(0, i - half), min(n, i + half + 1)
        vals = f0s[lo:hi][f0s[lo:hi] > 0]
        if vals.size >= 2:
            out[i] = float(np.median(vals))
    return out


def track(samples, sr, window=2048, hop=512, fmin=65.0, fmax=1400.0,
          silence_rms=DEFAULT_SILENCE_RMS, median_k=5, min_frames=3,
          gap_merge_frames=2):
    """Track a monophonic melody -> list of notes: {midi, hz, start, dur, name, clarity}."""
    n = len(samples)
    if n < window:
        return []
    n_frames = 1 + (n - window) // hop
    f0s = np.zeros(n_frames, dtype=np.float64)
    clar = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        seg = samples[i * hop : i * hop + window]
        f0, c = _nsdf_peak(seg, sr, fmin, fmax, 0.90, silence_rms)
        f0s[i] = f0
        clar[i] = c
    # Sliding median filter on the voiced frames (octave outliers + vibrato).
    f0s = _median_filter_f0(f0s, median_k)
    # frame-level midi (0 == unvoiced)
    midis = np.array([hz_to_midi(f) if f > 0 else 0.0 for f in f0s])
    # group into runs of equal quantized semitone
    notes = []
    i = 0
    while i < len(midis):
        if midis[i] <= 0:
            i += 1
            continue
        q = int(round(midis[i]))
        j = i
        while j < len(midis) and midis[j] > 0 and int(round(midis[j])) == q:
            j += 1
        # Absorb a short unvoiced gap followed by the SAME note: a dropped
        # frame in the middle of a held tone (or a vibrato trough) is not a
        # note boundary.
        k = j
        gap = 0
        while k < len(midis) and midis[k] <= 0 and gap < gap_merge_frames:
            gap += 1
            k += 1
        if gap and k < len(midis) and midis[k] > 0 and int(round(midis[k])) == q:
            j = k
            while j < len(midis) and midis[j] > 0 and int(round(midis[j])) == q:
                j += 1
        if j - i >= min_frames:
            # Note boundaries: the run of frames covering this pitch. End is the
            # start of the first frame after the run (a frame overlaps both sides,
            # so adding a whole window here would overstate every duration).
            start = i * hop / sr
            end = j * hop / sr
            voiced = f0s[i:j][f0s[i:j] > 0]
            hz = float(np.median(voiced)) if voiced.size else 0.0
            voiced_clar = clar[i:j][f0s[i:j] > 0]
            clarity = float(np.mean(voiced_clar)) if voiced_clar.size else 0.0
            notes.append(
                {
                    "midi": q,
                    "name": midi_to_name(q),
                    "start": round(start, 3),
                    "dur": round(end - start, 3),
                    "hz": round(hz, 1),
                    "clarity": round(clarity, 3),
                }
            )
        i = j
    return notes


def detect_notes(path, fmin=65.0, fmax=1400.0, silence_rms=None):
    """Analyze a WAV -> note list. `silence_rms=None` picks an adaptive gate.

    Returns (notes, info) where info records the sample rate, duration and the
    silence gate actually used (so a caller can report it honestly).
    """
    samples, sr = load_wav_mono(path)
    if silence_rms is None:
        silence_rms = adaptive_silence_rms(samples, sr)
    notes = track(samples, sr, fmin=fmin, fmax=fmax, silence_rms=silence_rms)
    info = {
        "path": os.path.abspath(path),
        "sr": sr,
        "dur": len(samples) / sr,
        "silence_rms": round(silence_rms, 4),
        "notes": notes,
    }
    return notes, info


def analyze_wav(path, fmin=65.0, fmax=1400.0, silence_rms=None, as_json=False):
    notes, info = detect_notes(path, fmin=fmin, fmax=fmax, silence_rms=silence_rms)
    if as_json:
        print(json.dumps(info, indent=2))
        return 0
    print(f"analyzed {info['dur']:.1f}s @ {info['sr']}Hz -> {len(notes)} notes "
          f"(monophonic, silence gate {info['silence_rms']}) from {info['path']}")
    for n in notes:
        print(f"  {n['start']:6.2f}s  {n['dur']:5.2f}s  {n['name']:>4} "
              f"(midi {n['midi']}, {n['hz']:.1f} Hz, clarity {n['clarity']:.2f})")
    return 0


# ---------------------------------------------------------------------------
# Integration: diff a hummed melody against a transcribed score
# ---------------------------------------------------------------------------

_STEP_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _pitch_to_midi(pitch):
    """MusicXML (step, alter, octave) -> MIDI, or None if not a real pitch."""
    if not pitch:
        return None
    step, alter, octave = pitch
    if step is None or octave is None:
        return None
    semi = _STEP_SEMITONE.get(str(step).upper())
    if semi is None:
        return None
    try:
        return 12 * (int(octave) + 1) + semi + int(alter or 0)
    except (TypeError, ValueError):
        return None


def _melody_from_musicxml(path, staff=None):
    """Pitched MIDI notes of a score, in (onset, ...) order.

    Uses score.parse_streams so the score-side semantics (chord ordering,
    rests excluded, durations) match `scorehalo score`. Imported lazily so
    `pitch.py` still runs standalone.
    """
    from scorehalo.score import PITCHED, parse_streams

    streams = parse_streams(path)
    rows = []
    for key, notes in streams.items():
        part, st, voice = key
        if staff is not None and str(st) != str(staff):
            continue
        for onset, kind, pitch, _dur in notes:
            if kind != PITCHED:
                continue
            midi = _pitch_to_midi(pitch)
            if midi is not None:
                rows.append((onset, part, st, voice, midi))
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return [r[4] for r in rows]


def _melody_from_notes(notes):
    """A detected note list / int sequence / name sequence -> ordered MIDI ints."""
    out = []
    for n in notes:
        if isinstance(n, dict):
            if n.get("midi") is not None:
                out.append(int(round(n["midi"])))
            elif n.get("name"):
                out.append(_name_to_midi(n["name"]))
        elif isinstance(n, str):
            out.append(_name_to_midi(n))
        elif n is not None:
            out.append(int(round(n)))
    return out


def _name_to_midi(name):
    if not name:
        return 0
    s = str(name).strip()
    letter = s[0].upper()
    i = 1
    accidental = 0
    while i < len(s) and s[i] in "#b":
        accidental += 1 if s[i] == "#" else -1
        i += 1
    octave = int(s[i:]) if i < len(s) else 4
    return 12 * (octave + 1) + _STEP_SEMITONE.get(letter, 0) + accidental


def load_melody(source, staff=None):
    """Coerce a melody source into ordered MIDI ints.

    Source may be: a path to a MusicXML/.mxl score, a list of MIDI ints, a
    list of note dicts (from `track`), a list of note-name strings, or an
    already-loaded score stream dict.
    """
    if isinstance(source, str):
        return _melody_from_musicxml(source, staff=staff)
    if isinstance(source, dict):
        from scorehalo.score import PITCHED

        rows = []
        for key, notes in source.items():
            part, st, voice = key
            if staff is not None and str(st) != str(staff):
                continue
            for onset, kind, pitch, _dur in notes:
                if kind != PITCHED:
                    continue
                midi = _pitch_to_midi(pitch)
                if midi is not None:
                    rows.append((onset, part, st, voice, midi))
        rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
        return [r[4] for r in rows]
    return _melody_from_notes(source)


def _lcs_ops(a, b, max_cells=1_500_000):
    """Align two pitch lists; return [(op, a_idx, b_idx)] with op in m/d/i.

    O(n*m) DP, with a linear greedy fallback for pathologically long melodies
    (the same guard `score._lcs_ops` uses).
    """
    n, m = len(a), len(b)
    if n * m > max_cells:
        ops = []
        i = j = 0
        while i < n and j < m:
            if a[i] == b[j]:
                ops.append(("m", i, j)); i += 1; j += 1
            elif a[i] in b[j : j + 8]:
                ops.append(("d", i, j)); i += 1
            else:
                ops.append(("i", i, j)); j += 1
        while i < n:
            ops.append(("d", i, j)); i += 1
        while j < m:
            ops.append(("i", i, j)); j += 1
        return ops
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, nxt, ai = dp[i], dp[i + 1], a[i]
        for j in range(m - 1, -1, -1):
            row[j] = nxt[j + 1] + 1 if ai == b[j] else max(nxt[j], row[j + 1])
    ops = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            ops.append(("m", i, j)); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            ops.append(("d", i, j)); i += 1
        else:
            ops.append(("i", i, j)); j += 1
    while i < n:
        ops.append(("d", i, j)); i += 1
    while j < m:
        ops.append(("i", i, j)); j += 1
    return ops


def diff_melody(truth, pred, staff=None):
    """Pitch-sequence diff of a hummed melody (truth) vs a score (pred).

    Reports the same substitution classes as `scorehalo score`: exact,
    wrong_pitch (matched position, different pitch), missed (truth only) and
    spurious (pred only). Durations are deliberately NOT compared -- the hum is
    in seconds and the score in quarter-note Fractions, so a "duration match"
    would be a coincidence of two unrelated clocks.
    """
    t = load_melody(truth, staff=staff)
    p = load_melody(pred, staff=staff)
    stats = {
        "truth_notes": len(t), "pred_notes": len(p),
        "exact": 0, "wrong_pitch": 0, "missed": 0, "spurious": 0,
    }
    loose_t, loose_p = [], []
    for op, i, j in _lcs_ops(t, p):
        if op == "m":
            stats["exact"] += 1
        elif op == "d":
            loose_t.append(t[i])
        else:
            loose_p.append(p[j])
    stats["wrong_pitch"] = min(len(loose_t), len(loose_p))
    stats["missed"] = max(0, len(loose_t) - len(loose_p))
    stats["spurious"] = max(0, len(loose_p) - len(loose_t))
    precision = 100.0 * stats["exact"] / stats["pred_notes"] if stats["pred_notes"] else 0.0
    recall = 100.0 * stats["exact"] / stats["truth_notes"] if stats["truth_notes"] else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    stats.update(pitch_precision=round(precision, 1),
                 pitch_recall=round(recall, 1), f1=round(f1, 1))
    return stats


def format_melody_diff(stats, source=None):
    """Human report for a diff_melody() result."""
    lines = []
    if source:
        lines.append(f"melody truth : hum ({stats['truth_notes']} notes)")
        lines.append(f"melody pred  : {source} ({stats['pred_notes']} notes)")
    lines += [
        f"exact pitches        : {stats['exact']:4d}",
        f"wrong pitch          : {stats['wrong_pitch']:4d}",
        f"missed (hum only)    : {stats['missed']:4d}",
        f"spurious (score only): {stats['spurious']:4d}",
        f"pitch precision      : {stats['pitch_precision']:.1f}%",
        f"pitch recall         : {stats['pitch_recall']:.1f}%",
        f"F1                   : {stats['f1']:.1f}%",
        "",
        "(pitch sequence only; hum seconds vs score quarter-notes are not "
        "comparable without a beat grid)",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _analyzer_selftest():
    """Verify NSDF pitch tracking on synthetic tones we generate ourselves."""
    sr = 16000
    seq = [69, 71, 72, 74, 76]  # A4 B4 C5 D5 E5
    tone_len, gap = 0.30, 0.10
    parts = []
    for m in seq:
        f = A4_HZ * 2 ** ((m - A4_MIDI) / 12.0)
        t = np.arange(int(tone_len * sr)) / sr
        parts.append(0.5 * np.sin(2 * np.pi * f * t))
        parts.append(np.zeros(int(gap * sr)))
    sig = np.concatenate(parts).astype(np.float32)
    notes = track(sig, sr)
    got = [n["midi"] for n in notes]
    ok = got == seq
    print(f"sent:     {seq}  ({[midi_to_name(m) for m in seq]})")
    print(f"recovered:{got}  ({[midi_to_name(m) for m in got]})")
    print(f"durations: {[n['dur'] for n in notes]}")
    print(f"clarity:   {[n['clarity'] for n in notes]}")
    print("NSDF SELFTEST:", "PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)
    # a rest must not invent notes
    silence = np.zeros(int(1.0 * sr), dtype=np.float32)
    assert track(silence, sr) == [], "selftest: silence produced notes!"
    print("silence->[]: PASS")
    return 0


def _capture_selftest():
    """Verify the PyAudio capture path can open/close a 44100/16/mono input stream.

    On a headless box there is no default input device; that is an environment
    limitation, reported as WARN, not a code failure.
    """
    pa = _pa()
    p = pa.PyAudio()
    try:
        info = p.get_default_input_device_info()
    except Exception:  # noqa: BLE001 - OSError/IOError depending on pyaudio version
        print("CAPTURE SELFTEST: WARN (no default input device on this box)")
        return 0
    try:
        stream = p.open(format=pa.paInt16, channels=1, rate=44100,
                        input=True, frames_per_buffer=1024,
                        input_device_index=info["index"])
        stream.stop_stream()
        stream.close()
        print(f"CAPTURE SELFTEST: PASS (default input '{info['name']}')")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"CAPTURE SELFTEST: FAIL ({e})")
        return 1


def _selftest():
    rc = _analyzer_selftest()
    return rc | _capture_selftest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scorehalo.pitch",
        description="live PyAudio capture -> WAV, and/or monophonic pitch tracking (NSDF)",
    )
    ap.add_argument("wav", nargs="?", default=None,
                    help="output WAV (missing/absent -> capture; existing -> analyze)")
    ap.add_argument("--record", action="store_true",
                    help="capture from the microphone (default when the target does not exist)")
    ap.add_argument("--secs", type=float, default=DEFAULT_SECS, help="capture seconds")
    ap.add_argument("--rate", type=int, default=DEFAULT_RATE, help="capture sample rate")
    ap.add_argument("--channels", type=int, default=DEFAULT_CHANNELS, help="capture channels")
    ap.add_argument("--chunk", type=int, default=DEFAULT_CHUNK, help="frames per buffer")
    ap.add_argument("--device", type=int, default=None, help="PyAudio input device index")
    ap.add_argument("--meter", action="store_true", help="show a live input-level meter while capturing")
    ap.add_argument("--target", default=None,
                    help="deprecated alias of --device (previous pw-record source id)")
    ap.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    ap.add_argument("--fmin", type=float, default=65.0, help="analysis floor (Hz)")
    ap.add_argument("--fmax", type=float, default=1400.0, help="analysis ceiling (Hz)")
    ap.add_argument("--silence-rms", type=float, default=None,
                    help="silence gate (default: adaptive from the noise floor)")
    ap.add_argument("--json", action="store_true", help="emit detected notes as JSON")
    ap.add_argument("--against", default=None,
                    help="MusicXML/.mxl score to diff the melody against (pitch sequence)")
    ap.add_argument("--staff", default=None, help="restrict the score melody to one staff number")
    ap.add_argument("--selftest", action="store_true", help="self-test NSDF + capture init")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.list_devices:
        list_devices()
        return 0

    # --target from the old pw-record era -> device index when numeric
    if args.target is not None and args.device is None:
        try:
            args.device = int(args.target)
        except ValueError:
            print(f"note: --target '{args.target}' is not an index; using the default input",
                  file=sys.stderr)

    path = args.wav or DEFAULT_OUTPUT
    if args.record or not os.path.exists(path):
        got = record_wav(path, secs=args.secs, rate=args.rate, channels=args.channels,
                         chunk=args.chunk, device=args.device, meter=args.meter)
        print(f"recorded {args.secs}s @ {args.rate}Hz -> {got}")
        if args.against is None:
            return 0
    rc = analyze_wav(path, fmin=args.fmin, fmax=args.fmax,
                     silence_rms=args.silence_rms, as_json=args.json)
    if args.against:
        notes, _info = detect_notes(path, fmin=args.fmin, fmax=args.fmax,
                                    silence_rms=args.silence_rms)
        stats = diff_melody(notes, args.against, staff=args.staff)
        print()
        print(format_melody_diff(stats, source=os.path.abspath(args.against)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
