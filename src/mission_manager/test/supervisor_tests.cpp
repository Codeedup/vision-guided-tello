#include <gtest/gtest.h>

#include "mission_manager/supervisor.hpp"
#include <cstdint>
#include <limits>

TEST(Supervisor, StartsDisabledWithoutMovementOrLandingRequest)
{
    mission_manager::Supervisor supervisor;

    const auto decision = supervisor.decision();

    EXPECT_EQ(
        decision.state,
        mission_manager::AutonomyState::DISABLED);
    EXPECT_FALSE(decision.allow_tracking);
    EXPECT_FALSE(decision.request_land);
}

TEST(Supervisor, EnablingRequiresAcquisitionBeforeMovement)
{
    mission_manager::Supervisor supervisor;

    supervisor.enable_autonomy();

    const auto decision = supervisor.decision();

    EXPECT_EQ(
        decision.state,
        mission_manager::AutonomyState::ACQUIRE);
    EXPECT_FALSE(decision.allow_tracking);
    EXPECT_FALSE(decision.request_land);
}

TEST(Supervisor, DisablingDuringAcquisitionBlocksAutonomy)
{
    mission_manager::Supervisor supervisor;

    supervisor.enable_autonomy();
    supervisor.disable_autonomy();

    const auto decision = supervisor.decision();

    EXPECT_EQ(
        decision.state,
        mission_manager::AutonomyState::DISABLED);
    EXPECT_FALSE(decision.allow_tracking);
    EXPECT_FALSE(decision.request_land);
}

TEST(Supervisor, ManualTakeoverRequiresExplicitReenable)
{
    mission_manager::Supervisor supervisor;

    supervisor.enable_autonomy();
    supervisor.manual_takeover();

    const auto after_takeover = supervisor.decision();

    EXPECT_EQ(
        after_takeover.state,
        mission_manager::AutonomyState::DISABLED);
    EXPECT_FALSE(after_takeover.allow_tracking);
    EXPECT_FALSE(after_takeover.request_land);

    supervisor.enable_autonomy();

    const auto after_reenable = supervisor.decision();

    EXPECT_EQ(
        after_reenable.state,
        mission_manager::AutonomyState::ACQUIRE);
    EXPECT_FALSE(after_reenable.allow_tracking);
}

namespace
{
    void send_fresh_targets(
        mission_manager::Supervisor & supervisor,
        std::int64_t first_stamp_ns,
        unsigned count)
    {
        for (unsigned i = 0; i < count; ++i)
        {
            const auto stamp =
                first_stamp_ns +
                static_cast<std::int64_t>(i) * 100000000;

            supervisor.observe_target(stamp, true, 0.01);
        }
    }
}

TEST(Supervisor, FiveFreshObservationsAreRequiredForTracking)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 1000000000, 4);

    EXPECT_EQ(
        supervisor.decision().state,
        mission_manager::AutonomyState::ACQUIRE);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(1400000000, true, 0.01);

    EXPECT_EQ(
        supervisor.decision().state,
        mission_manager::AutonomyState::TRACK);
    EXPECT_TRUE(supervisor.decision().allow_tracking);
    EXPECT_FALSE(supervisor.decision().request_land);
}

TEST(Supervisor, DuplicateAndOlderObservationsDoNotAdvanceAcquisition)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 1000000000, 4);

    for (unsigned i = 0; i < 10; ++i)
    {
        supervisor.observe_target(1300000000, true, 0.01);
    }

    supervisor.observe_target(1200000000, true, 0.01);

    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(1400000000, true, 0.01);

    EXPECT_TRUE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, InvalidObservationResetsAcquisition)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 1000000000, 4);

    supervisor.observe_target(1400000000, false, 0.01);

    send_fresh_targets(supervisor, 1500000000, 4);

    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(1900000000, true, 0.01);

    EXPECT_TRUE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, UnusableAgesResetAcquisition)
{
    const double rejected_ages[] = {
        -0.01,
        0.25,
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity()
    };

    for (double age : rejected_ages)
    {
        mission_manager::Supervisor supervisor;
        supervisor.enable_autonomy();

        send_fresh_targets(supervisor, 1000000000, 4);

        supervisor.observe_target(1400000000, true, age);

        send_fresh_targets(supervisor, 1500000000, 4);

        EXPECT_EQ(
            supervisor.decision().state,
            mission_manager::AutonomyState::ACQUIRE);
        EXPECT_FALSE(supervisor.decision().allow_tracking);
    }
}

