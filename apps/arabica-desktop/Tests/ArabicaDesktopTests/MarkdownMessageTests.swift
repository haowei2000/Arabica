import XCTest
@testable import ArabicaDesktop

final class MarkdownMessageTests: XCTestCase {
    func testBlockStructureAndLiteralCode() {
        let blocks = MarkdownBlock.parse("# Title\n\nHello **world**\n\n- Item\n2. Next\n> Quote\n---\n```swift\n# literal\n\n**code**\n```")
        XCTAssertEqual(blocks.map(\.kind), [.heading(1), .paragraph, .list("•"), .list("2."), .quote, .rule, .code])
        XCTAssertEqual(blocks.last?.text, "# literal\n\n**code**")
        XCTAssertEqual(blocks.last?.language, "swift")
    }

    func testStreamingUnclosedFenceAndPlainText() {
        XCTAssertEqual(MarkdownBlock.parse("Before\n~~~\nlet x = 1"), [
            MarkdownBlock(kind: .paragraph, text: "Before"),
            MarkdownBlock(kind: .code, text: "let x = 1"),
        ])
        XCTAssertEqual(MarkdownBlock.parse("#hashtag\ncontinued"), [
            MarkdownBlock(kind: .paragraph, text: "#hashtag\ncontinued"),
        ])
    }

    func testNestedListsKeepContinuationParagraphsAndCodeInsideTheirItem() {
        let source = "- First line\n  continuation\n  - Nested\n    wrapped\n\n  Another paragraph\n\n  ```swift\n  let value = 1\n  ```\n- Second\n\nOutside"
        let blocks = MarkdownBlock.parse(source)
        XCTAssertEqual(blocks.map(\.kind), [.list("•"), .list("•"), .paragraph])
        let children = MarkdownBlock.parse(blocks[0].text)
        XCTAssertEqual(children.map(\.kind), [.paragraph, .list("•"), .paragraph, .code])
        XCTAssertEqual(children[0].text, "First line\ncontinuation")
        let nested = MarkdownBlock.parse(children[1].text)
        XCTAssertEqual(nested.map(\.kind), [.paragraph])
        XCTAssertEqual(nested[0].text, "Nested\nwrapped")
        XCTAssertEqual(children[2].text, "Another paragraph")
        XCTAssertEqual(children[3].text, "let value = 1")
        XCTAssertEqual(children[3].language, "swift")
        XCTAssertEqual(blocks.last?.text, "Outside")
    }

    func testNestedListDedentsAfterChildAndKeepsOrderedMarkers() {
        let blocks = MarkdownBlock.parse("3. Parent\n   - Child\n   - Other child\n4. Next\n\nParagraph")
        XCTAssertEqual(blocks.map(\.kind), [.list("3."), .list("4."), .paragraph])
        XCTAssertEqual(MarkdownBlock.parse(blocks[0].text).map(\.kind), [.paragraph, .list("•"), .list("•")])
        XCTAssertEqual(MarkdownBlock.parse("- Parent\n\t- Child")[0].text, "Parent\n  - Child")
        XCTAssertEqual(MarkdownBlock.parse("-\tFirst\n\tcontinued")[0].text, "First\ncontinued")
    }

    func testTaskListsKeepCompletionStateAndNestedTasks() {
        let blocks = MarkdownBlock.parse("- [ ] Pending\n  - [X] Done\n- [x] Complete\n- [x]literal\n- [ ]")
        XCTAssertEqual(blocks.map(\.taskChecked), [false, true, nil, false])
        XCTAssertEqual(blocks[0].text, "Pending\n- [X] Done")
        XCTAssertEqual(MarkdownBlock.parse(blocks[0].text)[1].taskChecked, true)
        XCTAssertEqual(blocks[1].text, "Complete")
        XCTAssertEqual(blocks[2].text, "[x]literal")
        XCTAssertEqual(blocks[3].text, "")
    }

    func testQuotesPreserveParagraphsNestedListsAndNestedQuotes() {
        let source = "> First\ncontinued\n> line\n>\n> - Item\n>   - Child\n>\n> > Inner\n\nOutside"
        let blocks = MarkdownBlock.parse(source)
        XCTAssertEqual(blocks.map(\.kind), [.quote, .paragraph])
        let children = MarkdownBlock.parse(blocks[0].text)
        XCTAssertEqual(children.map(\.kind), [.paragraph, .list("•"), .quote])
        XCTAssertEqual(children[0].text, "First\ncontinued\nline")
        XCTAssertEqual(children.last?.text, "Inner")
        XCTAssertEqual(blocks.last?.text, "Outside")
    }

