# Matterhorn Pomodoro

A Pomodoro timer that runs in your browser. Each focus session takes you higher
up the Hörnli ridge of the Matterhorn, from Zermatt (1,608 m) to the summit
(4,478 m), and every finished session fills a square in a GitHub-style study log.

No install, no account, no backend: open the page and start.

## Features

- Cycle of 4 focus sessions: **50 min** focus, **10 min** short break, **15 min**
  long break (all configurable in Settings)
- Live altitude, checkpoints (Zermatt, Hörnli Hut, Solvay Hut, summit) and a
  segmented progress bar
- **Study log**: a GitHub-style grid of the last 12 months, every month labelled (scrolls sideways on small screens)
  - every finished focus session lights up today's square (darker = more sessions)
  - click a past square to tick a day you studied away from the timer (shown with a check mark)
  - current streak, best streak, total days studied and today's sessions
  - your data stays in your browser (`localStorage`); clearing site data erases it
- Bell chimes at every phase change
- Optional **brown noise** while you focus, with a volume control
- Keyboard: `Space` start/pause, `R` reset, `B` brown noise

## Run it

Open `web/index.html` in any modern browser (double-click it). Click **Start** once:
browsers only allow sound after a click.

## Terminal version 

`matterhorn_pomodoro.py` is a separate, dependency-free terminal version
(Python 3.8+, `curses`) with the same Matterhorn route, bell sounds and brown
noise. It has its own settings (25/5/15 by default) and does not include the study log.

```bash
python3 matterhorn_pomodoro.py --focus 50 --short 10 --long 15 --brown
```
