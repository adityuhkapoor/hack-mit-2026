#!/bin/bash
while true; do
  ffmpeg -nostdin -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 -i /dev/video0 -c copy -f mpjpeg -listen 1 http://0.0.0.0:8090/ >> /home/raspi4/mjpeg_stream.log 2>&1
  sleep 1
done
