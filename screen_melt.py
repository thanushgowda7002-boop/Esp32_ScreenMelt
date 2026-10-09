"""
SCREEN MELT - keep clicking or your desktop drips away  (100% harmless prank)
Install:  pip install pygame pillow pyserial
Run:      python screen_melt.py          (press Win+D first so your desktop is visible!)
Test:     python screen_melt.py --demo   (no ESP32 needed, tap SPACE to "click")

How it works:
  * First button press -> takes a screenshot of your REAL desktop (icons, taskbar
    and all) and shows it fullscreen, so nothing looks different... yet.
  * A STABILITY bar drains. It hits zero after DRAIN_SECONDS (7s) if you stop.
  * As it drains, the whole screen melts, icons included.
  * Every button click refills the bar and pulls the screen back up.
  * Bar hits 0 -> screen fully melted -> "SYSTEM MELTED" -> restores itself.

Nothing is changed on your PC. It only shows a picture of your screen.
EMERGENCY EXIT: Esc. Uses the same ESP32 code as before (no reflashing).
"""

import array
import math
import platform
import queue
import random
import sys
import threading
import time

import pygame
import serial
import serial.tools.list_ports
from PIL import Image, ImageGrab

if platform.system() == "Windows":  # make screenshot and window use real pixels
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

# ---------------- Settings ----------------
PORT = None               # e.g. "COM3". None = auto-detect
BAUD = 115200
DRAIN_SECONDS = 7.0       # no clicks -> fully melted after this long
CLICK_BOOST = 0.06        # each click refills 6% (~2.4 clicks/sec to hold steady)
STRIP_W = 4               # thinner = smoother melt, but heavier
SPREAD = 0.55             # how uneven the drips are (0 = whole screen slides)
MELT_HOLD = 2.5           # seconds to stay melted before restoring
UNDER_COLOR = (0, 0, 0)   # what shows where the screen has melted away
SHOW_BAR = True
SOUND = True
# -------------------------------------------

events = queue.Queue()


# ---------- ESP32 input ----------
def find_port():
    if PORT:
        return PORT
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if any(k in desc for k in ("cp210", "ch340", "usb serial", "uart", "silicon labs")):
            return p.device
    ports = list(serial.tools.list_ports.comports())
    return ports[0].device if ports else None


def serial_listener():
    port = find_port()
    if not port:
        print("No serial port found. Is the ESP32 plugged in? (or run with --demo)")
        return
    print(f"Listening on {port}...")
    while True:
        try:
            with serial.Serial(port, BAUD, timeout=1) as ser:
                while True:
                    if ser.readline().decode(errors="ignore").strip() == "PANIC":
                        events.put("CLICK")
        except serial.SerialException:
            time.sleep(2)


