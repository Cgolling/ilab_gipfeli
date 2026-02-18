import lerobot.processor
from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

config = SO101LeaderConfig(
    port="/dev/tty.usbmodem5AAF2642121",
    id="ilab_lerobot_leader_arm",
)
leader = SO101Leader(config)
leader.setup_motors()