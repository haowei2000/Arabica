"""Test script for skill management feature."""

import asyncio
import sys
from uuid import uuid4

sys.path.insert(0, "/Users/wanghaowei/PycharmProjects/agent_chat/src")


async def test_skill_feature():
    """Test skill CRUD operations and Markdown processing."""
    from structure.core.enums import ContextType
    from structure.extensions.database import get_session
    from structure.schemas.context.skill import SkillCreate, SkillUpdate
    from structure.services.context.skill_crud import SkillCRUD
    from structure.services.context.skill_processor import SkillProcessor

    print("=" * 70)
    print("  Skill Management Feature Test")
    print("=" * 70)

    user_id = uuid4()
    markdown_content = """---
author: Test User
version: 1.0
category: Programming
---

# Python Best Practices

## Code Style

Follow PEP 8 guidelines for clean, readable code:

```python
def calculate_total(items):
    \"\"\"Calculate the total price of items.\"\"\"
    return sum(item.price for item in items)
```

## Testing

- Write unit tests for all functions
- Use pytest for testing
- Aim for >80% code coverage

## Documentation

Document all public APIs:

```python
def process_data(data: dict) -> dict:
    \"\"\"
    Process input data and return results.

    Args:
        data: Input dictionary containing raw data

    Returns:
        Processed data dictionary
    \"\"\"
    pass
```
"""

    async with get_session("structure") as session:
        crud = SkillCRUD(session)
        processor = SkillProcessor(session)

        # Test 1: Create Skill
        print("\n1. Testing Skill Creation")
        print("-" * 70)
        skill_data = SkillCreate(
            name="Python Best Practices",
            description="Comprehensive guide to writing clean Python code",
            content=markdown_content,
            tags=["python", "best-practices", "coding-standards"],
        )

        skill = await crud.create(skill_data, user_id=user_id, auto_commit=False)
        print(f"  ✓ Created skill: {skill.id}")
        print(f"  - Name: {skill.meta.get('name')}")
        print(f"  - Type: {skill.context_type}")
        print(f"  - Tags: {skill.tags}")
        print(f"  - Glance: {skill.glance}")
        assert skill.context_type == ContextType.SKILL.value
        assert skill.meta["name"] == "Python Best Practices"

        # Test 2: Markdown Processing
        print("\n2. Testing Markdown Processing")
        print("-" * 70)
        processed_skill = await processor.process_skill(skill.id, auto_commit=False)
        print(f"  ✓ Processed skill: {skill.id}")
        print(f"  - Summary generated: {bool(processed_skill.summary)}")
        print(f"  - Parsed structure: {processed_skill.meta.get('parsed_structure')}")

        # Parse and display structure
        parsed = processor.parse_markdown(markdown_content)
        print("\n  Parsed Markdown Structure:")
        print(f"  - Sections: {len(parsed['sections'])}")
        for section in parsed["sections"]:
            indent = "  " * section["level"]
            print(f"    {indent}- [{section['level']}] {section['title']}")
        print(f"  - Code blocks: {len(parsed['code_blocks'])}")
        for cb in parsed["code_blocks"]:
            print(f"    - Language: {cb['language']}")
        print(f"  - Has frontmatter: {bool(parsed['metadata'])}")
        if parsed["metadata"]:
            print(f"  - Frontmatter keys: {list(parsed['metadata'].keys())}")

        # Test 3: Get by ID
        print("\n3. Testing Get by ID")
        print("-" * 70)
        retrieved = await crud.get_by_id(skill.id, user_id=user_id)
        print(f"  ✓ Retrieved skill: {retrieved.meta.get('name')}")
        assert retrieved is not None
        assert str(retrieved.id) == str(skill.id)

        # Test 4: Get by Name
        print("\n4. Testing Get by Name")
        print("-" * 70)
        by_name = await crud.get_by_name("Python Best Practices", user_id=user_id)
        print(f"  ✓ Found skill by name: {by_name.meta.get('name')}")
        assert by_name is not None
        assert str(by_name.id) == str(skill.id)

        # Test 5: Update Skill
        print("\n5. Testing Skill Update")
        print("-" * 70)
        update_data = SkillUpdate(
            description="Updated: Best practices for Python development",
            tags=["python", "best-practices", "coding-standards", "updated"],
        )
        updated = await crud.update(
            skill.id, update_data, user_id=user_id, auto_commit=False
        )
        print("  ✓ Updated skill")
        print(f"  - New description: {updated.meta.get('description')}")
        print(f"  - New tags: {updated.tags}")
        assert updated is not None
        assert len(updated.tags) == 4

        # Test 6: List Skills
        print("\n6. Testing List Skills")
        print("-" * 70)

        # Create another skill
        skill2_data = SkillCreate(
            name="JavaScript Patterns",
            description="Common design patterns in JavaScript",
            content="# JavaScript Patterns\n\nExamples of design patterns...",
            tags=["javascript", "patterns", "best-practices"],
        )
        skill2 = await crud.create(skill2_data, user_id=user_id, auto_commit=False)
        print(f"  ✓ Created second skill: {skill2.id}")

        # List all skills
        skills, total = await crud.list(user_id=user_id)
        print(f"  ✓ Listed skills: {total} total")
        for s in skills:
            print(f"    - {s.meta.get('name')} (tags: {s.tags})")
        assert total == 2

        # List with tag filter
        python_skills, python_total = await crud.list(
            user_id=user_id, tags=["python"]
        )
        print(f"  ✓ Filtered by 'python' tag: {python_total} found")
        assert python_total == 1

        # Test 7: Search Skills
        print("\n7. Testing Search")
        print("-" * 70)
        results, count = await crud.search(
            user_id=user_id, query_text="best practices"
        )
        print(f"  ✓ Search results for 'best practices': {count} found")
        for r in results:
            print(f"    - {r.meta.get('name')}")
        assert count >= 1  # At least one skill matches

        # Search for JavaScript
        js_results, js_count = await crud.search(
            user_id=user_id, query_text="JavaScript"
        )
        print(f"  ✓ Search results for 'JavaScript': {js_count} found")
        assert js_count == 1

        # Test 8: Delete Skill
        print("\n8. Testing Delete")
        print("-" * 70)
        deleted = await crud.delete(skill2.id, user_id=user_id, auto_commit=False)
        print(f"  ✓ Deleted skill: {deleted}")
        assert deleted is True

        # Verify deletion
        skills_after, total_after = await crud.list(user_id=user_id)
        print(f"  ✓ Skills after deletion: {total_after}")
        assert total_after == 1

        # Cleanup
        await crud.delete(skill.id, user_id=user_id, auto_commit=False)
        await session.rollback()  # Rollback all test data

        print("\n" + "=" * 70)
        print("  🎉 All Tests Passed!")
        print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_skill_feature())
