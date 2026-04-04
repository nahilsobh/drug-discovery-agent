#!/bin/bash

echo "🧼 Starting Project Connective Tissue Cleanup..."

# 1. Remove Python cache directories
echo "🧹 Removing __pycache__ folders..."
find . -type d -name "__pycache__" -exec rm -rf {} +

# 2. Remove macOS system metadata files
echo "🧹 Removing .DS_Store files..."
find . -type f -name ".DS_Store" -exec rm -f {} +

# 3. Clear temporary workspace data (keeping reports)
echo "🧹 Cleaning temporary data logs..."
rm -f workspace/data/*.log
rm -f *.log

# 4. Remove old submission zips to avoid recursion
echo "🧹 Removing old ZIP archives..."
rm -f *.zip

echo "✨ Cleanup Complete. System is lean and ready for packaging."
echo "🚀 Run 'zip -r Submission.zip .' to create your final deliverable."