    func testSetextATXAndSpacedThematicBreaks() {
        let blocks = MarkdownBlock.parse("Title\n===\n\nTwo\nlines\n---\n\n## Heading ###\n#\n###\tTabs\n\n- - -\n* * *\n_ _ _\n\n#hashtag")
        XCTAssertEqual(blocks.map(\.kind), [.heading(1), .heading(2), .heading(2), .heading(1), .heading(3),
                                           .rule, .rule, .rule, .paragraph])
        XCTAssertEqual(blocks.prefix(5).map(\.text), ["Title", "Two\nlines", "Heading", "", "Tabs"])
        XCTAssertEqual(MarkdownBlock.parse("Title\n    ===").first?.kind, .paragraph)
    }

    func testIndentedCodeIsLiteralAndCannotCloseFence() {
        XCTAssertEqual(MarkdownBlock.parse("    # literal\n\t- literal\n\nAfter"), [
            MarkdownBlock(kind: .code, text: "# literal\n- literal"),
            MarkdownBlock(kind: .paragraph, text: "After"),
        ])
        XCTAssertEqual(MarkdownBlock.parse("```\n    ```\n```"), [
            MarkdownBlock(kind: .code, text: "    ```"),
        ])
        XCTAssertEqual(MarkdownBlock.parse("Text\n    continued").first?.kind, .paragraph)
    }

    func testGFMTableAndColumnAlignment() {
        XCTAssertEqual(MarkdownBlock.parse("| Name | Count |\n|:-----|------:|\n| Tea | 2 |\n| Coffee | 5 |"), [
            MarkdownBlock(kind: .table(
                headers: ["Name", "Count"],
                rows: [["Tea", "2"], ["Coffee", "5"]],
                alignments: [.leading, .trailing]
            ), text: ""),
        ])
    }

    func testTableKeepsEscapedAndInlineCodePipesTogether() {
        let source = "| Name | Value |\n| --- | --- |\n| Inline | `a|b` |\n| Escaped | a\\|b |"
        XCTAssertEqual(MarkdownBlock.parse(source), [
            MarkdownBlock(kind: .table(
                headers: ["Name", "Value"],
                rows: [["Inline", "`a|b`"], ["Escaped", "a|b"]],
                alignments: [.leading, .leading]
            ), text: ""),
        ])
        XCTAssertEqual(MarkdownBlock.parse("```\n" + source + "\n```").first?.kind, .code)
        XCTAssertEqual(MarkdownBlock.parse("a | b\nnot | separator").first?.kind, .paragraph)
    }

    func testFenceLengthLanguageAndClosingFenceRules() {
        let source = "````Mermaid title\r\nflowchart LR\r\nA-->B\r\n```\r\n````not a closing fence\r\n`````\r\nAfter"
        let blocks = MarkdownBlock.parse(source)
        XCTAssertEqual(blocks, [
            MarkdownBlock(kind: .code, text: "flowchart LR\nA-->B\n```\n````not a closing fence", language: "mermaid"),
            MarkdownBlock(kind: .paragraph, text: "After"),
        ])
        XCTAssertEqual(MarkdownBlock.parse("~~~JSON\n{\"ok\":true}").first?.language, "json")
    }

    func testSingleColumnTableAndShortDelimiters() {
        XCTAssertEqual(MarkdownBlock.parse("| Name |\n| - |\n| Tea |"), [
            MarkdownBlock(kind: .table(headers: ["Name"], rows: [["Tea"]], alignments: [.leading]), text: ""),
        ])
    }

    func testTableCodeSpansAndFollowingBlocks() {
        let source = "Name | Value\n- | -\nCode | ``a`|b``\nUnmatched | `value\n# Heading | text\n```swift\nlet value = 1\n```"
        let blocks = MarkdownBlock.parse(source)
        XCTAssertEqual(blocks.first?.kind, .table(
            headers: ["Name", "Value"], rows: [["Code", "``a`|b``"], ["Unmatched", "`value"]],
            alignments: [.leading, .leading]
        ))
        XCTAssertEqual(blocks.map(\.kind).suffix(2), [.heading(1), .code])
        XCTAssertEqual(blocks.last?.language, "swift")
    }

    func testTablePadsMissingCellsAndClipsExcessCells() {
        XCTAssertEqual(MarkdownBlock.parse("A | B | C\n- | - | -\none | two\na | b | c | d").first?.kind,
                       .table(headers: ["A", "B", "C"], rows: [["one", "two", ""], ["a", "b", "c"]],
                              alignments: [.leading, .leading, .leading]))
        XCTAssertEqual(MarkdownBlock.parse("A | B\n- | -\n`a\\` | value").first?.kind,
                       .table(headers: ["A", "B"], rows: [["`a\\`", "value"]], alignments: [.leading, .leading]))
    }

