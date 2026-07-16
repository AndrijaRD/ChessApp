"""
Minimal Chess — play White against a local Stockfish engine, then review
the game with full post-game analysis (accuracy, move classification,
best-move suggestions).

Run with:  python main.py

See README.md for how to install / plug in Stockfish.
"""

import os
import sys
import math
import time
import queue
import shutil
import threading

# ---------------------------------------------------------------------------
# High-DPI bootstrap — must run before pygame.init() / window creation, or
# the whole app gets blurry-upscaled by the OS compositor on HiDPI screens.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor v2
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import pygame
import pygame.gfxdraw
import chess
import chess.engine

# --------------------------------------------------------------------------
# Stockfish discovery
# --------------------------------------------------------------------------

def find_stockfish():
    """Try a few sensible places to find a Stockfish binary. Returns a path
    or None. Never raises."""
    env_path = os.environ.get("STOCKFISH_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    base_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ("stockfish", "stockfish.exe", "stockfish-mac", "stockfish-linux"):
        candidate = os.path.join(base_dir, name)
        if os.path.isfile(candidate):
            return candidate

    which = shutil.which("stockfish")
    if which:
        return which

    return None


# --------------------------------------------------------------------------
# Colors / palette -- theme system (light + dark mode)
# --------------------------------------------------------------------------
#
# All color "constants" below are module globals that get reassigned by
# apply_theme() when the person switches theme. Every drawing function in
# this file refers to them by bare name (BG, TEXT_DARK, ...), so flipping
# the theme is just a matter of overwriting these globals -- no call site
# anywhere else needs to change.

LIGHT_THEME = {
    "BG":               (247, 246, 243),
    "PANEL_BG":         (252, 251, 249),
    "SURFACE":          (255, 255, 255),
    "SURFACE_ALT":      (241, 239, 235),

    "LIGHT_SQ":         (238, 236, 231),
    "DARK_SQ":          (176, 172, 163),
    "BOARD_BORDER":     (205, 202, 194),
    "SELECT_SQ":        (241, 214, 130),
    "LASTMOVE_SQ":      (232, 224, 190),
    "MOVE_DOT":         (120, 148, 112),
    "CAPTURE_RING":     (196, 90, 80),

    "WHITE_FILL":       (250, 250, 248),
    "WHITE_BORDER":     (60, 58, 55),
    "BLACK_FILL":       (46, 44, 42),
    "BLACK_BORDER":     (232, 230, 225),

    "TEXT_DARK":        (48, 46, 43),
    "TEXT_MUTED":       (128, 124, 116),
    "TEXT_FAINT":       (176, 172, 164),

    "ACCENT":           (76, 154, 143),
    "ACCENT_DARK":      (56, 122, 112),
    "ACCENT_SOFT":      (223, 236, 232),
    "DANGER":           (196, 90, 80),
    "DANGER_DARK":      (166, 68, 60),
    "HINT_ARROW":       (66, 133, 244, 165),

    "BUTTON_BG":        (255, 255, 255),
    "BUTTON_BORDER":    (210, 207, 199),
    "BUTTON_HOVER":     (240, 238, 233),
    "PANEL_BORDER":     (223, 220, 212),
    "PANEL_ROW_HOVER":  (243, 241, 236),
    "PANEL_ROW_ACTIVE": (233, 240, 232),
    "DIVIDER":          (228, 225, 218),

    "EVAL_WHITE":       (250, 250, 248),
    "EVAL_BLACK":       (46, 44, 42),

    "SHADOW":           (30, 28, 26, 26),
}

DARK_THEME = {
    "BG":               (21, 22, 24),
    "PANEL_BG":         (29, 30, 33),
    "SURFACE":          (36, 37, 41),
    "SURFACE_ALT":      (44, 45, 50),

    "LIGHT_SQ":         (95, 96, 104),
    "DARK_SQ":          (52, 52, 58),
    "BOARD_BORDER":     (68, 68, 76),
    "SELECT_SQ":        (198, 166, 82),
    "LASTMOVE_SQ":      (110, 100, 60),
    "MOVE_DOT":         (140, 178, 132),
    "CAPTURE_RING":     (214, 112, 100),

    "WHITE_FILL":       (232, 230, 225),
    "WHITE_BORDER":     (26, 25, 24),
    "BLACK_FILL":       (16, 15, 15),
    "BLACK_BORDER":     (214, 211, 205),

    "TEXT_DARK":        (232, 230, 226),
    "TEXT_MUTED":       (162, 159, 152),
    "TEXT_FAINT":       (108, 106, 100),

    "ACCENT":           (90, 182, 168),
    "ACCENT_DARK":      (68, 152, 140),
    "ACCENT_SOFT":      (34, 52, 49),
    "DANGER":           (218, 114, 102),
    "DANGER_DARK":      (186, 92, 82),
    "HINT_ARROW":       (104, 162, 255, 175),

    "BUTTON_BG":        (42, 43, 47),
    "BUTTON_BORDER":    (64, 64, 71),
    "BUTTON_HOVER":     (52, 53, 59),
    "PANEL_BORDER":     (58, 58, 65),
    "PANEL_ROW_HOVER":  (50, 51, 57),
    "PANEL_ROW_ACTIVE": (38, 55, 50),
    "DIVIDER":          (54, 54, 61),

    "EVAL_WHITE":       (228, 226, 221),
    "EVAL_BLACK":       (12, 11, 11),

    "SHADOW":           (0, 0, 0, 90),
}

# Text drawn on top of solidly-colored accent/danger buttons stays a fixed
# near-white in both themes, since those buttons themselves don't change.
TEXT_LIGHT = (247, 246, 243)

CURRENT_THEME_NAME = "light"


def apply_theme(name):
    """Overwrite the module-level color globals with the chosen theme's
    values. Every drawing routine reads these by bare name, so this single
    call is all that's needed to re-skin the whole app."""
    global CURRENT_THEME_NAME
    theme = DARK_THEME if name == "dark" else LIGHT_THEME
    globals().update(theme)
    CURRENT_THEME_NAME = "dark" if name == "dark" else "light"


apply_theme("light")

# --------------------------------------------------------------------------
# Design system -- one shared type scale and one shared spacing scale, used
# by every screen instead of one-off pixel values picked per call site.
# This is what keeps the app reading as a single consistent design instead
# of a patchwork of slightly-different sizes and gaps:
#
#   FONT_SCALE  -- five sizes, hero down to caption. Nearly all text in the
#                  app should map to one of these (piece glyphs and board
#                  coordinates are the only exceptions -- they're rendered
#                  shapes, not reading text, so they're sized to the board
#                  instead).
#   SPACE       -- five gap/padding sizes. Any margin, padding, or gap
#                  between elements should be one of these rather than an
#                  arbitrary number, so related paddings actually match.
# --------------------------------------------------------------------------
FONT_SCALE = {
    "hero": 46,  # the "Minimal Chess" title only
    "xl":   32,  # standout numbers (accuracy %)
    "lg":   24,  # dialog / panel / section titles, buttons
    "md":   19,  # body text -- moves, descriptions, list rows
    "sm":   15,  # captions, pills, secondary/muted detail text
}

SPACE = {
    "xs": 6,
    "sm": 10,
    "md": 16,
    "lg": 24,
    "xl": 32,
}

# --------------------------------------------------------------------------
# Tiny on-disk settings file (just the theme preference) -- best-effort,
# never raises, so a read-only filesystem or odd permissions never breaks
# the app; it just falls back to the light theme every launch.
# --------------------------------------------------------------------------

_SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chess_settings.json")


def load_settings():
    try:
        import json
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(settings):
    try:
        import json
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f)
    except Exception:
        pass

PIECE_LETTERS = {
    chess.PAWN: "P",
    chess.KNIGHT: "N",
    chess.BISHOP: "B",
    chess.ROOK: "R",
    chess.QUEEN: "Q",
    chess.KING: "K",
}
PIECE_VALUE = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

DIFFICULTIES = [
    {"name": "Beginner",     "elo": 800,  "time": 0.3},
    {"name": "Easy",         "elo": 1100, "time": 0.4},
    {"name": "Intermediate", "elo": 1400, "time": 0.6},
    {"name": "Advanced",     "elo": 1700, "time": 0.9},
    {"name": "Expert",       "elo": 2000, "time": 1.2},
    {"name": "Master",       "elo": 2400, "time": 1.6},
    {"name": "Maximum",      "elo": None, "time": 2.0},
]

ENGINE_MOVE_DELAY = 0.7
ANIM_DURATION = 0.3

# --------------------------------------------------------------------------
# Post-game review analysis depth.
#
# This is intentionally slow. Per position: a 3-second full-strength,
# multi-PV(3) Stockfish search, and if the top two lines are close (a
# genuinely tricky moment) or the position is a check, a *second*, much
# longer search (12s) to get a far more reliable read exactly where a
# shallow search is least trustworthy -- rather than spending the same
# fixed time everywhere. A 40-move game therefore takes several minutes,
# not fractions of a second; that trade is made deliberately, since a
# quick-and-shallow classifier is exactly what produces the wrong/insulting
# "brilliant"/"blunder" calls this feature exists to avoid.
# --------------------------------------------------------------------------
ANALYSIS_TIME_BASE = 3.0
ANALYSIS_TIME_DEEP = 12.0
ANALYSIS_MULTIPV = 3
ANALYSIS_CRITICAL_GAP_CP = 60  # top-2 lines within this many cp => re-search deeper

# --------------------------------------------------------------------------
# Review speed presets -- offered as a 3-position slider once a game ends,
# so the person can trade accuracy for time depending on how much the game
# actually deserves a deep look. "Deep" reuses the ANALYSIS_TIME_* constants
# above unchanged (that's the original always-on behavior); "Fast" and
# "Medium" scale both the base per-position search time and the follow-up
# deep re-search (or drop the re-search entirely, for Fast) so a full game
# finishes in roughly the target window on a typical multi-core machine.
# These are approximate -- actual wall-clock time still depends on game
# length and core count via _analysis_worker_plan().
# --------------------------------------------------------------------------
REVIEW_SPEEDS = [
    {
        "key": "fast", "label": "Fast",
        "blurb": "A few seconds. Single quick pass, good for a casual glance.",
        "movetime": 0.08, "deep_movetime": None,
    },
    {
        "key": "medium", "label": "Medium",
        "blurb": "About 2-3 minutes. Solid accuracy for most games.",
        "movetime": 0.6, "deep_movetime": 2.5,
    },
    {
        "key": "deep", "label": "Deep",
        "blurb": "Several minutes. Max-strength, the deepest read available.",
        "movetime": ANALYSIS_TIME_BASE, "deep_movetime": ANALYSIS_TIME_DEEP,
    },
]

# --------------------------------------------------------------------------
# Move classification (used by the post-game review)
#
# Modeled on the publicly-documented approach used by chess.com's "Expected
# Points" system (see support.chess.com's "How are moves classified"
# article and their own published table of win%-loss boundaries) rather
# than a raw centipawn-loss cutoff. Centipawns are converted to "win
# probability" first (win_percent(), the same logistic curve Lichess
# publishes), and moves are bucketed by how many points of win probability
# they gave up -- because losing 50cp when the game is already decided
# means something completely different from losing 50cp in a dead-even
# position, and a flat cp threshold can't tell those apart.
# --------------------------------------------------------------------------

CLASS_KEYS = ["brilliant", "great", "best", "excellent", "good", "inaccuracy", "mistake", "blunder", "miss"]

# Color is a signal, not decoration: "best"/"excellent"/"good" are what most
# moves in a normal game get classified as, so they're kept to quiet,
# closely-related neutral tones rather than three more saturated hues.
# Saturated color is reserved for the classes actually worth a person's
# attention -- the rare stand-out moves (brilliant/great) and the errors
# (inaccuracy/mistake/blunder/miss) -- so a scan of the move list draws the
# eye to what matters instead of turning every row into a rainbow.
CLASS_META = {
    "brilliant":  {"label": "Brilliant",  "color": (0, 150, 168)},
    "great":      {"label": "Great",      "color": (77, 97, 196)},
    "best":       {"label": "Best",       "color": (140, 148, 138)},
    "excellent":  {"label": "Excellent",  "color": (152, 158, 142)},
    "good":       {"label": "Good",       "color": (168, 165, 156)},
    "inaccuracy": {"label": "Inaccuracy", "color": (214, 158, 46)},
    "mistake":    {"label": "Mistake",    "color": (214, 122, 44)},
    "blunder":    {"label": "Blunder",    "color": (196, 68, 60)},
    "miss":       {"label": "Miss",       "color": (172, 64, 110)},
}

# Win-probability-loss boundaries, straight from chess.com's own published
# Table I (Best 0-0, Excellent 0-2, Good 2-5, Inaccuracy 5-10, Mistake
# 10-20, Blunder 20-100 -- expressed here as points on a 0-100 scale rather
# than 0-1). "Best" isn't in this table because it isn't loss-bucketed at
# all -- it's simply "you played the engine's own top choice."
WP_LOSS_EXCELLENT = 2.0
WP_LOSS_GOOD = 5.0
WP_LOSS_INACCURACY = 10.0
WP_LOSS_MISTAKE = 20.0
# anything above WP_LOSS_MISTAKE is a Blunder

GREAT_GAP_WP = 15.0     # "Great": next-best alternative loses at least this much more
MISS_SWING_WP = 15.0    # "Miss": squandered at least this much win% from an already-winning spot
MISS_WINNING_WP = 92.0  # win% threshold that counts as "already winning" for Miss purposes
DECISIVE_WP = 90.0      # position judged already decided -> Brilliant/Great don't apply


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def win_percent(cp_mover):
    """Win probability (0-100) for the side to move, given a centipawn
    score from that side's own point of view."""
    cp = clamp(cp_mover, -1000, 1000)
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy_percent(before_mover, after_mover):
    wb = win_percent(before_mover)
    wa = win_percent(after_mover)
    drop = max(0.0, wb - wa)
    acc = 103.1668 * math.exp(-0.04354 * drop) - 3.1668
    return clamp(acc, 0.0, 100.0)


def classify_move(before_mover, after_mover, second_mover, is_top_choice,
                   mate_before_mover, mate_after_mover, move, mover_color, board_before, board_after):
    """Move classification using the win%-loss approach chess.com documents
    publicly (see the module-level comment above), plus two special cases
    that a pure win%-loss table can't catch on its own:

      - Brilliant/Great: only apply to the engine's actual top choice, and
        only when the position wasn't already decisively won or lost.
      - Miss: a forced mate that was available and then not continued, or
        a big chunk of an already-winning position given up. A flat win%
        table under-counts these because win% saturates near 0/100, so a
        move that throws away a mate-in-3 for a merely "clearly winning"
        position can show only a small percentage-point drop even though
        it is exactly the kind of moment a human would call a miss.

    Returns (classification, facts) -- facts records the intermediate
    signals used (sacrifice / miss reason) so the review panel can explain
    its reasoning using the same numbers, not a guess made after the fact.
    """
    wp_before = win_percent(before_mover)
    wp_after = win_percent(after_mover)
    wp_second = win_percent(second_mover)
    wp_loss = max(0.0, wp_before - wp_after)
    wp_gap = max(0.0, wp_before - wp_second)
    decisive = wp_before >= DECISIVE_WP or wp_before <= (100 - DECISIVE_WP)

    facts = {"sac": False, "miss_type": None, "wp_loss": wp_loss, "wp_gap": wp_gap}

    had_forced_mate = mate_before_mover is not None and mate_before_mover > 0
    kept_forced_mate = mate_after_mover is not None and mate_after_mover > 0

    if is_top_choice:
        moved_piece = board_before.piece_at(move.from_square)
        sac = False
        if moved_piece is not None:
            was_capture = board_before.is_capture(move)
            captured_ok = True
            if was_capture:
                captured = board_before.piece_at(move.to_square)
                if board_before.is_en_passant(move):
                    captured_ok = True
                elif captured is not None:
                    captured_ok = PIECE_VALUE[captured.piece_type] < PIECE_VALUE[moved_piece.piece_type]
                else:
                    captured_ok = False
            if (not was_capture) or captured_ok:
                attackers = [sq for sq in board_after.attackers(not mover_color, move.to_square)
                             if board_after.piece_at(sq) and board_after.piece_at(sq).piece_type != chess.KING]
                if attackers:
                    min_att_val = min(PIECE_VALUE[board_after.piece_at(sq).piece_type] for sq in attackers)
                    if min_att_val <= PIECE_VALUE[moved_piece.piece_type]:
                        sac = True
        facts["sac"] = sac
        if sac and not decisive:
            return "brilliant", facts
        if wp_gap >= GREAT_GAP_WP and not decisive:
            return "great", facts
        return "best", facts

    # Not the engine's top choice from here on.
    if had_forced_mate and not kept_forced_mate:
        facts["miss_type"] = "mate"
        return "miss", facts
    if wp_before >= MISS_WINNING_WP and wp_loss >= MISS_SWING_WP:
        facts["miss_type"] = "swing"
        return "miss", facts

    if wp_loss <= WP_LOSS_EXCELLENT:
        return "excellent", facts
    if wp_loss <= WP_LOSS_GOOD:
        return "good", facts
    if wp_loss <= WP_LOSS_INACCURACY:
        return "inaccuracy", facts
    if wp_loss <= WP_LOSS_MISTAKE:
        return "mistake", facts
    return "blunder", facts


