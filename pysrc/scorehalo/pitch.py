"""ScoreHalo pitch: live PyAudio capture + monophonic pitch tracking from a WAV.

Purpose: Wilbur can hum or whistle a melody into a microphone; this records a
clean WAV (crystal-clear 44.1 kHz / 16-bit / mono) and, when asked, turns the
recording into a note sequence we can diff against what ScoreHalo transcribed.
This is the only independent (non-image) ground truth available for the
*melody* line.

Recording (the replaced path): the previous `record_wav` shelled out to
`pw-record` (PipeWire). The recording path is now a direct PyAudio capture with
explicitly configured buffers — the same parameters that were validated as
crystal-clear:

    CHUNK   = 1024   frames per buffer
    FORMAT  = paInt16 (16-bit, the rock-solid choice for pitch work)
    CHANNELS= 1      mono
    RATE    = 44100  CD quality

Analysis: NSDF (McLeod Pitch Method, 2002) for octave-robust f0, then median
filtering, semitone quantization, and run-length grouping into notes.

Scope, honestly stated:
  * MONOPHONIC analysis only. Chords produce a blurry/garbled track; this cannot
    transcribe polyphony. Humming or whistling one line is the use case.
  * Recording is a plain capture: it does not filter room noise. Use
    `scripts/pitch_listener.py` for a live, noise-guarded readout.
  * Does not recover *missing* music: a staff homr never read is silent, and
    silence is indistinguishable from "nothing there" here. This verifies the
    notes that survived; it cannot restore the ones that did not.

Usage:
    python -m scorehalo.pitch --help
    python -m scorehalo.pitch                    # capture 5 s -> output_crystal.wav
    python -m scorehalo.pitch out.wav            # capture to out.wav
    python -m scorehalo.pitch --record out.wav --secs 8
    python -m scorehalo.pitch exists.wav         # analyze an existing WAV
    python -m scorehalo.pitch --list-devices
    python -m scorehalo.pitch --selftest         # NSDF on synthetic tones + capture init
"""

import argparse
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


def record_wav(path, secs=DEFAULT_SECS, rate=DEFAULT_RATE, channels=DEFAULT_CHANNELS,
               chunk=DEFAULT_CHUNK, fmt=None, device=None):
    """Record `secs` seconds of mono audio to a WAV via PyAudio.

    Parameters mirror the validated crystal-clear configuration: 16-bit mono at
    44.1 kHz read in 1024-frame buffers.  Returns the absolute output path.
    """
    pa = _pa()
    if fmt is None:
        fmt = pa.paInt16

    p = pa.PyAudio()
    try:
        stream = p.open(format=fmt,
                        channels=channels,
                        rate=rate,
                        input=True,
                        frames_per_buffer=chunk,
                        input_device_index=device)
        try:
            frames = []
            n_buffers = int(rate / chunk * secs)
            for _ in range(n_buffers):
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(data)
        finally:
            stream.stop_stream()
            stream.close()
    finally:
        p.terminate()

    path = os.path.abspath(path)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(p.get_sample_size(fmt))
        wf.setframerate(rate)
        wf.writeframes(b"".join(frames))
    return path


# ---------------------------------------------------------------------------
# Analysis (NSDF) -- preserved from the previous pitch.py
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


