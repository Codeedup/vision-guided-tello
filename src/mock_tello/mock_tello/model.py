"""Pure image-plane dynamics and an independent approved-command consumer.

Scalars are NOT metres/second. Positive lateral moves the camera right and
reduces image x. Positive vertical moves it up and increases image y.
"""

from collections import deque
from dataclasses import dataclass
import math
import random


FRESHNESS = 0.25
SCALAR_LIMIT = 10.0
SCENARIOS = ('static', 'moving', 'dropout', 'loss', 'ambiguous', 'silence')


@dataclass(frozen=True)
class Config:
    initial_x: float = 0.6
    initial_y: float = -0.4
    gain_x: float = 0.04
    gain_y: float = 0.04
    response_tau: float = 0.15
    sensor_delay: float = 0.0
    noise_stddev: float = 0.0
    seed: int = 7

    def __post_init__(self):
        numbers = (self.initial_x, self.initial_y, self.gain_x, self.gain_y,
                   self.response_tau, self.sensor_delay, self.noise_stddev)
        if not all(math.isfinite(value) for value in numbers):
            raise ValueError('Model parameters must be finite')
        if abs(self.initial_x) > 1 or abs(self.initial_y) > 1:
            raise ValueError('Initial errors must lie within [-1, 1]')
        if self.gain_x <= 0 or self.gain_y <= 0:
            raise ValueError('Motion gains must be positive')
        if self.response_tau < 0 or not 0 <= self.sensor_delay <= 2:
            raise ValueError('Lag must be nonnegative; delay must be in [0, 2]')
        if self.noise_stddev < 0:
            raise ValueError('Noise standard deviation must be nonnegative')


@dataclass(frozen=True)
class Decision:
    stamp_ns: int
    source_stamp_ns: int = 0
    autonomy_enabled: bool = False
    tracking_allowed: bool = False
    request_land: bool = False
    lateral: float = 0.0
    vertical: float = 0.0


def fresh(stamp_ns, now_ns):
    return stamp_ns > 0 and 0 <= (now_ns - stamp_ns) / 1e9 < FRESHNESS


class ApprovedGate:
    """Decision, source, and monotonic receipt checks are separate.

    Repeated source stamps on NEW supervisor decisions are legitimate
    heartbeats. Replayed decision stamps cannot renew receipt freshness.
    Landing is a latched mock action, never a physical landing confirmation.
    """

    def __init__(self):
        self.last = None
        self.received_at = 0.0
        self.high_water_ns = 0
        self.terminal = False
        self.mode = 'NO_DECISION'

    def invalidate(self, reason):
        self.last = None
        self.mode = reason

    def accept(self, decision, now_ns, steady_now):
        if not math.isfinite(steady_now) or steady_now < 0:
            self.invalidate('INVALID_RECEIPT_TIME')
            return False
        if not fresh(decision.stamp_ns, now_ns):
            self.invalidate('INVALID_DECISION_TIME')
            return False
        if decision.stamp_ns <= self.high_water_ns:
            # Replays neither replace the payload nor renew receipt time.
            return False
        self.high_water_ns = decision.stamp_ns
        finite_bounded = all(math.isfinite(value) and abs(value) <= SCALAR_LIMIT
                             for value in (decision.lateral, decision.vertical))
        flags_valid = (
            (not decision.tracking_allowed or decision.autonomy_enabled)
            and (not decision.request_land or
                 (decision.autonomy_enabled and not decision.tracking_allowed))
        )
        neutral_valid = (decision.tracking_allowed or
                         (decision.lateral == 0 and decision.vertical == 0))
        if not finite_bounded or not flags_valid or not neutral_valid:
            self.invalidate('INVALID_DECISION')
            return False
        if decision.tracking_allowed and (
                not fresh(decision.source_stamp_ns, now_ns)
                or decision.source_stamp_ns > decision.stamp_ns):
            self.invalidate('INVALID_SOURCE_TIME')
            return False
        self.last = decision
        self.received_at = steady_now
        if decision.request_land:
            self.terminal = True
        self.command(now_ns, steady_now)
        return True

    def command(self, now_ns, steady_now):
        if self.terminal:
            self.mode = 'LANDING_REQUESTED'
            return 0.0, 0.0
        if self.last is None:
            return 0.0, 0.0
        receipt_age = steady_now - self.received_at
        if not math.isfinite(receipt_age) or not 0 <= receipt_age < FRESHNESS:
            self.invalidate('RECEIPT_TIMEOUT')
            return 0.0, 0.0
        if not fresh(self.last.stamp_ns, now_ns):
            self.invalidate('DECISION_TIMEOUT')
            return 0.0, 0.0
        if not self.last.autonomy_enabled:
            self.mode = 'YIELDED'
            return 0.0, 0.0
        if not self.last.tracking_allowed:
            self.mode = 'NEUTRAL'
            return 0.0, 0.0
        if not fresh(self.last.source_stamp_ns, now_ns):
            self.invalidate('SOURCE_TIMEOUT')
            return 0.0, 0.0
        self.mode = 'TRACKING'
        return self.last.lateral, self.last.vertical

    def reset_allowed(self, now_ns, steady_now):
        """Reset requires a current disabled decision, even after mock landing."""
        return (self.last is not None and not self.last.autonomy_enabled
                and fresh(self.last.stamp_ns, now_ns)
                and 0 <= steady_now - self.received_at < FRESHNESS)


