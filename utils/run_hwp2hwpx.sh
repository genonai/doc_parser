#!/bin/bash
if [ $# -ne 2 ]; then
    echo "Usage: run_hwp2hwpx.sh <input.hwp> <output.hwpx>"
    exit 1
fi
java -jar /app/hwp2hwpx/hwp2hwpx.jar "$1" "$2" 