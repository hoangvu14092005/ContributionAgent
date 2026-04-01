#!/usr/bin/env python3
"""
remove_emojis.py - Remove emojis from ContribAI codebase (Cross-platform)

Usage:
    python scripts/remove_emojis.py
    python scripts/remove_emojis.py --dry-run
    python scripts/remove_emojis.py --restore
"""

import re
import sys
from pathlib import Path
from typing import List, Tuple

# Color codes for terminal output
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

# Priority files that directly affect PR/commit output
PRIORITY_FILES = [
    "contribai/generator/engine.py",
    "contribai/github/guidelines.py",
    "contribai/pr/manager.py",
    "contribai/orchestrator/pipeline.py",
]

# Additional files with emojis in logs
ADDITIONAL_FILES = [
    "contribai/orchestrator/review_gate.py",
    "contribai/cli/main.py",
    "contribai/pr/patrol.py",
    "contribai/issues/solver.py",
]

# Emoji patterns to remove
EMOJI_PATTERNS = [
    "🔒",  # Security
    "✨",  # Quality
    "📝",  # Docs
    "🎨",  # UI/UX
    "⚡",  # Performance
    "🚀",  # Feature
    "♻️",  # Refactor
    "🔧",  # Fix
    "📋",  # Issue/Details
    "✅",  # Success
    "🧠",  # Intelligence
    "🛠️",  # Generating
    "🤖",  # LLM
    "🔗",  # Link/Found
    "🔄",  # Auto-fix
    "⚠️",  # Warning
    "✍️",  # Signing
]


def count_emojis(file_path: Path) -> int:
    """Count emojis in a file."""
    try:
        content = file_path.read_text(encoding='utf-8')
        count = sum(content.count(emoji) for emoji in EMOJI_PATTERNS)
        return count
    except Exception as e:
        print(f"{Colors.RED}✗{Colors.NC} Error reading {file_path}: {e}")
        return 0


def remove_emojis_from_file(file_path: Path, dry_run: bool = False) -> Tuple[bool, int]:
    """
    Remove emojis from a file.
    
    Returns:
        (changed, emoji_count): Whether file was changed and number of emojis removed
    """
    try:
        content = file_path.read_text(encoding='utf-8')
        original_content = content
        
        # Count emojis before removal
        emoji_count = sum(content.count(emoji) for emoji in EMOJI_PATTERNS)
        
        if emoji_count == 0:
            return False, 0
        
        # Remove each emoji pattern
        for emoji in EMOJI_PATTERNS:
            # Remove emoji with space after it
            content = content.replace(f"{emoji} ", "")
            # Remove standalone emoji
            content = content.replace(emoji, "")
        
        if content == original_content:
            return False, 0
        
        if not dry_run:
            # Create backup
            backup_path = file_path.with_suffix(file_path.suffix + '.emoji_backup')
            backup_path.write_text(original_content, encoding='utf-8')
            
            # Write cleaned content
            file_path.write_text(content, encoding='utf-8')
            
            print(f"{Colors.GREEN}✓{Colors.NC} Cleaned: {file_path} (backup: {backup_path.name})")
        else:
            print(f"{Colors.YELLOW}[DRY RUN]{Colors.NC} Would clean: {file_path}")
        
        return True, emoji_count
        
    except Exception as e:
        print(f"{Colors.RED}✗{Colors.NC} Error processing {file_path}: {e}")
        return False, 0


def restore_backups():
    """Restore all .emoji_backup files."""
    backup_files = list(Path('.').rglob('*.emoji_backup'))
    
    if not backup_files:
        print(f"{Colors.YELLOW}No backup files found{Colors.NC}")
        return
    
    print(f"Found {len(backup_files)} backup file(s)")
    
    for backup_path in backup_files:
        original_path = backup_path.with_suffix('')
        try:
            content = backup_path.read_text(encoding='utf-8')
            original_path.write_text(content, encoding='utf-8')
            backup_path.unlink()
            print(f"{Colors.GREEN}✓{Colors.NC} Restored: {original_path}")
        except Exception as e:
            print(f"{Colors.RED}✗{Colors.NC} Error restoring {original_path}: {e}")