@dataclass(frozen=True)
class Observation:
    stamp_ns: int
    detected: bool
    error_x: float
    error_y: float
    tracked_hands: int


def scenario_input(name, elapsed, fault_start=3.0, fault_duration=0.6):
    if name not in SCENARIOS:
        raise ValueError('Unknown scenario: ' + name)
    if name == 'moving':
        # Derivative of a 0.12-amplitude horizontal, 0.10 vertical sine target.
        return (0.12 * 2 * math.pi * 0.2 * math.cos(2 * math.pi * 0.2 * elapsed),
                0.10 * 2 * math.pi * 0.15 * math.cos(2 * math.pi * 0.15 * elapsed),
                'normal')
    in_fault = fault_start <= elapsed < fault_start + fault_duration
    mode = {'dropout': 'missing', 'loss': 'missing',
            'ambiguous': 'ambiguous', 'silence': 'silence'}.get(name, 'normal')
    return 0.0, 0.0, mode if in_fault else 'normal'


class ImagePlane:
    def __init__(self, config=Config()):
        self.config = config
        self.x = config.initial_x
        self.y = config.initial_y
        self.vx = self.vy = 0.0
        self.gate = ApprovedGate()
        self.rng = random.Random(config.seed)
        self.pending = deque()
        self.captured = self.delivered = 0
        self.lateral = self.vertical = 0.0

    @staticmethod
    def response(old, desired, dt, tau):
        """Exact first-order velocity and displacement for constant input."""
        if tau == 0:
            return desired, desired * dt
        decay = math.exp(-dt / tau)
        return (desired + (old - desired) * decay,
                desired * dt + (old - desired) * tau * (1 - decay))

    def advance(self, dt, now_ns, steady_now, target_vx=0.0, target_vy=0.0):
        if not all(math.isfinite(v) for v in (dt, target_vx, target_vy)) or dt <= 0:
            raise ValueError('Step must be positive and finite; target rates finite')
        self.lateral, self.vertical = self.gate.command(now_ns, steady_now)
        self.vx, dx = self.response(self.vx, self.config.gain_x * self.lateral,
                                    dt, self.config.response_tau)
        self.vy, dy = self.response(self.vy, self.config.gain_y * self.vertical,
                                    dt, self.config.response_tau)
        # True errors remain unbounded: leaving the field of view loses detection.
        self.x += target_vx * dt - dx
        self.y += target_vy * dt + dy

    def capture(self, now_ns, steady_now, mode='normal'):
        if mode not in ('normal', 'missing', 'ambiguous', 'silence'):
            raise ValueError('Unknown sensor mode')
        if mode == 'silence':
            return
        self.captured += 1
        x = self.x + self.rng.gauss(0, self.config.noise_stddev)
        y = self.y + self.rng.gauss(0, self.config.noise_stddev)
        visible = (not self.gate.terminal and abs(self.x) <= 1 and abs(self.y) <= 1
                   and abs(x) <= 1 and abs(y) <= 1)
        if mode == 'normal' and visible:
            observation = Observation(now_ns, True, x, y, 1)
        elif mode == 'ambiguous' and visible:
            observation = Observation(now_ns, True, 0.0, 0.0, 2)
        else:
            observation = Observation(now_ns, False, 0.0, 0.0, 0)
        self.pending.append((steady_now + self.config.sensor_delay, observation))
        # The ROS wrapper validates rate/delay; this also bounds standalone users.
        if len(self.pending) > 512:
            self.pending.popleft()

    def deliver(self, steady_now):
        observations = []
        while self.pending and self.pending[0][0] <= steady_now:
            _, observation = self.pending.popleft()
            observations.append(observation)
        self.delivered += len(observations)
        return observations

    def state(self, elapsed):
        return dict(elapsed=elapsed, error_x=self.x, error_y=self.y,
                    apparent_velocity_x=self.vx, apparent_velocity_up=self.vy,
                    lateral=self.lateral, vertical=self.vertical,
                    consumer_mode=self.gate.mode, mock_terminal=self.gate.terminal,
                    captured=self.captured, delivered=self.delivered,
                    pending=len(self.pending))
