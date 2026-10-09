import XCTest
import SwiftUI
@testable import ArabicaDesktop

@MainActor
final class ToolContentTests: XCTestCase {
    func testTextDiffAndTerminalContent() {
        let detail = DesktopModel.toolDetail([
            ["type": "content", "content": ["type": "text", "text": "Found 2 matches"]],
            ["type": "diff", "path": "/workspace/a.txt", "oldText": "before", "newText": "after"],
            ["type": "terminal", "terminalId": "term-1"],
        ])
        XCTAssertTrue(detail?.contains("Found 2 matches") == true)
        XCTAssertTrue(detail?.contains("/workspace/a.txt") == true)
        XCTAssertTrue(detail?.contains("− before") == true)
        XCTAssertTrue(detail?.contains("+ after") == true)
        XCTAssertTrue(detail?.contains("Terminal term-1") == true)
    }

    func testGroupedForFeedConsecutiveTools() {
        let items: [ChatItem] = [
            ChatItem(kind: .user, text: "Hello"),
            ChatItem(kind: .thought, text: "Thinking about it..."),
            ChatItem(kind: .tool, text: "Read file", status: "completed", toolID: "t1", toolKind: "read"),
            ChatItem(kind: .tool, text: "Grep symbols", status: "completed", toolID: "t2", toolKind: "search"),
            ChatItem(kind: .assistant, text: "Here is the result"),
            ChatItem(kind: .tool, text: "Run tests", status: "in_progress", toolID: "t3", toolKind: "execute"),
        ]

        let feed = items.groupedForFeed
        XCTAssertEqual(feed.count, 5)

        if case .user(let item) = feed[0] {
            XCTAssertEqual(item.text, "Hello")
        } else {
            XCTFail("Expected .user")
        }

        if case .thought(let item) = feed[1] {
            XCTAssertEqual(item.text, "Thinking about it...")
        } else {
            XCTFail("Expected .thought")
        }

        if case .toolGroup(_, let tools) = feed[2] {
            XCTAssertEqual(tools.count, 2)
            XCTAssertEqual(tools[0].toolKind, "read")
            XCTAssertEqual(tools[1].toolKind, "search")
        } else {
            XCTFail("Expected .toolGroup")
        }

        if case .assistant(let item) = feed[3] {
            XCTAssertEqual(item.text, "Here is the result")
        } else {
            XCTFail("Expected .assistant")
        }

        if case .toolGroup(_, let tools) = feed[4] {
            XCTAssertEqual(tools.count, 1)
            XCTAssertEqual(tools[0].toolKind, "execute")
        } else {
            XCTFail("Expected .toolGroup")
        }
    }

    func testToolVisualIcons() {
        XCTAssertEqual(ToolVisuals.icon(for: "read"), "doc.text.magnifyingglass")
        XCTAssertEqual(ToolVisuals.icon(for: "search"), "magnifyingglass")
        XCTAssertEqual(ToolVisuals.icon(for: "edit"), "square.and.pencil")
        XCTAssertEqual(ToolVisuals.icon(for: "execute"), "terminal")
        XCTAssertEqual(ToolVisuals.icon(for: "delete"), "trash")
        XCTAssertEqual(ToolVisuals.icon(for: "move"), "arrow.right.doc.on.clipboard")
        XCTAssertEqual(ToolVisuals.icon(for: "fetch"), "globe")
        XCTAssertEqual(ToolVisuals.icon(for: "think"), "brain.head.profile")
        XCTAssertEqual(ToolVisuals.icon(for: "switch_mode"), "arrow.triangle.2.circlepath")
        XCTAssertEqual(ToolVisuals.icon(for: "custom", title: "git status"), "arrow.triangle.branch")
        XCTAssertEqual(ToolVisuals.icon(for: "unknown", title: "custom tool"), "wrench.and.screwdriver")
    }

    func testThemeManagerModesAndPalettes() {
        let theme = ThemeManager.shared
        theme.mode = .dark
        XCTAssertEqual(theme.colorScheme, .dark)
        XCTAssertTrue(theme.isDark)

        theme.mode = .light
        XCTAssertEqual(theme.colorScheme, .light)
        XCTAssertFalse(theme.isDark)

        theme.mode = .system
        XCTAssertNil(theme.colorScheme)

        // Palette differences
        let darkPalette = ThemePalette.dark
        let lightPalette = ThemePalette.light
        XCTAssertNotEqual(darkPalette.canvas, lightPalette.canvas)
        XCTAssertNotEqual(darkPalette.ink, lightPalette.ink)

        // Reset to dark for consistency
        theme.mode = .dark
    }

    func testThemeToolColorAndStyles() {
        let theme = ThemeManager.shared
        theme.iconStyle = .monochrome
        XCTAssertEqual(theme.toolColor(for: "read"), theme.currentColors.muted)

        theme.iconStyle = .colorful
        XCTAssertNotEqual(theme.toolColor(for: "read"), theme.currentColors.muted)
        XCTAssertNotEqual(theme.toolColor(for: "execute"), theme.currentColors.muted)

        // Reset
        theme.iconStyle = .oli
    }

    func testThemeTypographyScale() {
        let theme = ThemeManager.shared
        theme.fontFamily = .system
        XCTAssertEqual(theme.fontFamily.design, .default)

        theme.fontFamily = .serif
        XCTAssertEqual(theme.fontFamily.design, .serif)

        theme.fontSizeDelta = 2.0
        XCTAssertEqual(theme.fontSizeDelta, 2.0)

        // Reset
        theme.fontFamily = .system
        theme.fontSizeDelta = 0.0
    }

    func testCustomThemeLifecycle() {
        let theme = ThemeManager.shared
        let originalThemeID = theme.activeThemeID
        let originalCustomCount = theme.customThemes.count

        // 1. Create custom theme
        let newTheme = theme.createCustomTheme(name: "Test Forest Theme", baseOn: .defaultDark)
        XCTAssertEqual(theme.customThemes.count, originalCustomCount + 1)
        XCTAssertEqual(newTheme.name, "Test Forest Theme")
        XCTAssertTrue(newTheme.isCustom)
        XCTAssertTrue(newTheme.isDark)
        XCTAssertEqual(theme.activeThemeID, newTheme.id)

        // 2. Modify theme color
        var updated = newTheme
        updated.accentHex = "#2ECC71"
        theme.updateCustomTheme(updated)
        XCTAssertEqual(theme.activeTheme.accentHex, "#2ECC71")

        // 3. Delete theme
        theme.deleteCustomTheme(id: newTheme.id)
        XCTAssertEqual(theme.customThemes.count, originalCustomCount)
        XCTAssertNotEqual(theme.activeThemeID, newTheme.id)

        // Reset
        theme.activeThemeID = originalThemeID
    }

    func testHexColorConversion() {
        let hex = "#ff8800"
        let color = Color(hex: hex)
        let backToHex = color.toHex()
        XCTAssertEqual(backToHex.lowercased(), hex.lowercased())
    }
}
