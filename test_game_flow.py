"""Regression tests for the one rule the play screen must never break:
a card leaves the screen only when an answer has been committed to it.

Run with:  uv run python test_game_flow.py

Cards used to disappear on their own while a child was still thinking. The cause
was voice: room noise was recognized as a number, submitted, and marked wrong,
so the question vanished with no one having answered it. The recognizer is much
harder to fool now, but a recognizer can always be fooled — so the play screen
enforces the rule itself, and these tests are what hold it to it.

Opens a real Tk window (briefly) and drives it with `update()`; it needs a
display, and it does not touch the microphone — the speech input is stubbed.
"""

import sys
import time

import main as M


def pump(app, seconds=0.2):
    """Let the Tk main loop actually run its timers and posted callbacks."""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.01)


def wait_for(app, predicate, timeout=15):
    end = time.time() + timeout
    while not predicate() and time.time() < end:
        app.update()
        time.sleep(0.02)
    return predicate()


class FakeSTT:
    """Stands in for voice.SpeechInput, with no microphone behind it."""

    def __init__(self):
        self.arm_count = 0
        self.break_chain = False      # swallow the result, like a dropped callback
        self._listening = False
        self._on_result = None

    def available(self):
        return True

    def listening(self):
        return self._listening

    def listen(self, on_result, on_state=None, pick=None):
        if self._listening:
            return                    # one capture at a time, like the real thing
        self.arm_count += 1
        self._listening = True
        self._on_result = on_result

    def deliver(self, n):
        """What the recognizer reports: an int, or None for 'not an answer'."""
        self._listening = False
        cb, self._on_result = self._on_result, None
        if cb and not self.break_chain:
            cb(n)

    def cancel(self):
        self._listening = False

    def stop(self):
        self._listening = False

    def warmup(self):
        pass

    def boost_input_volume(self, *a):
        pass

    def restore_input_volume(self, *a):
        pass


def start(voice: bool):
    app = M.Almathus()
    app.tts = None                    # no speech: the mic arms immediately
    app.stt = FakeSTT() if voice else None
    app.voice_enabled.set(voice)
    app.count_var.set(5)
    app.allowed = {4}
    app.start_game("addition")
    assert wait_for(app, lambda: app.current is not None), "no card was ever rendered"
    return app


def test_keypad_flow():
    """Nothing but a committed answer moves the card on."""
    app = start(voice=False)
    card = app.current
    try:
        # A stray advance must be a no-op. Watch the card's own clock as well as
        # the card: with nothing answered the deck index has not moved, so a
        # slipped-through advance re-renders *this* question and silently resets
        # the time it took — the same card, but the child's think time lost.
        started = app.card_start
        for _ in range(5):            # the advance timer firing on its own
            app._advance()
            app.update()
        assert app.current is card, "an unanswered card was skipped"
        assert app.card_start == started, "an unanswered card was re-rendered"

        pump(app, 3.0)                # a child thinking for a while
        assert app.current is card, "the card timed out and skipped itself"
        assert app.card_start == started, "the card restarted itself"

        for junk in ("", "-", "abc"):
            app.answer_var.set(junk)
            app.submit()
            app.update()
        assert app.current is card, f"a junk submit skipped the card"

        app.answer_var.set(str(card.answer))
        app.submit()
        assert wait_for(app, lambda: app.current is not card, 5), \
            "a correct answer did not advance"

        wrong = app.current
        app.answer_var.set(str(wrong.answer + 1))
        app.submit()
        assert wait_for(app, lambda: app.current is not wrong, 5), \
            "a wrong answer did not advance"
        assert wrong in app.retry_queue, "a failed card was not queued for retry"
    finally:
        app._shutdown()
        app.destroy()


def test_voice_flow():
    """Refused utterances never move the card, and the mic never goes deaf."""
    app = start(voice=True)
    card = app.current
    try:
        assert app.stt.arm_count >= 1, "the mic was never armed"

        for i in range(10):           # noise, chatter, fillers: "not an answer"
            assert app.stt.listening(), f"the mic was not armed for round {i}"
            app.stt.deliver(None)
            pump(app)
            assert app.current is card, f"a refused utterance skipped the card ({i})"

        # Break the chain the way a dropped callback would, and check the
        # watchdog notices rather than leaving the child talking to a deaf card.
        app.stt.break_chain = True
        app.stt.deliver(None)
        app.update()
        assert not app.stt.listening(), "test setup: the mic should be idle here"
        assert wait_for(app, app.stt.listening, 3), \
            "the card went deaf — the watchdog did not re-arm the mic"
        assert app.current is card, "the card moved while nothing was answered"

        app.stt.break_chain = False
        app.stt.deliver(card.answer)
        assert wait_for(app, lambda: app.current is not card, 5), \
            "a spoken number did not answer the card"
    finally:
        app._shutdown()
        app.destroy()


