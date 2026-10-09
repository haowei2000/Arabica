import SwiftUI
import AppKit

// MARK: - Theme Mode

enum ThemeMode: String, CaseIterable, Identifiable {
    case dark = "Dark"
    case light = "Light"
    case system = "System"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .dark: return "moon.fill"
        case .light: return "sun.max.fill"
        case .system: return "circle.lefthalf.filled"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .dark: return .dark
        case .light: return .light
        case .system: return nil
        }
    }
}

// MARK: - Font Family

enum AppFontFamily: String, CaseIterable, Identifiable {
    case system = "System (SF Pro)"
    case rounded = "Rounded"
    case serif = "Serif (New York)"
    case monospaced = "Monospaced"

    var id: String { rawValue }

    var design: Font.Design {
        switch self {
        case .system: return .default
        case .rounded: return .rounded
        case .serif: return .serif
        case .monospaced: return .monospaced
        }
    }
}

// MARK: - Tool Icon Style

enum ToolIconStyle: String, CaseIterable, Identifiable {
    case oli = "Oli Icons"
    case sfSymbols = "SF Symbols"
    case colorful = "Colorful Glyphs"
    case monochrome = "Monochrome"

    var id: String { rawValue }
}

// MARK: - Hex Color Helpers

extension Color {
    init(hex: String, defaultColor: Color = .black) {
        let clean = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: clean).scanHexInt64(&int)
        let a, r, g, b: UInt64
        switch clean.count {
        case 3:
            (a, r, g, b) = (255, (int >> 8) * 17, (int >> 4 & 0xF) * 17, (int & 0xF) * 17)
        case 6:
            (a, r, g, b) = (255, int >> 16, int >> 8 & 0xFF, int & 0xFF)
        case 8:
            (a, r, g, b) = (int >> 24, int >> 16 & 0xFF, int >> 8 & 0xFF, int & 0xFF)
        default:
            self = defaultColor
            return
        }
        self.init(
            .sRGB,
            red: Double(r) / 255.0,
            green: Double(g) / 255.0,
            blue: Double(b) / 255.0,
            opacity: Double(a) / 255.0
        )
    }

    func toHex() -> String {
        guard let components = NSColor(self).usingColorSpace(.sRGB) else { return "#000000" }
        let r = max(0, min(255, Int(round(components.redComponent * 255))))
        let g = max(0, min(255, Int(round(components.greenComponent * 255))))
        let b = max(0, min(255, Int(round(components.blueComponent * 255))))
        return String(format: "#%02X%02X%02X", r, g, b)
    }
}

// MARK: - Theme Definition Model

struct ThemeDefinition: Identifiable, Codable, Equatable {
    var id: String
    var name: String
    var isDark: Bool
    var isCustom: Bool
    var canvasHex: String
    var sidebarHex: String
    var raisedHex: String
    var cardHex: String
    var composerBgHex: String
    var userBubbleHex: String
    var inkHex: String
    var mutedHex: String
    var subtleHex: String
    var accentHex: String
    var codeBgHex: String
    var iconStyle: String
    var fontFamily: String

    static let defaultDark = ThemeDefinition(
        id: "default-dark",
        name: "Arabica Dark",
        isDark: true,
        isCustom: false,
        canvasHex: "#141518",
        sidebarHex: "#18191C",
        raisedHex: "#1D1E22",
        cardHex: "#212227",
        composerBgHex: "#1D1E22",
        userBubbleHex: "#24262C",
        inkHex: "#ECEBE8",
        mutedHex: "#9C9CA2",
        subtleHex: "#6E6E76",
        accentHex: "#5887EB",
        codeBgHex: "#16171B",
        iconStyle: ToolIconStyle.oli.rawValue,
        fontFamily: AppFontFamily.system.rawValue
    )

    static let defaultLight = ThemeDefinition(
        id: "default-light",
        name: "Arabica Clean Light",
        isDark: false,
        isCustom: false,
        canvasHex: "#F8F9FB",
        sidebarHex: "#F0F1F5",
        raisedHex: "#F2F4F8",
        cardHex: "#FFFFFF",
        composerBgHex: "#FFFFFF",
        userBubbleHex: "#E8EEFA",
        inkHex: "#141518",
        mutedHex: "#5F6470",
        subtleHex: "#9196A2",
        accentHex: "#2369E1",
        codeBgHex: "#F3F5F9",
        iconStyle: ToolIconStyle.oli.rawValue,
        fontFamily: AppFontFamily.system.rawValue
    )

    static let nord = ThemeDefinition(
        id: "preset-nord",
        name: "Nord Arctic",
        isDark: true,
        isCustom: false,
        canvasHex: "#2E3440",
        sidebarHex: "#242933",
        raisedHex: "#3B4252",
        cardHex: "#434C5E",
        composerBgHex: "#3B4252",
        userBubbleHex: "#4C566A",
        inkHex: "#ECEFF4",
        mutedHex: "#D8DEE9",
        subtleHex: "#99A4B8",
        accentHex: "#88C0D0",
        codeBgHex: "#2B303C",
        iconStyle: ToolIconStyle.oli.rawValue,
        fontFamily: AppFontFamily.system.rawValue
    )

