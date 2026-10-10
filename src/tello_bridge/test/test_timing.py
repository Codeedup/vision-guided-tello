"""Cadence, delayed renewals and bounded diagnostic evidence without ROS."""

import json

import pytest
from tello_bridge.cadence import run_heartbeats
from tello_bridge.policy import Authority, Command, Telemetry
from tello_bridge.timing import TimingTrace

EPOCH = 1800000000000000000


class VirtualClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def spin(self, timeout_sec):
        # Ready executor work can complete far sooner than the timeout.
        self.now += min(0.001, timeout_sec)


def prepared():
    guard = Authority()
    guard.link(True)
    guard.flight_state = 'HOVER_CONFIRMED'
    guard.telemetry = Telemetry(0, 80, 70)
    assert guard.accept(Command(EPOCH, None, False, False, False, 0, 0), EPOCH, 0)
    assert guard.arm(EPOCH, 0)
    return guard


def test_ready_work_cannot_burst_or_expire_ten_second_paced_session():
    clock = VirtualClock()
    guard = prepared()
    sends = []

    def heartbeat():
        sends.append(clock())
        assert guard.heartbeat(round(clock() * 1000000000))

    run_heartbeats(heartbeat, clock.spin, 10, clock=clock)
    assert 124 <= len(sends) <= 126
    assert all(b - a >= 0.079999 for a, b in zip(sends, sends[1:]))
    assert max(b - a for a, b in zip(sends, sends[1:])) < 0.081
    assert guard.armed and not guard.landing_latched


def test_late_execution_skips_missed_slots_without_catchup():
    clock = VirtualClock()
    sends = []

    def spin(timeout_sec):
        clock.now += 0.21 if len(sends) == 1 else timeout_sec

    run_heartbeats(lambda: sends.append(clock()), spin, 0.6, clock=clock)
    assert sends[:2] == [0.0, 0.21]
    assert all(b - a >= 0.079999 for a, b in zip(sends, sends[1:]))


@pytest.mark.parametrize('error', [KeyboardInterrupt, RuntimeError])
def test_interrupt_or_rejection_stops_sending_for_existing_cleanup(error):
    clock = VirtualClock()
    sends = []

    def heartbeat():
        sends.append(clock())
        raise error()

    with pytest.raises(error):
        run_heartbeats(heartbeat, clock.spin, 10, clock=clock)
    assert sends == [0]


def test_legacy_comparison_reproduces_ready_work_bursts():
    clock = VirtualClock()
    sends = []
    run_heartbeats(lambda: sends.append(clock()), clock.spin, 0.1,
                   legacy=True, clock=clock)
    assert len(sends) >= 90


def test_first_expiry_survives_later_reason_changes_and_ring_eviction(tmp_path):
    path = tmp_path / 'bridge.json'
    trace = TimingTrace(path, fake=True, capacity=2)
    guard = prepared()
    with trace.measure('operator_callback', guard, action='heartbeat') as event:
        event['accepted'] = guard.heartbeat(300000000)
    assert not event['accepted']
    with trace.measure('approved_callback', guard):
        assert guard.accept(Command(EPOCH + 1, None, False, False, False, 0, 0),
                            EPOCH + 1, 1)
    assert guard.reason == 'SUPERVISOR_DISABLED'
    trace.record('extra')
    assert not guard.heartbeat(300000001)
    trace.finish()
    trace.finish()  # Idempotent; no second write.
    report = json.loads(path.read_text())
    assert report['events_retained'] == 2 and report['events_omitted'] == 1
    assert report['first_expiry']['after']['reason'] == 'OPERATOR_LEASE_EXPIRED'
    assert report['first_expiry']['accepted'] is False
    assert report['first_fault'] == report['first_expiry']
    assert not report['first_expiry']['after']['armed']


def test_normal_renewal_records_actual_server_receipt_and_tick_gaps(tmp_path):
    trace = TimingTrace(tmp_path / 'trace.json', fake=True)
    guard = prepared()
    with trace.measure('operator_callback', guard):
        assert guard.heartbeat(80000000)
    with trace.measure('policy_tick', guard):
        pass
    with trace.measure('policy_tick', guard):
        pass
    trace.finish()
    report = json.loads((tmp_path / 'trace.json').read_text())
    events = report['events']
    assert events[0]['before']['operator_receipt_ns'] == 0
    assert events[0]['after']['operator_receipt_ns'] == 80000000
    assert events[2]['inter_tick_gap_ns'] >= 0
    assert report['first_expiry'] is None


def test_trace_refuses_real_transport_and_existing_evidence(tmp_path):
    path = tmp_path / 'existing.json'
    path.write_text('keep me')
    with pytest.raises(ValueError, match='FAKE'):
        TimingTrace(tmp_path / 'real.json', fake=False)
    with pytest.raises(FileExistsError):
        TimingTrace(path, fake=True)
    assert path.read_text() == 'keep me'
    for capacity in (0, 100001, True):
        with pytest.raises(ValueError):
            TimingTrace(tmp_path / 'invalid.json', fake=True, capacity=capacity)


def test_failed_callback_evidence_is_flushed_without_suppressing_error(tmp_path):
    path = tmp_path / 'error.json'
    trace = TimingTrace(path, fake=True)
    with pytest.raises(RuntimeError):
        with trace.measure('policy_tick', prepared()):
            raise RuntimeError('test')
    trace.finish('interrupted')
    report = json.loads(path.read_text())
    assert report['completion'] == 'interrupted'
    assert report['events'][0]['exception'] == 'RuntimeError'
