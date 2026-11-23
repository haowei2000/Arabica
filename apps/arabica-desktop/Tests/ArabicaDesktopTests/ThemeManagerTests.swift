import XCTest
@testable import ArabicaDesktop

@MainActor
final class ThemeManagerTests: XCTestCase {
    func testSwitchingModeAlignsNonDefaultPresetPolarity() {
        let manager = ThemeManager.shared
        let savedMode = manager.modeRaw
        let savedThemeID = manager.activeThemeID
        defer {
            manager.modeRaw = savedMode
            manager.activeThemeID = savedThemeID
        }

        manager.selectTheme(.nord)
        XCTAssertEqual(manager.activeThemeID, ThemeDefinition.nord.id)
        XCTAssertEqual(manager.mode, .dark)

        manager.mode = .light
        XCTAssertEqual(manager.activeThemeID, ThemeDefinition.defaultLight.id)
        XCTAssertFalse(manager.activeTheme.isDark)

        manager.selectTheme(.solarizedLight)
        XCTAssertEqual(manager.activeThemeID, ThemeDefinition.solarizedLight.id)
        XCTAssertEqual(manager.mode, .light)

        manager.mode = .dark
        XCTAssertEqual(manager.activeThemeID, ThemeDefinition.defaultDark.id)
        XCTAssertTrue(manager.activeTheme.isDark)
    }
}
