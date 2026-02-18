import lerobot.processor
from lerobot.teleoperators.so_leader import SO101LeaderConfig, SO101Leader

config = SO101LeaderConfig(
    port="/dev/tty.usbmodem5AAF2642121",
    id="ilab_lerobot_leader_arm",
)

leader = SO101Leader(config)
leader.connect(calibrate=False)
leader.calibrate()
leader.disconnect()