def main():
    """Main execution."""
    dry_run = '--dry-run' in sys.argv
    restore = '--restore' in sys.argv
    
    print("🔍 ContribAI Emoji Removal Script")
    print("==================================")
    print()
    
    if restore:
        print("Restoring from backups...")
        print("-------------------------")
        restore_backups()
        return
    
    if dry_run:
        print(f"{Colors.YELLOW}[DRY RUN MODE - No files will be modified]{Colors.NC}")
        print()
    
    # Process priority files
    print("Step 1: Checking priority files...")
    print("-----------------------------------")
    
    total_cleaned = 0
    total_emojis = 0
    
    for file_path_str in PRIORITY_FILES:
        file_path = Path(file_path_str)
        
        if not file_path.exists():
            print(f"{Colors.RED}✗{Colors.NC} File not found: {file_path}")
            continue
        
        emoji_count = count_emojis(file_path)
        
        if emoji_count > 0:
            print(f"{Colors.YELLOW}Found {emoji_count} emoji(s) in:{Colors.NC} {file_path}")
            changed, removed = remove_emojis_from_file(file_path, dry_run)
            if changed:
                total_cleaned += 1
                total_emojis += removed
        else:
            print(f"{Colors.GREEN}✓{Colors.NC} Clean: {file_path}")
    
    print()
    print("Step 2: Checking additional files...")
    print("-------------------------------------")
    
    for file_path_str in ADDITIONAL_FILES:
        file_path = Path(file_path_str)
        
        if not file_path.exists():
            print(f"{Colors.RED}✗{Colors.NC} File not found: {file_path}")
            continue
        
        emoji_count = count_emojis(file_path)
        
        if emoji_count > 0:
            print(f"{Colors.YELLOW}Found {emoji_count} emoji(s) in:{Colors.NC} {file_path}")
            changed, removed = remove_emojis_from_file(file_path, dry_run)
            if changed:
                total_cleaned += 1
                total_emojis += removed
        else:
            print(f"{Colors.GREEN}✓{Colors.NC} Clean: {file_path}")
    
    print()
    print("Step 3: Verification...")
    print("-----------------------")
    
    # Search for remaining emojis in all Python files
    remaining = 0
    remaining_files = []
    
    for py_file in Path('contribai').rglob('*.py'):
        count = count_emojis(py_file)
        if count > 0:
            remaining += count
            remaining_files.append((py_file, count))
    
    if remaining > 0:
        print(f"{Colors.YELLOW}⚠{Colors.NC}  Found {remaining} remaining emoji(s) in {len(remaining_files)} file(s):")
        for file_path, count in remaining_files[:5]:  # Show first 5
            print(f"    - {file_path}: {count} emoji(s)")
        if len(remaining_files) > 5:
            print(f"    ... and {len(remaining_files) - 5} more")
    else:
        print(f"{Colors.GREEN}✓{Colors.NC} No emojis found in Python files")
    
    print()
    print("Summary")
    print("=======")
    print(f"Files cleaned: {total_cleaned}")
    print(f"Emojis removed: ~{total_emojis}")
    print(f"Remaining: {remaining}")
    print()
    
    if dry_run:
        print(f"{Colors.BLUE}This was a dry run. Run without --dry-run to apply changes.{Colors.NC}")
    elif total_cleaned > 0:
        print(f"{Colors.GREEN}✓ Emoji removal complete!{Colors.NC}")
        print()
        print("Next steps:")
        print("1. Review changes: git diff")
        print("2. Test: python -m contribai --help")
        print("3. Commit: git add . && git commit -m 'chore: remove emojis from PR/commit output'")
        print()
        print("Backups created with .emoji_backup extension")
        print("To restore: python scripts/remove_emojis.py --restore")
    else:
        print(f"{Colors.GREEN}✓ No emojis found - codebase is clean!{Colors.NC}")
    
    print()
    print("For more details, see: docs/NO_EMOJI_POLICY.md")


if __name__ == "__main__":
    main()