TEST(Supervisor, GapAtFreshnessLimitBreaksAcquisition)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 1000000000, 4);

    // Previous stamp was 1.3 seconds: this gap is exactly 250 ms.
    supervisor.observe_target(1550000000, true, 0.01);

    send_fresh_targets(supervisor, 1650000000, 3);

    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(1950000000, true, 0.01);

    EXPECT_TRUE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, LossBlocksTrackingUntilStableReacquisition)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 1000000000, 5);

    ASSERT_TRUE(supervisor.decision().allow_tracking);

    supervisor.observe_target(1500000000, false, 0.01);

    EXPECT_EQ(
        supervisor.decision().state,
        mission_manager::AutonomyState::LOST_HOVER);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    send_fresh_targets(supervisor, 1600000000, 4);

    EXPECT_EQ(
        supervisor.decision().state,
        mission_manager::AutonomyState::LOST_HOVER);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(2000000000, true, 0.01);

    EXPECT_TRUE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, DisabledObservationsAndManualTakeoverCannotEnableTracking)
{
    mission_manager::Supervisor supervisor;

    send_fresh_targets(supervisor, 1000000000, 5);

    EXPECT_EQ(
        supervisor.decision().state,
        mission_manager::AutonomyState::DISABLED);

    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 2000000000, 5);

    ASSERT_TRUE(supervisor.decision().allow_tracking);

    supervisor.manual_takeover();
    send_fresh_targets(supervisor, 3000000000, 5);

    EXPECT_EQ(
        supervisor.decision().state,
        mission_manager::AutonomyState::DISABLED);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 4000000000, 4);

    EXPECT_FALSE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, DisableAndReenableClearsPartialAcquisition)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 1000000000, 4);

    supervisor.disable_autonomy();
    supervisor.enable_autonomy();

    send_fresh_targets(supervisor, 2000000000, 4);

    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(2400000000, true, 0.01);

    EXPECT_TRUE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, FreshSourceAndReceiptKeepTracking)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 1000000000, 5);

    supervisor.update_time(0.24, 0.24);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::TRACK);
    EXPECT_TRUE(supervisor.decision().allow_tracking);
    EXPECT_FALSE(supervisor.decision().request_land);
}

TEST(Supervisor, SourceExpiryBlocksTrackingDespiteRecentReceipt)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 1000000000, 5);

    // The observation expires at exactly 250 ms.
    supervisor.update_time(0.25, 0.01);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::LOST_HOVER);
    EXPECT_FALSE(supervisor.decision().allow_tracking);
    EXPECT_FALSE(supervisor.decision().request_land);
}

TEST(Supervisor, ReceiptExpiryBlocksTrackingDespiteFrozenSourceClock)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 1000000000, 5);

    // ROS time appears fresh, but local receipt time has expired.
    supervisor.update_time(0.01, 0.25);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::LOST_HOVER);
    EXPECT_FALSE(supervisor.decision().allow_tracking);
}

TEST(Supervisor, InvalidClockAgesBlockTracking)
{
    const double invalid_ages[] = {
        -0.01,
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity()
    };

    for (double invalid_age : invalid_ages)
    {
        for (bool invalid_source : {true, false})
        {
            mission_manager::Supervisor supervisor;
            supervisor.enable_autonomy();
            send_fresh_targets(supervisor, 1000000000, 5);

            supervisor.update_time(
                invalid_source ? invalid_age : 0.01,
                invalid_source ? 0.01 : invalid_age);

            EXPECT_EQ(supervisor.decision().state,
                      mission_manager::AutonomyState::LOST_HOVER);
            EXPECT_FALSE(supervisor.decision().allow_tracking);
        }
    }
}

TEST(Supervisor, TimeoutClearsPartialAcquisition)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 1000000000, 4);

    supervisor.update_time(0.25, 0.25);

    // Four new observations must still be insufficient.
    send_fresh_targets(supervisor, 1400000000, 4);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::ACQUIRE);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.observe_target(1800000000, true, 0.01);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::TRACK);
    EXPECT_TRUE(supervisor.decision().allow_tracking);
}
namespace
{
void start_timed_loss(mission_manager::Supervisor &supervisor)
{
    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 1000000000, 5);