    func testTableSizingUsesContentAndFitsAvailableSpace() {
        let headers = ["ID", "Description"]
        let rows = [["1", String(repeating: "Description ", count: 8)]]
        let preferred = MarkdownTableSizing.columnWidths(headers: headers, rows: rows, availableWidth: 0, fontSize: 12)
        XCTAssertGreaterThan(preferred[1], preferred[0])
        let compact = MarkdownTableSizing.columnWidths(headers: headers, rows: rows, availableWidth: 240, fontSize: 12)
        XCTAssertEqual(compact.reduce(0, +), 240, accuracy: 0.1)
        XCTAssertTrue(compact.allSatisfy { $0 >= 76 })
        let narrow = MarkdownTableSizing.columnWidths(headers: headers, rows: rows, availableWidth: 100, fontSize: 12)
        XCTAssertEqual(narrow, preferred, "Very narrow tables should scroll instead of producing unreadable columns")
    }

    func testMermaidFlowchartPreservesNodesEdgesAndLabels() throws {
        let source = "%% A comment\nflowchart LR; A[Start] --> B{Ready?}; B -->|Yes| C(Ship); B -- No --> A"
        guard case .flowchart(let chart) = try MermaidDiagram.parse(source) else { return XCTFail("Expected flowchart") }
        XCTAssertEqual(chart.direction, .leftToRight)
        XCTAssertEqual(chart.nodes.map(\.label), ["Start", "Ready?", "Ship"])
        XCTAssertEqual(chart.nodes.map(\.shape), [.rectangle, .diamond, .rounded])
        XCTAssertEqual(chart.edges.map(\.label), ["", "Yes", "No"])
        XCTAssertEqual(chart.edges.map(\.to), ["B", "C", "A"])
    }

    func testMermaidChainsQuotedLabelsAndExplicitNodeUpdates() throws {
        let source = "graph TD\nA[\"Start; [input]\"] -.-> B --> C((Done))\nB[\"Read<br/>data\"]"
        guard case .flowchart(let chart) = try MermaidDiagram.parse(source) else { return XCTFail("Expected flowchart") }
        XCTAssertEqual(chart.nodes.map(\.label), ["Start; [input]", "Read\ndata", "Done"])
        XCTAssertTrue(chart.edges[0].dashed)
        XCTAssertFalse(chart.edges[1].dashed)
        XCTAssertEqual(chart.edges.count, 2)
    }

    func testMermaidSequenceAliasesAndArrowStyles() throws {
        let source = "sequenceDiagram\nparticipant A as Client\nactor B as User\nautonumber\nB->>A: Open\nA-->>B: Result\nA->A: Check"
        guard case .sequence(let sequence) = try MermaidDiagram.parse(source) else { return XCTFail("Expected sequence") }
        XCTAssertEqual(sequence.participants.map(\.label), ["Client", "User"])
        XCTAssertTrue(sequence.participants[1].actor)
        XCTAssertTrue(sequence.numbered)
        XCTAssertEqual(sequence.messages.map(\.dashed), [false, true, false])
        XCTAssertEqual(sequence.messages.map(\.filledArrow), [true, true, false])
    }

    func testUnsupportedAndIncompleteMermaidFallsBackRatherThanLosingStatements() {
        for source in [
            "classDiagram\nA --> B",
            "flowchart TD\nsubgraph Group\nA-->B\nend",
            "flowchart TD\nA <--> B",
            "flowchart TD\nA-->B\nclick A \"https://example.com\"",
            "sequenceDiagram\nA->>B: Hello\nloop Retry\nB-->>A: Again\nend",
            "flowchart TD\nA[Unfinished",
            "flowchart TD\nA-->",
        ] { XCTAssertThrowsError(try MermaidDiagram.parse(source), source) }
        XCTAssertThrowsError(try MermaidDiagram.parse(String(repeating: "x", count: 30_001)))
        let tooManyParticipants = "sequenceDiagram\n" + (0..<21).map { "participant P\($0)" }.joined(separator: "\n")
        XCTAssertThrowsError(try MermaidDiagram.parse(tooManyParticipants))
    }

    func testMermaidLayoutKeepsAllNodesInBoundsWithoutOverlap() throws {
        for direction in ["TD", "BT", "LR", "RL"] {
            guard case .flowchart(let chart) = try MermaidDiagram.parse("flowchart \(direction)\nA[Start]-->B{Check}\nA-->C[Read]\nB-->D[Done]\nC-->D\nD-->A\nE[Separate]") else {
                return XCTFail("Expected flowchart")
            }
            let layout = MermaidFlowchartLayout(chart: chart, fontSize: 12)
            XCTAssertEqual(layout.frames.count, chart.nodes.count)
            let bounds = CGRect(origin: .zero, size: layout.size)
            for (id, frame) in layout.frames {
                XCTAssertTrue(bounds.contains(frame), "\(direction): \(id) is outside the canvas")
                for (otherID, otherFrame) in layout.frames where otherID != id {
                    XCTAssertFalse(frame.intersects(otherFrame), "\(direction): \(id) overlaps \(otherID)")
                }
            }
        }
    }
}
