"""FetchGate (T4 Step 3) — FIFO-fair mutual exclusion, escalating + PERSISTED
cooldown, and never-poll-during-cooldown sleep. Offline, fake clock, zero real
sleeps and zero real IG hits."""

from __future__ import annotations

import threading
import time

from ig_media_kit.fetch_gate import FetchGate, get_gate, reset_gate


class FakeClock:
    """Thread-safe virtual clock. ``sleep`` records the duration and ADVANCES the
    clock instead of really waiting (so cooldowns cost zero wall time)."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        with self._lock:
            return self.t

    def sleep(self, d: float) -> None:
        with self._lock:
            self.sleeps.append(d)
            self.t += max(0.0, d)


def _gate(clock: FakeClock, *, state_path=None, base=400.0, factor=2.0, cap=1800.0):
    return FetchGate(cooldown_base_s=base, cooldown_escalation_factor=factor,
                     cooldown_cap_s=cap, clock=clock.now, sleep=clock.sleep,
                     state_path=state_path)


# --- mutual exclusion + FIFO fairness (strict interleaving) ------------------

def test_two_acquirers_never_overlap_and_are_fifo_fair():
    clock = FakeClock()
    gate = _gate(clock)
    log: list[str] = []
    log_lock = threading.Lock()
    in_flight = 0
    max_in_flight = 0
    start = threading.Barrier(2)

    def worker(name: str):
        nonlocal in_flight, max_in_flight
        start.wait()
        for _ in range(5):
            with gate.acquire():
                with log_lock:
                    in_flight += 1
                    max_in_flight = max(max_in_flight, in_flight)
                    log.append(f"{name}+")
                time.sleep(0.001)  # widen the window so an overlap would show
                with log_lock:
                    log.append(f"{name}-")
                    in_flight -= 1

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # At most one window in flight ever — the single-IP invariant.
    assert max_in_flight == 1
    # Every enter is immediately followed by its own exit (no interleave overlap).
    for i in range(0, len(log), 2):
        assert log[i][0] == log[i + 1][0]
        assert log[i][1] == "+" and log[i + 1][1] == "-"


# --- escalating cooldown, sleeps it out, never polls ------------------------

def test_metered_stop_sets_escalated_cooldown_and_next_acquire_sleeps():
    clock = FakeClock()
    gate = _gate(clock, base=400.0, factor=2.0)

    # First metered stop → cooldown of base; the NEXT acquire sleeps exactly that.
    until1 = gate.note_metered_stop("rate_limited")
    assert until1 == clock.now() + 400.0
    with gate.acquire():
        pass
    assert clock.sleeps == [400.0]

    # Second metered stop escalates by the factor (base * factor**1).
    gate.note_metered_stop("rate_limited")
    with gate.acquire():
        pass
    assert clock.sleeps[-1] == 800.0


def test_no_sleep_when_no_cooldown():
    clock = FakeClock()
    gate = _gate(clock)
    with gate.acquire():
        pass
    assert clock.sleeps == []


def test_note_success_decays_escalation():
    clock = FakeClock()
    gate = _gate(clock)
    gate.note_metered_stop("rate_limited")
    gate.note_metered_stop("rate_limited")
    assert gate.escalation_count == 2
    gate.note_success()
    assert gate.escalation_count == 1
    gate.note_success()
    gate.note_success()
    assert gate.escalation_count == 0  # never negative


def test_cooldown_is_capped():
    clock = FakeClock()
    gate = _gate(clock, base=1000.0, factor=10.0, cap=1500.0)
    gate.note_metered_stop("rate_limited")   # 1000
    until = gate.note_metered_stop("rate_limited")  # 10000 -> capped 1500
    assert until == clock.now() + 1500.0


# --- refinement 1: cooldown is PERSISTED across a (simulated) restart --------

def test_cooldown_persists_across_restart(tmp_path):
    state = tmp_path / "_gate.json"
    clock = FakeClock()
    gate1 = _gate(clock, state_path=state)
    gate1.note_metered_stop("rate_limited")
    saved_until = gate1.cooldown_until

    # Simulate a full process restart: a brand-new gate loads the durable state.
    gate2 = _gate(clock, state_path=state)
    assert gate2.cooldown_until == saved_until
    assert gate2.escalation_count == 1
    # Its first acquire sleeps out the REMAINING cooldown — it does not re-hit IG.
    with gate2.acquire():
        pass
    assert clock.sleeps == [400.0]


def test_corrupt_gate_state_is_non_fatal(tmp_path):
    state = tmp_path / "_gate.json"
    state.write_text("{not valid json", encoding="utf-8")
    clock = FakeClock()
    gate = _gate(clock, state_path=state)
    assert gate.cooldown_until == 0.0
    assert gate.escalation_count == 0


# --- singleton accessor -----------------------------------------------------

def test_get_gate_is_a_singleton():
    reset_gate()
    try:
        a = get_gate()
        b = get_gate()
        assert a is b
    finally:
        reset_gate()