    supervisor.update_loss_timer(10.0);

    // Freshness expires independently of incoming messages.
    supervisor.update_time(0.25, 0.25);
    supervisor.update_loss_timer(10.0);
}
}

TEST(Supervisor, SustainedLossRequestsLandingAtExactDeadline)
{
    mission_manager::Supervisor supervisor;
    start_timed_loss(supervisor);

    supervisor.update_loss_timer(11.49);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::LOST_HOVER);
    EXPECT_FALSE(supervisor.decision().allow_tracking);
    EXPECT_FALSE(supervisor.decision().request_land);

    supervisor.update_loss_timer(11.5);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::LANDING_REQUESTED);
    EXPECT_FALSE(supervisor.decision().allow_tracking);
    EXPECT_TRUE(supervisor.decision().request_land);
}

TEST(Supervisor, InvalidMessagesAndPartialRecoveryDoNotExtendDeadline)
{
    mission_manager::Supervisor supervisor;
    start_timed_loss(supervisor);

    supervisor.update_loss_timer(10.5);
    supervisor.observe_target(1500000000, false, 0.01);

    supervisor.update_loss_timer(11.0);
    send_fresh_targets(supervisor, 1600000000, 4);

    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.update_loss_timer(11.5);

    EXPECT_TRUE(supervisor.decision().request_land);
}

TEST(Supervisor, StableRecoveryClearsPreviousLossInterval)
{
    mission_manager::Supervisor supervisor;
    start_timed_loss(supervisor);

    supervisor.update_loss_timer(10.5);
    send_fresh_targets(supervisor, 1600000000, 5);

    EXPECT_TRUE(supervisor.decision().allow_tracking);

    // A later loss must receive its own full interval.
    supervisor.update_loss_timer(11.0);
    supervisor.update_time(0.25, 0.25);
    supervisor.update_loss_timer(11.0);

    supervisor.update_loss_timer(12.49);
    EXPECT_FALSE(supervisor.decision().request_land);

    supervisor.update_loss_timer(12.5);
    EXPECT_TRUE(supervisor.decision().request_land);
}

TEST(Supervisor, InitialAcquisitionCannotWaitIndefinitely)
{
    mission_manager::Supervisor supervisor;
    supervisor.enable_autonomy();

    supervisor.update_loss_timer(10.0);
    supervisor.update_loss_timer(11.49);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::ACQUIRE);
    EXPECT_FALSE(supervisor.decision().request_land);

    supervisor.update_loss_timer(11.5);

    EXPECT_TRUE(supervisor.decision().request_land);
}

TEST(Supervisor, LandingRemainsLatchedUntilManualTakeover)
{
    mission_manager::Supervisor supervisor;
    start_timed_loss(supervisor);

    // Evaluate the deadline before processing late observations.
    supervisor.update_loss_timer(11.5);

    supervisor.disable_autonomy();
    supervisor.enable_autonomy();
    send_fresh_targets(supervisor, 2000000000, 5);

    EXPECT_TRUE(supervisor.decision().request_land);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.manual_takeover();
    send_fresh_targets(supervisor, 3000000000, 5);

    EXPECT_EQ(supervisor.decision().state,
              mission_manager::AutonomyState::DISABLED);
    EXPECT_FALSE(supervisor.decision().request_land);
    EXPECT_FALSE(supervisor.decision().allow_tracking);

    supervisor.enable_autonomy();
    supervisor.update_loss_timer(20.0);
    send_fresh_targets(supervisor, 4000000000, 5);

    EXPECT_TRUE(supervisor.decision().allow_tracking);
    EXPECT_FALSE(supervisor.decision().request_land);
}

TEST(Supervisor, InvalidOrBackwardTimerTimeRequestsLanding)
{
    const double invalid_times[] = {
        -0.01,
        9.0,  // Earlier than the previously supplied time of 10.0.
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity()
    };

    for (double invalid_time : invalid_times)
    {
        mission_manager::Supervisor supervisor;
        start_timed_loss(supervisor);

        supervisor.update_loss_timer(invalid_time);

        EXPECT_TRUE(supervisor.decision().request_land);
        EXPECT_FALSE(supervisor.decision().allow_tracking);
    }
}