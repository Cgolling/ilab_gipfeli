import lerobot.processor
from lerobot.teleoperators.so_leader import SO101LeaderConfig, SO101Leader
from lerobot.robots.so_follower import SO101FollowerConfig, SO101Follower

robot_config = SO101FollowerConfig(
    port="/dev/tty.usbmodem5AAF2631671",
    id="ilab_lerobot_follower_arm",
)

teleop_config = SO101LeaderConfig(
    port="/dev/tty.usbmodem5AAF2642121",
    id="ilab_lerobot_leader_arm",
)

robot = SO101Follower(robot_config)
teleop_device = SO101Leader(teleop_config)
robot.connect()
teleop_device.connect()

while True:
    action = teleop_device.get_action()
    robot.send_action(action)
