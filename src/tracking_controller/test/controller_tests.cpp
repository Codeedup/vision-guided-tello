#include <gtest/gtest.h>
#include "tracking_controller/controller.hpp"
#include <limits>
#include <cmath>
#include "tracking_controller/target_safety.hpp"

TEST(Controller, HandRightRequestsRight)
{
    auto command = tracking_controller::calculate_command(0.5, 0.0);

    EXPECT_GT(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, HandLeftRequestsLeft)
{
    auto command = tracking_controller::calculate_command(-0.5, 0.0);

    EXPECT_LT(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, HandAboveRequestsUp)
{
    auto command = tracking_controller::calculate_command(0.0, -0.5);

    EXPECT_GT(command.vertical, 0.0);
    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
}

TEST(Controller, HandBelowRequestsDown)
{
    auto command = tracking_controller::calculate_command(0.0, 0.5);

    EXPECT_LT(command.vertical, 0.0);
    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
}

TEST(Controller, CentredHandRequestsNoMovement)
{
    auto command = tracking_controller::calculate_command(0.0, 0.0);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, SmallErrorsRequestNoMovement)
{
    auto command = tracking_controller::calculate_command(0.04, -0.04);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, DeadZoneBoundaryRequestsNoMovement)
{
    auto command = tracking_controller::calculate_command(0.08, -0.08);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, HorizontalDeadZoneStillAllowsVerticalMovement)
{
    auto command = tracking_controller::calculate_command(0.04, -0.5);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_GT(command.vertical, 0.0);
}

TEST(Controller, VerticalDeadZoneStillAllowsHorizontalMovement)
{
    auto command = tracking_controller::calculate_command(-0.5, 0.04);

    EXPECT_LT(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, PositiveCommandsAreLimited)
{
    auto command = tracking_controller::calculate_command(0.9, -0.9);

    EXPECT_DOUBLE_EQ(command.lateral, 10.0);
    EXPECT_DOUBLE_EQ(command.vertical, 10.0);
}

TEST(Controller, NegativeCommandsAreLimited)
{
    auto command = tracking_controller::calculate_command(-0.9, 0.9);

    EXPECT_DOUBLE_EQ(command.lateral, -10.0);
    EXPECT_DOUBLE_EQ(command.vertical, -10.0);
}

TEST(Controller, CommandsBelowLimitRemainUnchanged)
{
    auto command = tracking_controller::calculate_command(0.25, 0.2);

    EXPECT_DOUBLE_EQ(command.lateral, 5.0);
    EXPECT_DOUBLE_EQ(command.vertical, -4.0);
}

TEST(Controller, InvalidHorizontalNumberStopsBothAxes)
{
    double invalid = std::numeric_limits<double>::quiet_NaN();
    auto command = tracking_controller::calculate_command(invalid, 0.5);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, InfiniteVerticalValueStopsBothAxes)
{
    double infinite = std::numeric_limits<double>::infinity();
    auto command = tracking_controller::calculate_command(0.5, infinite);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, HorizontalValueOutsideImageStopsBothAxes)
{
    auto command = tracking_controller::calculate_command(1.1, -0.5);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, VerticalValueOutsideImageStopsBothAxes)
{
    auto command = tracking_controller::calculate_command(0.5, -1.1);

    EXPECT_DOUBLE_EQ(command.lateral, 0.0);
    EXPECT_DOUBLE_EQ(command.vertical, 0.0);
}

TEST(Controller, ImageBoundaryValuesRemainValid)
{
    auto command = tracking_controller::calculate_command(1.0, -1.0);

    EXPECT_DOUBLE_EQ(command.lateral, 10.0);
    EXPECT_DOUBLE_EQ(command.vertical, 10.0);
}

TEST(TargetSafety, ValidTimestampFormsAreAccepted)
{
    EXPECT_TRUE(tracking_controller::is_valid_stamp(1, 0));
    EXPECT_TRUE(tracking_controller::is_valid_stamp(0, 1));
    EXPECT_TRUE(
        tracking_controller::is_valid_stamp(1, 999999999U));
}

TEST(TargetSafety, MissingTimestampIsRejected)
{
    EXPECT_FALSE(tracking_controller::is_valid_stamp(0, 0));
}

TEST(TargetSafety, MalformedTimestampIsRejected)
{
    EXPECT_FALSE(tracking_controller::is_valid_stamp(-1, 0));
    EXPECT_FALSE(
        tracking_controller::is_valid_stamp(1, 1000000000U));
}

TEST(TargetSafety, AgesBelowTimeoutAreFresh)
{
    EXPECT_TRUE(tracking_controller::is_fresh_age(0.0));
    EXPECT_TRUE(tracking_controller::is_fresh_age(0.1));

    const double just_before_timeout =
    std::nextafter(0.25, 0.0);

    EXPECT_TRUE(
        tracking_controller::is_fresh_age(just_before_timeout));
}

TEST(TargetSafety, ExactTimeoutAndOlderAgesAreRejected)
{
    EXPECT_FALSE(tracking_controller::is_fresh_age(0.25));
    EXPECT_FALSE(tracking_controller::is_fresh_age(0.3));
}

TEST(TargetSafety, FutureObservationIsRejected)
{
    EXPECT_FALSE(tracking_controller::is_fresh_age(-0.001));
}

TEST(TargetSafety, NonFiniteAgesAreRejected)
{
    const double nan =
    std::numeric_limits<double>::quiet_NaN();

    const double infinity =
    std::numeric_limits<double>::infinity();

    EXPECT_FALSE(tracking_controller::is_fresh_age(nan));
    EXPECT_FALSE(tracking_controller::is_fresh_age(infinity));
    EXPECT_FALSE(tracking_controller::is_fresh_age(-infinity));
}

TEST(TargetSafety, FreshSourceAndReceiptDoNotExpire)
{
    EXPECT_FALSE(
        tracking_controller::target_expired(0.1, 0.1));
}

TEST(TargetSafety, OldSourceExpiresDespiteRecentReceipt)
{
    EXPECT_TRUE(
        tracking_controller::target_expired(0.25, 0.01));
}

TEST(TargetSafety, ReceiptTimeoutExpiresDespiteFrozenSourceClock)
{
    EXPECT_TRUE(
        tracking_controller::target_expired(0.0, 0.25));
}

TEST(TargetSafety, NegativeClockAgesCauseExpiry)
{
    EXPECT_TRUE(
        tracking_controller::target_expired(-0.01, 0.1));

    EXPECT_TRUE(
        tracking_controller::target_expired(0.1, -0.01));
}

TEST(TargetSafety, NonFiniteClockAgesCauseExpiry)
{
    const double nan =
    std::numeric_limits<double>::quiet_NaN();

    const double infinity =
    std::numeric_limits<double>::infinity();

    EXPECT_TRUE(
        tracking_controller::target_expired(nan, 0.1));

    EXPECT_TRUE(
        tracking_controller::target_expired(0.1, infinity));
}
