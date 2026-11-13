"""Constants for Cavius LiviSense."""
from datetime import timedelta

DOMAIN = "cavius_livisense"
PLATFORMS = ("binary_sensor", "sensor")
UPDATE_INTERVAL = timedelta(minutes=5)

# Confirmed LiviSense API model identifiers.
FIRE_DETECTOR_MODELS = ("SMOKECAVIUS", "HEATCAVIUS")
SUPPORTED_MODELS = FIRE_DETECTOR_MODELS + ("REMOTECAVIUS",)
