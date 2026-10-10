#include <chrono>
#include <cstdint>
#include <limits>
#include <memory>

#include "rclcpp/rclcpp.hpp"
#include "drone_interfaces/msg/candidate_command.hpp"

#include "mission_manager/candidate_safety.hpp"
#include "mission_manager/supervisor.hpp"

#include "std_srvs/srv/set_bool.hpp"
#include "std_srvs/srv/trigger.hpp"
#include "drone_interfaces/msg/approved_command.hpp"

class MissionManager : public rclcpp::Node
{
public:
  MissionManager()
  : Node("mission_manager")
  {
    approved_publisher_ = create_publisher<Approved>(
             "approved_command", rclcpp::QoS(1));
    subscription_ = create_subscription<Candidate>(
            "candidate_command",
            rclcpp::QoS(1),
      [this](Candidate::ConstSharedPtr message)
      {
        handle_candidate(message);
            });

    watchdog_ = create_wall_timer(
            std::chrono::milliseconds(20),
      [this]() {publish_approved_command();});

    autonomy_service_ = create_service<SetAutonomy>(
            "~/set_autonomy",
      [this](
        std::shared_ptr<SetAutonomy::Request> request,
        std::shared_ptr<SetAutonomy::Response> response)
      {
        update_safety();

        const auto state = supervisor_.decision().state;

        if (state ==
        mission_manager::AutonomyState::LANDING_REQUESTED)
        {
          response->success = false;
          response->message =
          "Landing request latched; explicit manual "
          "takeover is required.";
          publish_approved_command();
          return;
        }

        if (request->data) {
          if (state ==
          mission_manager::AutonomyState::DISABLED)
          {
            clear_input();
            supervisor_.enable_autonomy();
            response->message =
            "Enabled; acquiring new observations.";
          } else {
            response->message =
            "Already enabled; existing deadlines preserved.";
          }
        } else {
          supervisor_.disable_autonomy();
          clear_input();
          response->message = "Autonomy disabled.";
        }


        publish_approved_command();
        response->success = true;
            });

    takeover_service_ = create_service<Takeover>(
            "~/manual_takeover",
      [this](
        std::shared_ptr<Takeover::Request>,
        std::shared_ptr<Takeover::Response> response)
      {
        update_safety();

        supervisor_.manual_takeover();
        clear_input();
        publish_approved_command();

        response->success = true;
        response->message =
        "Software autonomy disabled; explicit re-enable required.";
            });

    RCLCPP_INFO(
            get_logger(),
            "Waiting for candidates. Autonomy is DISABLED.");
  }

private:
  using Candidate = drone_interfaces::msg::CandidateCommand;
  using Approved = drone_interfaces::msg::ApprovedCommand;
  using SteadyClock = std::chrono::steady_clock;

  using SetAutonomy = std_srvs::srv::SetBool;
  using Takeover = std_srvs::srv::Trigger;

  mission_manager::Supervisor supervisor_;

  rclcpp::Subscription<Candidate>::SharedPtr subscription_;
  rclcpp::Publisher<Approved>::SharedPtr approved_publisher_;
  rclcpp::TimerBase::SharedPtr watchdog_;

  rclcpp::Service<SetAutonomy>::SharedPtr autonomy_service_;
  rclcpp::Service<Takeover>::SharedPtr takeover_service_;

  mission_manager::AutonomyState last_reported_state_{
    mission_manager::AutonomyState::DISABLED};

  Candidate last_candidate_{};
  SteadyClock::time_point last_receipt_{};

  bool have_candidate_{false};
  bool candidate_usable_{false};
  std::int64_t last_accepted_stamp_ns_{0};


  void clear_input()
  {
    have_candidate_ = false;
    candidate_usable_ = false;

        // Retain last_accepted_stamp_ns_ to reject replayed observations.
  }

  static const char * state_name(mission_manager::AutonomyState state)
  {
    using State = mission_manager::AutonomyState;

    switch (state) {
      case State::DISABLED:
        return "DISABLED";
      case State::ACQUIRE:
        return "ACQUIRE";
      case State::TRACK:
        return "TRACK";
      case State::LOST_HOVER:
        return "LOST_HOVER";
      case State::LANDING_REQUESTED:
        return "LANDING_REQUESTED";
    }

    return "UNKNOWN";
  }

  void report_state()
  {
    const auto decision = supervisor_.decision();

    if (decision.state == last_reported_state_) {
      return;
    }

    last_reported_state_ = decision.state;

    RCLCPP_INFO(
            get_logger(),
            "State: %s | tracking_allowed=%s | request_land=%s",
            state_name(decision.state),
            decision.allow_tracking ? "true" : "false",
            decision.request_land ? "true" : "false");
  }

