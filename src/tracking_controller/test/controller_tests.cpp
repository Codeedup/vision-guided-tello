#include <gtest/gtest.h>
#include "tracking_controller/controller.hpp"
#include <limits>

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