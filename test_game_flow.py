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

import main as almathus


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
        self.break_chain = False  # swallow the result, like a dropped callback
        self._listening = False
        self._on_result = None
        self._on_state = None
        self._pick = None

    def available(self):
        return True

    def listening(self):
        return self._listening

    def listen(self, on_result, on_state=None, pick=None):
        if self._listening:
            return  # one capture at a time, like the real thing
        self.arm_count += 1
        self._listening = True
        self._on_result = on_result
        self._on_state = on_state
        self._pick = pick

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

    def calibrate(self, *a):
        pass

    def boost_input_volume(self, *a):
        pass

    def restore_input_volume(self, *a):
        pass


class FakeTTS:
    """Stands in for voice.TTS, with nothing speaking behind it.

    The real one hands its done-callback back from a watcher thread; this one
    hands it back from finish(), so a test says when the sentence ends.
    """

    def __init__(self):
        self.said = []
        self._speaking = False
        self._on_done = None

    def available(self):
        return True

    def speak(self, text, on_done=None):
        self.said.append(text)
        self._speaking = True
        self._on_done = on_done

    def speaking(self):
        return self._speaking

    def finish(self):
        self._speaking = False
        cb, self._on_done = self._on_done, None
        if cb:
            cb()

    def stop(self):
        self._speaking = False
        self._on_done = None


def start(voice: bool, dictation: bool = False, tip: bool = False):
    app = almathus.Almathus()
    # Speech synthesis is always there to be switched on, so that "dictation
    # off" is tested as the choice it is and not as a machine that cannot talk.
    # FakeTTS stands in for TTS structurally, like FakeSTT below.
    app.tts = FakeTTS()  # type: ignore[assignment]
    # A test that plays a deck to the end reaches the recap, which logs a session
    # to results/ — the real file the progress screen reads. Nothing here is a
    # game anyone played, so none of it is written down.
    # Stubbing out a method on one instance is the point of the line.
    app._save_session = lambda *_a, **_k: None  # type: ignore[method-assign]
    # FakeSTT stands in for SpeechInput structurally; subclassing it would probe
    # for a microphone, which is exactly what these tests exist to avoid.
    app.stt = FakeSTT() if voice else None  # type: ignore[assignment]
    app.voice_enabled.set(voice)
    app.dictation_enabled.set(dictation)
    app.count_var.set(5)
    app.allowed = {4}
    app.start_game("addition")
    # A voiced game opens on the "speak clearly" tip and waits for GO; tests
    # tap through it unless they are the ones checking the tip itself.
    if voice and not tip:
        app._begin_countdown()
    if not tip:
        assert wait_for(app, lambda: app.current is not None), "no card was rendered"
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
        for _ in range(5):  # the advance timer firing on its own
            app._advance()
            app.update()
        assert app.current is card, "an unanswered card was skipped"
        assert app.card_start == started, "an unanswered card was re-rendered"

        pump(app, 3.0)  # a child thinking for a while
        assert app.current is card, "the card timed out and skipped itself"
        assert app.card_start == started, "the card restarted itself"

        for junk in ("", "-", "abc"):
            app.answer_var.set(junk)
            app.submit()
            app.update()
        assert app.current is card, "a junk submit skipped the card"

        app.answer_var.set(str(card.answer))
        app.submit()
        assert wait_for(app, lambda: app.current is not card, 5), (
            "a correct answer did not advance"
        )

        wrong = app.current
        app.answer_var.set(str(wrong.answer + 1))
        app.submit()
        assert wait_for(app, lambda: app.current is not wrong, 5), (
            "a wrong answer did not advance"
        )
        assert wrong in app.retry_queue, "a failed card was not queued for retry"
    finally:
        app._shutdown()
        app.destroy()


