import SwiftUI

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

// MARK: - Theme Palette

struct ThemePalette {
    let canvas: Color
    let sidebar: Color
    let raised: Color
    let card: Color
    let userBubble: Color
    let ink: Color
    let muted: Color
    let subtle: Color
    let rule: Color
    let accent: Color
    let codeBg: Color
    let toolHeaderBg: Color

    static let dark = ThemePalette(
        canvas: Color(red: 20 / 255, green: 21 / 255, blue: 24 / 255),
        sidebar: Color(red: 24 / 255, green: 25 / 255, blue: 28 / 255),
        raised: Color(red: 29 / 255, green: 30 / 255, blue: 34 / 255),
        card: Color(red: 33 / 255, green: 34 / 255, blue: 39 / 255),
        userBubble: Color(red: 36 / 255, green: 38 / 255, blue: 44 / 255),
        ink: Color(red: 236 / 255, green: 235 / 255, blue: 232 / 255),
        muted: Color(red: 156 / 255, green: 156 / 255, blue: 162 / 255),
        subtle: Color(red: 110 / 255, green: 110 / 255, blue: 118 / 255),
        rule: Color.white.opacity(0.10),
        accent: Color(red: 88 / 255, green: 135 / 255, blue: 235 / 255),
        codeBg: Color(red: 22 / 255, green: 23 / 255, blue: 27 / 255),
        toolHeaderBg: Color(red: 29 / 255, green: 30 / 255, blue: 34 / 255)
    )

    static let light = ThemePalette(
        canvas: Color(red: 250 / 255, green: 250 / 255, blue: 252 / 255),
        sidebar: Color(red: 242 / 255, green: 243 / 255, blue: 246 / 255),
        raised: Color(red: 232 / 255, green: 234 / 255, blue: 239 / 255),
        card: Color(red: 255 / 255, green: 255 / 255, blue: 255 / 255),
        userBubble: Color(red: 235 / 255, green: 240 / 255, blue: 250 / 255),
        ink: Color(red: 25 / 255, green: 26 / 255, blue: 30 / 255),
        muted: Color(red: 105 / 255, green: 110 / 255, blue: 120 / 255),
        subtle: Color(red: 155 / 255, green: 160 / 255, blue: 172 / 255),
        rule: Color.black.opacity(0.08),
        accent: Color(red: 45 / 255, green: 110 / 255, blue: 230 / 255),
        codeBg: Color(red: 242 / 255, green: 244 / 255, blue: 248 / 255),
        toolHeaderBg: Color(red: 236 / 255, green: 238 / 255, blue: 244 / 255)
    )
}

// MARK: - Theme Manager

final class ThemeManager: ObservableObject, @unchecked Sendable {
    static let shared = ThemeManager()

    @AppStorage("arabica.theme.mode") var modeRaw: String = ThemeMode.dark.rawValue
    @AppStorage("arabica.theme.fontFamily") var fontFamilyRaw: String = AppFontFamily.system.rawValue
    @AppStorage("arabica.theme.iconStyle") var iconStyleRaw: String = ToolIconStyle.oli.rawValue
    @AppStorage("arabica.theme.fontSizeDelta") var fontSizeDelta: Double = 0.0

    var mode: ThemeMode {
        get { ThemeMode(rawValue: modeRaw) ?? .dark }
        set {
            modeRaw = newValue.rawValue
            objectWillChange.send()
        }
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
        isDark ? .dark : .light
    }

    var colorScheme: ColorScheme? {
        mode.colorScheme
    }

    func cycleMode() {
        switch mode {
        case .dark: mode = .light
        case .light: mode = .system
        case .system: mode = .dark
        }
    }

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