def detect_f0(frame, sr, fmin=65.0, fmax=1400.0, thresh=0.90):
    """Estimate f0 (Hz) of one frame via NSDF peak picking. Returns 0.0 if unvoiced."""
    if len(frame) < 256:
        return 0.0
    x = frame - np.mean(frame)
    rms = float(np.sqrt(np.mean(x**2)))
    if rms < 0.01:  # silence / noise floor
        return 0.0
    tau_min = max(2, int(sr / fmax))
    tau_max = min(len(x) - 1, int(sr / fmin))
    if tau_max <= tau_min:
        return 0.0
    nsdf = _nsdf(x, tau_max)
    seg = nsdf[tau_min : tau_max + 1]
    if len(seg) == 0 or np.max(seg) <= 0:
        return 0.0
    # local maxima
    peaks = np.where((seg[1:-1] > seg[:-2]) & (seg[1:-1] >= seg[2:]))[0] + 1
    if len(peaks) == 0:
        return 0.0
    gmax = float(np.max(seg[peaks]))
    # first prominent peak (>= thresh*global max) avoids picking the octave-below
    cand = [p for p in peaks if seg[p] >= thresh * gmax]
    if not cand:
        cand = list(peaks)
    p = cand[0]
    tau = p + tau_min
    # parabolic interpolation around the peak for sub-sample precision
    if 0 < tau < len(nsdf) - 1:
        y0, y1, y2 = nsdf[tau - 1], nsdf[tau], nsdf[tau + 1]
        denom = y0 - 2 * y1 + y2
        if denom != 0:
            tau = tau + 0.5 * (y0 - y2) / denom
    f0 = sr / tau
    return f0 if fmin <= f0 <= fmax else 0.0


def hz_to_midi(f):
    return A4_MIDI + 12.0 * np.log2(f / A4_HZ)


def midi_to_name(m):
    m = int(round(m))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def track(samples, sr, window=2048, hop=512, fmin=65.0, fmax=1400.0,
          silence_rms=0.01, median_k=5, min_frames=3):
    """Track a monophonic melody -> list of notes: {midi, hz, start, dur, name}."""
    n = len(samples)
    if n < window:
        return []
    n_frames = 1 + (n - window) // hop
    f0s = np.zeros(n_frames, dtype=np.float64)
    for i in range(n_frames):
        seg = samples[i * hop : i * hop + window]
        f0s[i] = detect_f0(seg, sr, fmin=fmin, fmax=fmax)
    # median filter the voiced f0s to kill isolated octave jumps
    if median_k > 1:
        v = f0s[f0s > 0]
        if len(v):
            med = float(np.median(v))
            for i in range(len(f0s)):
                if f0s[i] > 0:
                    lo, hi = med * 0.5, med * 2.0
                    f0s[i] = med if not (lo <= f0s[i] <= hi) else f0s[i]
    # frame-level midi
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
        while j < len(midis) and round(midis[j]) == q and midis[j] > 0:
            j += 1
        if j - i >= min_frames:
            # Note boundaries: the run of frames covering this pitch. End is the
            # start of the first frame after the run (a frame overlaps both sides,
            # so adding a whole window here would overstate every duration).
            start = i * hop / sr
            end = j * hop / sr
            hz = float(np.median([f for f in f0s[i:j] if f > 0]) or 0.0)
            notes.append(
                {
                    "midi": q,
                    "name": midi_to_name(q),
                    "start": round(start, 3),
                    "dur": round(end - start, 3),
                    "hz": round(hz, 1),
                }
            )
        i = j
    return notes


def analyze_wav(path, fmin=65.0, fmax=1400.0):
    samples, sr = load_wav_mono(path)
    notes = track(samples, sr, fmin=fmin, fmax=fmax)
    dur = len(samples) / sr
    print(f"analyzed {dur:.1f}s @ {sr}Hz -> {len(notes)} notes (monophonic) from {os.path.abspath(path)}")
    for n in notes:
        print(f"  {n['start']:6.2f}s  {n['dur']:5.2f}s  {n['name']:>4} (midi {n['midi']}, {n['hz']:.1f} Hz)")
    return 0


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
    ap.add_argument("--target", default=None,
                    help="deprecated alias of --device (previous pw-record source id)")
    ap.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    ap.add_argument("--fmin", type=float, default=65.0, help="analysis floor (Hz)")
    ap.add_argument("--fmax", type=float, default=1400.0, help="analysis ceiling (Hz)")
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
                         chunk=args.chunk, device=args.device)
        print(f"recorded {args.secs}s @ {args.rate}Hz -> {got}")
        return 0
    return analyze_wav(path, fmin=args.fmin, fmax=args.fmax)


if __name__ == "__main__":
    sys.exit(main())