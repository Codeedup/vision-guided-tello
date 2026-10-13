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

struct ControllerConfig
{
  double gain{20.0};
  double dead_zone{0.08};
  double lateral_limit{10.0};
  double vertical_limit{10.0};

  bool valid() const
  {
    return std::isfinite(gain) && gain > 0.0 && gain <= 20.0 &&
           std::isfinite(dead_zone) && dead_zone >= 0.0 && dead_zone < 1.0 &&
           std::isfinite(lateral_limit) && lateral_limit >= 0.0 && lateral_limit <= 10.0 &&
           std::isfinite(vertical_limit) && vertical_limit >= 0.0 && vertical_limit <= 10.0;
  }
};

inline MotionCommand calculate_command(
  double error_x,
  double error_y,
  const ControllerConfig & config = ControllerConfig{})
{
  MotionCommand command{0.0, 0.0};

  if (!config.valid() || !std::isfinite(error_x) ||
    !std::isfinite(error_y) ||
    std::abs(error_x) > 1.0 ||
    std::abs(error_y) > 1.0)
  {
    return command;
  }

  if (std::abs(error_x) > config.dead_zone) {
    command.lateral = std::clamp(
            config.gain * error_x,
            -config.lateral_limit,
            config.lateral_limit);
  }

  if (std::abs(error_y) > config.dead_zone) {
    command.vertical = std::clamp(
            -config.gain * error_y,
            -config.vertical_limit,
            config.vertical_limit);
  }

  return command;
}
}
