# Sub-Agent Usage Guide

This document explains how to effectively use sub-agents during implementation of the rock-paper-sync tool.

## What Are Sub-Agents?

Sub-agents are focused Claude Code instances spawned for specific complex tasks. They receive targeted context and work on isolated problems, allowing the main agent to maintain high-level orchestration while sub-agents dive deep into technical details.

## When to Use Sub-Agents

### Recommended Sub-Agent Tasks

1. **rmscene Library Integration** (Task 6)
   - Understanding v6 binary format
   - Generating valid .rm files
   - Applying text formatting
   - Documenting library limitations

2. **Complex Parsing Edge Cases** (Task 5)
   - Nested markdown formatting
   - Table conversion strategies
   - Math notation handling
   - Malformed markdown recovery

3. **Database Schema Optimization** (Task 4)
   - Index design for query performance
   - Migration strategy planning
   - Concurrent access patterns
   - Backup and recovery procedures

4. **Test Coverage Completion** (Task 10)
   - Identifying untested edge cases
   - Creating comprehensive fixtures
   - Mocking external dependencies
   - Performance testing strategies

## How to Spawn a Sub-Agent

### Pattern 1: Research and Implement

```
Main Agent: "I need to implement reMarkable file generation. Spawning sub-agent for deep rmscene research."

Sub-Agent Context:
---
Task: Implement binary .rm file generation using rmscene library

Your focus:
1. Study rmscene library source code (GitHub: ricklupton/rmscene)
2. Understand scene_items.py data structures
3. Learn write_blocks.py serialization
4. Create minimal working example generating text
5. Document findings and limitations

Resources to examine:
- rmscene GitHub repository
- rmc tool for reference implementation
- rmscene test cases
- reMarkable format documentation

Deliverables:
- Working function to generate .rm bytes
- Documentation of API usage
- List of known issues/limitations
- Test cases validating output
---

Sub-Agent proceeds with focused research...

Returns to Main Agent:
- Implementation code
- Usage documentation
- Found limitations
- Recommendations
```

### Pattern 2: Problem Solving

```
Main Agent: "Parser is failing on nested formatting. Need sub-agent to investigate."

Sub-Agent Context:
---
Problem: Markdown parser incorrectly handles nested formatting like **bold *and italic* text**

Your focus:
1. Debug current parsing logic
2. Identify specific failure case
3. Research mistune AST structure
4. Develop correct recursive algorithm
5. Test with comprehensive edge cases

Test cases to handle:
- **bold *italic* bold**
- *italic **bold** italic*
- ***bold and italic***
- **bold with `code` inside**
- Nested to 3+ levels

Deliverables:
- Fixed parsing function
- Comprehensive test suite
- Edge case documentation
---
```

### Pattern 3: Performance Optimization

```
Sub-Agent Context:
---
Task: Optimize find_changed_files() for large vaults (1000+ files)

Current implementation:
[provide current code]

Performance requirements:
- < 10ms per file hash lookup
- < 1 second for full vault scan
- Minimal memory footprint

Your focus:
1. Profile current implementation
2. Identify bottlenecks
3. Optimize database queries
4. Consider caching strategies
5. Benchmark improvements

Deliverables:
- Optimized implementation
- Performance benchmarks
- Trade-off analysis
---
```

## Sub-Agent Best Practices

### DO:

✅ **Give Clear Context**
- Specific task description
- Relevant code snippets
- Expected deliverables
- Success criteria

✅ **Provide Resources**
- Documentation links
- Test fixtures
- Related code files
- Error messages

✅ **Set Boundaries**
- What's in scope
- What's out of scope
- Time/complexity constraints
- Integration points

✅ **Request Documentation**
- Code comments
- API usage examples
- Limitations found
- Future considerations

### DON'T:

❌ **Overload with Context**
- Don't include entire codebase
- Don't provide unrelated requirements
- Don't mix multiple concerns

❌ **Leave Tasks Ambiguous**
- Don't say "make it better"
- Don't skip success criteria
- Don't assume shared knowledge

❌ **Ignore Integration**
- Don't forget how code fits together
- Don't skip interface definitions
- Don't leave error handling unclear

## Integration Pattern

After sub-agent returns:

1. **Review Deliverables**
   - Does code meet requirements?
   - Are tests comprehensive?
   - Is documentation clear?

