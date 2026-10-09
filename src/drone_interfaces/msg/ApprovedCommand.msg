# Time the supervisor published this decision
std_msgs/Header header

# Original camera observation timestamp and frame
std_msgs/Header source_header

# Whether the supervisor owns autonomous command control
# False means yield control to the operator
bool autonomy_enabled

# Whether autonomous translation is permitted
bool tracking_allowed

# Latched request to land; does not confirm landing
bool request_land

# Bounded command scalars, not metres per second
# Positive lateral: right; positive vertical: up
# Both must be zero when tracking_allowed is false
float64 lateral
float64 vertical