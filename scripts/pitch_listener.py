"""Live microphone pitch readout: real-time FFT -> nearest note name.

The interactive sibling of `scorehalo.pitch`. `pitch.py` reads a *recorded*
WAV and returns a note sequence for diffing against a transcription; this
listens to a live input (microphone, line-in, or a system monitor source) and
prints the dominant frequency in Hz plus the nearest note name as you play or
sing. Handy for checking a hummed melody line before recording it, and for
seeing what the room is actually doing.

Honest scope, same as `pitch.py`: MONOPHONIC only. Three guards reject room
noise -- a minimum amplitude, a musical frequency window (80-1500 Hz), and a
spectral-prominence test -- because a bare FFT argmax locks onto fan hum and
keyboard clatter as readily as a note.

Requires numpy and sounddevice, and the PortAudio system library:

    sudo apt install portaudio19-dev
    pip install numpy sounddevice

Run:

    python scripts/pitch_listener.py              # default input device
    python scripts/pitch_listener.py --list       # list devices, then pick
    python scripts/pitch_listener.py --device 13  # use a specific device id

Press Ctrl+C to stop.
"""

import argparse
import sys
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 44100
BLOCK_SIZE = 2048

# Musical frequency limits (filters out room hums and high screeches)
MIN_FREQ = 80.0    # roughly E2
MAX_FREQ = 1500.0  # roughly G6
SILENCE_AMPLITUDE = 0.05   # raise if the room is loud
PEAK_PROMINENCE = 4.0      # peak must be this many times the surrounding mean

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def frequency_to_note(freq):
    """Nearest equal-tempered note name for a frequency, or None if too low."""
    if freq < MIN_FREQ:
        return None
    h = round(12 * np.log2(freq / 440.0)) + 69
    return f"{NOTE_NAMES[h % 12]}{(h // 12) - 1}"


def analyse(audio_data):
    """Dominant musical frequency in one block, or None if there is no clear note."""
    if np.max(np.abs(audio_data)) < SILENCE_AMPLITUDE:
        return None

    windowed = audio_data * np.hanning(len(audio_data))
    spectrum = np.abs(np.fft.rfft(windowed))
    frequencies = np.fft.rfftfreq(len(windowed), 1 / SAMPLE_RATE)

    valid = np.where((frequencies >= MIN_FREQ) & (frequencies <= MAX_FREQ))[0]
    if len(valid) == 0:
        return None

    sub = spectrum[valid]
    peak = int(np.argmax(sub))
    # A real note stands well above the surrounding noise floor.
    if sub[peak] < np.mean(sub) * PEAK_PROMINENCE:
        return None
    return float(frequencies[valid[peak]])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", type=int, default=None,
                        help="input device id (see --list)")
    parser.add_argument("--list", action="store_true",
                        help="list audio devices and exit")
    args = parser.parse_args(argv)

    if args.list:
        print(sd.query_devices())
        return 0

    def callback(indata, frames, time_info, status):
        if status:
            return
        freq = analyse(indata[:, 0])
        if freq is None:
            return
        note = frequency_to_note(freq)
        if note:
            print(f"Detected: {freq:.2f} Hz -> Note: {note}", flush=True)

    print("Listening for clean pitches (room noise filtered)... Press Ctrl+C to stop.")
    try:
        with sd.InputStream(callback=callback, channels=1,
                            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
                            device=args.device):
            while True:
                time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nStopped listening.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
