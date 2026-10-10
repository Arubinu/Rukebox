"""The blind test's own little sounds, synthesised on demand: one under the thinking time,
one right before the answer."""

import math
import os
import struct
import wave

RATE = 22050
THINK = ("tick", "heartbeat", "rise")
ANSWER = ("gong", "ding", "fanfare")
LEVEL = 0.28


def _tone(freq, seconds, decay, partials=((1.0, 1.0),)):
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        t = i / RATE
        value = sum(amp * math.sin(2 * math.pi * freq * ratio * t) for ratio, amp in partials)
        out.append(value * math.exp(-t * decay))
    return out


def _silence(seconds):
    return [0.0] * int(RATE * seconds)


def _mix_at(track, sound, at):
    start = int(at * RATE)
    if len(track) < start + len(sound):
        track.extend([0.0] * (start + len(sound) - len(track)))
    for i, value in enumerate(sound):
        track[start + i] += value


def _think(kind, seconds):
    track = _silence(seconds)
    if kind == "tick":
        for second in range(int(seconds)):
            _mix_at(track, _tone(1800 if second % 2 else 1400, 0.04, 90), second)
    elif kind == "heartbeat":
        beat = 0.0
        while beat < seconds:
            _mix_at(track, _tone(55, 0.12, 30, ((1.0, 1.0), (2.0, 0.3))), beat)
            _mix_at(track, _tone(50, 0.12, 35, ((1.0, 1.0), (2.0, 0.3))), beat + 0.25)
            beat += 0.9
    else:
        n = len(track)
        phase = 0.0
        for i in range(n):
            share = i / max(1, n)
            phase += 2 * math.pi * (220 + 440 * share) / RATE
            track[i] = math.sin(phase) * (0.15 + 0.55 * share)
    return track[:int(RATE * seconds)]


def _answer(kind):
    if kind == "gong":
        return _tone(110, 2.5, 1.6, ((1.0, 1.0), (2.76, 0.5), (5.4, 0.25), (8.9, 0.12)))
    if kind == "ding":
        return _tone(1320, 1.2, 4.0, ((1.0, 1.0), (2.0, 0.35)))
    track = []
    for at, freq, length in ((0.0, 523.25, 0.16), (0.17, 659.25, 0.16), (0.34, 783.99, 0.16), (0.51, 1046.5, 0.7)):
        _mix_at(track, _tone(freq, length, 3.0, ((1.0, 1.0), (2.0, 0.4), (3.0, 0.2))), at)
    return track


def _write(path, samples):
    peak = max((abs(v) for v in samples), default=0.0) or 1.0
    frames = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, v / peak * LEVEL)) * 32767)) for v in samples)
    tmp = path + ".tmp"
    with wave.open(tmp, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(frames)
    os.replace(tmp, path)
    return path


def think_sound(kind, seconds, folder):
    """A WAV of the thinking-time sound lasting `seconds`, or None for silence."""
    seconds = int(seconds)
    if kind not in THINK or seconds <= 0:
        return None
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "think-%s-%d.wav" % (kind, seconds))
    return path if os.path.isfile(path) else _write(path, _think(kind, seconds))


def answer_sound(kind, folder):
    """A WAV of the sound before the answer, or None for none."""
    if kind not in ANSWER:
        return None
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "answer-%s.wav" % kind)
    return path if os.path.isfile(path) else _write(path, _answer(kind))