def test_retry_time_adds_up():
    """A card asked again at the end is timed over both goes.

    The log ranks a session's hardest cards on that total, so a card missed in
    half a second and then laboured over at the end has to read as the hard card
    it is — not as the fastest answer of the round.
    """
    app = start(voice=False)
    failed = app.current
    try:
        app.answer_var.set(str(failed.answer + 1))
        app.submit()
        assert wait_for(app, lambda: app.current is not failed, 5), (
            "a wrong answer did not advance"
        )
        first_go = failed.time_taken
        assert first_go > 0, "the first sitting was never timed"

        # The rest of the deck, answered right, so the reinforcement round is
        # reached with exactly one card in it.
        while app.current is not None and not app.current.is_retry:
            card = app.current
            app.answer_var.set(str(card.answer))
            app.submit()
            if not wait_for(app, lambda c=card: app.current is not c, 5):
                break
        assert app.current is failed, "the failed card never came back"

        pump(app, 1.0)  # a second go that costs measurable time
        app.answer_var.set(str(failed.answer))
        app.submit()
        pump(app, 1.0)

        assert failed.time_taken == first_go, "the first sitting was overwritten"
        assert failed.retry_time > 0.9, f"second sitting not timed: {failed.retry_time}"
        assert failed.total_time == first_go + failed.retry_time
        assert almathus.difficult_cards(app.results)[0] is failed, (
            "the hardest card of the session did not rank first"
        )
    finally:
        app._shutdown()
        app.destroy()


def test_voice_flow():
    """Refused utterances never move the card, and the mic never goes deaf."""
    app = start(voice=True)
    card = app.current
    try:
        assert app.stt.arm_count >= 1, "the mic was never armed"

        for i in range(10):  # noise, chatter, fillers: "not an answer"
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
        assert wait_for(app, app.stt.listening, 3), (
            "the card went deaf — the watchdog did not re-arm the mic"
        )
        assert app.current is card, "the card moved while nothing was answered"

        app.stt.break_chain = False
        app.stt.deliver(card.answer)
        assert wait_for(app, lambda: app.current is not card, 5), (
            "a spoken number did not answer the card"
        )
    finally:
        app._shutdown()
        app.destroy()


def test_dictation_modes():
    """Dictation decides what the card opens with — and the mic follows it.

    On, the question is read and the mic stays shut until the reading is over,
    so the child is not recorded answering the machine's own voice. Off, there
    is nothing to wait for: the card is shown, the clock runs, the mic is open.
    Either way the clock starts with the card.
    """
    app = start(voice=True, dictation=True)
    try:
        card = app.current
        assert app.tts.said, "the question was never read aloud"
        assert not app.stt.listening(), "the mic opened over the question being read"
        assert app.card_start is None, "the card's clock started under the reading"
        held = app._elapsed()
        pump(app, 0.4)
        assert abs(app._elapsed() - held) < 0.05, (
            "the total ran on while the question was being read"
        )
        assert app._card_elapsed() == 0.0, "the card's clock ran under the reading"

        app.tts.finish()
        assert wait_for(app, app.stt.listening, 5), (
            "the mic never opened after the reading"
        )
        assert app.card_start is not None, "the clock never started after the reading"
        pump(app, 0.3)
        assert app._elapsed() > held, "the total never resumed after the reading"
        assert app._card_elapsed() > 0, "the card's clock never started"
        assert app.current is card, "the card moved while nothing was answered"

        # Answered on the keypad over the reading — the only way to spend less
        # than no time on a card. It is worth zero, never a negative.
        app.tts.speak("read the next one", None)
        app.card_start = None
        assert app._card_elapsed() == 0.0, (
            "a card answered under the reading went below zero"
        )
    finally:
        app._shutdown()
        app.destroy()

    app = start(voice=True, dictation=False)
    try:
        assert app.tts.said == [], "the card was read aloud with dictation off"
        assert app.stt.listening(), "the mic did not open with the card"
        assert app.card_start is not None, "the clock did not start with the card"
        ran = app._elapsed()
        pump(app, 0.3)
        assert app._elapsed() > ran, "the total did not run with dictation off"
    finally:
        app._shutdown()
        app.destroy()


def test_voiced_intro_tip():
    """A voiced game opens on the "speak clearly" tip and starts on GO."""
    app = start(voice=True, dictation=True, tip=True)
    try:
        assert app.current is None, "the tip was skipped and a card was dealt"
        assert app.tip_frame.winfo_exists(), "the tip screen was not shown"
        app._begin_countdown()  # what the GO button does
        assert wait_for(app, lambda: app.current is not None), (
            "GO did not start the game"
        )
    finally:
        app._shutdown()
        app.destroy()