def explain_move(rec):
    """Plain-language reason for a move's classification, built from the
    exact numbers classify_move used -- not a separate guess. This
    describes the *mechanism* of the label (what crossed what threshold),
    not a deep tactical narrative -- claiming to know "why" a sacrifice
    works tactically would overstate what a win%-loss classifier can
    actually tell you."""
    cls = rec["classification"]
    wp_loss = rec.get("wp_loss", 0.0)
    wp_gap = rec.get("wp_gap", 0.0)
    cp_loss = rec["loss"]
    sac = rec.get("sac", False)
    miss_type = rec.get("miss_type")
    best_san = rec["best_san"]
    san = rec["san"]
    has_alt = best_san and best_san != san

    if cls == "brilliant":
        return (f"{san} sacrifices material for a recapture that isn't guaranteed in your favor, yet the "
                f"engine's win probability barely moved (\u2212{wp_loss:.1f}%). Judged worth about as much "
                f"without the piece as with it.")
    if cls == "great":
        return (f"{san} matched the engine's best move, and the next-best alternative gave up {wp_gap:.1f}% "
                f"more win probability \u2014 close to the only move that kept your position intact.")
    if cls == "best":
        tail = (" It also happened to involve a material sacrifice, but the position was already fairly "
                "one-sided, so it isn't flagged as Brilliant.") if sac else ""
        return f"{san} matches the engine's top-rated move for this position.{tail}"
    if cls == "excellent":
        return (f"{san} costs {wp_loss:.1f}% win probability ({cp_loss} cp) compared to the engine's best "
                f"line \u2014 close enough that it barely matters.")
    if cls == "good":
        return (f"{san} costs {wp_loss:.1f}% win probability ({cp_loss} cp) \u2014 a sound move, just not "
                f"the sharpest one available.")
    if cls == "inaccuracy":
        alt = f" {best_san} would have kept more of your position." if has_alt else ""
        return f"{san} costs {wp_loss:.1f}% win probability ({cp_loss} cp) compared to the best move here.{alt}"
    if cls == "mistake":
        alt = f" {best_san} was clearly stronger." if has_alt else ""
        return (f"{san} costs {wp_loss:.1f}% win probability ({cp_loss} cp) \u2014 a real error, though not "
                f"immediately game-losing.{alt}")
    if cls == "blunder":
        alt = f" {best_san} was much stronger." if has_alt else ""
        return (f"{san} costs {wp_loss:.1f}% win probability ({cp_loss} cp) \u2014 enough to swing the "
                f"game's outcome.{alt}")
    if cls == "miss":
        if miss_type == "mate":
            return (f"A forced checkmate was on the board here, and {san} doesn't continue it \u2014 the "
                     f"engine can no longer force mate afterward, even though the position may still be fine.")
        alt = f" {best_san} would have kept your winning position clearly winning." if has_alt else ""
        return (f"You were in a winning position (\u2265{MISS_WINNING_WP:.0f}% win probability) and {san} "
                f"gives back {wp_loss:.1f}% of it \u2014 the advantage isn't gone, but a real chance to "
                f"finish the game was left on the table.{alt}")
    return ""


# --------------------------------------------------------------------------
# Engine worker thread
# --------------------------------------------------------------------------

class EngineThread(threading.Thread):
    """Owns the Stockfish process. All engine calls happen on this thread;
    the main (pygame) thread only ever pushes jobs / reads results."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.path = path
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.engine = None
        self._stop_flag = False
        self._cancel_analysis = False
        self.ready = threading.Event()
        self.error = None

    def run(self):
        try:
            self.engine = chess.engine.SimpleEngine.popen_uci(self.path)
        except Exception as exc:
            self.error = str(exc)
            self.ready.set()
            return
        self.ready.set()

        while not self._stop_flag:
            try:
                job = self.jobs.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                break
            try:
                kind = job.get("kind")
                if kind == "configure":
                    self._configure(job.get("options", {}))
                elif kind == "bestmove":
                    self._bestmove(job)
                elif kind == "eval":
                    self._eval(job)
                elif kind == "analyze_game":
                    self._cancel_analysis = False
                    self._analyze_game(job)
            except Exception as exc:
                self.results.put({"kind": "error", "tag": job.get("tag"), "error": str(exc)})

        try:
            if self.engine is not None:
                self.engine.quit()
        except Exception:
            pass

    def _configure(self, options):
        if self.engine is None:
            return
        available = self.engine.options
        safe = {k: v for k, v in options.items() if k in available}
        if safe:
            try:
                self.engine.configure(safe)
            except Exception:
                pass

    def _bestmove(self, job):
        board = chess.Board(job["fen"])
        limit = chess.engine.Limit(time=job["movetime"])
        result = self.engine.play(board, limit, info=chess.engine.INFO_SCORE)
        cp, mate = None, None
        if result.info and "score" in result.info:
            pov = result.info["score"].white()
            mate = pov.mate()
            cp = pov.score(mate_score=100000)
        self.results.put({
            "kind": "bestmove",
            "tag": job.get("tag"),
            "move": result.move.uci() if result.move else None,
            "cp": cp,
            "mate": mate,
        })

    def _eval(self, job):
        board = chess.Board(job["fen"])
        limit = chess.engine.Limit(time=job.get("movetime", 0.2))
        info = self.engine.analyse(board, limit)
        cp, mate = None, None
        if info and "score" in info:
            pov = info["score"].white()
            mate = pov.mate()
            cp = pov.score(mate_score=100000)
        self.results.put({"kind": "eval", "tag": job.get("tag"), "cp": cp, "mate": mate})

    # ---------------------------------------------------------- game review

    def _analyse_position(self, engine, board, limit, deep_limit=None):
        """Analyse one position at ANALYSIS_TIME_BASE with multipv=3, then
        -- if the top two lines are close (a genuinely ambiguous moment)
        or the position is a check -- redo the search at the much longer
        ANALYSIS_TIME_DEEP to get a trustworthy read exactly where a quick
        search is least reliable. Returns (best_cp_white, second_cp_white,
        best_move, best_mate_white).

        Takes an explicit `engine` (rather than always self.engine) so the
        same logic can run against a whole pool of engine processes during
        review analysis, not just the single live-play engine."""
        def run(lim):
            try:
                infos = engine.analyse(board, lim, multipv=ANALYSIS_MULTIPV)
            except Exception:
                infos = engine.analyse(board, lim)
            if isinstance(infos, dict):
                infos = [infos]
            return infos

        def pov_white(info):
            if not info or "score" not in info:
                return 0, None
            pov = info["score"].white()
            return pov.score(mate_score=100000), pov.mate()

        infos = run(limit)
        best = infos[0] if infos else None
        second = infos[1] if len(infos) > 1 else best
        best_cp, best_mate = pov_white(best)
        second_cp, _ = pov_white(second)

        if deep_limit is not None and (board.is_check() or abs(best_cp - second_cp) <= ANALYSIS_CRITICAL_GAP_CP):
            infos2 = run(deep_limit)
            if infos2:
                best2 = infos2[0]
                second2 = infos2[1] if len(infos2) > 1 else best2
                best_cp, best_mate = pov_white(best2)
                second_cp, _ = pov_white(second2)
                best = best2

        best_move = None
        if best and best.get("pv"):
            best_move = best["pv"][0]
        return best_cp, second_cp, best_move, best_mate

    def _analysis_worker_plan(self, num_positions):
        """How many parallel Stockfish processes to run and how many
        threads to give each one, based on the machine's CPU count. Uses
        several independent low-thread searches rather than one big
        many-threaded search, since parallelizing across *positions*
        scales close to linearly while a single search's speedup from
        extra threads (Lazy SMP) tails off well before 16 threads."""
        logical = os.cpu_count() or 4
        num_workers = int(clamp(logical // 4, 1, 6))
        num_workers = max(1, min(num_workers, num_positions))
        threads_per_engine = max(2, logical // max(1, num_workers))
        return num_workers, threads_per_engine

    def _analyze_game(self, job):
        # NOTE: worker() below staggers each engine process's startup by a
        # small amount. Launching several python-chess SimpleEngine
        # instances (each spinning up its own background asyncio event
        # loop + Stockfish subprocess) at the exact same instant can
        # occasionally trip a benign asyncio handshake race on some
        # systems -- results were never actually wrong when this happened,
        # but a ~150ms stagger avoids the noisy traceback in the console.
        moves_uci = job["moves"]
        tag = job.get("tag")
        base_time = job.get("movetime", ANALYSIS_TIME_BASE)
        deep_time = job.get("deep_movetime", ANALYSIS_TIME_DEEP)
        limit = chess.engine.Limit(time=base_time)
        deep_limit = chess.engine.Limit(time=deep_time) if deep_time else None

        # Replay the whole game up front -- cheap, no engine involved --
        # so every position that needs analysing is known before any
        # engine call happens. N moves share N+1 unique positions (each
        # move's "after" position is the next move's "before" position),
        # and critically, those N+1 positions don't depend on each other's
        # *analysis* -- only on the moves already played -- so they can
        # all be analysed independently, in any order, in parallel.
        board = chess.Board()
        boards = [board.copy(stack=False)]
        moves = []
        for uci in moves_uci:
            mv = chess.Move.from_uci(uci)
            moves.append(mv)
            board.push(mv)
            boards.append(board.copy(stack=False))
        num_positions = len(boards)

        num_workers, threads_per_engine = self._analysis_worker_plan(num_positions)
        self.results.put({"kind": "analysis_meta", "tag": tag, "workers": num_workers,
                           "threads_per_engine": threads_per_engine})

        results_by_index = [None] * num_positions
        done_lock = threading.Lock()
        done_count = 0
        start_time = time.time()

        def worker(worker_id, indices):
            nonlocal done_count
            time.sleep(worker_id * 0.15)  # stagger startup, see note above _analyze_game
            try:
                eng = chess.engine.SimpleEngine.popen_uci(self.path)
            except Exception:
                return
            try:
                try:
                    eng.configure({"Threads": threads_per_engine, "Hash": 256})
                except Exception:
                    pass
                for idx in indices:
                    if self._cancel_analysis:
                        return
                    results_by_index[idx] = self._analyse_position(eng, boards[idx], limit, deep_limit)
                    with done_lock:
                        done_count += 1
                        d = done_count
                    self.results.put({"kind": "analysis_progress", "tag": tag, "done": d, "total": num_positions,
                                       "elapsed": time.time() - start_time})
            finally:
                try:
                    eng.quit()
                except Exception:
                    pass

        # Round-robin the position list across workers so each engine gets
        # a mix of opening/middlegame/endgame positions rather than one
        # worker getting stuck with only the slower, more tactical stretch.
        buckets = [[] for _ in range(num_workers)]
        for i in range(num_positions):
            buckets[i % num_workers].append(i)

        threads = [threading.Thread(target=worker, args=(w, bucket), daemon=True)
                   for w, bucket in enumerate(buckets) if bucket]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if self._cancel_analysis:
            return

        # From here on it's pure CPU-light bookkeeping -- no more engine
        # calls -- assembling the classification pass sequentially exactly
        # as before, just reading pre-computed results instead of calling
        # the engine move-by-move.
        records = []
        for i, move in enumerate(moves):
            before = results_by_index[i]
            after = results_by_index[i + 1]
            if before is None or after is None:
                continue
            cp_before_w, second_before_w, best_move, mate_before_w = before
            cp_after_w, second_after_w, next_best_move, mate_after_w = after

            board_before = boards[i]
            board_after = boards[i + 1]
            mover = board_before.turn
            san = board_before.san(move)
            best_san = None
            if best_move is not None and best_move in board_before.legal_moves:
                best_san = board_before.san(best_move)

            before_mover = cp_before_w if mover == chess.WHITE else -cp_before_w
            after_mover = cp_after_w if mover == chess.WHITE else -cp_after_w
            second_mover = second_before_w if mover == chess.WHITE else -second_before_w
            mate_before_mover = mate_before_w if mover == chess.WHITE else (None if mate_before_w is None else -mate_before_w)
            # `mate_after_w` is from the position after the move, i.e. from the
            # side-to-move-next's (opponent's) own analysis call; flip it into
            # the mover's POV the same way the cp scores are flipped above.
            mate_after_mover = mate_after_w if mover == chess.WHITE else (None if mate_after_w is None else -mate_after_w)

            loss = max(0, min(1500, before_mover - after_mover))
            is_top = (best_move is not None and move == best_move)

            cls, facts = classify_move(before_mover, after_mover, second_mover, is_top,
                                        mate_before_mover, mate_after_mover, move, mover, board_before, board_after)
            acc = move_accuracy_percent(before_mover, after_mover)

            records.append({
                "ply": i,
                "color": mover,
                "san": san,
                "uci": move.uci(),
                "best_uci": best_move.uci() if best_move else None,
                "best_san": best_san,
                "loss": loss,
                "wp_loss": facts["wp_loss"],
                "wp_gap": facts["wp_gap"],
                "sac": facts.get("sac", False),
                "miss_type": facts.get("miss_type"),
                "classification": cls,
                "accuracy": acc,
                "cp_before_white": cp_before_w,
                "cp_after_white": cp_after_w,
            })

        stats = {}
        for color in (chess.WHITE, chess.BLACK):
            counts = {k: 0 for k in CLASS_KEYS}
            acc_sum, n = 0.0, 0
            for r in records:
                if r["color"] == color:
                    counts[r["classification"]] += 1
                    acc_sum += r["accuracy"]
                    n += 1
            stats[color] = {
                "counts": counts,
                "accuracy": (acc_sum / n) if n else 100.0,
                "n": n,
            }

        self.results.put({"kind": "analysis_done", "tag": tag, "records": records, "stats": stats})

    def submit(self, job):
        self.jobs.put(job)

    def cancel_analysis(self):
        self._cancel_analysis = True

    def stop(self):
        self._stop_flag = True
        self._cancel_analysis = True
        self.jobs.put(None)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def lerp(a, b, t):
    return a + (b - a) * t


def smooth_circle(surface, color, center, radius, width=0):
    """Anti-aliased circle (filled or outlined), avoids pygame.draw.circle's
    jagged edges."""
    x, y = int(round(center[0])), int(round(center[1]))
    r = max(1, int(round(radius)))
    if width <= 0:
        pygame.gfxdraw.filled_circle(surface, x, y, r, color)
        pygame.gfxdraw.aacircle(surface, x, y, r, color)
    else:
        for i in range(max(1, int(width))):
            pygame.gfxdraw.aacircle(surface, x, y, max(1, r - i), color)


def draw_arrow(surface, color, start, end, width=8):
    """Anti-aliased arrow from start to end, used for best-move hints."""
    sx, sy = start
    ex, ey = end
    dx, dy = ex - sx, ey - sy
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    px, py = -uy, ux

    head_len = min(22, length * 0.4)
    head_w = width * 2.1
    shaft_end_x = ex - ux * head_len * 0.85
    shaft_end_y = ey - uy * head_len * 0.85

    overlay = pygame.Surface(surface.get_size(), pygame.SRCALPHA)

    half = width / 2
    shaft_poly = [
        (sx + px * half, sy + py * half),
        (shaft_end_x + px * half, shaft_end_y + py * half),
        (shaft_end_x - px * half, shaft_end_y - py * half),
        (sx - px * half, sy - py * half),
    ]
    pygame.gfxdraw.filled_polygon(overlay, shaft_poly, color)
    pygame.gfxdraw.aapolygon(overlay, shaft_poly, color)

    tip_poly = [
        (ex, ey),
        (shaft_end_x + px * head_w, shaft_end_y + py * head_w),
        (shaft_end_x - px * head_w, shaft_end_y - py * head_w),
    ]
    pygame.gfxdraw.filled_polygon(overlay, tip_poly, color)
    pygame.gfxdraw.aapolygon(overlay, tip_poly, color)

    surface.blit(overlay, (0, 0))


def draw_star(surface, color, center, radius, points=5, inner_ratio=0.42, rotation=-90):
    """Filled anti-aliased N-pointed star, used for the 'Best' move icon."""
    cx, cy = center
    verts = []
    for i in range(points * 2):
        ang = math.radians(rotation + i * (360 / (points * 2)))
        r = radius if i % 2 == 0 else radius * inner_ratio
        verts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    pygame.gfxdraw.filled_polygon(surface, verts, color)
    pygame.gfxdraw.aapolygon(surface, verts, color)


def draw_sparkle(surface, color, center, radius):
    """Four-pointed sparkle / diamond-burst, used for the 'Brilliant' icon —
    visually distinct from the 5-point 'Best' star."""
    draw_star(surface, color, center, radius, points=4, inner_ratio=0.32, rotation=-90)


def draw_check_mark(surface, color, center, radius, width=None):
    """Thick anti-aliased checkmark, used for the 'Good' move icon."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.34)
    p1 = (cx - radius * 0.55, cy + radius * 0.05)
    p2 = (cx - radius * 0.12, cy + radius * 0.5)
    p3 = (cx + radius * 0.62, cy - radius * 0.5)
    _draw_thick_polyline(surface, color, [p1, p2, p3], width)


