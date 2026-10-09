#!/usr/bin/env bash
# Build build/extractor.zip for Lambda (python3.12, linux x86_64).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build && mkdir -p build/pkg
pip install -q -r requirements.txt -t build/pkg \
  --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.12 --implementation cp
cp -r src config build/pkg/
(cd build/pkg && zip -qr ../extractor.zip . -x "*/__pycache__/*")
echo "built build/extractor.zip"
