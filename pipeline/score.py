"""Original 60 s score + sound design, synthesised from scratch (numpy/scipy). No samples, no copyrighted music.

Musical idea: D minor mythic theme over a tanpura drone, bansuri (flute) melody with meend (glides),
low strings, choir pad and low brass; percussion grows from a heartbeat to taiko/dhol and lands on
the hero shot, resolving to D major at the title. Sound design is synced to the picture's shot list.
"""
import sys

import numpy as np
from scipy import signal
from scipy.io import wavfile

SR = 48000
DUR = 60.0
N = int(SR * DUR)
rng = np.random.default_rng(108)

MUSIC = np.zeros((N, 2))
SFX = np.zeros((N, 2))
AMB = np.zeros((N, 2))


def midi(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def tt(dur):
    return np.arange(int(dur * SR)) / SR


def place(bus, start, sig, pan=0.0, gain=1.0):
    i0 = int(start * SR)
    if i0 >= N:
        return
    if sig.ndim == 1:
        l = np.cos((pan + 1) * np.pi / 4)
        r = np.sin((pan + 1) * np.pi / 4)
        sig = np.stack([sig * l, sig * r], 1)
    if i0 < 0:
        sig = sig[-i0:]
        i0 = 0
    n = min(len(sig), N - i0)
    bus[i0:i0 + n] += sig[:n] * gain


def env(n, a, r, curve=1.0):
    e = np.ones(n)
    na, nr = max(1, int(a * SR)), max(1, int(r * SR))
    na, nr = min(na, n), min(nr, n)
    e[:na] = np.linspace(0, 1, na) ** curve
    e[n - nr:] *= np.linspace(1, 0, nr) ** curve
    return e


def lp(x, fc, order=2):
    b, a = signal.butter(order, min(fc / (SR / 2), 0.99))
    return signal.lfilter(b, a, x, axis=0)


def hp(x, fc, order=2):
    b, a = signal.butter(order, fc / (SR / 2), "high")
    return signal.lfilter(b, a, x, axis=0)


def bp(x, lo, hi, order=2):
    b, a = signal.butter(order, [lo / (SR / 2), min(hi / (SR / 2), 0.99)], "band")
    return signal.lfilter(b, a, x, axis=0)


def noise(dur):
    return rng.standard_normal(int(dur * SR))


# ------------------------------------------------------------------ instruments
def tanpura(f, dur=3.4, amp=0.16):
    t = tt(dur)
    out = np.zeros_like(t)
    centre = 3 + 12 * (t / dur) ** 0.7          # the jawari "shimmer" sweeping up the harmonics
    for n in range(1, 26):
        if n * f > 9000:
            break
        a = (1 / n ** 0.75) * np.exp(-t * (0.6 + 0.08 * n)) * (0.35 + np.exp(-((n - centre) ** 2) / 10))
        out += a * np.sin(2 * np.pi * n * f * t * (1 + 0.0004 * n) + rng.uniform(0, 6.28))
    out *= env(len(t), 0.004, 0.4)
    return out * amp


def additive(freq_or_curve, t, nh=10, bright=None, detune=0.0):
    f = freq_or_curve
    ph = 2 * np.pi * np.cumsum(np.broadcast_to(f, t.shape)) / SR
    out = np.zeros_like(t)
    for n in range(1, nh + 1):
        if np.max(np.broadcast_to(f, t.shape)) * n > 12000:
            break
        a = 1.0 / n if bright is None else np.exp(-n / (1.5 + 9 * bright)) / n ** 0.5
        out += a * np.sin(n * ph * (1 + detune) + rng.uniform(0, 6.28))
    return out


def strings(notes, start, dur, amp=0.05, a=1.6, r=2.4):
    t = tt(dur)
    e = env(len(t), a, r, 1.5)
    for m in notes:
        f = midi(m)
        sig = np.zeros_like(t)
        for d in (-0.006, -0.002, 0.003, 0.007):
            vib = 1 + 0.0025 * np.sin(2 * np.pi * (4.6 + d * 100) * t + rng.uniform(0, 6))
            sig += additive(f * (1 + d) * vib, t, 10)
        sig = lp(sig, 2400 if m < 60 else 3600)
        place(MUSIC, start, sig * e * amp, pan=rng.uniform(-0.5, 0.5))


def brass(m, start, dur, amp=0.08):
    t = tt(dur)
    b = np.clip(t / 0.25, 0, 1) * np.exp(-t / (dur * 1.5))
    sig = additive(midi(m) * (1 + 0.002 * np.sin(2 * np.pi * 5 * t)), t, 14, bright=b)
    sig += additive(midi(m) * 1.004, t, 14, bright=b)
    place(MUSIC, start, lp(sig, 2200) * env(len(t), 0.06, 0.6) * amp, pan=rng.uniform(-0.3, 0.3))


def choir(notes, start, dur, amp=0.04):
    t = tt(dur)
    e = env(len(t), 1.2, 2.0, 1.4)
    for m in notes:
        src = np.zeros_like(t)
        for d in (-0.004, 0.0, 0.005):
            src += additive(midi(m) * (1 + d) * (1 + 0.004 * np.sin(2 * np.pi * 5.3 * t)), t, 30)
        voc = bp(src, 650, 950) * 1.0 + bp(src, 1050, 1300) * 0.6 + bp(src, 2600, 3100) * 0.25
        place(MUSIC, start, voc * e * amp, pan=rng.uniform(-0.6, 0.6))


def flute(phrase, start, amp=0.11, pan=0.15):
    """phrase: list of (dur, midi or None). Legato with meend glides and delayed vibrato."""
    total = sum(d for d, _ in phrase) + 0.6
    t = tt(total)
    f = np.zeros_like(t)
    a = np.zeros_like(t)
    vib_amt = np.zeros_like(t)
    pos = 0.0
    last = None
    for d, m in phrase:
        i0, i1 = int(pos * SR), int((pos + d) * SR)
        if m is not None:
            f[i0:i1] = midi(m)
            a[i0:i1] = 1.0
            local = np.arange(i1 - i0) / SR
            vib_amt[i0:i1] = np.clip((local - 0.3) / 0.4, 0, 1)
            last = midi(m)
        else:
            f[i0:i1] = last or 440
        pos += d
    f[int(pos * SR):] = last or 440
    # glide (meend) by smoothing log-frequency
    b_, a_ = signal.butter(1, 7.0 / (SR / 2))
    lf = signal.lfilter(b_, a_, np.log(f), zi=signal.lfilter_zi(b_, a_) * np.log(f[0]))[0]
    vib = 1 + 0.006 * vib_amt * np.sin(2 * np.pi * 5.2 * t)
    fr = np.exp(lf) * vib
    a = lp(a, 9.0, 1)
    ph = 2 * np.pi * np.cumsum(fr) / SR
    tone = np.sin(ph) + 0.22 * np.sin(2 * ph + 0.4) + 0.07 * np.sin(3 * ph + 1.1)
    breath = bp(noise(total), 900, 4500) * 0.16
    sig = (tone + breath) * a * (1 + 0.08 * np.sin(2 * np.pi * 0.7 * t))
    place(MUSIC, start, sig * amp, pan=pan)


def taiko(t0, amp=0.5, f0=52):
    t = tt(1.8)
    fr = f0 * (1 + 0.7 * np.exp(-t / 0.04))
    ph = 2 * np.pi * np.cumsum(fr) / SR
    body = np.sin(ph) * np.exp(-t / 0.42)
    skin = lp(noise(1.8), 900) * np.exp(-t / 0.05) * 0.8
    place(MUSIC, t0, (body + skin) * amp, pan=rng.uniform(-0.15, 0.15))


def dhol(t0, kind="bass", amp=0.25):
    t = tt(0.6)
    if kind == "bass":
        fr = 88 * (1 + 0.35 * np.exp(-t / 0.03))
        s = np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.exp(-t / 0.2)
        pan = -0.2
    else:
        s = np.sin(2 * np.pi * 340 * t) * np.exp(-t / 0.05) + bp(noise(0.6), 1800, 6000) * np.exp(-t / 0.03) * 0.9
        pan = 0.25
    place(MUSIC, t0, s * amp, pan=pan)


def boom(t0, amp=0.6):
    t = tt(3.5)
    s = np.sin(2 * np.pi * 38 * t + 3 * np.exp(-t / 0.1)) * np.exp(-t / 1.1)
    place(MUSIC, t0, s * amp)
    taiko(t0, amp * 0.9, 46)


def swell(t_hit, dur=2.0, amp=0.12):
    t = tt(dur)
    s = hp(noise(dur), 4000) * (t / dur) ** 3
    place(MUSIC, t_hit - dur, np.stack([s, hp(noise(dur), 4000) * (t / dur) ** 3], 1) * amp)


# ------------------------------------------------------------------ sound design
def resonant_clicks(dur, rate0, rate1, f_res, amp, decay=0.006):
    n = int(dur * SR)
    x = np.zeros(n)
    tcur = 0.0
    while tcur < dur:
        rate = rate0 + (rate1 - rate0) * (tcur / dur)
        x[int(tcur * SR)] = rng.uniform(0.4, 1.0)
        tcur += (1 / rate) * rng.uniform(0.6, 1.4)
    k = np.arange(int(0.04 * SR)) / SR
    ir = np.sin(2 * np.pi * f_res * k) * np.exp(-k / decay)
    return signal.fftconvolve(x, ir)[:n] * amp


def footstep(t0, amp=0.35, close=True):
    t = tt(0.25)
    crunch = np.zeros_like(t)
    for g in range(4):
        o = int(rng.uniform(0, 0.06) * SR)
        burst = noise(0.05) * np.exp(-np.arange(int(0.05 * SR)) / SR / 0.012)
        crunch[o:o + len(burst)] += burst[:len(crunch) - o]
    crunch = bp(crunch, 350 if close else 600, 5500 if close else 3500)
    thump = np.sin(2 * np.pi * 70 * t) * np.exp(-t / 0.04) * (0.8 if close else 0.3)
    place(SFX, t0, (crunch * 0.6 + thump) * amp, pan=rng.uniform(-0.2, 0.2))


def chirp(t0, amp=0.04, pan=0.0):
    notes = rng.integers(2, 6)
    pos = t0
    f0 = rng.uniform(2600, 5200)
    for _ in range(notes):
        d = rng.uniform(0.05, 0.16)
        t = tt(d)
        fr = f0 * (1 + 0.35 * np.sin(2 * np.pi * rng.uniform(8, 25) * t)) * (1 - 0.3 * t / d)
        s = np.sin(2 * np.pi * np.cumsum(fr) / SR) * np.sin(np.pi * t / d) ** 2
        place(AMB, pos, s * amp, pan=pan)
        pos += d + rng.uniform(0.03, 0.12)


def flutter(t0, dur=1.8, amp=0.25):
    t = tt(dur)
    beats = 0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 17 * t))
    s = bp(noise(dur), 250, 2200) * lp(beats, 60) * np.exp(-t / 0.8)
    place(SFX, t0, s * amp, pan=-0.3)
    for k in range(6):
        chirp(t0 + rng.uniform(0, 1.2), 0.07, rng.uniform(-0.8, 0.8))


