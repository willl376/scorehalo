# Live pitch listener

`scripts/pitch_listener.py` prints the dominant frequency and nearest note
name from a live input, in real time. It is the interactive sibling of
`scorehalo.pitch` (`pitch.py` reads a recorded WAV and yields a note sequence
for diffing; this listens live).

## Why it is here

ScoreHalo's only independent, non-image ground truth for the melody line is a
hummed or whistled recording (`pitch.py`). Before recording, it helps to know
the room and the input are behaving — a quick live readout shows whether the
mic is picking up a clean tone or whether the noise floor is confusing the
tracker. Same monophonic scope, same cautions, as `pitch.py`.

## Use

```bash
sudo apt install portaudio19-dev
pip install numpy sounddevice

python scripts/pitch_listener.py --list        # find an input device id
python scripts/pitch_listener.py --device 0    # listen on that device
```

Three guards reject room noise, because a bare FFT argmax tracks fan hum and
keyboard clatter as readily as a note:

* a minimum block amplitude (`SILENCE_AMPLITUDE`, 0.05);
* a musical frequency window (80–1500 Hz, about E2–G6);
* a spectral-prominence test (the peak must sit well above the surrounding
  mean energy, `PEAK_PROMINENCE` = 4x).

## Capturing a browser/YouTube track

A microphone hears the room. To analyse a clean recording instead, either
select a system monitor source as the input device (PipeWire/Pulse expose one
via the `pulse`/`pipewire` host API, not the ALSA list) or download the audio
and point `scorehalo.pitch` at the WAV — the offline path has no room noise at
all.

## Status

Standalone demo tool, not wired into the pipeline. Depends on `numpy` and
`sounddevice` (PortAudio). PRESERVED for the music pipeline; provenance: built
interactively with a second assistant, kept here so it is version-controlled.
