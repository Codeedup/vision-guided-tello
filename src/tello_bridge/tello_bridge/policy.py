"""
Independent ApprovedCommand validation and explicit operator authority.

Times are integer nanoseconds. Output units are provisional SDK command scalars.
No method performs I/O. Lifecycle evidence must be supplied separately from ROS
supervisor flags; height or an SDK is_flying cache cannot establish hover.
"""

from dataclasses import dataclass
import math

FRESH_NS = 250_000_000


@dataclass(frozen=True)
class Command:
    decision_ns: int
    source_ns: int | None
    enabled: bool
    tracking: bool
    land: bool
    lateral: float
    vertical: float


@dataclass(frozen=True)
class Limits:
    minimum_battery_percent: float = 25.0
    minimum_height_cm: float = 30.0
    maximum_height_cm: float = 150.0
    telemetry_timeout_ns: int = 500_000_000
    operator_timeout_ns: int = 300_000_000
    input_loss_land_ns: int = 1_500_000_000
    maximum_land_attempts: int = 3
    land_retry_ns: int = 1_000_000_000

    def __post_init__(self):
        values = vars(self)
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in values.values()):
            raise ValueError('Limits must be finite numbers')
        if not 0 <= self.minimum_battery_percent <= 100:
            raise ValueError('Battery percentage outside [0,100]')
        if not 0 <= self.minimum_height_cm < self.maximum_height_cm:
            raise ValueError('Height bounds are inconsistent')
        for key in ('telemetry_timeout_ns', 'operator_timeout_ns', 'input_loss_land_ns',
                    'maximum_land_attempts', 'land_retry_ns'):
            if type(values[key]) is not int or values[key] <= 0:
                raise ValueError(key + ' must be a positive integer')


@dataclass(frozen=True)
class Telemetry:
    receipt_ns: int
    battery_percent: float | None
    height_cm: float | None


def stamp_valid(value):
    return type(value) is int and 0 < value <= 2_147_483_647_999_999_999


def fresh(now, stamp, limit=FRESH_NS):
    return stamp_valid(stamp) and 0 <= now - stamp < limit


def sdk_integer(value):
    """Round halves away from zero, after independent +/-10 validation."""
    if type(value) not in (float, int) or not math.isfinite(value) or abs(value) > 10:
        raise ValueError('Command outside finite development cap +/-10')
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