  void update_safety()
  {
    const auto steady_now = SteadyClock::now();

    const double timer_time_seconds =
      std::chrono::duration<double>(
                steady_now.time_since_epoch()).count();

        // Check an existing landing deadline before further state changes.
    supervisor_.update_loss_timer(timer_time_seconds);

    double source_age_seconds =
      std::numeric_limits<double>::infinity();
    double receipt_age_seconds =
      std::numeric_limits<double>::infinity();

    if (have_candidate_) {
      const rclcpp::Time source_time(
        last_candidate_.header.stamp,
        get_clock()->get_clock_type());

      source_age_seconds = (now() - source_time).seconds();

      receipt_age_seconds =
        std::chrono::duration<double>(
                    steady_now - last_receipt_).count();
    }

    supervisor_.update_time(
            source_age_seconds, receipt_age_seconds);

        // Start the loss interval if freshness just expired.
    supervisor_.update_loss_timer(timer_time_seconds);
    report_state();
    if (!candidate_usable_) {
      return;
    }

    const bool source_fresh =
      mission_manager::candidate_values_are_usable(
                last_candidate_.target_valid,
                last_candidate_.lateral,
                last_candidate_.vertical,
                source_age_seconds);

    const bool receipt_fresh =
      mission_manager::candidate_values_are_usable(
                last_candidate_.target_valid,
                last_candidate_.lateral,
                last_candidate_.vertical,
                receipt_age_seconds);

    if (!source_fresh || !receipt_fresh) {
      candidate_usable_ = false;

      RCLCPP_WARN(
                get_logger(),
                "Candidate expired: source_age=%.3f s, "
                "receipt_age=%.3f s",
                source_age_seconds,
                receipt_age_seconds);
    }
  }

  void publish_approved_command()
  {
        // Recheck deadlines and candidate freshness before publishing.
    update_safety();

    const auto decision = supervisor_.decision();

    Approved command{};
    command.header.stamp = now();

    if (have_candidate_) {
      command.source_header = last_candidate_.header;
    }

    command.autonomy_enabled =
      decision.state != mission_manager::AutonomyState::DISABLED;

    command.tracking_allowed =
      decision.allow_tracking &&
      have_candidate_ &&
      candidate_usable_;

    command.request_land = decision.request_land;

        // Neutral unless policy AND candidate validation permit movement.
    command.lateral = 0.0;
    command.vertical = 0.0;

    if (command.tracking_allowed) {
      command.lateral = last_candidate_.lateral;
      command.vertical = last_candidate_.vertical;
    }

    approved_publisher_->publish(command);
  }

  void reject_candidate()
  {
    candidate_usable_ = false;
    supervisor_.observe_target(
            0, false, std::numeric_limits<double>::infinity());

    publish_approved_command();
  }

  void handle_candidate(Candidate::ConstSharedPtr message)
  {
        // A late observation cannot cancel an already-due landing.
    update_safety();

    const auto & stamp = message->header.stamp;

    if (!mission_manager::candidate_stamp_is_valid(
                stamp.sec, stamp.nanosec))
    {
      reject_candidate();

      RCLCPP_WARN(
                get_logger(),
                "Rejected candidate: missing or malformed timestamp.");
      return;
    }

    const rclcpp::Time source_time(
      stamp, get_clock()->get_clock_type());

    const double source_age_seconds =
      (now() - source_time).seconds();

    if (!mission_manager::candidate_values_are_usable(
                message->target_valid,
                message->lateral,
                message->vertical,
                source_age_seconds))
    {
      reject_candidate();

      RCLCPP_WARN(
                get_logger(),
                "Rejected candidate: invalid target, values, or age.");
      return;
    }

    const auto stamp_ns = source_time.nanoseconds();

    if (stamp_ns <= last_accepted_stamp_ns_) {
      RCLCPP_WARN(
                get_logger(),
                "Ignored duplicate or out-of-order candidate.");
      return;
    }

    update_safety();

    last_candidate_ = *message;
    last_receipt_ = SteadyClock::now();
    last_accepted_stamp_ns_ = stamp_ns;
    have_candidate_ = true;
    candidate_usable_ = true;

    supervisor_.observe_target(
            stamp_ns, true, source_age_seconds);

    update_safety();

    RCLCPP_INFO(
            get_logger(),
            "Usable candidate: lateral=%.3f, vertical=%.3f, "
            "tracking_allowed=%s",
            message->lateral,
            message->vertical,
            supervisor_.decision().allow_tracking ? "true" : "false");
  }
};

int main(int argc, char *argv[])
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<MissionManager>());
  rclcpp::shutdown();
  return 0;
}
