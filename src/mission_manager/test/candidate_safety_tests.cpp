#include <gtest/gtest.h>
#include <limits>

#include "mission_manager/candidate_safety.hpp"

TEST(CandidateSafety, TimestampValidation)
{
    EXPECT_TRUE(mission_manager::candidate_stamp_is_valid(1, 0));
    EXPECT_TRUE(mission_manager::candidate_stamp_is_valid(0, 1));
    EXPECT_TRUE(
        mission_manager::candidate_stamp_is_valid(1, 999999999U));

    EXPECT_FALSE(mission_manager::candidate_stamp_is_valid(0, 0));
    EXPECT_FALSE(mission_manager::candidate_stamp_is_valid(-1, 0));
    EXPECT_FALSE(
        mission_manager::candidate_stamp_is_valid(1, 1000000000U));
}

TEST(CandidateSafety, FreshBoundedValuesAreAccepted)
{
    EXPECT_TRUE(mission_manager::candidate_values_are_usable(
        true, 10.0, -10.0, 0.24));

    EXPECT_TRUE(mission_manager::candidate_values_are_usable(
        true, -10.0, 10.0, 0.01));

    // A centred target can legitimately produce zero commands.
    EXPECT_TRUE(mission_manager::candidate_values_are_usable(
        true, 0.0, 0.0, 0.0));
}

TEST(CandidateSafety, ControllerRejectionBlocksCandidate)
{
    EXPECT_FALSE(mission_manager::candidate_values_are_usable(
        false, 0.0, 0.0, 0.01));
}

TEST(CandidateSafety, InvalidSourceAgesAreRejected)
{
    const double invalid_ages[] = {
    -0.01,
    0.25,
    1.0,
    std::numeric_limits<double>::quiet_NaN(),
    std::numeric_limits<double>::infinity()
    };

    for (double age : invalid_ages) {
    EXPECT_FALSE(mission_manager::candidate_values_are_usable(
            true, 5.0, 5.0, age));
    }
}

TEST(CandidateSafety, NonFiniteCommandsAreRejected)
{
    const double invalid_values[] = {
    std::numeric_limits<double>::quiet_NaN(),
    std::numeric_limits<double>::infinity(),
    -std::numeric_limits<double>::infinity()
    };

    for (double value : invalid_values) {
    EXPECT_FALSE(mission_manager::candidate_values_are_usable(
            true, value, 0.0, 0.01));

    EXPECT_FALSE(mission_manager::candidate_values_are_usable(
            true, 0.0, value, 0.01));
    }
}

TEST(CandidateSafety, CommandsBeyondEitherLimitAreRejected)
{
    const double invalid_values[] = {
    10.0001,
    -10.0001,
    11.0,
    -11.0
    };

    for (double value : invalid_values) {
    EXPECT_FALSE(mission_manager::candidate_values_are_usable(
            true, value, 0.0, 0.01));

    EXPECT_FALSE(mission_manager::candidate_values_are_usable(
            true, 0.0, value, 0.01));
    }
}