def snap(t0, amp=0.5):
    t = tt(0.3)
    s = hp(noise(0.3), 2000) * np.exp(-t / 0.006)
    s += np.sin(2 * np.pi * 1700 * t) * np.exp(-t / 0.01) * 0.5
    s2 = hp(noise(0.3), 1500) * np.exp(-t / 0.01) * 0.5
    place(SFX, t0, s * amp, pan=0.7)
    place(SFX, t0 + 0.07, s2 * amp, pan=0.75)


def rustle(t0, dur, amp=0.15):
    t = tt(dur)
    e = np.sin(np.pi * t / dur) ** 1.5
    s = bp(noise(dur), 900, 5000) * e + bp(noise(dur), 4500, 9000) * e * np.clip((t / dur - 0.6) * 3, 0, 1) * 0.6
    place(SFX, t0, s * amp, pan=-0.2)


def nock(t0, amp=0.35):
    t = tt(0.12)
    s = np.sin(2 * np.pi * 2400 * t) * np.exp(-t / 0.008) + np.sin(2 * np.pi * 800 * t) * np.exp(-t / 0.02) * 0.6
    place(SFX, t0, s * amp, pan=0.1)


def ambience():
    # wind through the canopy
    w = np.cumsum(rng.standard_normal(N)) * 0.002
    w = hp(lp(w, 450), 25)
    gust = lp(rng.standard_normal(N), 0.3, 1)
    gust = 0.6 + 0.4 * gust / (np.max(np.abs(gust)) + 1e-9)
    leaves = bp(rng.standard_normal(N), 2000, 7000) * 0.03 * gust
    amb = w / (np.max(np.abs(w)) + 1e-9) * 0.12 * gust + leaves
    place(AMB, 0, np.stack([amb, np.roll(amb, 2400)], 1))
    tcur = 0.4
    while tcur < DUR - 1:
        chirp(tcur, rng.uniform(0.015, 0.045), rng.uniform(-0.9, 0.9))
        tcur += rng.uniform(0.6, 2.4)
    t = tt(DUR)
    ins = np.sin(2 * np.pi * 6100 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 31 * t)) * 0.004
    place(AMB, 0, ins, pan=0.4)