# ---------- melt model ----------
def make_delays(n):
    """Smooth random walk = jagged drips, plus a few extra-long ones."""
    d = random.uniform(0, SPREAD)
    out = []
    for _ in range(n):
        d = min(SPREAD, max(0.0, d + random.uniform(-0.035, 0.035)))
        out.append(d)
    for _ in range(max(6, n // 20)):
        i, w = random.randrange(n), random.randint(2, 5)
        for j in range(i, min(n, i + w)):
            out[j] *= 0.15
    return out


def draw_melt(screen, img, m, delays, exps, H):
    """m: 0 = intact, 1 = fully melted. Pure function of m, so healing rewinds."""
    screen.fill(UNDER_COLOR)
    if m >= 0.999:
        return
    t = m * (1 + SPREAD)
    for i, (d, e) in enumerate(zip(delays, exps)):
        q = min(1.0, max(0.0, t - d))
        off = int((q ** e) * H)
        if off >= H:
            continue
        x = i * STRIP_W
        screen.blit(img, (x, off), pygame.Rect(x, 0, STRIP_W, H - off))


# ---------- HUD ----------
def draw_hud(screen, font, big, s, W, H, now):
    s = max(0.0, s)
    bw, bh = int(W * 0.42), max(26, H // 36)
    x, y = (W - bw) // 2, int(H * 0.04)
    pygame.draw.rect(screen, (15, 15, 15), (x - 8, y - 8, bw + 16, bh + 16), border_radius=10)
    color = (46, 204, 113) if s > 0.5 else (241, 196, 15) if s > 0.25 else (231, 76, 60)
    pygame.draw.rect(screen, color, (x, y, int(bw * s), bh), border_radius=6)
    pygame.draw.rect(screen, (255, 255, 255), (x, y, bw, bh), 2, border_radius=6)
    label = font.render(f" SYSTEM STABILITY {int(s * 100)}% ", True, (255, 255, 255), (15, 15, 15))
    screen.blit(label, ((W - label.get_width()) // 2, y + bh + 14))
    if s < 0.35 and int(now * 5) % 2 == 0:
        warn = big.render(" CLICK! CLICK! CLICK! ", True, (255, 70, 70), (15, 15, 15))
        screen.blit(warn, ((W - warn.get_width()) // 2, y + bh + 14 + label.get_height() + 10))


# ---------- sounds (generated, no files needed) ----------
def make_sounds():
    try:
        rate = 22050

        def tone(freq, ms, vol):
            n = int(rate * ms / 1000)
            buf = array.array("h", (int(vol * 32767 * math.sin(2 * math.pi * freq * i / rate)
                                        * (1 - i / n)) for i in range(n)))
            return pygame.mixer.Sound(buffer=buf.tobytes())

        rn = rate
        rumble = array.array("h", (int(9000 * math.sin(2 * math.pi * 55 * i / rate)
                                       + random.randint(-5000, 5000)) for i in range(rn)))
        rum = pygame.mixer.Sound(buffer=rumble.tobytes())
        rum.set_volume(0)
        rum.play(loops=-1)
        return tone(1500, 60, 0.35), tone(500, 140, 0.45), rum
    except Exception as e:
        print("Sound disabled:", e)
        return None, None, None


# ---------- one round ----------
def run_game(shot):
    pygame.mixer.pre_init(22050, -16, 1, 512)
    pygame.init()
    try:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        W, H = screen.get_size()
        pygame.mouse.set_visible(False)
        if shot is None:
            shot = Image.new("RGB", (W, H), (30, 90, 160))
        img = pygame.image.fromstring(shot.tobytes(), shot.size, "RGB").convert()
        if img.get_size() != (W, H):
            img = pygame.transform.smoothscale(img, (W, H))

        click_snd, warn_snd, rumble = make_sounds() if SOUND else (None, None, None)
        n = (W + STRIP_W - 1) // STRIP_W
        delays = make_delays(n)
        exps = [random.uniform(1.4, 2.2) for _ in range(n)]
        font = pygame.font.SysFont("consolas,couriernew,monospace", max(18, H // 40), bold=True)
        big = pygame.font.SysFont("consolas,couriernew,monospace", max(28, H // 22), bold=True)
        huge = pygame.font.SysFont("consolas,couriernew,monospace", max(40, H // 12), bold=True)
        red = pygame.Surface((W, H))
        red.fill((255, 0, 0))

        s, m, state, state_t, last_warn = 1.0, 0.0, "play", time.time(), 0
        clock = pygame.time.Clock()
        running = True
        while running:
            dt = clock.tick(60) / 1000
            now = time.time()
            clicks = 0
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    running = False
                elif e.type == pygame.KEYDOWN:
                    if e.key == pygame.K_ESCAPE:
                        running = False
                    elif e.key == pygame.K_SPACE:
                        clicks += 1
            try:
                while True:
                    events.get_nowait()
                    clicks += 1
            except queue.Empty:
                pass

            if state == "play":
                s -= dt / DRAIN_SECONDS
                if clicks:
                    s = min(1.0, s + CLICK_BOOST * clicks)
                    if click_snd:
                        click_snd.play()
                if s < 0.3 and now - last_warn > 0.45:
                    last_warn = now
                    if warn_snd:
                        warn_snd.play()
                if s <= 0:
                    s, state, state_t = 0.0, "melted", now
            elif state == "melted":
                if now - state_t > MELT_HOLD:
                    state = "restore"
            else:  # restore
                s = min(1.0, s + dt / 1.0)
                if s >= 1.0 and m < 0.01:
                    running = False

            m += ((1 - s) - m) * min(1.0, dt * 12)  # smooth, so clicks don't snap
            if rumble:
                rumble.set_volume(min(1.0, m * 0.9))

            draw_melt(screen, img, m, delays, exps, H)
            if s < 0.3 and state == "play":
                red.set_alpha(int(35 + 30 * math.sin(now * 12)))
                screen.blit(red, (0, 0))
            if m > 0.97:
                msg = huge.render("SYSTEM MELTED", True, (230, 230, 230))
                screen.blit(msg, ((W - msg.get_width()) // 2, (H - msg.get_height()) // 2))
            if SHOW_BAR and state == "play":
                draw_hud(screen, font, big, s, W, H, now)
            pygame.display.flip()
    finally:
        pygame.mixer.stop()
        pygame.quit()


def grab_screen():
    try:
        return ImageGrab.grab().convert("RGB")
    except Exception as e:
        print("Screen capture failed:", e)
        return None


def drain():
    try:
        while True:
            events.get_nowait()
    except queue.Empty:
        pass


def main():
    if "--demo" in sys.argv:
        print("Demo mode: tap SPACE to click, Esc to quit.")
        run_game(grab_screen())
        return
    threading.Thread(target=serial_listener, daemon=True).start()
    print("Press Win+D to show your desktop, then press the button to start. Ctrl+C to quit.")
    try:
        while True:
            try:
                events.get(timeout=0.2)
            except queue.Empty:
                continue
            shot = grab_screen()
            run_game(shot)
            drain()
            print("Round over. Press the button for another one.")
    except KeyboardInterrupt:
        print("Bye!")


if __name__ == "__main__":
    main()