def test_spoken_answer():
    """Which number of an utterance is the answer — and which is the card.

    No window and no mic: this only picks a number out of an utterance. Whether
    that number is trusted enough to take the card away is test_voice_confirm.
    """
    card = M.Card(9, 4, "subtraction", 5)       # a "− 4" session: 4 is on every card

    assert M.spoken_answer([5], card) == 5, "the right answer was not taken"
    assert M.spoken_answer([7], card) == 7, "a wrong answer was not taken"
    assert M.spoken_answer([9, 4, 5], card) == 5, "the question read back, then answered"
    assert M.spoken_answer([], card) is None
    assert M.spoken_answer([9, 4], card) is None, "the question read back was scored"
    assert M.spoken_answer([3, 7], card) == 7, "self-correction: the last number wins"

    # A lone operand is a perfectly ordinary wrong answer — a whole session is
    # built on one number ("− 4"), so refusing it here made that number unsayable
    # on every card of the session. It is picked; confirming it is the gate's job.
    assert M.spoken_answer([4], card) == 4, "a lone operand was refused outright"

    # An operand that IS the answer was never in doubt.
    exact = M.Card(8, 4, "subtraction", 4)
    assert M.spoken_answer([4], exact) == 4, "the right answer was refused"


def test_voice_confirm():
    """A wrong number takes the card only when it is said twice.

    This is the bug that outlived three patches. Each capture is an independent
    chance for the recognizer to be fooled, and the mic re-arms every second or
    two, so a child who thought for a while eventually had a slip — a filler, an
    operand read back, a gurgle — answer for them and take the question away. It
    read as "the card times out"; it was really "the card is up long enough to be
    stolen". Nothing but the same number, twice, may score it.
    """
    app = start(voice=True)
    card = app.current
    wrong = card.answer + 1
    try:
        # Twenty slips, all different, spread over a long think: not one of them
        # may move the card, however long the child takes.
        for i in range(20):
            app.stt.deliver(wrong + i)
            pump(app, 0.05)
            assert app.current is card, f"a single unconfirmed number skipped the card ({i})"
            assert not app._card_done, f"a single unconfirmed number scored the card ({i})"

        # The same number twice, but not in a row: the second one is a fresh
        # offer, not a confirmation of the first.
        app.stt.deliver(wrong)
        pump(app)
        app.stt.deliver(wrong + 99)
        pump(app)
        app.stt.deliver(wrong)
        pump(app)
        assert app.current is card, "two hearings with another number between them scored"

        # ...and now the repeat that means it.
        app.stt.deliver(wrong)
        assert wait_for(app, lambda: app.current is not card, 5), \
            "a number said twice did not answer the card"
        assert card.correct_first_try is False and card.attempts == 1, \
            "the confirmed answer was not scored once, as wrong"

        # The right answer is taken as it is said — no repeat, no lost time.
        nxt = app.current
        app.stt.deliver(nxt.answer)
        assert wait_for(app, lambda: app.current is not nxt, 5), \
            "the right answer had to be repeated"
    finally:
        app._shutdown()
        app.destroy()


def test_confirm_window():
    """A repeat that comes too late is a fresh offer, not a confirmation."""
    app = start(voice=True)
    card = app.current
    wrong = card.answer + 1
    try:
        app.stt.deliver(wrong)
        pump(app)
        assert app._pending and app._pending[0] == wrong, "the number was not offered"
        # Age the offer past the window without waiting out the clock.
        app._pending = (wrong, app._pending[1] - M.VOICE_CONFIRM_WINDOW - 1)
        app.stt.deliver(wrong)
        pump(app)
        assert app.current is card, "a stale offer was confirmed by a much later slip"
        assert app._pending and app._pending[0] == wrong, "the late hearing did not re-offer"
    finally:
        app._shutdown()
        app.destroy()


def main() -> int:
    failures = []
    for test in (test_spoken_answer, test_keypad_flow, test_voice_flow,
                 test_voice_confirm, test_confirm_window):
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as e:            # noqa: BLE001 — a failure report, not a handler
            failures.append(f"FAIL {test.__name__}: {e}")
            print(failures[-1])
    print(f"{5 - len(failures)}/5 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