def draw_x_mark(surface, color, center, radius, width=None):
    """Thick anti-aliased X, used for the 'Blunder' icon."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.3)
    d = radius * 0.62
    _draw_thick_polyline(surface, color, [(cx - d, cy - d), (cx + d, cy + d)], width)
    _draw_thick_polyline(surface, color, [(cx + d, cy - d), (cx - d, cy + d)], width)


def draw_chevron_up(surface, color, center, radius, width=None):
    """Single bold upward chevron, used for the 'Great' move icon."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.3)
    p1 = (cx - radius * 0.58, cy + radius * 0.32)
    p2 = (cx, cy - radius * 0.5)
    p3 = (cx + radius * 0.58, cy + radius * 0.32)
    _draw_thick_polyline(surface, color, [p1, p2, p3], width)


def draw_chevron_down(surface, color, center, radius, width=None):
    """Single bold downward chevron (mirror of the 'Great' icon), used for
    the 'Miss' move icon -- a visual counterpoint to Great's upward
    chevron, since a Miss is exactly a Great-caliber chance not taken."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.3)
    p1 = (cx - radius * 0.58, cy - radius * 0.32)
    p2 = (cx, cy + radius * 0.5)
    p3 = (cx + radius * 0.58, cy - radius * 0.32)
    _draw_thick_polyline(surface, color, [p1, p2, p3], width)


def draw_double_check_mark(surface, color, center, radius, width=None):
    """Two overlapping checkmarks, used for the 'Excellent' icon -- reads
    as a stronger tier of the single-check 'Good' icon."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.3)
    offset = radius * 0.28
    draw_check_mark(surface, color, (cx - offset, cy), radius * 0.85, width)
    draw_check_mark(surface, color, (cx + offset, cy), radius * 0.85, width)


def draw_bang_mark(surface, color, center, radius, width=None):
    """Vector exclamation mark (bar + dot), used for 'Inaccuracy'/'Mistake'."""
    cx, cy = center
    w = width if width is not None else max(2, radius * 0.26)
    bar_top = cy - radius * 0.55
    bar_bottom = cy + radius * 0.12
    pygame.draw.line(surface, color, (cx, bar_top), (cx, bar_bottom), int(round(w)))
    smooth_circle(surface, color, (cx, cy + radius * 0.48), max(1, w * 0.55))


def draw_double_bang_mark(surface, color, center, radius, width=None):
    """Two vector exclamation marks side by side, used for 'Blunder'."""
    cx, cy = center
    offset = radius * 0.34
    draw_bang_mark(surface, color, (cx - offset, cy), radius * 0.82, width)
    draw_bang_mark(surface, color, (cx + offset, cy), radius * 0.82, width)


def draw_question_mark(surface, color, center, radius, width=None):
    """Vector question mark (hook + dot), used for the 'Mistake' icon."""
    cx, cy = center
    w = width if width is not None else max(2, radius * 0.24)
    hook_rect = pygame.Rect(0, 0, radius * 1.05, radius * 1.05)
    hook_rect.center = (cx, cy - radius * 0.22)
    pygame.draw.arc(surface, color, hook_rect, math.radians(-40), math.radians(230), int(round(w)))
    stem_top = cy + radius * 0.12
    stem_bottom = cy + radius * 0.32
    pygame.draw.line(surface, color, (cx, stem_top), (cx, stem_bottom), int(round(w)))
    smooth_circle(surface, color, (cx, cy + radius * 0.62), max(1, w * 0.55))


def _draw_thick_polyline(surface, color, points, width):
    """Anti-aliased thick polyline built from filled quads + round joints,
    since pygame's built-in thick lines are not anti-aliased and look jagged
    at small icon sizes."""
    half = width / 2
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 0.001:
            continue
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        quad = [
            (x1 + px * half, y1 + py * half),
            (x2 + px * half, y2 + py * half),
            (x2 - px * half, y2 - py * half),
            (x1 - px * half, y1 - py * half),
        ]
        pygame.gfxdraw.filled_polygon(surface, quad, color)
        pygame.gfxdraw.aapolygon(surface, quad, color)
    for x, y in points:
        smooth_circle(surface, color, (x, y), half)


CLASS_ICON_DRAW = {
    "brilliant":  draw_sparkle,
    "great":      draw_chevron_up,
    "best":       draw_star,
    "excellent":  draw_double_check_mark,
    "good":       draw_check_mark,
    "inaccuracy": draw_bang_mark,
    "mistake":    draw_question_mark,
    "blunder":    draw_x_mark,
    "miss":       draw_chevron_down,
}


def draw_class_badge(surface, key, center, radius):
    """Draws the colored circle + real vector icon for a move classification
    (replaces the old text-glyph badges, which relied on Unicode glyphs like
    ★ / ✓ that many fonts render as empty 'tofu' boxes)."""
    meta = CLASS_META[key]
    smooth_circle(surface, meta["color"], center, radius)
    icon_fn = CLASS_ICON_DRAW.get(key, draw_bang_mark)
    icon_fn(surface, TEXT_LIGHT, center, radius * 0.62)


def draw_triangle_icon(surface, color, center, radius, direction="right"):
    """Small filled triangle pointer, used in place of unicode arrow glyphs
    (e.g. the old '\u2190 \u2192' / '\u25b6' hint text, which showed as empty
    boxes on fonts lacking those glyphs)."""
    cx, cy = center
    r = radius
    if direction == "right":
        pts = [(cx - r * 0.55, cy - r), (cx - r * 0.55, cy + r), (cx + r * 0.75, cy)]
    else:
        pts = [(cx + r * 0.55, cy - r), (cx + r * 0.55, cy + r), (cx - r * 0.75, cy)]
    pygame.gfxdraw.filled_polygon(surface, pts, color)
    pygame.gfxdraw.aapolygon(surface, pts, color)


