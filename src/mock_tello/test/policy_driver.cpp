// Deterministic policy harness only. It does not execute the ROS wrappers.
// Inputs: elapsed_seconds action source_stamp_ns detected hands error_x error_y
// Actions: 0 timer-only, 1 observation, 2 explicit enable, 3 manual takeover.
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <limits>

#include "mission_manager/supervisor.hpp"
#include "tracking_controller/controller.hpp"
#include "tracking_controller/target_safety.hpp"

int main()
{
    mission_manager::Supervisor supervisor;
    double now, receipt = 0.0;
    std::int64_t stamp, accepted = 0;
    int action, detected, hands;
    double x, y;
    bool have = false, usable = false;
    tracking_controller::MotionCommand command{0, 0};
    std::cout << std::setprecision(17);
    while (std::cin >> now >> action >> stamp >> detected >> hands >> x >> y)
    {
        const auto update = [&]() {
            supervisor.update_loss_timer(now);
            const double source_age = have ? (now - accepted / 1e9) :
                std::numeric_limits<double>::infinity();
            const double receipt_age = have ? now - receipt :
                std::numeric_limits<double>::infinity();
            supervisor.update_time(source_age, receipt_age);
            supervisor.update_loss_timer(now);
            if (tracking_controller::target_expired(source_age, receipt_age))
                usable = false;
        };
        update();
        if (action == 2)
        {
            supervisor.enable_autonomy();
            have = usable = false;
        }
        else if (action == 3)
        {
            supervisor.manual_takeover();
            have = usable = false;
        }
        else if (action == 1)
        {
            const double age = now - stamp / 1e9;
            const bool valid = stamp > 0 && detected && hands == 1 &&
                std::isfinite(x) && std::isfinite(y) && std::abs(x) <= 1 &&
                std::abs(y) <= 1 && tracking_controller::is_fresh_age(age);
            if (!valid)
            {
                usable = false;
                supervisor.observe_target(0, false,
                    std::numeric_limits<double>::infinity());
            }
            else if (stamp > accepted)
            {
                accepted = stamp;
                receipt = now;
                have = usable = true;
                command = tracking_controller::calculate_command(x, y);
                supervisor.observe_target(stamp, true, age);
            }
        }
        update();
        const auto decision = supervisor.decision();
        const bool tracking = decision.allow_tracking && have && usable;
        std::cout << (decision.state != mission_manager::AutonomyState::DISABLED)
                  << ' ' << tracking << ' ' << decision.request_land << ' '
                  << (tracking ? command.lateral : 0) << ' '
                  << (tracking ? command.vertical : 0) << ' '
                  << (have ? accepted : 0) << std::endl;
    }
}