# ------------------------------------------------------------------ the cue
def compose():
    # shot starts (s): S1 0, S2 6, S3 11, S4 17, S5 22, S6 26, S7 31, S8 35, S9 42, S10 49
    D2, A2, D3 = midi(38), midi(45), midi(50)
    tcur = 0.4
    while tcur < DUR - 2:
        for f in (A2, D3, D3, D2):
            place(MUSIC, tcur, tanpura(f), pan=-0.35, gain=0.9 if tcur < 49 else 1.1)
            tcur += 1.2
    # strings: Dm - Bb - C - Dm ... resolving to D major for the title
    prog = [(3.0, 8.0, [38, 50, 53, 57]), (11.0, 6.0, [34, 46, 50, 53]), (17.0, 5.0, [36, 48, 52, 55]),
            (22.0, 4.0, [38, 50, 57]), (26.0, 5.0, [34, 50, 53, 58]), (31.0, 4.0, [38, 50, 53]),
            (35.0, 7.0, [38, 50, 53, 57, 62]), (42.0, 3.5, [34, 46, 53, 58, 62]), (45.5, 3.5, [36, 48, 55, 60, 64]),
            (49.0, 7.0, [38, 50, 57, 62, 65, 69]), (56.0, 4.2, [38, 50, 57, 62, 66, 69])]
    for s, d, notes in prog:
        strings(notes, s, d + 1.2, amp=0.035 if s < 35 else 0.05)
    choir([62, 65, 69], 42.0, 7.2, 0.03)
    choir([62, 65, 69, 74], 49.0, 7.2, 0.045)
    choir([62, 66, 69, 74], 56.0, 4.2, 0.05)
    for s, m, d in ((42.0, 38, 0.8), (43.2, 38, 0.8), (44.4, 41, 0.8), (45.6, 43, 0.8), (46.8, 45, 1.6),
                    (49.0, 38, 3.0), (52.0, 34, 2.0), (54.0, 36, 2.0), (56.0, 38, 3.5)):
        brass(m, s, d, 0.07 if s < 49 else 0.1)
        brass(m + 12, s, d, 0.04)
    # bansuri
    flute([(0.9, 57), (0.5, 60), (1.3, 62), (0.4, 64), (1.6, 62), (0.8, None)], 12.0, 0.09)
    flute([(1.2, 69), (0.6, 67), (1.8, 65), (0.6, None)], 22.6, 0.06, pan=-0.2)
    flute([(0.6, 62), (0.6, 65), (0.9, 69), (0.5, 70), (0.5, 69), (1.4, 67), (1.4, 69)], 43.0, 0.08)
    flute([(1.0, 74), (0.5, 72), (0.5, 70), (1.2, 69), (0.6, 67), (0.6, 65), (1.6, 67), (1.0, 69)], 49.6, 0.1)
    flute([(1.6, 74), (2.4, 78)], 56.1, 0.09)
    # percussion arc
    boom(6.0, 0.25)
    for k in range(5):
        taiko(11.0 + 1.2 * k, 0.18, 58)
    boom(22.0, 0.15)
    for k, s in enumerate((31.3, 32.1, 33.5, 34.3)):
        taiko(s, 0.3 if k % 2 == 0 else 0.18, 50)
    beat = 0.6
    for i in range(int(7 / (beat / 2))):
        s = 35.0 + i * beat / 2
        lvl = 0.06 + 0.12 * (i / 23)
        dhol(s, "bass" if i % 4 in (0, 3) else "treble", lvl)
    for i in range(int(7 / (beat / 2))):
        s = 42.0 + i * beat / 2
        dhol(s, "bass" if i % 2 == 0 else "treble", 0.2)
        if i % 4 == 0:
            taiko(s, 0.3, 55)
    roll = 48.0
    gap = 0.12
    while roll < 48.95:
        taiko(roll, 0.22, 70)
        roll += gap
        gap *= 0.9
    swell(49.0, 2.2, 0.1)
    boom(49.0, 0.75)
    for k in range(6):
        taiko(50.2 + 1.2 * k, 0.32, 48)
        dhol(50.8 + 1.2 * k, "treble", 0.12)
    swell(56.0, 1.6, 0.12)
    boom(56.0, 0.85)


