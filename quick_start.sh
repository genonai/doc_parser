#!/bin/bash
# Quick Start Script for Document Preprocessor API

set -e  # Exit on error

echo "🚀 Document Preprocessor API - Quick Start"
echo "=========================================="
echo ""

# Check Python version
echo "✓ Checking Python version..."
python3 --version || { echo "❌ Python3 not found"; exit 1; }

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "✓ Creating virtual environment..."
    python3 -m venv venv
else
    echo "✓ Virtual environment already exists"
fi

# Activate virtual environment
echo "✓ Activating virtual environment..."
source venv/bin/activate

# Upgrade pip
echo "✓ Upgrading pip..."
pip install --upgrade pip setuptools wheel -q

# Install dependencies
echo "✓ Installing dependencies (this may take a few minutes)..."
pip install -r requirements.txt -q

echo ""
echo "=========================================="
echo "✅ Setup complete!"
echo "=========================================="
echo ""
echo "📝 Next steps:"
echo "1. Start the server:"
echo "   cd genon/preprocessor/src"
echo "   python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085 --reload"
echo ""
echo "2. In another terminal, test the API:"
echo "   curl http://127.0.0.1:7085/healthcheck"
echo ""
echo "3. Process a document:"
echo "   curl -X POST http://127.0.0.1:7085/upload/run \\"
echo "     -F 'file=@document.pdf'"
echo ""
echo "📖 For more information, see README.md or USAGE.md"
