# Sounds Folder

Put your `.wav` files in this folder.

Examples:
- `sounds/beep.wav` -> `/sound beep`
- `sounds/welcome.wav` -> `/sound welcome`
- Optional gain: `/sound beep 0.8`

Notes:
- The Telegram command `/sound` lists available files from this directory.
- Use `/volume` and `/volume 60` to read/set Spot CAM master volume.
- Playback uses Spot CAM Audio service. If your robot has no Spot CAM audio,
  `/sound` will report that audio is unavailable.
