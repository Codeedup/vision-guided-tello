#pragma once
#include <cmath>
#include <algorithm>

namespace tracking_controller
{
struct MotionCommand
{
  double lateral;
  double vertical;
};

inline MotionCommand calculate_command(
  double error_x,
  double error_y)
{
  constexpr double gain = 20.0;
  constexpr double dead_zone = 0.08;
  constexpr double max_command = 10.0;

  MotionCommand command{0.0, 0.0};

  if (!std::isfinite(error_x) ||
    !std::isfinite(error_y) ||
    std::abs(error_x) > 1.0 ||
    std::abs(error_y) > 1.0)
  {
    return command;
  }

  if (std::abs(error_x) > dead_zone) {
    command.lateral = std::clamp(
            gain * error_x,
            -max_command,
            max_command);
  }

  if (std::abs(error_y) > dead_zone) {
    command.vertical = std::clamp(
            -gain * error_y,
            -max_command,
            max_command);
  }

  return command;
}
}