    static let solarizedLight = ThemeDefinition(
        id: "preset-solarized-light",
        name: "Solarized Paper",
        isDark: false,
        isCustom: false,
        canvasHex: "#FDF6E3",
        sidebarHex: "#F5EED9",
        raisedHex: "#EEE8D5",
        cardHex: "#FFFDF7",
        composerBgHex: "#FFFDF7",
        userBubbleHex: "#E7DEC4",
        inkHex: "#073642",
        mutedHex: "#657B83",
        subtleHex: "#93A1A1",
        accentHex: "#268BD2",
        codeBgHex: "#F6EFDC",
        iconStyle: ToolIconStyle.oli.rawValue,
        fontFamily: AppFontFamily.system.rawValue
    )

    static let builtInPresets: [ThemeDefinition] = [
        defaultDark,
        defaultLight,
        nord,
        solarizedLight
    ]
}

// MARK: - Theme Palette

struct ThemePalette {
    let canvas: Color
    let sidebar: Color
    let raised: Color
    let card: Color
    let composerBg: Color
    let userBubble: Color
    let ink: Color
    let muted: Color
    let subtle: Color
    let rule: Color
    let accent: Color
    let codeBg: Color
    let toolHeaderBg: Color
    let hoverBg: Color
    let selectionBg: Color

    static func from(definition: ThemeDefinition) -> ThemePalette {
        let isDark = definition.isDark
        return ThemePalette(
            canvas: Color(hex: definition.canvasHex),
            sidebar: Color(hex: definition.sidebarHex),
            raised: Color(hex: definition.raisedHex),
            card: Color(hex: definition.cardHex),
            composerBg: Color(hex: definition.composerBgHex),
            userBubble: Color(hex: definition.userBubbleHex),
            ink: Color(hex: definition.inkHex),
            muted: Color(hex: definition.mutedHex),
            subtle: Color(hex: definition.subtleHex),
            rule: isDark ? Color.white.opacity(0.10) : Color.black.opacity(0.09),
            accent: Color(hex: definition.accentHex),
            codeBg: Color(hex: definition.codeBgHex),
            toolHeaderBg: Color(hex: definition.raisedHex),
            hoverBg: isDark ? Color.white.opacity(0.05) : Color.black.opacity(0.04),
            selectionBg: isDark ? Color.white.opacity(0.09) : Color.black.opacity(0.08)
        )
    }

    static let dark = ThemePalette.from(definition: .defaultDark)
    static let light = ThemePalette.from(definition: .defaultLight)
}

// MARK: - Theme Manager

final class ThemeManager: ObservableObject, @unchecked Sendable {
    static let shared = ThemeManager()

    @AppStorage("arabica.theme.mode") var modeRaw: String = ThemeMode.dark.rawValue
    @AppStorage("arabica.theme.active_theme_id") var activeThemeID: String = ThemeDefinition.defaultDark.id
    @AppStorage("arabica.theme.fontFamily") var fontFamilyRaw: String = AppFontFamily.system.rawValue
    @AppStorage("arabica.theme.iconStyle") var iconStyleRaw: String = ToolIconStyle.oli.rawValue
    @AppStorage("arabica.theme.fontSizeDelta") var fontSizeDelta: Double = 0.0
    @AppStorage("arabica.theme.custom_themes_json") var customThemesJSON: String = "[]"

    var mode: ThemeMode {
        get { ThemeMode(rawValue: modeRaw) ?? .dark }
        set {
            modeRaw = newValue.rawValue
            // When mode is explicitly switched, update active theme ID accordingly if using defaults
            if newValue == .dark && activeThemeID == ThemeDefinition.defaultLight.id {
                activeThemeID = ThemeDefinition.defaultDark.id
            } else if newValue == .light && activeThemeID == ThemeDefinition.defaultDark.id {
                activeThemeID = ThemeDefinition.defaultLight.id
            }
            objectWillChange.send()
        }
    }

    var customThemes: [ThemeDefinition] {
        get {
            guard let data = customThemesJSON.data(using: .utf8),
                  let decoded = try? JSONDecoder().decode([ThemeDefinition].self, from: data) else {
                return []
            }
            return decoded
        }
        set {
            if let data = try? JSONEncoder().encode(newValue),
               let str = String(data: data, encoding: .utf8) {
                customThemesJSON = str
            }
            objectWillChange.send()
        }
    }

    var allThemes: [ThemeDefinition] {
        ThemeDefinition.builtInPresets + customThemes
    }

    var activeTheme: ThemeDefinition {
        if let found = allThemes.first(where: { $0.id == activeThemeID }) {
            return found
        }
        return isDark ? ThemeDefinition.defaultDark : ThemeDefinition.defaultLight
    }

