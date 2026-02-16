"""Grep tool for searching text content using patterns."""

import re
from typing import Any

from pydantic import Field

from aiwen.core.interfaces.tool import (
    InnerTool,
    ToolInputSchema,
    ToolMetadata,
    ToolOutputSchema,
)


class GrepTool(InnerTool):
    """Search text content using patterns, similar to the Unix grep command"""

    METADATA = ToolMetadata(
        name="grep",
        display_name="Grep",
        description=(
            "Search arbitrary text for lines matching a pattern (regex or literal). "
            "Supports options like case-insensitive matching, context lines, "
            "invert match, and line numbers — similar to the Unix grep command."
        ),
        category="utility",
        tags=["grep", "search", "text", "regex", "filter"],
        timeout=30,
    )

    class InputSchema(ToolInputSchema):
        text: str = Field(description="The text content to search through")
        pattern: str = Field(description="Search pattern (regex by default, or literal if fixed_string is true)")
        fixed_string: bool = Field(
            default=False,
            description="Treat pattern as a literal string instead of a regex",
        )
        ignore_case: bool = Field(
            default=False,
            description="Perform case-insensitive matching",
        )
        invert_match: bool = Field(
            default=False,
            description="Select non-matching lines (like grep -v)",
        )
        max_count: int = Field(
            default=0,
            ge=0,
            description="Stop after this many matches (0 = unlimited, like grep -m)",
        )
        context_before: int = Field(
            default=0,
            ge=0,
            le=50,
            description="Number of lines to show before each match (like grep -B)",
        )
        context_after: int = Field(
            default=0,
            ge=0,
            le=50,
            description="Number of lines to show after each match (like grep -A)",
        )
        line_number: bool = Field(
            default=True,
            description="Include line numbers in the output (like grep -n)",
        )
        count_only: bool = Field(
            default=False,
            description="Only return the count of matching lines (like grep -c)",
        )
        multiline: bool = Field(
            default=False,
            description="Enable multiline mode where ^ and $ match line boundaries",
        )

    async def execute(self, input_data: InputSchema) -> ToolOutputSchema:
        # Build regex flags
        flags = 0
        if input_data.ignore_case:
            flags |= re.IGNORECASE
        if input_data.multiline:
            flags |= re.MULTILINE

        # Compile the pattern
        raw_pattern = input_data.pattern
        if input_data.fixed_string:
            raw_pattern = re.escape(raw_pattern)

        try:
            regex = re.compile(raw_pattern, flags)
        except re.error as e:
            return ToolOutputSchema(
                success=False,
                message=f"Invalid regex pattern: {e}",
                error=str(e),
            )

        lines = input_data.text.splitlines()
        total_lines = len(lines)

        # Find matching line indices
        match_indices: list[int] = []
        for idx, line in enumerate(lines):
            found = regex.search(line) is not None
            if found != input_data.invert_match:
                match_indices.append(idx)
                if input_data.max_count > 0 and len(match_indices) >= input_data.max_count:
                    break

        # Count-only mode
        if input_data.count_only:
            return ToolOutputSchema(
                success=True,
                message=f"{len(match_indices)} matching line(s)",
                data={
                    "count": len(match_indices),
                    "total_lines": total_lines,
                    "pattern": input_data.pattern,
                },
            )

        # Build output with optional context lines
        ctx_before = input_data.context_before
        ctx_after = input_data.context_after

        if ctx_before > 0 or ctx_after > 0:
            # Collect ranges of lines to include, merging overlapping ranges
            ranges: list[tuple[int, int]] = []
            for mi in match_indices:
                start = max(0, mi - ctx_before)
                end = min(total_lines - 1, mi + ctx_after)
                if ranges and start <= ranges[-1][1] + 1:
                    ranges[-1] = (ranges[-1][0], end)
                else:
                    ranges.append((start, end))

            match_set = set(match_indices)
            result_groups: list[list[dict]] = []
            for range_start, range_end in ranges:
                group: list[dict] = []
                for i in range(range_start, range_end + 1):
                    entry: dict[str, Any] = {
                        "text": lines[i],
                        "is_match": i in match_set,
                    }
                    if input_data.line_number:
                        entry["line_number"] = i + 1
                    group.append(entry)
                result_groups.append(group)

            return ToolOutputSchema(
                success=True,
                message=f"{len(match_indices)} matching line(s) in {total_lines} total lines",
                data={
                    "groups": result_groups,
                    "match_count": len(match_indices),
                    "total_lines": total_lines,
                    "pattern": input_data.pattern,
                },
            )

        # Simple output — only matching lines
        result_lines: list[dict] = []
        for mi in match_indices:
            entry = {"text": lines[mi]}
            if input_data.line_number:
                entry["line_number"] = mi + 1
            result_lines.append(entry)

        return ToolOutputSchema(
            success=True,
            message=f"{len(match_indices)} matching line(s) in {total_lines} total lines",
            data={
                "lines": result_lines,
                "match_count": len(match_indices),
                "total_lines": total_lines,
                "pattern": input_data.pattern,
            },
        )