def test_card_hint_waits_then_go():
    """A dictated card says wait, then cues GO when the question ends."""
    app = start(voice=True, dictation=True)
    try:
        assert app.card_hint is not None, "no wait/GO cue on a dictated card"
        assert app.card_hint.cget("text") == almathus.T("wait_answer")
        assert not app.stt.listening(), "the mic opened before the question ended"

        app.tts.finish()  # the question has finished being read
        assert wait_for(app, app.stt.listening, 5), (
            "the mic never opened after the question"
        )
        assert app.card_hint.cget("text") == almathus.T("go"), (
            "no GO cue after the question"
        )
        assert str(app.card_hint.cget("fg")) == almathus.GOOD
    finally:
        app._shutdown()
        app.destroy()


def test_spoken_answer():
    """Which number of an utterance is the answer — and which is the card.

    No window and no mic: this only picks a number out of an utterance. Whether
    the sound behind it was a voice at all is decided upstream, by the acoustic
    gate in voice.SpeechInput._judge.
    """
    card = almathus.Card(9, 4, "subtraction", 5)  # a "− 4" session: 4 is on every card

    assert almathus.spoken_answer([5], card) == 5, "the right answer was not taken"
    assert almathus.spoken_answer([7], card) == 7, "a wrong answer was not taken"
    assert almathus.spoken_answer([9, 4, 5], card) == 5, (
        "the question read back, then answered"
    )
    assert almathus.spoken_answer([], card) is None
    assert almathus.spoken_answer([9, 4], card) is None, (
        "the question read back was scored"
    )
    assert almathus.spoken_answer([3, 7], card) == 7, (
        "self-correction: the last number wins"
    )

    # A lone operand is a perfectly ordinary wrong answer — a whole session is
    # built on one number ("− 4"), so refusing it here made that number unsayable
    # on every card of the session.
    assert almathus.spoken_answer([4], card) == 4, "a lone operand was refused outright"

    # An operand that IS the answer was never in doubt.
    exact = almathus.Card(8, 4, "subtraction", 4)
    assert almathus.spoken_answer([4], exact) == 4, "the right answer was refused"


def test_voice_wrong_answer():
    """A wrong spoken number is scored the moment it is heard.

    No confirmation, no repeating yourself: the answer is committed exactly as
    if ✓ had been pressed, the wrong-answer sound plays, and the question comes
    back once — at the end of the game, out of the reinforcement queue — and not
    again after that.
    """
    app = start(voice=True)
    try:
        card = app.current
        assert wait_for(app, app.stt.listening, 5), "the mic was never armed"
        app.stt.deliver(card.answer + 1)
        assert wait_for(app, lambda: app.current is not card, 5), (
            "a wrong spoken number did not answer the card"
        )
        assert card.attempts == 1 and card.correct_first_try is False, (
            "the wrong answer was not scored once, as wrong"
        )
        assert card in app.retry_queue, "the failed card was not queued for the end"

        # The rest of the deck, answered right: nothing else may join the queue.
        while app.idx < len(app.deck):
            cur = app.current
            assert wait_for(app, app.stt.listening, 5), "the mic went deaf mid-deck"
            app.stt.deliver(cur.answer)
            assert wait_for(app, lambda cur=cur: app.current is not cur, 5), (
                "a correct spoken answer did not advance"
            )

        assert app.current is card, "the failed card was not asked again at the end"
        assert wait_for(app, app.stt.listening, 5), (
            "the mic was not armed for the retry"
        )
        app.stt.deliver(card.answer + 1)
        assert wait_for(app, lambda: card.attempts == 2, 5), (
            "the retry was not answered by a spoken number"
        )
        pump(app, 2.0)
        assert not app.retry_queue, "a failed retry was queued to be asked yet again"
    finally:
        app._shutdown()
        app.destroy()


def main() -> int:
    tests = (
        test_spoken_answer,
        test_keypad_flow,
        test_retry_time_adds_up,
        test_voice_flow,
        test_voice_wrong_answer,
        test_dictation_modes,
        test_voiced_intro_tip,
        test_card_hint_waits_then_go,
    )
    failures = []
    for test in tests:
        try:
            test()
            print(f"ok   {test.__name__}")
        except Exception as e:  # noqa: BLE001 — a failure report, not a handler
            failures.append(f"FAIL {test.__name__}: {e}")
            print(failures[-1])
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