    var fontFamily: AppFontFamily {
        get { AppFontFamily(rawValue: fontFamilyRaw) ?? .system }
        set {
            fontFamilyRaw = newValue.rawValue
            objectWillChange.send()
        }
    }

    var iconStyle: ToolIconStyle {
        get { ToolIconStyle(rawValue: iconStyleRaw) ?? .oli }
        set {
            iconStyleRaw = newValue.rawValue
            objectWillChange.send()
        }
    }

    var isDark: Bool {
        switch mode {
        case .dark: return true
        case .light: return false
        case .system:
            if Thread.isMainThread {
                return NSApp?.effectiveAppearance.name.rawValue.contains("Dark") ?? true
            } else {
                return DispatchQueue.main.sync {
                    NSApp?.effectiveAppearance.name.rawValue.contains("Dark") ?? true
                }
            }
        }
    }

    var currentColors: ThemePalette {
        ThemePalette.from(definition: activeTheme)
    }

    var colorScheme: ColorScheme? {
        mode.colorScheme
    }

    func selectTheme(_ theme: ThemeDefinition) {
        activeThemeID = theme.id
        if theme.isDark && mode == .light {
            mode = .dark
        } else if !theme.isDark && mode == .dark {
            mode = .light
        }
        objectWillChange.send()
    }

    func cycleMode() {
        switch mode {
        case .dark:
            mode = .light
            activeThemeID = ThemeDefinition.defaultLight.id
        case .light:
            mode = .system
        case .system:
            mode = .dark
            activeThemeID = ThemeDefinition.defaultDark.id
        }
    }

    // MARK: - Custom Theme Actions

    func createCustomTheme(name: String, baseOn: ThemeDefinition? = nil) -> ThemeDefinition {
        let base = baseOn ?? (isDark ? ThemeDefinition.defaultDark : ThemeDefinition.defaultLight)
        let newID = "custom-\(UUID().uuidString.prefix(8))"
        let newTheme = ThemeDefinition(
            id: newID,
            name: name.isEmpty ? "My Custom Theme" : name,
            isDark: base.isDark,
            isCustom: true,
            canvasHex: base.canvasHex,
            sidebarHex: base.sidebarHex,
            raisedHex: base.raisedHex,
            cardHex: base.cardHex,
            composerBgHex: base.composerBgHex,
            userBubbleHex: base.userBubbleHex,
            inkHex: base.inkHex,
            mutedHex: base.mutedHex,
            subtleHex: base.subtleHex,
            accentHex: base.accentHex,
            codeBgHex: base.codeBgHex,
            iconStyle: base.iconStyle,
            fontFamily: base.fontFamily
        )
        var list = customThemes
        list.append(newTheme)
        customThemes = list
        selectTheme(newTheme)
        return newTheme
    }

    func updateCustomTheme(_ updated: ThemeDefinition) {
        var list = customThemes
        if let idx = list.firstIndex(where: { $0.id == updated.id }) {
            list[idx] = updated
            customThemes = list
            objectWillChange.send()
        }
    }

    func deleteCustomTheme(id: String) {
        var list = customThemes
        list.removeAll { $0.id == id }
        customThemes = list
        if activeThemeID == id {
            activeThemeID = ThemeDefinition.defaultDark.id
            mode = .dark
        }
        objectWillChange.send()
    }

    // MARK: - Tool Color Resolution

    func toolColor(for kind: String?) -> Color {
        let k = kind?.lowercased() ?? ""
        if iconStyle == .monochrome {
            return currentColors.muted
        }
        if iconStyle == .colorful {
            if k == "execute" || k.contains("term") || k.contains("bash") {
                return isDark ? Color(red: 52/255, green: 199/255, blue: 89/255) : Color(red: 40/255, green: 167/255, blue: 69/255)
            }
            if k == "read" {
                return isDark ? Color(red: 90/255, green: 160/255, blue: 255/255) : Color(red: 30/255, green: 120/255, blue: 235/255)
            }
            if k == "edit" || k == "write" {
                return isDark ? Color(red: 191/255, green: 90/255, blue: 242/255) : Color(red: 147/255, green: 53/255, blue: 211/255)
            }
            if k == "search" {
                return isDark ? Color(red: 255/255, green: 159/255, blue: 10/255) : Color(red: 220/255, green: 130/255, blue: 10/255)
            }
            if k == "delete" {
                return isDark ? Color(red: 255/255, green: 69/255, blue: 58/255) : Color(red: 215/255, green: 50/255, blue: 50/255)
            }
            if k == "think" {
                return isDark ? Color(red: 175/255, green: 110/255, blue: 255/255) : Color(red: 130/255, green: 60/255, blue: 220/255)
            }
            if k == "fetch" {
                return isDark ? Color(red: 100/255, green: 210/255, blue: 255/255) : Color(red: 20/255, green: 150/255, blue: 200/255)
            }
        }
        return currentColors.muted
    }
}