def draw_flip_icon(surface, color, center, radius, width=None):
    """Two opposing chevrons (one up, one down) -- reads as 'flip
    vertically', used for the board-orientation toggle."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.26)
    off = radius * 0.5
    top = [(cx - radius * 0.62, cy - off + radius * 0.32), (cx, cy - off - radius * 0.4), (cx + radius * 0.62, cy - off + radius * 0.32)]
    bottom = [(cx - radius * 0.62, cy + off - radius * 0.32), (cx, cy + off + radius * 0.4), (cx + radius * 0.62, cy + off - radius * 0.32)]
    _draw_thick_polyline(surface, color, top, width)
    _draw_thick_polyline(surface, color, bottom, width)


def draw_sun_icon(surface, color, center, radius, width=None):
    """Simple sun glyph (circle + rays) used on the light-mode side of the
    theme toggle."""
    cx, cy = center
    width = width if width is not None else max(2, radius * 0.16)
    smooth_circle(surface, color, center, radius * 0.42, width=width)
    for i in range(8):
        ang = i * (math.pi / 4)
        x1 = cx + math.cos(ang) * radius * 0.62
        y1 = cy + math.sin(ang) * radius * 0.62
        x2 = cx + math.cos(ang) * radius * 0.95
        y2 = cy + math.sin(ang) * radius * 0.95
        pygame.draw.line(surface, color, (x1, y1), (x2, y2), max(1, int(width)))


def draw_moon_icon(surface, color, center, radius, width=None):
    """Simple crescent-moon glyph used on the dark-mode side of the theme
    toggle. Drawn as a filled circle with a smaller offset circle 'cut out'
    of it via alpha-subtract blending, so it reads as a solid crescent
    instead of an outline."""
    cx, cy = center
    dim = int(radius * 2.4)
    big = pygame.Surface((dim, dim), pygame.SRCALPHA)
    bcx, bcy = dim // 2, dim // 2
    smooth_circle(big, color, (bcx, bcy), radius * 0.85)
    mask = pygame.Surface((dim, dim), pygame.SRCALPHA)
    smooth_circle(mask, (255, 255, 255, 255), (bcx + radius * 0.42, bcy - radius * 0.28), radius * 0.72)
    big.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_SUB)
    rect = big.get_rect(center=(cx, cy))
    surface.blit(big, rect)


def captured_piece_for_move(board, move):
    """Return the captured chess.Piece (or None) for a move, BEFORE it is
    pushed onto the board. Handles en-passant correctly."""
    if board.is_en_passant(move):
        cap_square = chess.square(chess.square_file(move.to_square), chess.square_rank(move.from_square))
        return board.piece_at(cap_square)
    if board.is_capture(move):
        return board.piece_at(move.to_square)
    return None


def termination_text(board):
    outcome = board.outcome(claim_draw=True)
    if outcome is None:
        return "Game over."
    if outcome.winner is True:
        return "Checkmate — you win!"
    if outcome.winner is False:
        return "Checkmate — the engine wins."
    term = outcome.termination
    names = {
        chess.Termination.STALEMATE: "Draw by stalemate.",
        chess.Termination.INSUFFICIENT_MATERIAL: "Draw — insufficient material.",
        chess.Termination.FIVEFOLD_REPETITION: "Draw by repetition.",
        chess.Termination.THREEFOLD_REPETITION: "Draw by repetition.",
        chess.Termination.SEVENTYFIVE_MOVES: "Draw — 75-move rule.",
        chess.Termination.FIFTY_MOVES: "Draw — 50-move rule.",
    }
    return names.get(term, "Draw.")


# --------------------------------------------------------------------------
# Layout — computed dynamically from the actual window size so the board
# renders crisply at whatever resolution the screen provides.
# --------------------------------------------------------------------------

class Layout:
    def __init__(self, window_w, window_h):
        self.window_w = window_w
        self.window_h = window_h

        margin = max(28, int(window_w * 0.022))
        gap = max(20, int(window_w * 0.014))
        panel_w = int(clamp(window_w * 0.275, 360, 480))

        eval_gap = 14
        eval_w = max(18, int(window_w * 0.011))

        top_block = 108
        bottom_block = 168

        panel_x = window_w - margin - panel_w
        avail_w = panel_x - gap - margin - eval_gap - eval_w
        avail_h = window_h - margin * 2 - top_block - bottom_block

        square = int(min(avail_w, avail_h) // 8)
        square = clamp(square, 44, 140)
        board_size = square * 8

        total_block_h = top_block + board_size + bottom_block
        start_y = margin + max(0, (window_h - margin * 2 - total_block_h) // 2)

        board_x = margin + max(0, (avail_w - board_size) // 2)
        board_y = start_y + top_block

        self.margin = margin
        self.square = square
        self.board_x = board_x
        self.board_y = board_y
        self.board_size = board_size
        self.board_bottom = board_y + board_size
        self.board_right = board_x + board_size

        self.eval_x = self.board_right + eval_gap
        self.eval_w = eval_w
        self.eval_y = board_y
        self.eval_h = board_size

        self.label_top_y = board_y - 78
        self.captured_top_y = board_y - 42
        self.captured_bottom_y = self.board_bottom + 30
        self.label_bottom_y = self.board_bottom + 62
        self.controls_y = self.board_bottom + 108
        self.hint_y = self.controls_y + 38

        self.panel_x = panel_x
        self.panel_w = panel_w
        self.panel_y = start_y
        self.panel_h = total_block_h
        self.gap = gap

        # Whether the board is shown from Black's point of view. This is a
        # pure viewing preference (you still always play White) so it lives
        # on the layout rather than the game state, and survives resizes.
        self.flipped = False

    def square_to_px(self, square):
        file_ = chess.square_file(square)
        rank = chess.square_rank(square)
        if self.flipped:
            col = 7 - file_
            row = rank
        else:
            col = file_
            row = 7 - rank
        x = self.board_x + col * self.square + self.square // 2
        y = self.board_y + row * self.square + self.square // 2
        return x, y

    def px_to_square(self, x, y):
        if x < self.board_x or y < self.board_y or x >= self.board_right or y >= self.board_bottom:
            return None
        col = (x - self.board_x) // self.square
        row = (y - self.board_y) // self.square
        if self.flipped:
            file_ = 7 - col
            rank = row
        else:
            file_ = col
            rank = 7 - row
        return chess.square(int(file_), int(rank))


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

MENU, PLAYING = "menu", "playing"


class ChessApp:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Minimal Chess")

        settings = load_settings()
        apply_theme(settings.get("theme", "light"))
        self.dark_mode = CURRENT_THEME_NAME == "dark"

        self.windowed_size = (1440, 900)
        self.fullscreen = os.environ.get("CHESS_WINDOWED") != "1"
        self._create_window()

        self.clock = pygame.time.Clock()

        # Every one of these maps onto the shared FONT_SCALE (see the design
        # system block near the top of the file) rather than picking its own
        # pixel size -- that's what keeps text sizing consistent screen to
        # screen. The two "piece_*" fonts are the deliberate exception: they
        # render chess-piece glyphs, not reading text, so they're sized
        # relative to the board/square instead of the type scale.
        self.font_title = self._load_font(FONT_SCALE["hero"], bold=True)
        self.font_sub = self._load_font(FONT_SCALE["md"])
        self.font_label = self._load_font(FONT_SCALE["lg"], bold=True)
        self.font_small = self._load_font(FONT_SCALE["sm"])
        self.font_tiny = self._load_font(FONT_SCALE["sm"])
        self.font_button = self._load_font(FONT_SCALE["lg"], bold=True)
        self.font_piece_lg = self._load_font(30, bold=True)
        self.font_piece_sm = self._load_font(15, bold=True)
        self.font_eval = self._load_font(FONT_SCALE["sm"], bold=True)
        self.font_panel_title = self._load_font(FONT_SCALE["lg"], bold=True)
        self.font_move = self._load_font(FONT_SCALE["md"])
        self.font_accuracy = self._load_font(FONT_SCALE["xl"], bold=True)
        self.font_coord = self._load_font(FONT_SCALE["sm"])
        self.font_banner = self._load_font(FONT_SCALE["lg"], bold=True)
        self.font_chart_label = self._load_font(FONT_SCALE["sm"])
        self.font_card_title = self._load_font(FONT_SCALE["md"], bold=True)
        self.font_meter = self._load_font(FONT_SCALE["sm"], bold=True)

        self.piece_images = {}
        self.piece_image_cache = {}
        self._icon_cache = {}
        self.images_available = False
        self._load_piece_images()

        self.state = MENU
        self.difficulty_index = 2

        self.stockfish_path = find_stockfish()
        self.engine_thread = None
        if self.stockfish_path:
            self.engine_thread = EngineThread(self.stockfish_path)
            self.engine_thread.start()

        self.reset_game()

        self.running = True

    # --------------------------------------------------------------- fonts

    # A handful of high-quality, widely-installed system fonts (checked in
    # priority order per-platform) used instead of pygame's bundled default
    # font. The bundled default renders acceptably at large sizes but turns
    # muddy/hard-to-read at the smaller sizes this UI uses, and it is missing
    # glyphs for some punctuation. A real system font with proper hinting
    # fixes both problems and is bundled on essentially every desktop OS.
    _FONT_CANDIDATES = [
        "Segoe UI", "SegoeUI", "Helvetica Neue", "HelveticaNeue", "Helvetica",
        "Arial", "Noto Sans", "NotoSans", "DejaVu Sans", "DejaVuSans",
        "Liberation Sans", "LiberationSans", "Verdana", "Tahoma",
    ]
    _font_path_cache = {}

    def _load_font(self, size, bold=False):
        cache_key = bold
        path = self._font_path_cache.get(cache_key, "unset")
        if path == "unset":
            path = None
            for name in self._FONT_CANDIDATES:
                try:
                    found = pygame.font.match_font(name, bold=bold)
                except Exception:
                    found = None
                if found:
                    path = found
                    break
            self._font_path_cache[cache_key] = path
        try:
            if path:
                font = pygame.font.Font(path, size)
            else:
                font = pygame.font.Font(None, size)
                if bold:
                    font.set_bold(True)
        except Exception:
            font = pygame.font.Font(None, size)
        # Smooth hinting: pygame/SDL_ttf renders anti-aliased already via the
        # render(..., True, ...) calls used throughout, but bold synthesis on
        # a system font (when no real bold face was found) needs this flag.
        return font

    # -------------------------------------------------------------- window

    def _create_window(self):
        # NOTE: deliberately NOT using pygame.SCALED here. SCALED renders to a
        # lower-resolution logical surface and stretches it to fill the real
        # display -- that upscale blur is what was making all text and icons
        # in the app look low-quality/overlapping on HiDPI screens. Rendering
        # directly at the native pixel resolution keeps every font glyph and
        # vector icon crisp.
        if self.fullscreen:
            info = pygame.display.Info()
            w, h = info.current_w, info.current_h
            try:
                self.screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN)
            except pygame.error:
                self.fullscreen = False
                w, h = self.windowed_size
                self.screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
        else:
            w, h = self.windowed_size
            self.screen = pygame.display.set_mode((w, h), pygame.RESIZABLE)
        self.window_w, self.window_h = self.screen.get_size()
        flipped = getattr(getattr(self, "layout", None), "flipped", False)
        self.layout = Layout(self.window_w, self.window_h)
        self.layout.flipped = flipped

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self._create_window()

    def handle_resize(self, w, h):
        if not self.fullscreen:
            self.windowed_size = (w, h)
            self.window_w, self.window_h = w, h
            flipped = getattr(self.layout, "flipped", False)
            self.layout = Layout(w, h)
            self.layout.flipped = flipped

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        apply_theme("dark" if self.dark_mode else "light")
        save_settings({"theme": "dark" if self.dark_mode else "light"})

    def theme_toggle_rect(self):
        size = 40
        return pygame.Rect(self.window_w - 24 - size, 24, size, size)

    def toggle_board_flip(self):
        self.layout.flipped = not getattr(self.layout, "flipped", False)

    # ---------------------------------------------------------------- game

    def reset_game(self):
        self.board = chess.Board()
        self.selected_square = None
        self.legal_targets = []
        self.awaiting_review_choice = False
        self._slider_dragging = False
        self.captured_by_white = []  # black pieces White has taken
        self.captured_by_black = []  # white pieces Black has taken
        self.history = [{"fen": self.board.fen(), "cw": [], "cb": [], "last_move": None}]
        self.history_index = 0

        self.animating = False
        self.anim_move = None
        self.anim_start = 0.0
        self.anim_is_engine = False

        self.pending_promotion = None  # (from_sq, to_sq)
        self.pending_engine = None     # dict with move/ready_time
        self.awaiting_engine = False
        self.engine_request_time = 0.0

        self.eval_cp = 0
        self.eval_mate = None
        self._history_eval_cache = {}
        self._eval_pending_index = None

        self.show_resign_confirm = False
        self.game_over = False
        self.game_over_text = ""

        self.last_move_squares = None

        self.panel_scroll = 0
        self.panel_scroll_review = 0
        self._panel_click_targets = []

        self.analysis = None
        self.analysis_pending = False
        self.analysis_progress = (0, 0)
        self.analysis_elapsed = 0.0
        self.analysis_workers = 1
        self.analysis_threads_per_engine = 1

        # Post-game "how deep should the review be" prompt -- shown once
        # the game ends, before any analysis is submitted to the engine.
        self.awaiting_review_choice = False
        self.review_speed_index = 1  # default to Medium
        self._slider_dragging = False
        self._review_snap_xs = None
        self._review_slider_hit_rect = None
        self._review_start_btn_rect = None

        self._graph_rect = None
        self._graph_n = 0

    # ------------------------------------------------------------- artwork

    def _load_piece_images(self):
        """Load the 12 base piece images (one per piece/color) if present.
        Never raises — if anything goes wrong we just fall back to simple
        drawn pieces so the app can never crash over missing artwork."""
        pieces_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pieces")
        images = {}
        try:
            for piece_type, letter in PIECE_LETTERS.items():
                for color_is_white in (True, False):
                    prefix = "w" if color_is_white else "b"
                    path = os.path.join(pieces_dir, f"{prefix}{letter}.png")
                    surf = pygame.image.load(path).convert_alpha()
                    images[(piece_type, color_is_white)] = surf
            self.piece_images = images
            self.images_available = True
        except Exception:
            self.piece_images = {}
            self.images_available = False

    def get_piece_surface(self, piece_type, color_is_white, size):
        size = max(1, int(size))
        key = (piece_type, color_is_white, size)
        cached = self.piece_image_cache.get(key)
        if cached is not None:
            return cached
        base = self.piece_images.get((piece_type, color_is_white))
        if base is None:
            return None
        scaled = pygame.transform.smoothscale(base, (size, size))
        self.piece_image_cache[key] = scaled
        return scaled

    # ----------------------------------------------------------- icon cache
    #
    # Small vector icons (move-rating badges, nav arrows) looked weak/muddy
    # when drawn directly at their final tiny on-screen size -- even with
    # anti-aliased primitives, a handful of pixels just isn't enough data
    # for a clean edge. The fix used by essentially every professional app:
    # rasterize the icon at a much higher resolution (supersampling) and
    # downscale with a high-quality filter, which produces a genuinely
    # crisp result no matter how small the final icon is. Results are
    # cached since the geometry never changes -- this costs real CPU only
    # once per (icon, size) combination, then it's a cheap blit forever
    # after, so it stays smooth even on modest hardware.

    ICON_SUPERSAMPLE = 8

    def _get_cached_icon(self, cache_key, size_px, draw_fn):
        size_px = max(2, int(round(size_px)))
        key = (cache_key, size_px)
        surf = self._icon_cache.get(key)
        if surf is not None:
            return surf
        big = size_px * self.ICON_SUPERSAMPLE
        tmp = pygame.Surface((big, big), pygame.SRCALPHA)
        draw_fn(tmp, big)
        surf = pygame.transform.smoothscale(tmp, (size_px, size_px))
        self._icon_cache[key] = surf
        return surf

    def get_class_badge_surface(self, key, diameter):
        def _draw(tmp, big):
            r = big / 2
            smooth_circle(tmp, CLASS_META[key]["color"], (r, r), r * 0.98)
            icon_fn = CLASS_ICON_DRAW.get(key, draw_bang_mark)
            icon_fn(tmp, TEXT_LIGHT, (r, r), r * 0.62)
        return self._get_cached_icon(("badge", key), diameter, _draw)

    def draw_class_badge_at(self, key, center, diameter):
        surf = self.get_class_badge_surface(key, diameter)
        self.screen.blit(surf, surf.get_rect(center=center))

    def get_triangle_icon_surface(self, direction, diameter, color):
        def _draw(tmp, big):
            r = big / 2
            draw_triangle_icon(tmp, color, (r, r), r * 0.8, direction)
        return self._get_cached_icon(("tri", direction, color), diameter, _draw)

    def draw_triangle_icon_at(self, direction, center, diameter, color):
        surf = self.get_triangle_icon_surface(direction, diameter, color)
        self.screen.blit(surf, surf.get_rect(center=center))

    def start_game(self):
        if self.engine_thread is None or self.engine_thread.error:
            return
        diff = DIFFICULTIES[self.difficulty_index]
        options = {}
        if diff["elo"] is None:
            options["UCI_LimitStrength"] = False
        else:
            options["UCI_LimitStrength"] = True
            options["UCI_Elo"] = max(1320, min(3190, diff["elo"]))
        if self.engine_thread.engine is not None and "Skill Level" in self.engine_thread.engine.options \
                and "UCI_Elo" not in self.engine_thread.engine.options:
            if diff["elo"] is None:
                options = {"Skill Level": 20}
            else:
                skill = max(0, min(20, round((diff["elo"] - 800) / 1600 * 20)))
                options = {"Skill Level": skill}

        self.engine_thread.submit({"kind": "configure", "options": options})
        self.reset_game()
        self.state = PLAYING
        self.engine_thread.submit({"kind": "eval", "tag": "init", "fen": self.board.fen(), "movetime": 0.15})

    def current_movetime(self):
        return DIFFICULTIES[self.difficulty_index]["time"]

    # ------------------------------------------------------------- moving

    def begin_move(self, move, is_engine):
        self.animating = True
        self.anim_move = move
        self.anim_start = time.time()
        self.anim_is_engine = is_engine
        self.selected_square = None
        self.legal_targets = []

    def finish_animation(self):
        move = self.anim_move
        captured = captured_piece_for_move(self.board, move)
        self.board.push(move)

        if captured is not None:
            if captured.color == chess.WHITE:
                self.captured_by_black.append(captured.piece_type)
            else:
                self.captured_by_white.append(captured.piece_type)

        self.history.append({
            "fen": self.board.fen(),
            "cw": list(self.captured_by_white),
            "cb": list(self.captured_by_black),
            "last_move": move.uci(),
        })
        self.history_index = len(self.history) - 1
        self.last_move_squares = (move.from_square, move.to_square)

        was_engine = self.anim_is_engine
        self.animating = False
        self.anim_move = None

        if self.board.is_game_over(claim_draw=True):
            self.game_over_text = termination_text(self.board)
            self.end_game()
            return

        if not was_engine and self.board.turn == chess.BLACK:
            self.start_engine_turn()

    def start_engine_turn(self):
        self.awaiting_engine = True
        self.engine_request_time = time.time()
        self._eval_pending_index = len(self.history) - 1
        self.engine_thread.submit({
            "kind": "bestmove",
            "tag": "turn",
            "fen": self.board.fen(),
            "movetime": self.current_movetime(),
        })

    def try_select_or_move(self, square):
        if square is None:
            return
        if self.history_index != len(self.history) - 1:
            return  # viewing history — read only
        board = self.board

        if self.selected_square is None:
            piece = board.piece_at(square)
            if piece is not None and piece.color == chess.WHITE:
                self.selected_square = square
                self.legal_targets = [m.to_square for m in board.legal_moves if m.from_square == square]
            return

        if square == self.selected_square:
            self.selected_square = None
            self.legal_targets = []
            return

        piece = board.piece_at(square)
        if piece is not None and piece.color == chess.WHITE:
            self.selected_square = square
            self.legal_targets = [m.to_square for m in board.legal_moves if m.from_square == square]
            return

        promo_moves = [m for m in board.legal_moves
                        if m.from_square == self.selected_square and m.to_square == square and m.promotion]
        if promo_moves:
            self.pending_promotion = (self.selected_square, square)
            self.selected_square = None
            self.legal_targets = []
            return

        plain = chess.Move(self.selected_square, square)
        if plain in board.legal_moves:
            self.begin_move(plain, is_engine=False)
        else:
            self.selected_square = None
            self.legal_targets = []

    def complete_promotion(self, piece_type):
        if not self.pending_promotion:
            return
        frm, to = self.pending_promotion
        move = chess.Move(frm, to, promotion=piece_type)
        self.pending_promotion = None
        if move in self.board.legal_moves:
            self.begin_move(move, is_engine=False)

    def resign(self):
        self.show_resign_confirm = False
        self.game_over_text = "You resigned."
        self.end_game()

    def take_back(self):
        """Undoes the player's last move (and the engine's reply to it, if
        it already replied), so the player can try something else. Always
        operates on the real/live game position, regardless of which move
        is currently being viewed in the history -- and returns the view
        to the new, shorter live position."""
        if self.game_over or self.animating or self.awaiting_engine or self.pending_engine:
            return
        if not self.board.move_stack:
            return

        # If it's currently White's turn, the engine has already replied to
        # the player's last move, so undo both plies. Otherwise (mid-flow,
        # shouldn't normally be reachable given the guards above, but this
        # keeps the function correct on its own) only the player's move
        # needs undoing.
        moves_to_undo = 2 if self.board.turn == chess.WHITE else 1
        moves_to_undo = min(moves_to_undo, len(self.board.move_stack))
        for _ in range(moves_to_undo):
            self.board.pop()

        new_len = len(self.board.move_stack) + 1
        self.history = self.history[:new_len]
        self.history_index = new_len - 1

        last_state = self.history[-1]
        self.captured_by_white = list(last_state["cw"])
        self.captured_by_black = list(last_state["cb"])
        self.last_move_squares = None
        if last_state["last_move"]:
            mv = chess.Move.from_uci(last_state["last_move"])
            self.last_move_squares = (mv.from_square, mv.to_square)

        self.selected_square = None
        self.legal_targets = []

        for k in list(self._history_eval_cache.keys()):
            if k >= new_len:
                del self._history_eval_cache[k]
        cached = self._history_eval_cache.get(self.history_index)
        if cached is not None:
            self.eval_cp, self.eval_mate = cached

    def end_game(self):
        self.game_over = True
        self.selected_square = None
        self.legal_targets = []
        self.panel_scroll_review = 0
        self.awaiting_review_choice = True

    def confirm_review_choice(self):
        """Called once the person picks a speed and presses Start -- kicks
        off the actual engine analysis at the chosen depth."""
        self.awaiting_review_choice = False
        self.start_analysis()

    def back_to_menu(self):
        if self.engine_thread is not None:
            self.engine_thread.cancel_analysis()
        self.state = MENU

    # ---------------------------------------------------------- analysis

    def start_analysis(self):
        if self.engine_thread is None or self.engine_thread.error:
            return
        if not self.board.move_stack:
            return
        preset = REVIEW_SPEEDS[self.review_speed_index]
        self.analysis = None
        self.analysis_pending = True
        self.analysis_progress = (0, len(self.board.move_stack))
        self.analysis_elapsed = 0.0
        self.engine_thread.submit({"kind": "configure", "options": {"UCI_LimitStrength": False}})
        moves_uci = [m.uci() for m in self.board.move_stack]
        self.engine_thread.submit({
            "kind": "analyze_game",
            "tag": "analysis",
            "moves": moves_uci,
            "movetime": preset["movetime"],
            "deep_movetime": preset["deep_movetime"],
        })

    def review_ready(self):
        """True once a finished game's full analysis is available, at which
        point the side panel automatically shows the review layout instead
        of the live move list -- no manual tab needed."""
        return bool(self.analysis) and not self.analysis_pending

    def record_for_history_index(self, index):
        """The analysis record for the move that produced history[index]."""
        if not self.analysis or index <= 0:
            return None
        records = self.analysis["records"]
        if index - 1 >= len(records):
            return None
        return records[index - 1]

    def current_display_eval(self):
        """The (cp, mate) evaluation to show on the eval bar for whatever
        position is currently being VIEWED (self.history_index) -- not
        just the live game position. When a full post-game analysis is
        available it's used directly (exact for every position). During a
        live game we fall back to a per-position cache populated as real
        evaluations arrive, so browsing back through history mid-game is
        still as accurate as the data we actually have."""
        idx = self.history_index
        if self.analysis:
            records = self.analysis["records"]
            if idx == 0:
                return (records[0]["cp_before_white"], None) if records else (0, None)
            rec_idx = idx - 1
            if rec_idx < len(records):
                return (records[rec_idx]["cp_after_white"], None)

        cached = self._history_eval_cache.get(idx)
        if cached is not None:
            return cached
        if idx == len(self.history) - 1:
            return (self.eval_cp, self.eval_mate)
        for probe in range(idx - 1, -1, -1):
            if probe in self._history_eval_cache:
                return self._history_eval_cache[probe]
        return (self.eval_cp, self.eval_mate)

    # ------------------------------------------------------------- update

    def poll_engine(self):
        if self.engine_thread is None:
            return
        try:
            while True:
                res = self.engine_thread.results.get_nowait()
                kind = res["kind"]
                if kind == "eval":
                    self.eval_cp = res.get("cp") if res.get("cp") is not None else self.eval_cp
                    self.eval_mate = res.get("mate")
                    if res.get("tag") == "init" and res.get("cp") is not None:
                        self._history_eval_cache[0] = (res["cp"], res.get("mate"))
                elif kind == "bestmove":
                    if res.get("cp") is not None:
                        self.eval_cp = res["cp"]
                    self.eval_mate = res.get("mate")
                    if res.get("cp") is not None and self._eval_pending_index is not None:
                        self._history_eval_cache[self._eval_pending_index] = (res["cp"], res.get("mate"))
                    if res.get("move"):
                        ready_time = max(time.time(), self.engine_request_time + ENGINE_MOVE_DELAY)
                        self.pending_engine = {"move": res["move"], "ready_time": ready_time}
                    self.awaiting_engine = False
                elif kind == "analysis_meta":
                    self.analysis_workers = res.get("workers", 1)
                    self.analysis_threads_per_engine = res.get("threads_per_engine", 1)
                elif kind == "analysis_progress":
                    self.analysis_progress = (res["done"], res["total"])
                    self.analysis_elapsed = res.get("elapsed", self.analysis_elapsed)
                elif kind == "analysis_done":
                    self.analysis = {"records": res["records"], "stats": res["stats"]}
                    self.analysis_pending = False
                elif kind == "error":
                    self.awaiting_engine = False
                    self.analysis_pending = False
        except queue.Empty:
            pass

    def update(self):
        self.poll_engine()

        if self.game_over:
            return

        if self.animating:
            if time.time() - self.anim_start >= ANIM_DURATION:
                self.finish_animation()
            return

        if self.pending_engine and time.time() >= self.pending_engine["ready_time"]:
            uci = self.pending_engine["move"]
            self.pending_engine = None
            try:
                move = chess.Move.from_uci(uci)
                if move in self.board.legal_moves:
                    self.begin_move(move, is_engine=True)
            except Exception:
                pass

    # -------------------------------------------------------------- input

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.handle_keydown(event)
            elif event.type == pygame.VIDEORESIZE:
                self.handle_resize(event.w, event.h)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.handle_click(event.pos)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self._slider_dragging = False
            elif event.type == pygame.MOUSEMOTION:
                if self._slider_dragging:
                    self.update_review_slider_from_x(event.pos[0])
            elif event.type == pygame.MOUSEWHEEL:
                self.handle_wheel(event.y)

    def handle_keydown(self, event):
        if event.key == pygame.K_ESCAPE:
            self.running = False
        elif event.key == pygame.K_F11:
            self.toggle_fullscreen()
        elif self.state == PLAYING and not self.animating and not self.show_resign_confirm \
                and not self.pending_promotion:
            if event.key == pygame.K_LEFT and self.history_index > 0:
                self.history_index -= 1
            elif event.key == pygame.K_RIGHT and self.history_index < len(self.history) - 1:
                self.history_index += 1

    def handle_wheel(self, dy):
        if self.state != PLAYING:
            return
        layout = self.layout
        mouse = pygame.mouse.get_pos()
        panel_rect = pygame.Rect(layout.panel_x, layout.panel_y, layout.panel_w, layout.panel_h)
        if not panel_rect.collidepoint(mouse):
            return
        if self.awaiting_review_choice:
            return
        if not self.review_ready():
            self.panel_scroll = max(0, self.panel_scroll - dy * 32)
        else:
            self.panel_scroll_review = max(0, self.panel_scroll_review - dy * 32)

    def handle_click(self, pos):
        if self.theme_toggle_rect().collidepoint(pos):
            self.toggle_theme()
            return
        if self.state == MENU:
            self.handle_menu_click(pos)
        elif self.state == PLAYING:
            if self.show_resign_confirm:
                self.handle_resign_confirm_click(pos)
            elif self.pending_promotion:
                self.handle_promotion_click(pos)
            else:
                self.handle_playing_click(pos)

    def update_review_slider_from_x(self, x):
        """Snap the review-speed slider to whichever of the 3 fixed
        positions (fast/medium/deep) is closest to the given x -- there is
        no free/continuous position, only the 3 snap points."""
        xs = self._review_snap_xs
        if not xs:
            return
        idx = min(range(len(xs)), key=lambda i: abs(xs[i] - x))
        self.review_speed_index = idx

    def handle_review_choice_click(self, pos):
        if self._review_start_btn_rect and self._review_start_btn_rect.collidepoint(pos):
            self.confirm_review_choice()
            return
        hit = self._review_slider_hit_rect
        if hit and hit.collidepoint(pos):
            self._slider_dragging = True
            self.update_review_slider_from_x(pos[0])

    def handle_menu_click(self, pos):
        layout = self.menu_layout()
        for i, rect in enumerate(layout["diff_rects"]):
            if rect.collidepoint(pos):
                self.difficulty_index = i
                return
        if layout["start_rect"].collidepoint(pos):
            self.start_game()

    def handle_playing_click(self, pos):
        if self.awaiting_review_choice:
            self.handle_review_choice_click(pos)
            return
        if getattr(self, "_flip_btn_rect", None) and self._flip_btn_rect.collidepoint(pos):
            self.toggle_board_flip()
            return
        controls = self.controls_layout()
        if controls["back"].collidepoint(pos):
            if self.history_index > 0:
                self.history_index -= 1
            return
        if controls["forward"].collidepoint(pos):
            if self.history_index < len(self.history) - 1:
                self.history_index += 1
            return
        if controls["takeback"].collidepoint(pos):
            self.take_back()
            return
        if controls["action"].collidepoint(pos):
            if self.game_over:
                self.back_to_menu()
            elif not self.animating and not self.awaiting_engine and not self.pending_engine:
                self.show_resign_confirm = True
            return

        for rect, hist_idx in self._panel_click_targets:
            if rect.collidepoint(pos):
                self.history_index = hist_idx
                return

        if self._graph_rect is not None and self._graph_n and self._graph_rect.collidepoint(pos):
            t = clamp((pos[0] - self._graph_rect.left) / max(1, self._graph_rect.width), 0, 1)
            idx = round(t * (self._graph_n - 1))
            self.history_index = idx + 1
            return

        if self.game_over:
            return
        x, y = pos
        if self.animating or self.board.turn != chess.WHITE or self.awaiting_engine or self.pending_engine:
            return
        square = self.layout.px_to_square(x, y)
        self.try_select_or_move(square)

    def handle_promotion_click(self, pos):
        layout = self.promotion_layout()
        for piece_type, rect in layout["tiles"]:
            if rect.collidepoint(pos):
                self.complete_promotion(piece_type)
                return

    def handle_resign_confirm_click(self, pos):
        layout = self.resign_confirm_layout()
        if layout["yes"].collidepoint(pos):
            self.resign()
        elif layout["no"].collidepoint(pos):
            self.show_resign_confirm = False

    # -------------------------------------------------------------- layouts

    def _menu_geometry(self):
        """Single source of truth for every menu rect, shared by the click
        handler and the renderer so they can never drift out of sync."""
        content_w = int(clamp(self.window_w - 200, 700, 1040))
        content_x = (self.window_w - content_w) // 2

        title_h = self.font_title.get_height()
        sub_h = self.font_sub.get_height()
        title_y = int(self.window_h * 0.11)
        subtitle_y = title_y + title_h // 2 + SPACE["sm"] + sub_h // 2
        header_bottom = subtitle_y + sub_h // 2 + SPACE["lg"]

        col_gap = 32
        left_w = int(content_w * 0.60)
        right_w = content_w - left_w - col_gap
        left_x = content_x
        right_x = left_x + left_w + col_gap

        cards_top = max(header_bottom + 44, int(self.window_h * 0.30))

        pad = 22
        cols = 2
        grid_gap = 12
        btn_w = (left_w - pad * 2 - grid_gap * (cols - 1)) // cols

        # Card height is derived from what actually goes inside it (name +
        # elo line + strength meter, each separated by a standard SPACE
        # gap) rather than a fixed guess -- that's what guarantees the three
        # can't end up crowded/overlapping regardless of font size.
        card_pad = SPACE["sm"]
        name_h = self.font_button.get_height()
        sub_h = self.font_small.get_height()
        meter_h = 5
        btn_h = card_pad + name_h + SPACE["xs"] + sub_h + SPACE["sm"] + meter_h + card_pad
        rows = (len(DIFFICULTIES) + cols - 1) // cols

        grid_title_h = 32
        grid_top = cards_top + pad + grid_title_h

        diff_rects = []
        for i in range(len(DIFFICULTIES)):
            col = i % cols
            row = i // cols
            rx = left_x + pad + col * (btn_w + grid_gap)
            ry = grid_top + row * (btn_h + grid_gap)
            diff_rects.append(pygame.Rect(rx, ry, btn_w, btn_h))

        grid_bottom = grid_top + rows * (btn_h + grid_gap) - grid_gap
        start_rect = pygame.Rect(left_x + pad, grid_bottom + 18, left_w - pad * 2, 56)
        left_card_h = (start_rect.bottom - cards_top) + pad

        right_card_h = left_card_h

        return {
            "content_x": content_x, "content_w": content_w,
            "title_y": title_y, "subtitle_y": subtitle_y,
            "left_card": pygame.Rect(left_x, cards_top, left_w, left_card_h),
            "right_card": pygame.Rect(right_x, cards_top, right_w, right_card_h),
            "grid_top": grid_top, "pad": pad,
            "diff_rects": diff_rects,
            "card_pad": card_pad, "name_h": name_h, "sub_h": sub_h, "meter_h": meter_h,
            "start_rect": start_rect,
        }

    def menu_layout(self):
        geo = self._menu_geometry()
        return {"diff_rects": geo["diff_rects"], "start_rect": geo["start_rect"]}

    def controls_layout(self):
        layout = self.layout
        cx = layout.board_x + layout.board_size // 2
        r = 23
        btn_h = 44
        pad_x = 20

        tb_label = "Take Back"
        tb_w = max(132, self.font_button.size(tb_label)[0] + pad_x * 2)

        action_label = "Menu" if self.game_over else "Resign"
        action_w = max(112, self.font_button.size(action_label)[0] + pad_x * 2)

        gap_pair = 10
        gap_group = 24

        total_w = r * 4 + gap_pair + gap_group * 2 + tb_w + action_w
        left = cx - total_w // 2

        back = pygame.Rect(left, layout.controls_y - r, r * 2, r * 2)
        fwd_left = back.right + gap_pair
        forward = pygame.Rect(fwd_left, layout.controls_y - r, r * 2, r * 2)
        tb_left = forward.right + gap_group
        takeback = pygame.Rect(tb_left, layout.controls_y - btn_h // 2, tb_w, btn_h)
        action_left = takeback.right + gap_group
        action = pygame.Rect(action_left, layout.controls_y - btn_h // 2, action_w, btn_h)
        return {"back": back, "forward": forward, "takeback": takeback, "action": action}

    def _dialog_box_rect(self, width, height):
        """Center a modal dialog box of the given size on the window. Every
        popup (promotion picker, resign confirm, ...) goes through this so
        they're all positioned the same way -- and, crucially, so the box
        size passed in always comes from measuring that popup's own title/
        text/buttons first, never a guessed constant that content can
        outgrow."""
        x = self.window_w // 2 - width // 2
        y = self.window_h // 2 - height // 2
        return pygame.Rect(x, y, width, height)

    def promotion_layout(self):
        pieces = [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]
        pad = SPACE["lg"]
        tile, gap = 60, SPACE["sm"]
        title_h = self.font_label.get_height()

        box_w = pad * 2 + tile * len(pieces) + gap * (len(pieces) - 1)
        box_h = pad + title_h + SPACE["md"] + tile + pad
        box = self._dialog_box_rect(box_w, box_h)

        title_y = box.top + pad + title_h // 2
        tx, ty = box.left + pad, box.top + pad + title_h + SPACE["md"]
        tiles = []
        for pt in pieces:
            tiles.append((pt, pygame.Rect(tx, ty, tile, tile)))
            tx += tile + gap

        return {"box": box, "title_y": title_y, "tiles": tiles}

    def resign_confirm_layout(self):
        """Single source of truth for the resign-confirm dialog's box,
        text, and button rects -- shared by the renderer and the click
        handler (see the note on _menu_geometry). The box height/width are
        derived from the actual title, wrapped message, and button sizes
        rather than a fixed guess, so the buttons can never land outside it
        and the message can never run past its edges."""
        pad = SPACE["lg"]
        title = "Resign this game?"
        message = "This will end the game and start the full review."
        max_text_w = int(clamp(self.window_w - 120, 260, 420))

        title_h = self.font_label.get_height()
        title_w = self.font_label.size(title)[0]
        message_lines = self._wrap_text(message, self.font_small, max_text_w)
        line_h = self.font_small.get_height() + SPACE["xs"]
        message_h = len(message_lines) * line_h

        btn_w, btn_h, btn_gap = 132, 48, SPACE["md"]
        content_w = max(max_text_w, btn_w * 2 + btn_gap, title_w)
        box_w = content_w + pad * 2
        box_h = pad + title_h + SPACE["md"] + message_h + SPACE["lg"] + btn_h + pad
        box = self._dialog_box_rect(box_w, box_h)

        title_y = box.top + pad + title_h // 2
        message_y0 = box.top + pad + title_h + SPACE["md"] + line_h // 2
        btn_y = box.bottom - pad - btn_h
        yes = pygame.Rect(box.centerx - btn_gap // 2 - btn_w, btn_y, btn_w, btn_h)
        no = pygame.Rect(box.centerx + btn_gap // 2, btn_y, btn_w, btn_h)

        return {
            "box": box, "title": title, "title_y": title_y,
            "message_lines": message_lines, "message_y0": message_y0, "line_h": line_h,
            "yes": yes, "no": no,
        }

    # -------------------------------------------------------------- drawing

    def draw(self):
        self.screen.fill(BG)
        if self.state == MENU:
            self.draw_menu()
        else:
            self.draw_playing()
            if self.show_resign_confirm:
                self.draw_resign_confirm()
            elif self.pending_promotion:
                self.draw_promotion_overlay()
        self.draw_theme_toggle()
        pygame.display.flip()

    def draw_theme_toggle(self):
        """Small pill switch, top-right corner, present on every screen."""
        rect = self.theme_toggle_rect()
        mouse = pygame.mouse.get_pos()
        hover = rect.collidepoint(mouse)
        bg = BUTTON_HOVER if hover else BUTTON_BG
        pygame.draw.rect(self.screen, bg, rect, border_radius=rect.height // 2)
        pygame.draw.rect(self.screen, BUTTON_BORDER, rect, width=2, border_radius=rect.height // 2)
        icon_fn = draw_moon_icon if self.dark_mode else draw_sun_icon
        icon_fn(self.screen, ACCENT_DARK, rect.center, rect.height * 0.28)

    def draw_text_center(self, text, font, color, cx, cy):
        surf = font.render(text, True, color)
        rect = surf.get_rect(center=(cx, cy))
        self.screen.blit(surf, rect)
        return rect

    def draw_text_left(self, text, font, color, x, cy):
        surf = font.render(text, True, color)
        rect = surf.get_rect(midleft=(x, cy))
        self.screen.blit(surf, rect)
        return rect

    def draw_card(self, rect, radius=16):
        """Shared rounded-panel background used by every card on the menu
        and (via draw_panel) during play, so the whole app reads as one
        consistent design language instead of one-off styled boxes."""
        pygame.draw.rect(self.screen, SURFACE, rect, border_radius=radius)
        pygame.draw.rect(self.screen, PANEL_BORDER, rect, width=2, border_radius=radius)

    def draw_menu(self):
        geo = self._menu_geometry()
        cx = self.window_w // 2

        # Decorative piece glyphs flanking the title -- purely cosmetic,
        # low-opacity so they read as a watermark rather than competing
        # with the title for attention.
        deco_size = int(clamp(self.window_h * 0.09, 60, 100))
        deco_y = geo["title_y"]
        deco_gap = int(self.font_title.size("Minimal Chess")[0] / 2) + deco_size
        for dx, piece in ((-deco_gap, chess.KNIGHT), (deco_gap, chess.KING)):
            deco = pygame.Surface((deco_size * 2, deco_size * 2), pygame.SRCALPHA)
            prev_screen = self.screen
            self.screen = deco
            self.draw_piece_icon(deco_size, deco_size, piece, True, deco_size * 1.5)
            self.screen = prev_screen
            deco.set_alpha(50)
            self.screen.blit(deco, deco.get_rect(center=(cx + dx, deco_y)))

        self.draw_text_center("Minimal Chess", self.font_title, TEXT_DARK, cx, geo["title_y"])
        self.draw_text_center("You play White against Stockfish", self.font_sub, TEXT_MUTED,
                               cx, geo["subtitle_y"])

        mouse = pygame.mouse.get_pos()

        # ---- left card: difficulty picker ----------------------------
        left = geo["left_card"]
        self.draw_card(left)
        self.draw_text_left("Difficulty", self.font_panel_title, TEXT_DARK,
                             left.left + geo["pad"], geo["grid_top"] - 16)

        for i, rect in enumerate(geo["diff_rects"]):
            selected = i == self.difficulty_index
            hover = rect.collidepoint(mouse)
            bg = ACCENT if selected else (BUTTON_HOVER if hover else BUTTON_BG)
            border = ACCENT_DARK if selected else BUTTON_BORDER
            pygame.draw.rect(self.screen, bg, rect, border_radius=10)
            pygame.draw.rect(self.screen, border, rect, width=2, border_radius=10)
            diff = DIFFICULTIES[i]
            label = diff["name"]
            sub = f"~{diff['elo']} elo" if diff["elo"] else "full strength"
            color = TEXT_LIGHT if selected else TEXT_DARK
            sub_color = TEXT_LIGHT if selected else TEXT_MUTED

            # Same top-down, metric-based stack as the height calculation in
            # _menu_geometry -- name, then elo line, then meter, each a
            # standard SPACE gap apart, so they can never crowd together.
            card_pad, name_h, sub_h = geo["card_pad"], geo["name_h"], geo["sub_h"]
            name_cy = rect.top + card_pad + name_h // 2
            sub_cy = name_cy + name_h // 2 + SPACE["xs"] + sub_h // 2
            name_surf = self.font_button.render(label, True, color)
            name_rect = name_surf.get_rect(midleft=(rect.left + 14, name_cy))
            self.screen.blit(name_surf, name_rect)
            sub_surf = self.font_small.render(sub, True, sub_color)
            sub_rect = sub_surf.get_rect(midleft=(rect.left + 14, sub_cy))
            self.screen.blit(sub_surf, sub_rect)

            # Strength meter: a quick visual read of how the options rank,
            # rather than only conveying it through raw elo numbers.
            meter_w = rect.width - 28
            meter_rect = pygame.Rect(rect.left + 14, rect.bottom - card_pad - geo["meter_h"], meter_w, geo["meter_h"])
            frac = 1.0 if diff["elo"] is None else clamp(diff["elo"] / 2400.0, 0.08, 1.0)
            fill_color = TEXT_LIGHT if selected else ACCENT
            if selected:
                overlay = pygame.Surface((meter_w, 5), pygame.SRCALPHA)
                overlay.fill((255, 255, 255, 70))
                self.screen.blit(overlay, meter_rect.topleft)
            else:
                pygame.draw.rect(self.screen, DIVIDER, meter_rect, border_radius=3)
            fill_w = max(4, int(meter_w * frac))
            pygame.draw.rect(self.screen, fill_color, (meter_rect.left, meter_rect.top, fill_w, 5), border_radius=3)

        start_rect = geo["start_rect"]
        can_start = self.engine_thread is not None and not self.engine_thread.error
        hover = start_rect.collidepoint(mouse)
        bg = ACCENT_DARK if (can_start and hover) else (ACCENT if can_start else (200, 198, 192))
        pygame.draw.rect(self.screen, bg, start_rect, border_radius=12)
        label = "Start Game" if can_start else "Stockfish not found"
        self.draw_text_center(label, self.font_button, TEXT_LIGHT, start_rect.centerx, start_rect.centery)

        # ---- right card: engine status + quick help -------------------
        right = geo["right_card"]
        self.draw_card(right)
        rx = right.left + geo["pad"]
        ry = right.top + geo["pad"]
        rw = right.width - geo["pad"] * 2

        # Line heights derived from the actual font metrics + a standard
        # SPACE gap, rather than hand-picked pixel increments -- this is
        # what guarantees no two lines in this card can ever end up
        # touching or overlapping, no matter what the type scale is.
        title_line = self.font_panel_title.get_height() + SPACE["sm"]
        body_line = self.font_small.get_height() + SPACE["xs"]
        tiny_line = self.font_tiny.get_height() + SPACE["xs"]

        self.draw_text_left("Engine", self.font_panel_title, TEXT_DARK, rx, ry + self.font_panel_title.get_height() // 2)
        ry += title_line
        dot_color = ACCENT_DARK if can_start else DANGER
        smooth_circle(self.screen, dot_color, (rx + 6, ry + body_line // 2), 6)
        status = "Stockfish ready" if can_start else "Stockfish not found"
        self.draw_text_left(status, self.font_small, TEXT_DARK, rx + 22, ry + body_line // 2)
        ry += body_line
        if self.stockfish_path:
            path_text = os.path.basename(self.stockfish_path)
            if self.font_tiny.size(path_text)[0] > rw:
                path_text = path_text[:28] + "\u2026"
            self.draw_text_left(path_text, self.font_tiny, TEXT_FAINT, rx + 22, ry + tiny_line // 2)
            ry += tiny_line
        ry += SPACE["sm"]

        pygame.draw.line(self.screen, DIVIDER, (rx, ry), (rx + rw, ry), 1)
        ry += SPACE["lg"]

        self.draw_text_left("Shortcuts", self.font_panel_title, TEXT_DARK, rx, ry + self.font_panel_title.get_height() // 2)
        ry += title_line + SPACE["xs"]
        shortcuts = [
            ("Esc", "Quit"),
            ("F11", "Toggle fullscreen"),
            ("\u2190 / \u2192", "Step through moves"),
            ("Scroll", "Scroll the side panel"),
        ]
        row_h = max(24, tiny_line) + SPACE["sm"]
        for key, desc in shortcuts:
            key_surf = self.font_tiny.render(key, True, ACCENT_DARK)
            key_w = max(64, key_surf.get_width() + 16)
            key_rect = pygame.Rect(rx, ry, key_w, 24)
            pygame.draw.rect(self.screen, ACCENT_SOFT, key_rect, border_radius=6)
            self.screen.blit(key_surf, key_surf.get_rect(center=key_rect.center))
            self.draw_text_left(desc, self.font_small, TEXT_MUTED, key_rect.right + SPACE["sm"], key_rect.centery)
            ry += row_h

        ry += SPACE["xs"]
        pygame.draw.line(self.screen, DIVIDER, (rx, ry), (rx + rw, ry), 1)
        ry += SPACE["lg"]
        self.draw_text_left("Tip", self.font_panel_title, TEXT_DARK, rx, ry + self.font_panel_title.get_height() // 2)
        ry += title_line
        tip = "After the game ends, choose Fast, Medium, or Deep to control how long the move-by-move review takes."
        for line in self._wrap_text(tip, self.font_small, rw):
            self.draw_text_left(line, self.font_small, TEXT_MUTED, rx, ry + body_line // 2)
            ry += body_line

        if not can_start:
            msg1 = "Install Stockfish and place the binary next to this script"
            msg2 = "(named 'stockfish'), or set the STOCKFISH_PATH env variable."
            self.draw_text_center(msg1, self.font_small, DANGER, cx, start_rect.bottom + 26)
            self.draw_text_center(msg2, self.font_small, DANGER, cx, start_rect.bottom + 46)

        self.draw_text_center("Minimal Chess \u00b7 powered by Stockfish", self.font_tiny, TEXT_FAINT,
                               cx, self.window_h - 24)

    def _format_duration(self, seconds):
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}" if m else f"{s}s"

    def _wrap_text(self, text, font, max_w):
        words = text.split(" ")
        lines = []
        cur = ""
        for w in words:
            trial = (cur + " " + w).strip()
            if font.size(trial)[0] > max_w and cur:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines

    def draw_playing(self):
        layout = self.layout
        state = self.history[self.history_index]
        board = chess.Board(state["fen"])
        cap_white = state["cw"]
        cap_black = state["cb"]
        last_move_uci = state["last_move"]
        last_move = None
        if last_move_uci:
            last_move = chess.Move.from_uci(last_move_uci)

        diff_name = DIFFICULTIES[self.difficulty_index]["name"]
        self.draw_text_center(f"Stockfish \u00b7 {diff_name}", self.font_label, TEXT_DARK,
                               layout.board_x + layout.board_size // 2, layout.label_top_y)
        self.draw_captured_row(cap_black, layout.board_x, layout.captured_top_y, is_white_pieces=True)

        self.draw_board(board, last_move)
        self.draw_eval_bar()
        self.draw_best_move_hint(board)

        self.draw_captured_row(cap_white, layout.board_x, layout.captured_bottom_y, is_white_pieces=False)
        self.draw_text_center("You", self.font_label, TEXT_DARK,
                               layout.board_x + layout.board_size // 2, layout.label_bottom_y)

        self.draw_controls()

        hint_cx = layout.board_x + layout.board_size // 2
        if self.history_index != len(self.history) - 1:
            self.draw_hint_with_icon(
                f"Viewing move {self.history_index} / {len(self.history) - 1} \u2014 press",
                "right", "to return", self.font_small, ACCENT_DARK, hint_cx, layout.hint_y)
        elif self.awaiting_engine or self.pending_engine:
            self.draw_text_center("The engine is thinking\u2026", self.font_small, TEXT_MUTED,
                                   hint_cx, layout.hint_y)
        elif self.game_over:
            self.draw_game_over_banner(hint_cx, layout.hint_y)
        else:
            self.draw_browse_hint(hint_cx, layout.hint_y)

        self.draw_panel()

    def draw_hint_with_icon(self, text_before, icon_dir, text_after, font, color, cx, cy):
        """Draws 'text_before [triangle icon] text_after' centered on one
        line. Replaces the old '... press \u25b6 to return' string, whose
        unicode triangle glyph rendered as an empty box on fonts that lack
        it -- a real vector triangle always renders correctly."""
        icon_r = 6
        gap = 6
        t1 = font.render(text_before, True, color)
        t2 = font.render(text_after, True, color) if text_after else None
        total_w = t1.get_width() + gap + icon_r * 2 + (gap + t2.get_width() if t2 else 0)
        x = cx - total_w // 2
        self.screen.blit(t1, t1.get_rect(midleft=(x, cy)))
        x += t1.get_width() + gap
        self.draw_triangle_icon_at("right" if icon_dir == "right" else "left",
                                    (x + icon_r, cy), icon_r * 2, color)
        x += icon_r * 2
        if t2:
            x += gap
            self.screen.blit(t2, t2.get_rect(midleft=(x, cy)))

    def draw_browse_hint(self, cx, cy):
        """Draws '[<] [>] to browse moves' with vector arrow icons instead
        of the old '\u2190 \u2192' unicode glyphs (which showed as empty
        tofu boxes on fonts missing those characters)."""
        font, color = self.font_tiny, TEXT_FAINT
        label_surf = font.render("to browse moves", True, color)
        icon_r, gap = 5, 6
        total_w = icon_r * 4 + gap * 3 + label_surf.get_width()
        x = cx - total_w // 2
        self.draw_triangle_icon_at("left", (x + icon_r, cy), icon_r * 2, color)
        x += icon_r * 2 + gap
        self.draw_triangle_icon_at("right", (x + icon_r, cy), icon_r * 2, color)
        x += icon_r * 2 + gap
        self.screen.blit(label_surf, label_surf.get_rect(midleft=(x, cy)))

    def draw_game_over_banner(self, cx, cy):
        """Makes the end of the game unmistakable: shows who won (or the
        draw reason) right under the board. Previously game_over_text was
        computed but never actually drawn anywhere."""
        text = self.game_over_text or "Game over."
        outcome = None
        if self.board.is_game_over(claim_draw=True):
            outcome = self.board.outcome(claim_draw=True)
        lowered = text.lower()
        if (outcome is not None and outcome.winner is True) :
            color = ACCENT_DARK
        elif (outcome is not None and outcome.winner is False) or "resigned" in lowered:
            color = DANGER_DARK
        else:
            color = TEXT_MUTED
        self.draw_text_center(text, self.font_banner, color, cx, cy)

    def draw_best_move_hint(self, board):
        if not self.review_ready():
            return
        record = self.record_for_history_index(self.history_index)
        if record is None:
            return
        played_uci = record["uci"]
        best_uci = record["best_uci"]
        if not best_uci or best_uci == played_uci:
            return
        before_state = self.history[self.history_index - 1]
        before_board = chess.Board(before_state["fen"])
        try:
            best_move = chess.Move.from_uci(best_uci)
        except Exception:
            return
        if best_move not in before_board.legal_moves:
            return
        start = self.layout.square_to_px(best_move.from_square)
        end = self.layout.square_to_px(best_move.to_square)
        draw_arrow(self.screen, HINT_ARROW, start, end, width=max(6, self.layout.square // 9))

    def draw_board(self, board, last_move):
        layout = self.layout
        sq = layout.square
        flipped = getattr(layout, "flipped", False)
        # squares
        for rank in range(8):
            for file_ in range(8):
                square = chess.square(file_, rank)
                col = (7 - file_) if flipped else file_
                row = rank if flipped else (7 - rank)
                x = layout.board_x + col * sq
                y = layout.board_y + row * sq
                is_light = (file_ + rank) % 2 == 1
                color = LIGHT_SQ if is_light else DARK_SQ
                if last_move and square in (last_move.from_square, last_move.to_square):
                    color = LASTMOVE_SQ if is_light else tuple(max(0, c - 18) for c in LASTMOVE_SQ)
                if self.selected_square == square:
                    color = SELECT_SQ
                pygame.draw.rect(self.screen, color, (x, y, sq, sq))

        pygame.draw.rect(self.screen, BOARD_BORDER, (layout.board_x, layout.board_y, layout.board_size, layout.board_size), width=2)

        # coordinate labels
        for file_ in range(8):
            col = (7 - file_) if flipped else file_
            x = layout.board_x + col * sq + sq - 11
            y = layout.board_bottom - 13
            is_light = (file_ + 0) % 2 == 1
            color = DARK_SQ if is_light else LIGHT_SQ
            letter = "abcdefgh"[file_]
            surf = self.font_coord.render(letter, True, color)
            self.screen.blit(surf, surf.get_rect(bottomright=(x + 11, y + 13)))
        for rank in range(8):
            row = rank if flipped else (7 - rank)
            x = layout.board_x + 4
            y = layout.board_y + row * sq + 4
            is_light = (0 + rank) % 2 == 1
            color = DARK_SQ if is_light else LIGHT_SQ
            surf = self.font_coord.render(str(rank + 1), True, color)
            self.screen.blit(surf, surf.get_rect(topleft=(x, y)))

        # legal move dots
        for target in self.legal_targets:
            x, y = layout.square_to_px(target)
            occupied = board.piece_at(target) is not None
            if occupied:
                smooth_circle(self.screen, MOVE_DOT, (x, y), sq // 2 - 4, width=3)
            else:
                smooth_circle(self.screen, MOVE_DOT, (x, y), max(6, sq // 7))

        # pieces (skip the one currently animating)
        anim_from = anim_to = None
        if self.animating and self.anim_move:
            anim_from = self.anim_move.from_square
            anim_to = self.anim_move.to_square

        piece_size = sq * 0.82

        for square in chess.SQUARES:
            piece = board.piece_at(square)
            if piece is None:
                continue
            if self.animating and square in (anim_from, anim_to):
                continue
            x, y = layout.square_to_px(square)
            self.draw_piece_icon(x, y, piece.piece_type, piece.color, piece_size)

        if self.animating and self.anim_move:
            piece = board.piece_at(anim_from) or chess.Piece(chess.PAWN, not board.turn)
            t = min(1.0, (time.time() - self.anim_start) / ANIM_DURATION)
            t = t * t * (3 - 2 * t)  # smoothstep
            fx, fy = layout.square_to_px(anim_from)
            tx, ty = layout.square_to_px(anim_to)
            x = lerp(fx, tx, t)
            y = lerp(fy, ty, t)
            self.draw_piece_icon(x, y, piece.piece_type, piece.color, piece_size)

    def draw_piece_icon(self, x, y, piece_type, color_is_white, size):
        """Draw a piece centered at (x, y) inside a size x size box. Uses the
        real vector-art piece images when available, and falls back to a
        simple drawn disc + letter if artwork couldn't be loaded."""
        size = max(1, int(size))

        if size >= 32:
            shadow_w, shadow_h = size * 0.82, size * 0.24
            shadow_surf = pygame.Surface((int(shadow_w), int(shadow_h)), pygame.SRCALPHA)
            pygame.draw.ellipse(shadow_surf, (20, 18, 16, 55), shadow_surf.get_rect())
            shadow_rect = shadow_surf.get_rect(center=(int(x), int(y + size * 0.40)))
            self.screen.blit(shadow_surf, shadow_rect)

        surf = self.get_piece_surface(piece_type, color_is_white, size) if self.images_available else None
        if surf is not None:
            rect = surf.get_rect(center=(int(x), int(y)))
            self.screen.blit(surf, rect)
        else:
            self._draw_piece_fallback(x, y, piece_type, color_is_white, size)

    def _draw_piece_fallback(self, x, y, piece_type, color_is_white, size):
        radius = size / 2
        fill = WHITE_FILL if color_is_white else BLACK_FILL
        border = WHITE_BORDER if color_is_white else BLACK_BORDER
        text_color = WHITE_BORDER if color_is_white else WHITE_FILL
        smooth_circle(self.screen, fill, (x, y), radius)
        smooth_circle(self.screen, border, (x, y), radius, width=2)
        letter = PIECE_LETTERS[piece_type]
        font = self.font_piece_lg if size >= 30 else self.font_piece_sm
        surf = font.render(letter, True, text_color)
        rect = surf.get_rect(center=(int(x), int(y)))
        self.screen.blit(surf, rect)

    def draw_captured_row(self, piece_list, x_start, y, is_white_pieces):
        order = sorted(piece_list, key=lambda pt: -PIECE_VALUE[pt])
        size = max(20, int(self.layout.square * 0.4))
        gap = 4
        cx = x_start + size // 2
        for pt in order:
            self.draw_piece_icon(cx, y, pt, is_white_pieces, size)
            cx += size + gap

    def draw_eval_bar(self):
        layout = self.layout
        rect = pygame.Rect(layout.eval_x, layout.eval_y, layout.eval_w, layout.eval_h)
        pygame.draw.rect(self.screen, EVAL_BLACK, rect, border_radius=6)
        display_cp, display_mate = self.current_display_eval()
        cp = display_cp if display_cp is not None else 0
        cp = max(-1000, min(1000, cp))
        white_fraction = (cp + 1000) / 2000.0
        white_h = int(layout.eval_h * white_fraction)
        white_rect = pygame.Rect(layout.eval_x, layout.eval_y + (layout.eval_h - white_h), layout.eval_w, white_h)
        if white_h > 0:
            pygame.draw.rect(self.screen, EVAL_WHITE, white_rect,
                              border_bottom_left_radius=6, border_bottom_right_radius=6,
                              border_top_left_radius=6 if white_h >= layout.eval_h else 0,
                              border_top_right_radius=6 if white_h >= layout.eval_h else 0)
        pygame.draw.rect(self.screen, BOARD_BORDER, rect, width=2, border_radius=6)

        if display_mate:
            label = f"M{display_mate}"
        else:
            label = f"{cp/100:+.1f}"
        self.draw_text_center(label, self.font_eval, TEXT_MUTED, layout.eval_x + layout.eval_w // 2, layout.eval_y - 14)

    def draw_controls(self):
        controls = self.controls_layout()
        mouse = pygame.mouse.get_pos()

        for key in ("back", "forward"):
            rect = controls[key]
            r = rect.width // 2
            hover = rect.collidepoint(mouse)
            enabled = (key == "back" and self.history_index > 0) or \
                      (key == "forward" and self.history_index < len(self.history) - 1)
            bg = BUTTON_HOVER if (hover and enabled) else BUTTON_BG
            smooth_circle(self.screen, bg, rect.center, r)
            smooth_circle(self.screen, BUTTON_BORDER, rect.center, r, width=2)
            color = TEXT_DARK if enabled else (200, 198, 192)
            direction = "left" if key == "back" else "right"
            self.draw_triangle_icon_at(direction, rect.center, r * 2 - 20, color)

        tb_rect = controls["takeback"]
        tb_enabled = (not self.game_over and not self.animating and not self.awaiting_engine
                      and not self.pending_engine and len(self.board.move_stack) > 0)
        hover = tb_rect.collidepoint(mouse) and tb_enabled
        tb_bg = BUTTON_HOVER if hover else BUTTON_BG
        pygame.draw.rect(self.screen, tb_bg, tb_rect, border_radius=10)
        pygame.draw.rect(self.screen, BUTTON_BORDER, tb_rect, width=2, border_radius=10)
        tb_color = TEXT_DARK if tb_enabled else (200, 198, 192)
        self.draw_text_center("Take Back", self.font_button, tb_color, tb_rect.centerx, tb_rect.centery)

        rect = controls["action"]
        hover = rect.collidepoint(mouse)
        if self.game_over:
            bg = ACCENT_DARK if hover else ACCENT
            label = "Menu"
        else:
            bg = DANGER_DARK if hover else DANGER
            label = "Resign"
        pygame.draw.rect(self.screen, bg, rect, border_radius=10)
        self.draw_text_center(label, self.font_button, TEXT_LIGHT, rect.centerx, rect.centery)

    def draw_overlay_backdrop(self):
        overlay = pygame.Surface((self.window_w, self.window_h), pygame.SRCALPHA)
        overlay.fill((30, 28, 26, 140))
        self.screen.blit(overlay, (0, 0))

    def draw_promotion_overlay(self):
        self.draw_overlay_backdrop()
        layout = self.promotion_layout()
        box = layout["box"]
        pygame.draw.rect(self.screen, BUTTON_BG, box, border_radius=14)
        pygame.draw.rect(self.screen, BUTTON_BORDER, box, width=2, border_radius=14)
        self.draw_text_center("Promote to:", self.font_label, TEXT_DARK, box.centerx, layout["title_y"])

        mouse = pygame.mouse.get_pos()
        for pt, rect in layout["tiles"]:
            hover = rect.collidepoint(mouse)
            bg = BUTTON_HOVER if hover else BG
            pygame.draw.rect(self.screen, bg, rect, border_radius=10)
            pygame.draw.rect(self.screen, BUTTON_BORDER, rect, width=2, border_radius=10)
            self.draw_piece_icon(rect.centerx, rect.centery, pt, True, 46)

    def draw_resign_confirm(self):
        self.draw_overlay_backdrop()
        layout = self.resign_confirm_layout()
        box = layout["box"]
        pygame.draw.rect(self.screen, BUTTON_BG, box, border_radius=14)
        pygame.draw.rect(self.screen, BUTTON_BORDER, box, width=2, border_radius=14)
        self.draw_text_center(layout["title"], self.font_label, TEXT_DARK, box.centerx, layout["title_y"])

        y = layout["message_y0"]
        for line in layout["message_lines"]:
            self.draw_text_center(line, self.font_small, TEXT_MUTED, box.centerx, y)
            y += layout["line_h"]

        mouse = pygame.mouse.get_pos()
        yes, no = layout["yes"], layout["no"]
        pygame.draw.rect(self.screen, DANGER_DARK if yes.collidepoint(mouse) else DANGER, yes, border_radius=10)
        self.draw_text_center("Resign", self.font_button, TEXT_LIGHT, yes.centerx, yes.centery)
        pygame.draw.rect(self.screen, BUTTON_HOVER if no.collidepoint(mouse) else BUTTON_BG, no, border_radius=10)
        pygame.draw.rect(self.screen, BUTTON_BORDER, no, width=2, border_radius=10)
        self.draw_text_center("Cancel", self.font_button, TEXT_DARK, no.centerx, no.centery)

    # ---------------------------------------------------------- side panel

    def draw_panel(self):
        layout = self.layout
        panel_rect = pygame.Rect(layout.panel_x, layout.panel_y, layout.panel_w, layout.panel_h)
        pygame.draw.rect(self.screen, PANEL_BG, panel_rect, border_radius=14)
        pygame.draw.rect(self.screen, PANEL_BORDER, panel_rect, width=2, border_radius=14)

        self._panel_click_targets = []
        self._graph_rect = None
        self._graph_n = 0

        pad = 18
        ready = self.review_ready()
        if self.awaiting_review_choice:
            title = "Review Options"
        else:
            title = "Game Review" if ready else "Moves"
        title_y = panel_rect.top + 16
        self.draw_text_left(title, self.font_panel_title, TEXT_DARK, panel_rect.left + pad, title_y + 10)

        flip_size = 32
        self._flip_btn_rect = pygame.Rect(panel_rect.right - pad - flip_size, title_y - 2, flip_size, flip_size)
        mouse = pygame.mouse.get_pos()
        flip_hover = self._flip_btn_rect.collidepoint(mouse)
        flipped = getattr(layout, "flipped", False)
        flip_bg = ACCENT_SOFT if flipped else (BUTTON_HOVER if flip_hover else BUTTON_BG)
        pygame.draw.rect(self.screen, flip_bg, self._flip_btn_rect, border_radius=8)
        pygame.draw.rect(self.screen, BUTTON_BORDER, self._flip_btn_rect, width=2, border_radius=8)
        draw_flip_icon(self.screen, ACCENT_DARK if flipped else TEXT_MUTED,
                        self._flip_btn_rect.center, flip_size * 0.32)

        status_y = title_y + 34
        self.draw_status_strip(panel_rect, pad, status_y)

        content_top = status_y + 34
        content_rect = pygame.Rect(panel_rect.left + pad, content_top,
                                    panel_rect.width - pad * 2, panel_rect.bottom - content_top - 16)

        if self.awaiting_review_choice:
            self.draw_review_choice(content_rect)
        elif not ready:
            prev_clip = self.screen.get_clip()
            self.screen.set_clip(content_rect)
            self.draw_moves_list(content_rect, self.analysis_pending)
            self.screen.set_clip(prev_clip)
        else:
            self.draw_review_panel(content_rect)

    def draw_status_strip(self, panel_rect, pad, y):
        """A compact row of info pills (difficulty / turn / move count) so
        the top of the panel carries useful context instead of empty
        whitespace above the move list."""
        diff_name = DIFFICULTIES[self.difficulty_index]["name"]
        move_no = (self.history_index + 1) // 2 + 1
        if self.game_over:
            turn_text = "Game over"
        elif self.board.turn == chess.WHITE:
            turn_text = "Your move"
        else:
            turn_text = "Engine's move"

        pills = [diff_name, turn_text, f"Move {move_no}"]
        x = panel_rect.left + pad
        for text in pills:
            surf = self.font_tiny.render(text, True, TEXT_MUTED)
            w = surf.get_width() + 20
            rect = pygame.Rect(x, y, w, 24)
            pygame.draw.rect(self.screen, SURFACE_ALT, rect, border_radius=12)
            self.screen.blit(surf, surf.get_rect(center=rect.center))
            x += w + 8

    def draw_review_choice(self, rect):
        """Shown right after the game ends, before any analysis has been
        submitted: lets the person pick how deep the review should go via
        a 3-snap-point slider (Fast / Medium / Deep), then a Start button
        actually kicks off start_analysis() at that depth."""
        x = rect.left

        self.draw_text_left("How deep should the review go?", self.font_panel_title, TEXT_DARK, x, rect.top + 14)
        y = rect.top + 40
        hint = "Pick Fast for a quick look, Deep for your best games."
        for line in self._wrap_text(hint, self.font_small, rect.width):
            self.draw_text_left(line, self.font_small, TEXT_MUTED, x, y + 6)
            y += 20
        y += 22

        # ---- slider -----------------------------------------------------
        track_pad = 14
        track_left = x + track_pad
        track_right = rect.right - track_pad
        track_y = y + 34

        n = len(REVIEW_SPEEDS)
        snap_xs = [track_left + (track_right - track_left) * i / (n - 1) for i in range(n)]
        self._review_snap_xs = snap_xs

        selected = self.review_speed_index
        mouse = pygame.mouse.get_pos()

        # labels above each snap point
        for i, preset in enumerate(REVIEW_SPEEDS):
            color = ACCENT_DARK if i == selected else TEXT_MUTED
            font = self.font_panel_title if i == selected else self.font_small
            self.draw_text_center(preset["label"], font, color, snap_xs[i], track_y - 22)

        # track (filled up to the selected snap point)
        track_rect = pygame.Rect(track_left, track_y - 3, track_right - track_left, 6)
        pygame.draw.rect(self.screen, DIVIDER, track_rect, border_radius=3)
        fill_rect = pygame.Rect(track_left, track_y - 3, snap_xs[selected] - track_left, 6)
        if fill_rect.width > 0:
            pygame.draw.rect(self.screen, ACCENT, fill_rect, border_radius=3)

        # snap tick marks
        for i, sx in enumerate(snap_xs):
            r = 4 if i != selected else 0
            if r:
                smooth_circle(self.screen, DIVIDER, (int(sx), track_y), r)

        # draggable knob
        knob_r = 11
        knob_center = (int(snap_xs[selected]), track_y)
        knob_hover = math.hypot(mouse[0] - knob_center[0], mouse[1] - knob_center[1]) <= knob_r + 4
        knob_fill = ACCENT_DARK if (knob_hover or self._slider_dragging) else ACCENT
        smooth_circle(self.screen, SURFACE, knob_center, knob_r + 3)
        smooth_circle(self.screen, knob_fill, knob_center, knob_r)
        smooth_circle(self.screen, ACCENT_DARK, knob_center, knob_r, width=2)

        # generous invisible hit area along the whole track so clicking or
        # dragging anywhere near the slider (not just exactly on the knob)
        # works, and snaps to the nearest of the 3 positions.
        self._review_slider_hit_rect = pygame.Rect(track_left - 16, track_y - 20, (track_right - track_left) + 32, 40)

        y = track_y + 30
        blurb = REVIEW_SPEEDS[selected]["blurb"]
        for line in self._wrap_text(blurb, self.font_small, rect.width):
            self.draw_text_center(line, self.font_small, TEXT_MUTED, rect.centerx, y + 8)
            y += 20
        y += 18

        # ---- start button -------------------------------------------------
        btn_h = 48
        btn_rect = pygame.Rect(x, y, rect.width, btn_h)
        self._review_start_btn_rect = btn_rect
        hover = btn_rect.collidepoint(mouse)
        bg = ACCENT_DARK if hover else ACCENT
        pygame.draw.rect(self.screen, bg, btn_rect, border_radius=10)
        label = f"Start {REVIEW_SPEEDS[selected]['label']} Review"
        self.draw_text_center(label, self.font_button, TEXT_LIGHT, btn_rect.centerx, btn_rect.centery)

    def draw_moves_list(self, rect, is_review_waiting):
        row_h = 34
        x = rect.left

        moves = self.board.move_stack
        san_list = []
        if moves:
            b = chess.Board()
            for m in moves:
                san_list.append(b.san(m))
                b.push(m)

        # Header height is fixed/deterministic (independent of the actual
        # move data), so we can compute the scrollable content height and
        # clamp the scroll offset up front -- BEFORE drawing anything. Doing
        # the clamp only after rendering (against last frame's bounds) is
        # what caused the glitchy snap-back when scrolling past the bottom.
        header_h = 170 if is_review_waiting else 0
        rows = (len(san_list) + 1) // 2
        content_h = header_h + (rows * row_h if san_list else 26)
        max_scroll = max(0, content_h - rect.height)
        self.panel_scroll = clamp(self.panel_scroll, 0, max_scroll)
        self._content_height_moves = content_h

        y = rect.top - self.panel_scroll

        if is_review_waiting:
            done, total = self.analysis_progress
            speed_label = REVIEW_SPEEDS[self.review_speed_index]["label"]
            self.draw_text_left(f"Running {speed_label} analysis\u2026", self.font_panel_title, TEXT_DARK, x, y + 14)
            y += 34
            bar_rect = pygame.Rect(x, y, rect.width, 10)
            pygame.draw.rect(self.screen, DIVIDER, bar_rect, border_radius=5)
            frac = (done / total) if total else 0
            fill_w = int(rect.width * frac)
            if fill_w > 0:
                pygame.draw.rect(self.screen, ACCENT, (x, y, fill_w, 10), border_radius=5)
            y += 22
            elapsed = self.analysis_elapsed
            eta_text = ""
            if done > 0 and done < total:
                per_move = elapsed / done
                remaining = per_move * (total - done)
                eta_text = f"  \u00b7  ~{self._format_duration(remaining)} left"
            self.draw_text_left(f"{done} / {total} positions  \u00b7  {self._format_duration(elapsed)} elapsed{eta_text}",
                                 self.font_small, TEXT_MUTED, x, y + 8)
            y += 26
            workers = self.analysis_workers
            threads = self.analysis_threads_per_engine
            has_deep_pass = REVIEW_SPEEDS[self.review_speed_index]["deep_movetime"] is not None
            extra = "extra time on close or tactical positions." if has_deep_pass else "single pass, no re-search."
            engines_text = (f"{workers} parallel Stockfish engines \u00d7 {threads} threads each, full "
                             f"strength, {extra}")
            for line in self._wrap_text(engines_text, self.font_tiny, rect.width):
                self.draw_text_left(line, self.font_tiny, TEXT_FAINT, x, y + 6)
                y += 17
            y += 13
            pygame.draw.line(self.screen, DIVIDER, (x, y), (rect.right, y), 1)
            y += 14

        if not san_list:
            self.draw_text_left("No moves yet \u2014 make the first move.", self.font_small, TEXT_MUTED, x, y + 14)
            self.draw_text_left("Tip: legal moves are shown as dots on the board.", self.font_tiny, TEXT_FAINT,
                                 x, y + 44)
            return

        num_font = self.font_move
        for i in range(0, len(san_list), 2):
            move_no = i // 2 + 1
            row_rect = pygame.Rect(x, y, rect.width, row_h)
            if row_rect.bottom >= rect.top and row_rect.top <= rect.bottom:
                if move_no % 2 == 0:
                    pygame.draw.rect(self.screen, SURFACE_ALT, row_rect, border_radius=6)

                white_idx = i + 1
                black_idx = i + 2 if i + 1 < len(san_list) else None
                white_active = self.history_index == white_idx
                if white_active:
                    pygame.draw.rect(self.screen, PANEL_ROW_ACTIVE, row_rect, border_radius=6)
                num_surf = self.font_small.render(f"{move_no}.", True, TEXT_FAINT)
                self.screen.blit(num_surf, (x, y + 8))

                w_rect = pygame.Rect(x + 34, y, 90, row_h)
                self.draw_text_left(san_list[i], num_font, TEXT_DARK, x + 34, y + 17)
                self._panel_click_targets.append((w_rect, white_idx))

                if black_idx is not None:
                    black_active = self.history_index == black_idx
                    b_rect = pygame.Rect(x + 130, y, 90, row_h)
                    if black_active:
                        pygame.draw.rect(self.screen, PANEL_ROW_ACTIVE, b_rect.inflate(6, 4), border_radius=6)
                    self.draw_text_left(san_list[i + 1], num_font, TEXT_DARK, x + 130, y + 17)
                    self._panel_click_targets.append((b_rect, black_idx))
            y += row_h

    # ---------------------------------------------- review panel (split layout)
    #
    # Redesigned so the accuracy header (top) and the currently-selected
    # move's detail (bottom) stay locked in place, and ONLY the middle
    # move-by-move list scrolls in its own clipped container. Both the
    # header height and footer height below are fixed constants that match
    # exactly what draw_review_header()/draw_review_footer() render, so the
    # scrollable middle rect can be computed up front with no guesswork.

    REVIEW_HEADER_H = 476
    REVIEW_FOOTER_H = 118
    REVIEW_ROW_H = 30

    def draw_review_panel(self, rect):
        header_h = min(self.REVIEW_HEADER_H, rect.height)
        footer_h = min(self.REVIEW_FOOTER_H, max(0, rect.height - header_h))
        header_rect = pygame.Rect(rect.left, rect.top, rect.width, header_h)
        list_rect = pygame.Rect(rect.left, header_rect.bottom, rect.width,
                                 max(0, rect.height - header_h - footer_h))
        footer_rect = pygame.Rect(rect.left, rect.bottom - footer_h, rect.width, footer_h)

        self.draw_review_header(header_rect)

        prev_clip = self.screen.get_clip()
        self.screen.set_clip(list_rect)
        self.draw_review_move_list(list_rect)
        self.screen.set_clip(prev_clip)

        pygame.draw.line(self.screen, DIVIDER, (rect.left, list_rect.bottom), (rect.right, list_rect.bottom), 1)
        self.draw_review_footer(footer_rect)

    def draw_review_header(self, rect):
        stats = self.analysis["stats"]
        w_stats = stats[chess.WHITE]
        b_stats = stats[chess.BLACK]

        x = rect.left
        y = rect.top
        col_w = rect.width // 2 - 8

        if self.game_over and self.game_over_text:
            outcome = self.board.outcome(claim_draw=True) if self.board.is_game_over(claim_draw=True) else None
            if outcome is not None and outcome.winner is True:
                banner_color = ACCENT_DARK
            elif (outcome is not None and outcome.winner is False) or "resigned" in self.game_over_text.lower():
                banner_color = DANGER_DARK
            else:
                banner_color = TEXT_MUTED
            self.draw_text_center(self.game_over_text, self.font_label, banner_color, rect.centerx, y + 10)
            y += 26

        self.draw_text_center("You", self.font_panel_title, TEXT_DARK, x + col_w // 2, y + 12)
        self.draw_text_center("Stockfish", self.font_panel_title, TEXT_DARK, x + col_w + 16 + col_w // 2, y + 12)
        y += 30

        acc_you = f"{w_stats['accuracy']:.1f}%"
        acc_eng = f"{b_stats['accuracy']:.1f}%"
        self.draw_text_center(acc_you, self.font_accuracy, ACCENT_DARK, x + col_w // 2, y + 16)
        self.draw_text_center(acc_eng, self.font_accuracy, ACCENT_DARK, x + col_w + 16 + col_w // 2, y + 16)
        y += 34
        self.draw_text_center("accuracy", self.font_tiny, TEXT_MUTED, x + col_w // 2, y)
        self.draw_text_center("accuracy", self.font_tiny, TEXT_MUTED, x + col_w + 16 + col_w // 2, y)
        y += 20

        # Position-advantage graph: y axis = white's win probability (50% is
        # even), x axis = move number. Click anywhere on it to jump there.
        graph_rect = pygame.Rect(x, y, rect.width, 58)
        self.draw_advantage_graph(graph_rect)
        y += graph_rect.height + 14

        pygame.draw.line(self.screen, DIVIDER, (x, y), (rect.right, y), 1)
        y += 14

        y = self.draw_class_breakdown(rect, x, y, col_w, w_stats, b_stats)

        y += 10
        pygame.draw.line(self.screen, DIVIDER, (x, y), (rect.right, y), 1)
        y += 16
        self.draw_text_left("Move-by-move", self.font_panel_title, TEXT_DARK, x, y + 6)

    def draw_class_breakdown(self, rect, x, y, col_w, w_stats, b_stats):
        for key in CLASS_KEYS:
            meta = CLASS_META[key]
            self.draw_class_badge_at(key, (x + 11, y + 13), 22)
            self.draw_text_left(meta["label"], self.font_small, TEXT_DARK, x + 26, y + 13)
            self.draw_text_center(str(w_stats["counts"][key]), self.font_small, TEXT_DARK, x + col_w - 14, y + 13)
            self.draw_text_center(str(b_stats["counts"][key]), self.font_small, TEXT_DARK,
                                   x + col_w + 16 + col_w - 14, y + 13)
            y += 26
        return y

    def draw_advantage_graph(self, rect):
        pygame.draw.rect(self.screen, BG, rect, border_radius=8)
        pygame.draw.rect(self.screen, DIVIDER, rect, width=1, border_radius=8)
        inner = rect.inflate(-14, -12)
        mid_y = inner.centery
        pygame.draw.line(self.screen, DIVIDER, (inner.left, mid_y), (inner.right, mid_y), 1)

        records = self.analysis["records"] if self.analysis else []
        n = len(records)
        if n < 2:
            self.draw_text_center("Not enough moves yet", self.font_tiny, TEXT_FAINT, rect.centerx, rect.centery)
            return

        pts = []
        for i, r in enumerate(records):
            t = i / (n - 1)
            px = inner.left + t * inner.width
            wp = win_percent(r["cp_after_white"])
            py = inner.bottom - (wp / 100.0) * inner.height
            pts.append((px, py))

        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            base = ACCENT if (y1 + y2) / 2 <= mid_y else DANGER
            fill = (base[0], base[1], base[2], 70)
            poly = [(x1, y1), (x2, y2), (x2, mid_y), (x1, mid_y)]
            pygame.gfxdraw.filled_polygon(self.screen, poly, fill)

        line_pts = [(int(px), int(py)) for px, py in pts]
        if len(line_pts) >= 2:
            pygame.draw.lines(self.screen, ACCENT_DARK, False, line_pts, 2)
            pygame.draw.aalines(self.screen, ACCENT_DARK, False, line_pts, 1)

        cur_ply = self.history_index - 1
        if 0 <= cur_ply < n:
            cx, cy = pts[cur_ply]
            smooth_circle(self.screen, TEXT_DARK, (cx, cy), 4)
            smooth_circle(self.screen, TEXT_LIGHT, (cx, cy), 2)

        self._graph_rect = inner
        self._graph_n = n

    def draw_review_move_list(self, rect):
        x = rect.left
        row_h = self.REVIEW_ROW_H
        col_w = rect.width // 2 - 8

        san_list = [r["san"] for r in self.analysis["records"]]
        rows = (len(san_list) + 1) // 2
        content_h = rows * row_h
        max_scroll = max(0, content_h - rect.height)
        self.panel_scroll_review = clamp(self.panel_scroll_review, 0, max_scroll)

        # Only the moves actually worth a second look get a background tint
        # -- "best"/"excellent"/"good" are what most rows in a normal game
        # are, so leaving them untinted keeps a scan of the table pointing
        # at what matters instead of every row competing for attention.
        notable = {"brilliant", "great", "inaccuracy", "mistake", "blunder", "miss"}

        def draw_cell(rec, idx, cell_x):
            active = self.history_index == idx
            cell_rect = pygame.Rect(cell_x, y, col_w - 10, row_h)
            key = rec["classification"]
            color = CLASS_META[key]["color"]
            if active:
                pygame.draw.rect(self.screen, PANEL_ROW_ACTIVE, cell_rect, border_radius=6)
            elif key in notable:
                tint = pygame.Surface(cell_rect.size, pygame.SRCALPHA)
                tint.fill((color[0], color[1], color[2], 22))
                self.screen.blit(tint, cell_rect.topleft)
            badge_d = row_h - 10
            self.draw_class_badge_at(key, (cell_x + badge_d // 2 + 2, y + row_h // 2), badge_d)
            self.draw_text_left(rec["san"], self.font_move, TEXT_DARK, cell_x + badge_d + 8, y + row_h // 2 + 1)
            self._panel_click_targets.append((cell_rect, idx))

        y = rect.top - self.panel_scroll_review
        for i in range(0, len(san_list), 2):
            move_no = i // 2 + 1
            row_top = y
            if row_top + row_h >= rect.top and row_top <= rect.bottom:
                if move_no % 2 == 0:
                    zebra_rect = pygame.Rect(x, y, rect.width, row_h)
                    pygame.draw.rect(self.screen, SURFACE_ALT, zebra_rect, border_radius=6)

                num_surf = self.font_small.render(f"{move_no}.", True, TEXT_FAINT)
                self.screen.blit(num_surf, num_surf.get_rect(midleft=(x + 2, y + row_h // 2)))

                white_idx = i + 1
                draw_cell(self.analysis["records"][i], white_idx, x + 30)

                if i + 1 < len(san_list):
                    black_idx = i + 2
                    draw_cell(self.analysis["records"][i + 1], black_idx, x + col_w + 20)
            y += row_h

    def draw_review_footer(self, rect):
        x, y = rect.left, rect.top + 10
        record = self.record_for_history_index(self.history_index)
        if record is None:
            self.draw_text_left("Select a move to see its rating.", self.font_small, TEXT_MUTED, x, y + 10)
            return
        meta = CLASS_META[record["classification"]]
        who = "You" if record["color"] == chess.WHITE else "Stockfish"
        self.draw_class_badge_at(record["classification"], (x + 12, y + 11), 24)
        self.draw_text_left(f"{who} played {record['san']} \u2014 {meta['label']}",
                             self.font_small, TEXT_DARK, x + 32, y + 11)
        y += 30
        prev_clip = self.screen.get_clip()
        self.screen.set_clip(rect)
        reason = explain_move(record)
        max_lines = max(1, (rect.bottom - y) // 19)
        for line in self._wrap_text(reason, self.font_tiny, rect.width)[:max_lines]:
            self.draw_text_left(line, self.font_tiny, TEXT_MUTED, x, y + 6)
            y += 19
        self.screen.set_clip(prev_clip)

    # -------------------------------------------------------------- main loop

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(60)
        self.shutdown()

    def shutdown(self):
        if self.engine_thread is not None:
            self.engine_thread.stop()
            self.engine_thread.join(timeout=2)
        pygame.quit()


def main():
    app = ChessApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()