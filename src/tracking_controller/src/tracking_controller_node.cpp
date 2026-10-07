#include <memory>

#include "rclcpp/rclcpp.hpp"
#include "drone_interfaces/msg/hand_target.hpp"
#include "tracking_controller/controller.hpp"
#include <cmath>
#include "drone_interfaces/msg/candidate_command.hpp"
#include <chrono>
#include "tracking_controller/target_safety.hpp"

class TrackingControllerNode : public rclcpp::Node
{
public:
    TrackingControllerNode()
        : Node("tracking_controller")
    {
        publisher_ =
        create_publisher<drone_interfaces::msg::CandidateCommand>(
            "candidate_command",
            rclcpp::QoS(1));
        subscription_ =
            create_subscription<drone_interfaces::msg::HandTarget>(
                "hand_target",
                10,
                [this](const drone_interfaces::msg::HandTarget & target)
                {
                    handle_target(target);
                });

        watchdog_timer_ = create_wall_timer(
            std::chrono::milliseconds(20),
            [this]()
            {
                check_target_timeout();
            });
        RCLCPP_INFO(get_logger(), "Waiting for hand targets");
    }

private:


drone_interfaces::msg::CandidateCommand last_candidate_;

std::chrono::steady_clock::time_point last_received_at_{};

rclcpp::TimerBase::SharedPtr watchdog_timer_;
rclcpp::Publisher<
    drone_interfaces::msg::CandidateCommand>::SharedPtr publisher_;


void check_target_timeout()
{
    if (!last_candidate_.target_valid)
    {
        return;
    }

    const rclcpp::Time observation_time(
        last_candidate_.header.stamp,
        get_clock()->get_clock_type());

    const double source_age_seconds =
        (now() - observation_time).seconds();

    const double receipt_age_seconds =
        std::chrono::duration<double>(
            std::chrono::steady_clock::now() - last_received_at_)
        .count();

    const bool expired =
        tracking_controller::target_expired(
        source_age_seconds,
        receipt_age_seconds);

    if (!expired)
    {
        return;
    }

    last_candidate_.target_valid = false;
    last_candidate_.lateral = 0.0;
    last_candidate_.vertical = 0.0;

    publisher_->publish(last_candidate_);

    RCLCPP_WARN(
        get_logger(),
        "Target expired: published neutral candidate command");
}
    void handle_target(
    const drone_interfaces::msg::HandTarget & target)
{
    

    const auto & stamp = target.header.stamp;

    const bool stamp_valid =
        tracking_controller::is_valid_stamp(
            stamp.sec,
            stamp.nanosec);
    bool target_fresh = false;

    if (stamp_valid)
    {
        const rclcpp::Time observation_time(
            stamp,
            get_clock()->get_clock_type());

        const double age_seconds =
            (now() - observation_time).seconds();

        target_fresh =
            tracking_controller::is_fresh_age(age_seconds);
    }
    const bool target_valid =
        target_fresh &&
        target.detected &&
        target.tracked_hands == 1 &&
        std::isfinite(target.error_x) &&
        std::isfinite(target.error_y) &&
        std::abs(target.error_x) <= 1.0 &&
        std::abs(target.error_y) <= 1.0;

    tracking_controller::MotionCommand command{0.0, 0.0};

    if (target_valid)
    {
        command = tracking_controller::calculate_command(
            target.error_x,
            target.error_y);
    }

    drone_interfaces::msg::CandidateCommand output;

    output.header = target.header;
    output.target_valid = target_valid;
    output.lateral = command.lateral;
    output.vertical = command.vertical;

    last_received_at_ = std::chrono::steady_clock::now();
    last_candidate_ = output;

    publisher_->publish(output);

    RCLCPP_INFO(
        get_logger(),
        "Candidate command: valid=%s, lateral=%.2f, vertical=%.2f",
        target_valid ? "true" : "false",
        command.lateral,
        command.vertical);
}
    rclcpp::Subscription<
        drone_interfaces::msg::HandTarget>::SharedPtr subscription_;
};

int main(int argc, char * argv[])
{
    rclcpp::init(argc, argv);

    auto node = std::make_shared<TrackingControllerNode>();
    rclcpp::spin(node);

    rclcpp::shutdown();
    return 0;
}

