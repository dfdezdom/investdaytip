#!/bin/bash

# Create virtual environment
python -m venv .venv

# Activate virtual environment
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install package in development mode
pip install -e .

echo "Installation complete! Virtual environment activated."
