# Timestamp and camera frame associated with the observation
std_msgs/Header header

# True when exactly one usable hand target is available
bool detected

# Normalized image errors in [-1, 1]
# Positive x: right of centre
# Positive y: below centre
float32 error_x
float32 error_y

# Number of hands found by perception in this observation
uint8 tracked_hands