#pragma once

#include <cmath>
#include <cstdint>

namespace tracking_controller
{
inline constexpr double target_timeout_seconds = 0.25;

inline bool is_valid_stamp(
  std::int32_t seconds,
  std::uint32_t nanoseconds)
{
  return seconds >= 0 &&
         nanoseconds < 1000000000U &&
         (seconds != 0 || nanoseconds != 0);
}

inline bool is_fresh_age(double age_seconds)
{
  return std::isfinite(age_seconds) &&
         age_seconds >= 0.0 &&
         age_seconds < target_timeout_seconds;
}

inline bool target_expired(
  double source_age_seconds,
  double receipt_age_seconds)
{
  return !is_fresh_age(source_age_seconds) ||
         !is_fresh_age(receipt_age_seconds);
}
}
