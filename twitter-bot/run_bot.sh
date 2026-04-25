#!/bin/bash
cd ~/xb
export PATH=$PATH:/Library/Frameworks/Python.framework/Versions/3.11/bin:/usr/local/bin
python3 twitter_bot.py run > twitter_bot.log 2>&1
