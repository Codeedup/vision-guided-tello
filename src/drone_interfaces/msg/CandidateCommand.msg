# Timestamp and camera frame of the source observation
std_msgs/Header header

# Whether the controller accepted the target observation
bool target_valid

# Bounded command scalars, not metres per second
# Positive lateral: right
# Positive vertical: up
float64 lateral
float64 vertical