#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Logger configuration for SunBlocker
"""
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
stream_handler = logging.StreamHandler()
format_str = "%(levelname)s %(asctime)s.%(msecs)03d %(module)s - %(funcName)s: %(message)s"
formatter = logging.Formatter(format_str, "%Y-%m-%d %H:%M:%S")
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)