2. **Integrate Code**
   - Merge into appropriate module
   - Ensure consistent style
   - Add type hints if missing

3. **Run Full Test Suite**
   - Unit tests pass
   - Integration tests pass
   - No regressions

4. **Document Findings**
   - Add to project docs
   - Update README if needed
   - Note limitations

## Example: rmscene Sub-Agent Session

### Main Agent Request

```
SPAWNING SUB-AGENT FOR RMSCENE INTEGRATION

Context for Sub-Agent:
---
I need you to figure out how to generate reMarkable v6 .rm files using the rmscene library.

Background:
- rmscene is a Python library for reading/writing reMarkable tablet files
- Version 0.7.0+ has experimental write capabilities
- We need to create files with typed text (not handwritten strokes)
- Text should support bold and italic formatting

Your Mission:
1. Install rmscene: pip install rmscene>=0.7.0
2. Explore the API: import rmscene; help(rmscene)
3. Read the source code, especially:
   - scene_items.py (data structures)
   - write_blocks.py (serialization)
   - crdt_sequence.py (text storage)
4. Study existing tests in the repo for examples
5. Create a minimal function that generates a valid .rm file with text

Required Function Signature:
```python
def generate_rm_page(text_items: list[TextItem]) -> bytes:
    """
    Generate binary .rm file content for a single page.
    
    Args:
        text_items: List of TextItem objects with text, position, formatting
    
    Returns:
        bytes: Valid v6 .rm file content
    """
    pass
```

Key Questions to Answer:
- How is text represented? (CrdtSequence, CrdtStr, etc.)
- How are formatting properties applied? (bold, italic)
- What block types are required for a minimal file?
- How to position text on the page? (x, y coordinates)

Validation:
- Generated bytes should parse back with rmscene.read_blocks()
- Text content should be extractable
- Formatting should be preserved

Deliverables:
1. Working generate_rm_page() function
2. Code comments explaining each step
3. List of rmscene API quirks or bugs found
4. Test cases that validate the output
5. Recommendations for error handling

Time Budget: Focus on minimal working implementation first, then add features.
---
```

### Expected Sub-Agent Return

```python
# Sub-Agent Findings for rmscene Integration

## Summary
Successfully implemented .rm file generation using rmscene 0.7.0. Key insight: 
The library uses a scene tree structure with CRDT sequences for text. Experimental 
write API works but has some limitations.

## Implementation

from rmscene import scene_items as si
from rmscene.tagged_block_writer import write_blocks
import io

def generate_rm_page(text_items: list) -> bytes:
    """
    Generate binary .rm file content for single page.
    
    Implementation notes:
    - Uses SceneTree as root structure
    - Text stored as RootTextBlock with CrdtSequence
    - Formatting via TextFormat objects with ranges
    """
    # Create scene tree
    tree = si.SceneTree()
    
    # For each text item, create appropriate scene item
    for item in text_items:
        text_item = si.Text(
            item_id=si.CrdtId(...),  # Generate unique ID
            value=si.CrdtSequence([
                si.CrdtStr(item.text)
            ]),
            # Position and size
            pos_x=item.x,
            pos_y=item.y,
            width=item.width,
        )
        
        # Apply formatting
        for fmt in item.formatting:
            text_item.add_format(
                start=fmt.start,
                end=fmt.end,
                style=fmt.style.value  # 'bold' or 'italic'
            )
        
        tree.add_item(text_item)
    
    # Serialize to bytes
    output = io.BytesIO()
    write_blocks(output, tree)
    return output.getvalue()

## Limitations Found
1. write_blocks() is not fully documented
2. Multi-line text boxes can have positioning issues
3. No direct support for headers (style applied uniformly)
4. Generated files slightly larger than device-native files

## Test Cases
[comprehensive tests included]

## Recommendations
1. Use unique CrdtId generation (timestamp-based)
2. Validate output with read_blocks() round-trip
3. Consider contributing fixes to upstream rmscene
4. Keep formatting simple initially, add features incrementally
```

## Summary

Sub-agents are powerful for tackling complex, focused problems. Use them when:
- Deep library exploration needed
- Complex algorithms require research
- Performance optimization needed
- Edge cases require extensive testing

Key to success:
- Clear, focused context
- Specific deliverables
- Proper integration back to main codebase
- Documentation of findings

The main agent orchestrates the overall project while sub-agents provide deep expertise on specific challenges.
