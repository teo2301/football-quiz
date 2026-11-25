#!/usr/bin/env python3
"""Synthesise the app's three sound effects (no recordings, nothing downloaded).

    python3 tools/make_sounds.py      -> docs/sounds/{cheer,groan,whistle}.mp3

cheer   ~1.4 s  crowd roar: many detuned "voices" + shaped noise + a few claps
groan   ~1.3 s  crowd "ohhh": low voices sliding down through an "oh" formant
whistle ~1.9 s  referee's final whistle: two short blasts and a long one (pea trill)

Needs numpy and ffmpeg (libmp3lame). Seeded, so reruns give the same files.
"""
import os
import subprocess
import tempfile
import wave

import numpy as np

SR = 22050
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "sounds")
rng = np.random.default_rng(2301)


def t_of(secs):
    return np.arange(int(SR * secs)) / SR


def band(x, lo, hi, soft=0.15):
    """Band-pass in the frequency domain with soft edges."""
    f = np.fft.rfftfreq(len(x), 1 / SR)
    m = np.clip((f - lo * (1 - soft)) / (lo * soft * 2 + 1e-9), 0, 1) * np.clip((hi * (1 + soft) - f) / (hi * soft * 2), 0, 1)
    return np.fft.irfft(np.fft.rfft(x) * m, len(x))


def formant(x, peaks):
    """Sum of resonances (centre, width, gain) - a crude vowel filter."""
    f = np.fft.rfftfreq(len(x), 1 / SR)
    m = sum(g / (1 + ((f - c) / w) ** 2) for c, w, g in peaks)
    return np.fft.irfft(np.fft.rfft(x) * m, len(x))


def env(n, attack, release, total):
    t = np.arange(n) / SR
    a = np.clip(t / attack, 0, 1)
    r = np.clip((total - t) / release, 0, 1)
    return a * r


def voice(t, f0, glide=0.0, vib=5.0, onset=0.0):
    """A buzzy harmonic tone (sawtooth-ish, 12 partials) with vibrato and jitter."""
    jitter = np.cumsum(rng.normal(0, 1, len(t))) / SR * 4
    freq = f0 * (1 + glide * t / t[-1]) * (1 + 0.012 * np.sin(2 * np.pi * vib * t + rng.uniform(0, 6))) + jitter
    ph = 2 * np.pi * np.cumsum(freq) / SR
    s = sum(np.sin(k * ph) / k for k in range(1, 13))
    s[t < onset] = 0
    return s


def norm(x, peak=0.8):
    return x / (np.max(np.abs(x)) + 1e-9) * peak


def cheer():
    T = 1.4
    t = t_of(T)
    v = sum(voice(t, rng.uniform(170, 480), glide=rng.uniform(0, .12), vib=rng.uniform(4, 7),
                  onset=rng.uniform(0, .18)) for _ in range(48))
    v = formant(v, [(700, 160, 1.0), (1150, 220, .7), (2600, 400, .35)])       # "ah"
    noise = band(rng.normal(0, 1, len(t)), 350, 4200)
    claps = np.zeros(len(t))
    for _ in range(26):
        i = int(rng.uniform(.15, 1.1) * SR)
        n = int(.012 * SR)
        claps[i:i + n] += rng.normal(0, 1, n) * np.exp(-np.arange(n) / (n / 4))
    claps = band(claps, 900, 5000)
    x = norm(v) * .7 + norm(noise) * .45 + norm(claps) * .35
    return norm(x * env(len(t), .12, .55, T), .75)


def groan():
    T = 1.3
    t = t_of(T)
    v = sum(voice(t, rng.uniform(110, 260), glide=-rng.uniform(.18, .32), vib=rng.uniform(3, 5),
                  onset=rng.uniform(0, .1)) for _ in range(40))
    v = formant(v, [(480, 120, 1.0), (850, 160, .5), (2400, 400, .12)])        # "oh"
    noise = band(rng.normal(0, 1, len(t)), 200, 1600)
    x = norm(v) * .85 + norm(noise) * .25
    return norm(x * env(len(t), .18, .7, T), .7)


def whistle():
    blasts = [(.0, .26), (.38, .26), (.76, 1.05)]       # start, length
    T = 1.9
    t = t_of(T)
    x = np.zeros(len(t))
    for start, length in blasts:
        i, n = int(start * SR), int(length * SR)
        tt = np.arange(n) / SR
        trill = 0.5 + 0.5 * np.sin(2 * np.pi * 34 * tt)                     # the pea rattling
        f = 2950 * (1 + 0.025 * trill) * (1 - 0.01 * tt / length)
        ph = 2 * np.pi * np.cumsum(f) / SR
        tone = np.sin(ph) + 0.18 * np.sin(2 * ph)
        breath = band(rng.normal(0, 1, n), 2400, 3600) * 0.25
        e = np.clip(tt / .02, 0, 1) * np.clip((length - tt) / .05, 0, 1)
        x[i:i + n] += (tone * (0.65 + 0.35 * trill) + breath) * e
    return norm(x, .6)


def write(name, x):
    os.makedirs(OUT, exist_ok=True)
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    dst = os.path.join(OUT, name + ".mp3")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", path, "-ac", "1", "-codec:a", "libmp3lame",
                    "-b:a", "48k", "-map_metadata", "-1", dst], check=True)
    os.remove(path)
    print(dst, os.path.getsize(dst), "bytes")


if __name__ == "__main__":
    write("cheer", cheer())
    write("groan", groan())
    write("whistle", whistle())
