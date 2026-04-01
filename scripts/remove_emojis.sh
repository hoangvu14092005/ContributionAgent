#!/bin/bash
# remove_emojis.sh - Automatically remove emojis from ContribAI codebase
# Usage: bash scripts/remove_emojis.sh

set -e

echo "🔍 ContribAI Emoji Removal Script"
echo "=================================="
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Priority files that directly affect PR/commit output
PRIORITY_FILES=(
    "contribai/generator/engine.py"
    "contribai/github/guidelines.py"
    "contribai/pr/manager.py"
    "contribai/orchestrator/pipeline.py"
)

# Additional files with emojis in logs
ADDITIONAL_FILES=(
    "contribai/orchestrator/review_gate.py"
    "contribai/cli/main.py"
    "contribai/pr/patrol.py"
    "contribai/issues/solver.py"
)

# Emoji patterns to search for (with common context)
EMOJI_PATTERNS=(
    "🔒"  # Security
    "✨"  # Quality
    "📝"  # Docs
    "🎨"  # UI/UX
    "⚡"  # Performance
    "🚀"  # Feature
    "♻️"  # Refactor
    "🔧"  # Fix
    "📋"  # Issue/Details
    "✅"  # Success
    "🧠"  # Intelligence
    "🛠️"  # Generating
    "🤖"  # LLM
    "🔗"  # Link/Found
    "🔄"  # Auto-fix
    "⚠️"  # Warning
    "✍️"  # Signing
)

# Function to check if file exists
check_file() {
    if [ ! -f "$1" ]; then
        echo -e "${RED}✗${NC} File not found: $1"
        return 1
    fi
    return 0
}

# Function to count emojis in file
count_emojis() {
    local file=$1
    local count=0
    for emoji in "${EMOJI_PATTERNS[@]}"; do
        local matches=$(grep -c "$emoji" "$file" 2>/dev/null || true)
        count=$((count + matches))
    done
    echo $count
}

# Function to remove emojis from file
remove_emojis_from_file() {
    local file=$1
    local backup="${file}.emoji_backup"
    
    # Create backup
    cp "$file" "$backup"
    
    # Remove each emoji pattern
    for emoji in "${EMOJI_PATTERNS[@]}"; do
        # Remove emoji with space after it
        sed -i "s/${emoji} //g" "$file" 2>/dev/null || sed -i '' "s/${emoji} //g" "$file"
        # Remove standalone emoji
        sed -i "s/${emoji}//g" "$file" 2>/dev/null || sed -i '' "s/${emoji}//g" "$file"
    done
    
    # Check if file changed
    if diff -q "$file" "$backup" > /dev/null 2>&1; then
        rm "$backup"
        return 1  # No changes
    else
        echo -e "${GREEN}✓${NC} Cleaned: $file (backup: $backup)"
        return 0  # Changes made
    fi
}

# Main execution
echo "Step 1: Checking priority files..."
echo "-----------------------------------"

total_cleaned=0
total_emojis=0

for file in "${PRIORITY_FILES[@]}"; do
    if check_file "$file"; then
        emoji_count=$(count_emojis "$file")
        if [ $emoji_count -gt 0 ]; then
            echo -e "${YELLOW}Found $emoji_count emoji(s) in:${NC} $file"
            total_emojis=$((total_emojis + emoji_count))
            if remove_emojis_from_file "$file"; then
                total_cleaned=$((total_cleaned + 1))
            fi
        else
            echo -e "${GREEN}✓${NC} Clean: $file"
        fi
    fi
done

echo ""
echo "Step 2: Checking additional files..."
echo "-------------------------------------"

for file in "${ADDITIONAL_FILES[@]}"; do
    if check_file "$file"; then
        emoji_count=$(count_emojis "$file")
        if [ $emoji_count -gt 0 ]; then
            echo -e "${YELLOW}Found $emoji_count emoji(s) in:${NC} $file"
            total_emojis=$((total_emojis + emoji_count))
            if remove_emojis_from_file "$file"; then
                total_cleaned=$((total_cleaned + 1))
            fi
        else
            echo -e "${GREEN}✓${NC} Clean: $file"
        fi
    fi
done

echo ""
echo "Step 3: Verification..."
echo "-----------------------"

# Search for any remaining emojis in Python files
remaining=$(grep -r "🔒\|✨\|📝\|🎨\|⚡\|🚀\|♻️\|🔧\|📋\|✅\|🧠\|🛠️\|🤖\|🔗\|🔄\|⚠️\|✍️" --include="*.py" contribai/ 2>/dev/null | wc -l || echo "0")

if [ "$remaining" -gt 0 ]; then
    echo -e "${YELLOW}⚠${NC}  Found $remaining remaining emoji(s) in other files"
    echo "    Run: grep -r \"🔒\|✨\|📝\" --include=\"*.py\" contribai/"
else
    echo -e "${GREEN}✓${NC} No emojis found in Python files"
fi

echo ""
echo "Summary"
echo "======="
echo "Files cleaned: $total_cleaned"
echo "Emojis removed: ~$total_emojis"
echo "Remaining: $remaining"
echo ""

if [ $total_cleaned -gt 0 ]; then
    echo -e "${GREEN}✓ Emoji removal complete!${NC}"
    echo ""
    echo "Next steps:"
    echo "1. Review changes: git diff"
    echo "2. Test: python -m contribai --help"
    echo "3. Commit: git add . && git commit -m 'chore: remove emojis from PR/commit output'"
    echo ""
    echo "Backups created with .emoji_backup extension"
    echo "To restore: for f in *.emoji_backup; do mv \"\$f\" \"\${f%.emoji_backup}\"; done"
else
    echo -e "${GREEN}✓ No emojis found - codebase is clean!${NC}"
fi

echo ""
echo "For more details, see: docs/NO_EMOJI_POLICY.md"
