"""Monophonic pitch tracking from a recording: WAV -> note sequence.

Purpose: Wilbur can hum or whistle a melody into a microphone; this turns that
into a note sequence we can diff against what ScoreHalo transcribed. This is
the only independent (non-image) ground truth available for the *melody* line.

Method: NSDF (McLeod Pitch Method, 2002) for octave-robust f0, then median
filtering, semitone quantization, and run-length grouping into notes.

Scope, honestly stated:
  * MONOPHONIC only. Chords produce a blurry/garbled track; this cannot
    transcribe polyphony. Humming or whistling one line is the use case.
  * Does not recover *missing* music: a staff homr never read is silent, and
    silence is indistinguishable from "nothing there" here. This verifies the
    notes that survived; it cannot restore the ones that did not.

Run `python -m scorehalo.pitch --selftest` to verify on synthetic tones.
"""

import argparse
import os
import subprocess
import sys
import wave

import numpy as np

# musical constants
A4_MIDI = 69
A4_HZ = 440.0
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


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


def record_wav(path, secs, target=None, rate=16000):
    """Record from a PipeWire source with pw-record (bounded by timeout)."""
    cmd = ["pw-record", "--rate", str(rate), "--channels", "1"]
    if target:
        cmd += ["--target", str(target)]
    cmd.append(os.path.abspath(path))
    if os.path.exists(path):
        os.remove(path)
    # no --duration flag on this pw-record; bound with timeout and SIGINT
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            p.wait(timeout=secs)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
    except FileNotFoundError:
        raise SystemExit("pw-record not found (install pipewire-utils)")
    return path if os.path.exists(path) else None


def _selftest():
    """Verify pitch tracking on synthetic tones we generate ourselves."""
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
    print("SELFTEST:", "PASS" if ok else "FAIL")
    if not ok:
        raise SystemExit(1)
    # a rest must not invent notes
    silence = np.zeros(int(1.0 * sr), dtype=np.float32)
    assert track(silence, sr) == [], "selftest: silence produced notes!"
    print("silence->[]: PASS")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="scorehalo.pitch", description="monophonic pitch tracking (hum/whistle -> notes)"
    )
    ap.add_argument("wav", nargs="?", help="WAV to analyze (or use --record)")
    ap.add_argument("--record", action="store_true", help="record from mic first")
    ap.add_argument("--secs", type=float, default=8.0, help="recording seconds")
    ap.add_argument("--target", help="PipeWire source id (default: system default)")
    ap.add_argument("--fmin", type=float, default=65.0)
    ap.add_argument("--fmax", type=float, default=1400.0)
    ap.add_argument("--selftest", action="store_true", help="verify on synthetic tones")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    path = args.wav
    if args.record:
        if not path:
            path = "/tmp/scorehalo-pitch.wav"
        got = record_wav(path, args.secs, args.target)
        if not got:
            raise SystemExit("recording failed (no data captured)")
        print(f"recorded {args.secs}s -> {got}")
    if not path:
        ap.error("give a WAV or use --record")
    samples, sr = load_wav_mono(path)
    notes = track(samples, sr, fmin=args.fmin, fmax=args.fmax)
    dur = len(samples) / sr
    print(f"analyzed {dur:.1f}s @ {sr}Hz -> {len(notes)} notes (monophonic)")
    for n in notes:
        print(f"  {n['start']:6.2f}s  {n['dur']:5.2f}s  {n['name']:>4} (midi {n['midi']}, {n['hz']:.1f} Hz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
