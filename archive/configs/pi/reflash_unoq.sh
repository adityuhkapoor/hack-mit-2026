#!/bin/bash
# The UNO Q lost its sketch once after a USB brownout (no 0x08 on I2C, silent /dev/ttyACM0). This puts it back.
adb shell "cd ~/nimbus_unoq && arduino-cli compile --fqbn arduino:zephyr:unoq . 2>&1 | grep -E \"error|Sketch uses\"; arduino-cli upload --fqbn arduino:zephyr:unoq -p 192.168.8.122 --upload-field password=arduino . 2>&1 | tail -1"
sleep 45; sudo i2cdetect -y 1 | sed -n 2p
