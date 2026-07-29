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


def main() -> int:
    failures = []
    for test in (test_keypad_flow, test_voice_flow):
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as e:            # noqa: BLE001 — a failure report, not a handler
            failures.append(f"FAIL {test.__name__}: {e}")
            print(failures[-1])
    print(f"{2 - len(failures)}/2 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
