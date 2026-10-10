#pragma once

#include <cmath>
#include <cstdint>

namespace mission_manager
{
inline bool candidate_stamp_is_valid(
  std::int32_t sec,
  std::uint32_t nanosec)
{
  return sec >= 0 &&
         nanosec < 1000000000U &&
         (sec != 0 || nanosec != 0);
}

inline bool candidate_values_are_usable(
  bool target_valid,
  double lateral,
  double vertical,
  double source_age_seconds)
{
  constexpr double freshness_limit_seconds = 0.25;
  constexpr double command_limit = 10.0;

  return target_valid &&
         std::isfinite(source_age_seconds) &&
         source_age_seconds >= 0.0 &&
         source_age_seconds < freshness_limit_seconds &&
         std::isfinite(lateral) &&
         std::isfinite(vertical) &&
         std::abs(lateral) <= command_limit &&
         std::abs(vertical) <= command_limit;
}
}