class Authority:
    """Single latest command, no backlog, sticky landing, explicit leased arm."""

    def __init__(self, limits=None):
        self.limits = limits or Limits()
        self.connected = False
        self.armed = False
        self.awaiting_enabled = False
        self.flight_state = 'UNKNOWN'
        self.landing_latched = False
        self.telemetry = None
        self.command = None
        self.receipt_ns = None
        self.decision_watermark = 0
        self.source_watermark = 0
        self.disabled_seen = False
        self.arm_barrier_ns = 0
        self.operator_receipt_ns = None
        self.last_ros_ns = self.last_mono_ns = None
        self.loss_start_ns = None
        self.land_attempts = 0
        self.last_land_ns = None
        self.reason = 'STARTUP_DISABLED'

    def link(self, connected):
        self.connected = bool(connected)
        self.disarm('LINK_CHANGE')
        self.telemetry = None
        self.flight_state = 'UNKNOWN'
        self.disabled_seen = False
        # Landing is a lifecycle latch and survives reconnect/manual takeover.

    def disarm(self, reason='OPERATOR_DISABLE'):
        self.armed = False
        self.awaiting_enabled = False
        self.command = None
        self.receipt_ns = None
        self.operator_receipt_ns = None
        self.loss_start_ns = None
        self.reason = reason

    def request_land(self, reason='OPERATOR_LAND'):
        self.landing_latched = True
        self.flight_state = 'LANDING_REQUESTED'
        self.disarm(reason)

    def clock_ok(self, ros_ns, mono_ns):
        well_formed = (type(ros_ns) is int and type(mono_ns) is int
                       and ros_ns > 0 and mono_ns >= 0)
        valid = well_formed
        if valid and self.last_mono_ns is not None:
            valid = (mono_ns >= self.last_mono_ns and ros_ns >= self.last_ros_ns
                     and abs((ros_ns - self.last_ros_ns) -
                             (mono_ns - self.last_mono_ns)) <= 50_000_000)
        if not valid:
            if self.armed:
                self.request_land('CLOCK_FAULT')
            else:
                self.disarm('CLOCK_FAULT')
        if well_formed:
            self.last_ros_ns, self.last_mono_ns = ros_ns, mono_ns
        return valid

    def telemetry_ok(self, mono_ns):
        data = self.telemetry
        if data is None or not 0 <= mono_ns - data.receipt_ns < self.limits.telemetry_timeout_ns:
            return False
        battery, height = data.battery_percent, data.height_cm
        if any(type(v) not in (float, int) or not math.isfinite(v) for v in (battery, height)):
            return False
        return (self.limits.minimum_battery_percent <= battery <= 100
                and self.limits.minimum_height_cm <= height <= self.limits.maximum_height_cm)

    def arm(self, ros_ns, mono_ns):
        if not self.clock_ok(ros_ns, mono_ns):
            return False
        if (not self.connected or not self.disabled_seen or self.landing_latched
                or self.flight_state != 'HOVER_CONFIRMED' or not self.telemetry_ok(mono_ns)):
            self.reason = 'ARM_INTERLOCK'
            return False
        self.arm_barrier_ns = ros_ns
        self.armed = True
        self.awaiting_enabled = True
        self.disabled_seen = False
        self.operator_receipt_ns = mono_ns
        self.command = None
        self.receipt_ns = None
        self.loss_start_ns = mono_ns
        self.reason = 'ARMED_WAITING_FOR_NEW_DECISION'
        return True

    def heartbeat(self, mono_ns):
        if (self.armed and self.operator_receipt_ns is not None
                and 0 <= mono_ns - self.operator_receipt_ns < self.limits.operator_timeout_ns):
            self.operator_receipt_ns = mono_ns
            return True
        if self.armed:
            self.request_land('OPERATOR_LEASE_EXPIRED')
        return False

    def accept(self, command, ros_ns, mono_ns):
        if not self.clock_ok(ros_ns, mono_ns):
            return False
        if not isinstance(command, Command):
            self.command = None
            self.reason = 'INVALID_TYPE'
            return False
        c = command
        valid = all(type(v) is bool for v in (c.enabled, c.tracking, c.land))
        valid = valid and fresh(ros_ns, c.decision_ns)
        valid = valid and c.decision_ns > self.decision_watermark
        valid = valid and not (c.tracking and (not c.enabled or c.land))
        valid = valid and not (c.land and not c.enabled)
        try:
            sdk_integer(c.lateral)
            sdk_integer(c.vertical)
        except ValueError:
            valid = False
        valid = valid and (c.tracking or (c.lateral == 0 and c.vertical == 0))
        if c.tracking:
            valid = valid and fresh(ros_ns, c.source_ns)
            valid = valid and c.source_ns >= self.source_watermark
            valid = valid and c.source_ns <= c.decision_ns
        if not valid:
            self.command = None
            self.reason = 'REJECTED_COMMAND'
            return False
        self.decision_watermark = c.decision_ns
        if c.tracking:
            self.source_watermark = c.source_ns
        if c.land:
            self.request_land('SUPERVISOR_LAND')
            return True
        if not c.enabled:
            self.disabled_seen = True
            if self.armed and self.awaiting_enabled:
                # Arm is a leased, neutral preparation step. Disabled heartbeats
                # cannot race it away before the explicit upstream enable arrives.
                self.command = None
                self.receipt_ns = None
            else:
                self.disarm('SUPERVISOR_DISABLED')
            return True
        self.disabled_seen = False
        # A fresh enabled decision alone never arms after restart/reconnect.
        if self.armed and c.decision_ns > self.arm_barrier_ns:
            self.awaiting_enabled = False
            self.command, self.receipt_ns = c, mono_ns
        return True

    def action(self, ros_ns, mono_ns):
        """Revalidate immediately before a nonblocking send. Return RC or land."""
        if not self.clock_ok(ros_ns, mono_ns):
            return ('rc', (0, 0, 0, 0))
        if self.armed:
            if not self.telemetry_ok(mono_ns) or self.flight_state != 'HOVER_CONFIRMED':
                self.request_land('TELEMETRY_OR_LIFECYCLE_FAULT')
            elif (self.operator_receipt_ns is None or not
                  0 <= mono_ns - self.operator_receipt_ns < self.limits.operator_timeout_ns):
                self.request_land('OPERATOR_LEASE_EXPIRED')
        c = self.command
        active = (self.connected and self.armed and not self.landing_latched and c is not None
                  and c.tracking and fresh(ros_ns, c.decision_ns) and fresh(ros_ns, c.source_ns)
                  and self.receipt_ns is not None and 0 <= mono_ns - self.receipt_ns < FRESH_NS)
        if active:
            self.loss_start_ns = None
            self.reason = 'TRACKING'
            return ('rc', (sdk_integer(c.lateral), 0, sdk_integer(c.vertical), 0))
        if self.armed:
            self.reason = ('ARMED_WAITING_FOR_NEW_DECISION' if self.awaiting_enabled
                           else 'NEUTRAL_INPUT_EXPIRED_OR_INVALID')
            if self.loss_start_ns is None:
                self.loss_start_ns = mono_ns
            if mono_ns - self.loss_start_ns >= self.limits.input_loss_land_ns:
                self.request_land('APPROVED_INPUT_LOSS')
        if self.connected and self.landing_latched:
            if (self.land_attempts < self.limits.maximum_land_attempts and
                    (self.last_land_ns is None or
                     mono_ns - self.last_land_ns >= self.limits.land_retry_ns)):
                self.land_attempts += 1
                self.last_land_ns = mono_ns
                return ('land', None)
        # Unarmed ownership yields to the operator; never continuously fight RC.
        return ('rc', (0, 0, 0, 0)) if self.armed or self.landing_latched else ('yield', None)

    def status(self):
        return {'connected': self.connected, 'armed': self.armed,
                'awaiting_enabled': self.awaiting_enabled,
                'flight_state': self.flight_state, 'landing_latched': self.landing_latched,
                'reason': self.reason, 'land_attempts': self.land_attempts}
