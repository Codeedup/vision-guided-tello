#include <iostream>
#include "tracking_controller/controller.hpp"

int main()
{
  double error_x = 0.5;
  double error_y = -0.3;

  auto command =
    tracking_controller::calculate_command(error_x, error_y);

  std::cout   << "Lateral command: "
              << command.lateral << '\n';

  std::cout   << "Vertical command: "
              << command.vertical << '\n';

  return 0;
}
