#!/usr/bin/env bash
PC_IP=[host_ip] PC_PORT=[host_port] AUDIO_DEVICE="plughw:CARD=B101,DEV=0" AUDIO_CHUNK_SEC=1 FRAME_INTERVAL_SEC=0.25 python stream_sender.py