def design():
    # S2 + S3 walking: foot strikes every 0.6 s (walk cycle 1.2 s, phase 0 at the cut)
    for s0, s1, close in ((6.0, 11.0, True), (11.0, 17.0, False)):
        k = 0
        while s0 + k * 0.6 < s1:
            footstep(s0 + k * 0.6 + 0.01, 0.4 if close else 0.18, close)
            k += 1
    place(SFX, 17.5, resonant_clicks(2.2, 15, 70, 620, 0.12), pan=0.3)   # leather grip tightening
    flutter(22.8)
    snap(26.5)
    rustle(37.3, 1.7)
    nock(40.2)
    place(SFX, 43.5, resonant_clicks(1.9, 20, 140, 480, 0.16), pan=0.1)
    place(SFX, 53.6, resonant_clicks(2.2, 90, 15, 520, 0.1), pan=0.1)


def reverb(x, seconds, damp):
    n = int(seconds * SR)
    t = np.arange(n) / SR
    irs = []
    for ch in range(2):
        ir = rng.standard_normal(n) * np.exp(-t * 6.9 / seconds)
        ir = lp(ir, damp)
        ir[:int(0.012 * SR)] *= np.linspace(0, 1, int(0.012 * SR))
        irs.append(ir / np.sqrt(np.sum(ir ** 2)))
    return np.stack([signal.fftconvolve(x[:, c], irs[c])[:N] for c in range(2)], 1)


def main(out):
    ambience()
    compose()
    design()
    music = lp(MUSIC, 9000)
    mix = music * 0.85 + reverb(music, 3.2, 5000) * 0.38
    mix += SFX + reverb(SFX, 1.6, 6000) * 0.15
    amb_gain = np.interp(np.arange(N) / SR, [0, 1, 34, 42, 49, 56, 60], [0.0, 1.0, 1.0, 0.55, 0.3, 0.35, 0.5])
    mix += (AMB + reverb(AMB, 2.0, 7000) * 0.2) * amb_gain[:, None]
    mix = hp(mix, 25)
    fade = np.ones(N)
    fade[:int(0.5 * SR)] = np.linspace(0, 1, int(0.5 * SR))
    fade[-int(1.8 * SR):] = np.linspace(1, 0, int(1.8 * SR)) ** 1.5
    mix *= fade[:, None]
    mix = np.tanh(1.2 * mix / (np.max(np.abs(mix)) + 1e-9) * 1.4) / np.tanh(1.2 * 1.4)
    mix *= 10 ** (-1.0 / 20)
    wavfile.write(out, SR, (mix * 32767).astype(np.int16))
    print("score written", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "score.wav")
