#pragma once
#include <cmath>
#include <cstdint>

namespace mission_manager
{
    enum class AutonomyState
    {
        DISABLED,
        ACQUIRE,
        TRACK,
        LOST_HOVER,
        LANDING_REQUESTED
    };

    struct SupervisorDecision
    {
        AutonomyState state{AutonomyState::DISABLED};
        bool allow_tracking{false};
        bool request_land{false};
    };

    class Supervisor
    {
    public:
        void enable_autonomy()
        {
            if (state_ == AutonomyState::DISABLED)
            {
                reset_acquisition();
                state_ = AutonomyState::ACQUIRE;
            }
        }

        void disable_autonomy()
        {
            // Disabling tracking must not cancel a landing request.
            if (state_ != AutonomyState::LANDING_REQUESTED)
            {
                reset_acquisition();
                state_ = AutonomyState::DISABLED;
            }
        }

        void manual_takeover()
        {
            // A deliberate takeover returns control to the operator.
            reset_acquisition();
            state_ = AutonomyState::DISABLED;
        }

        SupervisorDecision decision() const
        {
            return SupervisorDecision{
                state_,
                state_ == AutonomyState::TRACK,
                state_ == AutonomyState::LANDING_REQUESTED
            };
        }

        void observe_target(
        std::int64_t observation_stamp_ns,
        bool target_valid,
        double source_age_seconds)
        
    {
        if (state_ == AutonomyState::DISABLED ||
            state_ == AutonomyState::LANDING_REQUESTED)
        {
            return;
        }

        const bool usable =
            target_valid &&
            observation_stamp_ns > 0 &&
            std::isfinite(source_age_seconds) &&
            source_age_seconds >= 0.0 &&
            source_age_seconds < freshness_limit_seconds_;

        if (!usable)
        {
            valid_observations_ = 0;

            if (state_ == AutonomyState::TRACK)
            {
                state_ = AutonomyState::LOST_HOVER;
            }

            return;
        }

        // Duplicate or out-of-order observations cannot advance acquisition.
        if (observation_stamp_ns <= last_observation_stamp_ns_)
        {
            return;
        }

        const bool gap_too_large =
            last_observation_stamp_ns_ > 0 &&
            observation_stamp_ns - last_observation_stamp_ns_
                >= maximum_gap_ns_;

        if (gap_too_large)
        {
            valid_observations_ = 0;

            if (state_ == AutonomyState::TRACK)
            {
                state_ = AutonomyState::LOST_HOVER;
            }
        }

        last_observation_stamp_ns_ = observation_stamp_ns;

        if (state_ == AutonomyState::TRACK)
        {
            return;
        }

        ++valid_observations_;

        if (valid_observations_ >= required_observations_)
        {
            reset_loss_timer();
            state_ = AutonomyState::TRACK;
        }
        
    }
        void update_time(double source_age_seconds,
                     double receipt_age_seconds)
    {
        if (state_ == AutonomyState::DISABLED ||
            state_ == AutonomyState::LANDING_REQUESTED)
        {
            return;
        }

        const bool source_fresh =
            std::isfinite(source_age_seconds) &&
            source_age_seconds >= 0.0 &&
            source_age_seconds < freshness_limit_seconds_;

        const bool receipt_fresh =
            std::isfinite(receipt_age_seconds) &&
            receipt_age_seconds >= 0.0 &&
            receipt_age_seconds < freshness_limit_seconds_;

        if (source_fresh && receipt_fresh)
        {
            return;
        }

        // Expired data breaks any partial acquisition sequence.
        valid_observations_ = 0;

        if (state_ == AutonomyState::TRACK)
        {
            state_ = AutonomyState::LOST_HOVER;
        }
    }

        void update_loss_timer(double now_seconds)
    {
        if (state_ == AutonomyState::DISABLED ||
            state_ == AutonomyState::LANDING_REQUESTED)
        {
            return;
        }

        // Timing faults must not leave autonomy active indefinitely.
        if (!std::isfinite(now_seconds) ||
            now_seconds < 0.0 ||
            (have_timer_time_ &&
             now_seconds < last_timer_time_seconds_))
        {
            valid_observations_ = 0;
            state_ = AutonomyState::LANDING_REQUESTED;
            return;
        }

        have_timer_time_ = true;
        last_timer_time_seconds_ = now_seconds;

        if (state_ == AutonomyState::TRACK)
        {
            reset_loss_timer();
            return;
        }

        // ACQUIRE and LOST_HOVER both need bounded waiting.
        if (!loss_timer_running_)
        {
            loss_timer_running_ = true;
            loss_started_seconds_ = now_seconds;
        }

        if (now_seconds - loss_started_seconds_ >=
            loss_timeout_seconds_)
        {
            valid_observations_ = 0;
            state_ = AutonomyState::LANDING_REQUESTED;
        }
    }
    

    private:
    static constexpr unsigned required_observations_ = 5;
    static constexpr double freshness_limit_seconds_ = 0.25;
    static constexpr std::int64_t maximum_gap_ns_ = 250000000;
    static constexpr double loss_timeout_seconds_ = 1.5;

    bool loss_timer_running_{false};
    double loss_started_seconds_{0.0};

    bool have_timer_time_{false};
    double last_timer_time_seconds_{0.0};

    unsigned valid_observations_{0};
    std::int64_t last_observation_stamp_ns_{0};

        void reset_loss_timer()
    {
        loss_timer_running_ = false;
        loss_started_seconds_ = 0.0;
    }

        void reset_acquisition()
    {
        valid_observations_ = 0;
        last_observation_stamp_ns_ = 0;

        reset_loss_timer();
        have_timer_time_ = false;
        last_timer_time_seconds_ = 0.0;
    }
        AutonomyState state_{AutonomyState::DISABLED};
    };
}