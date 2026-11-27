#!/usr/bin/env python3
import argparse
import curses
import locale
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from array import array
from pathlib import Path

__version__ = "1.0.0"

ZERMATT, SUMMIT = 1608, 4478
CHECKPOINTS = [("Zermatt", 1608), ("Hörnli Hut", 3260),
               ("Solvay Hut", 4003), ("Matterhorn", 4478)]
TITLES = {"focus": "F O C U S", "short": "S H O R T   B R E A K",
          "long": "L O N G   B R E A K"}

# ----------------------------------------------------------------- timer ---


class Pomodoro:
    """Pure state machine: no curses, no sound, easy to test."""

    def __init__(self, focus, short, long_):
        self.cfg = {"focus": focus, "short": short, "long": long_}
        self.mode, self.done, self.sessions = "focus", 0, 0
        self.running, self.end = False, 0.0
        self.left = self.dur()

    def dur(self, mode=None):
        return self.cfg[mode or self.mode] * 60.0

    def remaining(self):
        if self.running:
            return max(0.0, self.end - time.monotonic())
        return self.left

    def toggle(self):
        if self.running:
            self.left, self.running = self.remaining(), False
        else:
            self.running, self.end = True, time.monotonic() + self.left
        return self.running

    def phase_fraction(self):
        return 1 - self.remaining() / self.dur()

    def advance(self, natural):
        """Move to the next phase. Returns a sound/notification event."""
        was, event = self.mode, None
        if was == "focus":
            if natural:
                self.done += 1
                self.sessions += 1
            self.mode = "long" if self.done >= 4 else "short"
            if natural:
                event = "summit" if self.done >= 4 else "rest"
        else:
            if was == "long":
                self.done = 0
            self.mode = "focus"
            if natural:
                event = "go"
        self.left = self.dur()
        if self.running:
            self.end = time.monotonic() + self.left
        return event

    def tick(self):
        if self.running and self.remaining() <= 0:
            return self.advance(True)
        return None

    def reset(self):
        self.running, self.mode, self.done = False, "focus", 0
        self.left = self.dur()

    def progress(self):
        f = self.phase_fraction() if self.mode == "focus" else 0
        return min(1.0, (self.done + f) / 4)

    def altitude(self):
        return ZERMATT + self.progress() * (SUMMIT - ZERMATT)


# ----------------------------------------------------------------- audio ---

RATE = 22050
# name: (notes as (frequency Hz, start offset s), bell length s, volume)
SOUNDS = {
    "rest": ([(1046.5, 0), (783.99, .4), (659.25, .8)], 2.4, .5),
    "go": ([(392, 0), (523.25, .3), (659.25, .6)], 2.4, .5),
    "summit": ([(523.25, 0), (659.25, .25), (783.99, .5),
                (1046.5, .75), (1318.5, 1.1)], 2.6, .45),
    "start": ([(440, 0)], .7, .3),
}
PLAYERS = [("pw-play", []), ("paplay", []), ("aplay", ["-q"]),
           ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet"]),
           ("play", ["-q"]), ("afplay", [])]


def _write_wav(path, samples):
    peak = max(1e-9, max(abs(v) for v in samples))
    scale = 0.85 * 32767 / peak
    data = array("h", (int(v * scale) for v in samples))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def _bell_mix(notes, length, vol):
    total = int(RATE * (max(o for _, o in notes) + length))
    buf = [0.0] * total
    for freq, off in notes:
        start = int(off * RATE)
        for i in range(int(RATE * length)):
            t = i / RATE
            env = min(1.0, t / 0.008) * math.exp(-t * 2.3 * 2.4 / length)
            v = math.sin(2 * math.pi * freq * t) + 0.22 * math.exp(-t * 1.5) \
                * math.sin(2 * math.pi * freq * 2.76 * t)
            if start + i < total:
                buf[start + i] += vol * env * v
    return buf


def _brown(seconds=20):
    """Steady brown noise (-6 dB per octave), faded at both ends for a smooth loop."""
    import random
    n, v, out = RATE * seconds, 0.0, []
    fade = RATE // 2
    for i in range(n):
        v = (v + 0.02 * random.uniform(-1, 1)) / 1.02
        out.append(v * min(1.0, i / fade, (n - i) / fade))
    return out


