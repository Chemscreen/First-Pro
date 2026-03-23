#!/bin/bash
# Setup script for Karpathy's AutoResearch
# Requirements: Python 3.10+, a GPU (NVIDIA recommended), pip
# Repo: https://github.com/karpathy/autoresearch

set -e

echo "=== AutoResearch Setup ==="
echo "This script clones and prepares Karpathy's autoresearch tool."
echo ""

# Check for GPU
if command -v nvidia-smi &> /dev/null; then
    echo "GPU detected:"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
else
    echo "WARNING: No NVIDIA GPU detected. AutoResearch requires a GPU to run experiments."
    echo "You can still explore the code, but experiments won't run without a GPU."
fi

echo ""

# Clone autoresearch if not already present
if [ ! -d "autoresearch" ]; then
    echo "Cloning autoresearch repository..."
    git clone https://github.com/karpathy/autoresearch.git
else
    echo "autoresearch directory already exists, pulling latest..."
    cd autoresearch && git pull && cd ..
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. cd autoresearch"
echo "  2. pip install -r requirements.txt  (if requirements file exists)"
echo "  3. python prepare.py                (downloads dataset & trains tokenizer)"
echo "  4. python autoresearch.py           (starts the autonomous research loop)"
echo ""
echo "See https://github.com/karpathy/autoresearch for full documentation."
