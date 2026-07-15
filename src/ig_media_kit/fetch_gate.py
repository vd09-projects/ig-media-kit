"""Process-wide fetch gate — the single serialization + global-cooldown point
for ALL IG-hitting work in the process (T4, Step 3).

Politeness is load-bearing and the runner is the system's only sleeper (CLAUDE.md):
against a single IP there may be AT MOST ONE metadata window in flight
process-wide — never two handles, never two jobs, never a batch racing a sync
tool. This gate enforces that invariant. Every batch ``run_list_reels`` /
re-resolve call is wrapped in ``acquire()``; T5 wraps the sync ``list_reels`` /
``download`` entrypoints in the SAME gate.

Three load-bearing behaviours:

  1. **Mutual exclusion, FIFO-fair.** ``acquire()`` blocks until it is the sole
     holder. Waiters are served strictly first-come (a ticket queue over a
     ``threading.Condition``), NOT an unfair bare ``Lock`` — so a second job
     queues *behind* rather than starving, and two acquirers' windows strictly
     interleave (never overlap).
  2. **Never poll during a cooldown — sleep it out.** Before yielding, if
     ``now < cooldown_until`` the holder SLEEPS until then (polling IG during a
     cooldown *extends* it). This is the one place the system sleeps.
  3. **Escalating, persisted cooldown.** ``note_metered_stop`` advances an
     escalation counter and sets ``cooldown_until = now + base * factor**(n-1)``
     (bounded by ``cooldown_cap_s``); ``note_success`` decays it. The counter +
     ``cooldown_until`` are PERSISTED to a small ``_gate.json`` state file so a
     process restart (which ``resume_pending_jobs`` is designed to tolerate) does
     NOT immediately re-hit IG during an active cooldown — a fresh gate loads the
     live cooldown and sleeps out the remainder on its first ``acquire()``.

The clock + sleep are injectable so the whole thing is unit-testable with a fake
clock (zero real sleeps, zero real IG hits in CI).
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

# Bound the escalation exponent so ``factor**n`` cannot overflow into an absurd
# sleep even if a pathological run keeps metering; ``cooldown_cap_s`` is the real
# ceiling but this keeps the intermediate arithmetic sane.
_MAX_ESCALATION = 12


class FetchGate:
    """A FIFO-fair mutual-exclusion gate carrying a shared, persisted cooldown.

    Instantiable (tests inject a fake clock/sleep + a tmp ``state_path``); the
    process uses the module singleton via :func:`get_gate`.
    """

    def __init__(
        self,
        *,
        cooldown_base_s: float = 400.0,
        cooldown_escalation_factor: float = 2.0,
        cooldown_cap_s: float = 1800.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        state_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._cond = threading.Condition()
        self._waiters: deque[int] = deque()
        self._holder: int | None = None
        self._next_ticket = 0

        self._clock = clock
        self._sleep = sleep
        self.cooldown_base_s = float(cooldown_base_s)
        self.cooldown_escalation_factor = float(cooldown_escalation_factor)
        self.cooldown_cap_s = float(cooldown_cap_s)

        # Shared cooldown state (persisted).
        self.cooldown_until = 0.0
        self.escalation_count = 0

        self._state_path = Path(state_path) if state_path is not None else None
        self._load_state()

    # --- acquisition (FIFO) -------------------------------------------------
    @contextmanager
    def acquire(self) -> Iterator["FetchGate"]:
        """Block until sole holder, sleep out any active cooldown, then yield.

        The cooldown sleep happens WHILE holding the gate (so no other acquirer
        can hit IG during it) but OUTSIDE the condition lock (so other callers may
        still enqueue and wait). Releases on exit even if the body raises."""
        ticket = self._take_ticket()
        self._await_turn(ticket)
        try:
            self._sleep_out_cooldown()
            yield self
        finally:
            self._release()

    def _take_ticket(self) -> int:
        with self._cond:
            ticket = self._next_ticket
            self._next_ticket += 1
            self._waiters.append(ticket)
            return ticket

    def _await_turn(self, ticket: int) -> None:
        with self._cond:
            # Proceed only when nobody holds the gate AND this ticket is at the
            # head of the FIFO queue — strict first-come fairness.
            while self._holder is not None or self._waiters[0] != ticket:
                self._cond.wait()
            self._holder = ticket
            self._waiters.popleft()

    def _release(self) -> None:
        with self._cond:
            self._holder = None
            self._cond.notify_all()

    def _sleep_out_cooldown(self) -> None:
        remaining = self.cooldown_until - self._clock()
        if remaining > 0:
            # NEVER a poll loop — a single sleep of the whole remaining span.
            self._sleep(remaining)

    # --- cooldown accounting (persisted) ------------------------------------
    def note_metered_stop(self, stop_reason: str | None = None) -> float:
        """Record a metered stop: escalate the counter and set the cooldown.

        Returns the new ``cooldown_until`` epoch. Persisted immediately so a
        crash-and-restart mid-cooldown does not re-hit IG."""
        with self._cond:
            self.escalation_count = min(self.escalation_count + 1, _MAX_ESCALATION)
            span = self.cooldown_base_s * (
                self.cooldown_escalation_factor ** (self.escalation_count - 1)
            )
            span = min(span, self.cooldown_cap_s)
            self.cooldown_until = self._clock() + span
            self._persist()
            return self.cooldown_until

    def note_success(self) -> None:
        """Decay the escalation counter after a clean (non-metered) window."""
        with self._cond:
            if self.escalation_count > 0:
                self.escalation_count -= 1
            self._persist()

    # --- durable gate state -------------------------------------------------
    def _persist(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(
                {"cooldown_until": self.cooldown_until,
                 "escalation_count": self.escalation_count},
                fh,
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self._state_path)

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            with self._state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.cooldown_until = float(data.get("cooldown_until", 0.0))
            self.escalation_count = int(data.get("escalation_count", 0))
        except (OSError, ValueError, TypeError):
            # A corrupt gate-state file is non-fatal: fall back to no cooldown.
            self.cooldown_until = 0.0
            self.escalation_count = 0


# --- process singleton ------------------------------------------------------

_SINGLETON: FetchGate | None = None
_SINGLETON_LOCK = threading.Lock()


def get_gate(
    config: object | None = None,
    *,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchGate:
    """Return the process-wide gate singleton, creating it on first call.

    ``config`` (a ``Config``) supplies the cooldown constants + the durable
    ``_gate.json`` path under ``store_dir/_batch``. Subsequent calls return the
    same instance regardless of args — there is exactly one gate per process."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            batch = getattr(config, "batch", None)
            output = getattr(config, "output", None)
            state_path = None
            if output is not None:
                state_path = Path(output.store_dir) / "_batch" / "_gate.json"
            _SINGLETON = FetchGate(
                cooldown_base_s=getattr(batch, "cooldown_base_s", 400.0),
                cooldown_escalation_factor=getattr(
                    batch, "cooldown_escalation_factor", 2.0),
                cooldown_cap_s=getattr(batch, "cooldown_cap_s", 1800.0),
                clock=clock,
                sleep=sleep,
                state_path=state_path,
            )
        return _SINGLETON


def reset_gate() -> None:
    """Drop the singleton (test-only seam — production never resets the gate)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        _SINGLETON = None