class Audio:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.player = next(((shutil.which(n), a) for n, a in PLAYERS
                            if shutil.which(n)), None)
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
        self.dir = Path(base) / "matterhorn-pomodoro"
        self._brown_on, self._brown_proc = False, None

    def path(self, name):
        return self.dir / f"{name}-v1.wav"

    def prepare(self):
        """Synthesise the sounds once and cache them as WAV files."""
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, (notes, length, vol) in SOUNDS.items():
            if not self.path(name).exists():
                _write_wav(self.path(name), _bell_mix(notes, length, vol))
        if not self.path("brown").exists():
            _write_wav(self.path("brown"), _brown())

    def _spawn(self, name):
        exe, args = self.player
        proc = subprocess.Popen([exe, *args, str(self.path(name))],
                                stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        return proc

    def play(self, name):
        if not self.enabled:
            return
        if self.player is None:
            curses.beep()
            return
        proc = self._spawn(name)
        threading.Thread(target=proc.wait, daemon=True).start()

    def set_brown(self, on):
        on = on and self.enabled and self.player is not None
        if on and not self._brown_on:
            self._brown_on = True
            threading.Thread(target=self._brown_loop, daemon=True).start()
        elif not on and self._brown_on:
            self._brown_on = False
            if self._brown_proc and self._brown_proc.poll() is None:
                self._brown_proc.terminate()

    def _brown_loop(self):
        while self._brown_on:
            self._brown_proc = self._spawn("brown")
            self._brown_proc.wait()

    def close(self):
        self.set_brown(False)


def notify(title, body):
    exe = shutil.which("notify-send")
    if exe:
        subprocess.Popen([exe, "-a", "Matterhorn Pomodoro", title, body],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -------------------------------------------------------------------- UI ---

DIGITS = {
    "0": ["###", "# #", "# #", "# #", "###"], "1": [" # ", "## ", " # ", " # ", "###"],
    "2": ["###", "  #", "###", "#  ", "###"], "3": ["###", "  #", "###", "  #", "###"],
    "4": ["# #", "# #", "###", "  #", "  #"], "5": ["###", "#  ", "###", "  #", "###"],
    "6": ["###", "#  ", "###", "# #", "###"], "7": ["###", "  #", "  #", "  #", "  #"],
    "8": ["###", "# #", "###", "# #", "###"], "9": ["###", "# #", "###", "  #", "###"],
    ":": [" ", "#", " ", "#", " "],
}


def big_clock(text):
    """Render 'MM:SS' as 5 rows of double-width block characters."""
    rows = []
    for r in range(5):
        line = " ".join(DIGITS[ch][r] for ch in text)
        rows.append(line.replace("#", "██").replace(" ", "  "))
    return rows


def palette():
    if curses.COLORS >= 256:
        return dict(sky=[17, 17, 17, 18, 54, 54, 53, 89, 125, 131, 173, 216],
                    lit=139, shade=60, snow=225, snow_shade=146,
                    lit_tex=133, shade_tex=17, snow_tex=189, ss_tex=103,
                    lake=[53, 53, 17, 17], refl_lit=96, refl_shade=54,
                    ink=255, dim=248, gold=221, teal=79, route=252, off=240)
    return dict(sky=[4, 4, 4, 4, 5, 5, 5, 5, 1, 1, 3, 3], lit=7, shade=0,
                snow=7, snow_shade=0, lit_tex=7, shade_tex=0, snow_tex=7,
                ss_tex=0, lake=[4, 4, 4, 4], refl_lit=5, refl_shade=5,
                ink=7, dim=7, gold=3, teal=6, route=0, off=7)


def edges(r, H, B, cx):
    """Left edge, right edge and centre of the mountain at row r (0 = top).

    Flanks are concave (t ** 1.45) so the horn stays slim and steep, then
    flares out towards the base. The summit leans slightly to the right.
    """
    t = r / max(1, H - 1)
    hw = 0.6 + (B / 2 - 0.6) * t ** 1.45
    c = cx + B * 0.07 * (1 - t) ** 1.6
    lx = int(round(c - hw * 1.05)) - (1 if (r * 7) % 6 == 0 else 0)
    rx = int(round(c + hw * 0.90)) + (1 if (r * 11) % 7 == 0 else 0)
    return lx, rx, c


class UI:
    def __init__(self, scr):
        self.scr, self.cache, self.pal = scr, {}, palette()
        self.R = self.C = 0

    def pair(self, fg, bg):
        key = (fg, bg)
        if key not in self.cache:
            try:
                curses.init_pair(len(self.cache) + 1, fg, bg)
            except (curses.error, ValueError):
                return 0
            self.cache[key] = len(self.cache) + 1
        return curses.color_pair(self.cache[key])

    def put(self, y, x, text, fg, bg, bold=False):
        if not (0 <= y < self.R) or x >= self.C or not text:
            return
        if x < 0:
            text, x = text[-x:], 0
        try:
            self.scr.addstr(y, x, text[:self.C - x],
                            self.pair(fg, bg) | (curses.A_BOLD if bold else 0))
        except curses.error:
            pass

    def bg_at(self, y, yb):
        sky = self.pal["sky"]
        if y > yb:
            return self.pal["lake"][min(y - yb - 1, 3)]
        return sky[min(len(sky) - 1, int(y / max(1, yb) * (len(sky) - 1)))]

    def draw(self, pom, audio, brown_on):
        scr = self.scr
        R, C = scr.getmaxyx()
        self.R, self.C = R, C
        p = self.pal
        if R < 24 or C < 80:
            scr.erase()
            self.put(0, 0, f"Terminal too small ({C}x{R}). Need 80x24.", p["ink"], 0)
            scr.refresh()
            return
        panel, y0, yb = 44, 1, R - 5
        H = yb - y0 + 1
        B = max(20, min(C - panel - 3, int(H * 2.3)))
        cx = panel + (C - panel) // 2

        for y in range(R):                                   # sky + lake
            self.put(y, 0, " " * C, p["ink"], self.bg_at(y, yb))
        for i in range(45):                                  # stars
            x, y = panel + (i * 53 + 7) % (C - panel), (i * 29 + 3) % max(1, yb // 2)
            self.put(y, x, "·", p["ink"], self.bg_at(y, yb))

        alt = pom.altitude()
        frac = (alt - ZERMATT) / (SUMMIT - ZERMATT)
        crow = round((H - 1) * (1 - frac))
        accent = p["gold"] if pom.mode == "focus" else p["teal"]

        row_of = lambda a: round((H - 1) * (1 - (a - ZERMATT) / (SUMMIT - ZERMATT)))
        info = {}                                            # row -> (lx, rx, ci, lit)
        for r in range(H):                                   # mountain
            y = y0 + r
            lx, rx, c = edges(r, H, B, cx)
            ci = int(round(c))
            snow = r < H * 0.22
            lit = p["snow"] if snow else p["lit"]
            sh = p["snow_shade"] if snow else p["shade"]
            ltex = p["snow_tex"] if snow else p["lit_tex"]
            stex = p["ss_tex"] if snow else p["shade_tex"]
            info[r] = (lx, rx, ci, lit)
            if ci > lx:
                self.put(y, lx, " " * (ci - lx), p["ink"], lit)
            self.put(y, ci, " " * (rx - ci + 1), p["ink"], sh)
            for x in range(lx, rx + 1):
                if (x * 31 + r * 17 + (x * r) % 7) % 13 == 0:
                    left = x < ci
                    self.put(y, x, "╱" if left else "╲",
                             ltex if left else stex, lit if left else sh)

        def route_x(r):
            lx, _, ci, _ = info[r]
            x = lx + 2 + int(round(1.2 * math.sin(r * 0.8)))
            return min(max(x, lx + 1), max(lx + 1, ci - 1))

        for r in range(H):                                   # route
            walked = r >= crow
            self.put(y0 + r, route_x(r), "•" if walked else "·",
                     accent if walked else p["route"], info[r][3], walked)

        for name, a in CHECKPOINTS:                          # checkpoints
            r = row_of(a)
            lx, rx, _, lit = info[r]
            reached = alt >= a
            y, x = y0 + r, route_x(r)
            self.put(y, x, "◆", accent if reached else p["ink"], lit, True)
            text = f"{name} {a:,} m"
            limit = panel if y <= 22 else 2      # free space below the HUD
            if lx - len(text) - 2 > limit:
                self.put(y, lx - len(text) - 1, text,
                         accent if reached else p["ink"], self.bg_at(y, yb))
            elif rx + 2 + len(text) < C:
                self.put(y, rx + 2, text,
                         accent if reached else p["ink"], self.bg_at(y, yb))

        cx_, cy_ = route_x(crow), y0 + crow                  # climber
        self.put(cy_, cx_, "◉", accent, info[crow][3], True)

        for j in range(1, 5):                                # reflection
            rr = max(0, H - 1 - (j - 1) * 2)
            lx, rx, c = edges(rr, H, B, cx)
            ci = int(round(c))
            self.put(yb + j, lx, " " * (ci - lx), p["ink"], p["refl_lit"])
            self.put(yb + j, ci, " " * (rx - ci + 1), p["ink"], p["refl_shade"])
            lake = p["lake"][j - 1]
            for x in range(panel, C, 7):
                self.put(yb + j, x + (j * 3) % 5, "──", p["off"], lake)

        # ---- left panel -------------------------------------------------
        bg = lambda y: self.bg_at(y, yb)
        self.put(2, 4, TITLES[pom.mode], accent, bg(2), True)
        rem = math.ceil(pom.remaining())
        for i, line in enumerate(big_clock(f"{rem // 60:02d}:{rem % 60:02d}")):
            self.put(4 + i, 4, line, p["ink"], bg(4 + i))

        self._bar(pom, 10, 4, 36, accent, bg(10))
        self.put(12, 4, "ALTITUDE", p["dim"], bg(12))
        self.put(12, 15, f"{round(alt):,} m", p["ink"], bg(12), True)
        nxt = next((n for n, a in CHECKPOINTS if a > alt), None)
        self.put(13, 4, "NEXT", p["dim"], bg(13))
        self.put(13, 15, nxt.upper() if nxt else "SUMMIT REACHED", p["ink"], bg(13))
        n = min(4, pom.done + (pom.mode == "focus"))
        self.put(14, 4, "SESSION", p["dim"], bg(14))
        self.put(14, 15, f"{n} of 4", p["ink"], bg(14))
        state = "climbing" if pom.running else "paused"
        self.put(16, 4, state, accent, bg(16))
        self.put(18, 4, "space start/pause   s skip   r reset", p["dim"], bg(18))
        self.put(19, 4, "m sound   b brown noise   q quit", p["dim"], bg(19))
        snd = ("sound on" if audio.enabled else "sound off") + \
              ("  ·  brown noise on" if brown_on else "  ·  brown noise off")
        self.put(21, 4, snd, p["dim"], bg(21))
        scr.refresh()

    def _bar(self, pom, y, x, width, accent, bg):
        segs = []
        for i in range(4):
            segs.append(("f", i, pom.cfg["focus"]))
            segs.append(("l" if i == 3 else "s", i,
                         pom.cfg["long" if i == 3 else "short"]))
        total = sum(d for *_, d in segs)
        avail = width - (len(segs) - 1)
        acc = prev = 0
        for kind, i, d in segs:
            acc += d
            end = round(acc / total * avail)
            w = max(1, end - prev)
            prev = end
            if kind == "f":
                fill = 1 if i < pom.done else (
                    pom.phase_fraction() if i == pom.done and pom.mode == "focus" else 0)
            elif i < pom.done - 1 or (i == pom.done - 1 and pom.mode == "focus"):
                fill = 1
            elif i == pom.done - 1:
                fill = pom.phase_fraction()
            else:
                fill = 0
            k = round(w * fill)
            self.put(y, x, "━" * k, accent, bg, True)
            self.put(y, x + k, "━" * (w - k), self.pal["off"], bg)
            x += w + 1


# ------------------------------------------------------------------ main ---


def run(stdscr, args, audio):
    curses.curs_set(0)
    stdscr.timeout(200)
    ui = UI(stdscr)
    pom = Pomodoro(args.focus, args.short, args.long)
    brown_on = args.brown
    messages = {"rest": ("Focus session done", "Time to rest."),
                "go": ("Break over", "Back to climbing."),
                "summit": ("Summit reached!", "Take a long break.")}
    while True:
        event = pom.tick()
        if event:
            audio.play(event)
            notify(*messages[event])
        audio.set_brown(brown_on and pom.running and pom.mode == "focus")
        ui.draw(pom, audio, brown_on)
        key = stdscr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return pom
        if key == ord(" "):
            if pom.toggle():
                audio.play("start")
        elif key in (ord("s"), ord("S")):
            event = pom.advance(False)
        elif key in (ord("r"), ord("R")):
            pom.reset()
        elif key in (ord("m"), ord("M")):
            audio.enabled = not audio.enabled
            if not audio.enabled:
                audio.set_brown(False)
        elif key in (ord("b"), ord("B")):
            brown_on = not brown_on


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Climb the Matterhorn, one focus session at a time.")
    ap.add_argument("--focus", type=int, default=25, help="focus minutes (25)")
    ap.add_argument("--short", type=int, default=5, help="short break minutes (5)")
    ap.add_argument("--long", type=int, default=15, help="long break minutes (15)")
    ap.add_argument("--no-sound", action="store_true", help="start muted")
    ap.add_argument("--brown", action="store_true", help="brown noise while focusing")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)
    if min(args.focus, args.short, args.long) < 1:
        ap.error("durations must be at least 1 minute")
    locale.setlocale(locale.LC_ALL, "")
    audio = Audio(enabled=not args.no_sound)
    if audio.enabled:
        audio.prepare()
    pom = None
    try:
        pom = curses.wrapper(run, args, audio)
    except KeyboardInterrupt:
        pass
    finally:
        audio.close()
    if pom:
        mins = pom.sessions * args.focus
        print(f"Back at base camp. Focus sessions completed: {pom.sessions} ({mins} min).")


if __name__ == "__main__":
    sys.exit(main())
