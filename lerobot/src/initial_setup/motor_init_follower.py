from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig

config = SO101FollowerConfig(
    port="/dev/tty.usbmodem5AAF2631671",
    id="ilab_lerobot_follower_arm",
)
follower = SO101Follower(config)
follower.setup_motors()