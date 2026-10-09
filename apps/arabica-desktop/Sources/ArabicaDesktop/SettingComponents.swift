import SwiftUI

// MARK: - Setting Section Container

struct SettingSection<Content: View>: View {
    let title: String
    var subtitle: String?
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(AppFont.sans(13, weight: .semibold))
                    .foregroundStyle(Palette.ink)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(AppFont.sans(11))
                        .foregroundStyle(Palette.muted)
                }
            }
            VStack(spacing: 1) {
                content
            }
            .background(RoundedRectangle(cornerRadius: 8).fill(Palette.raised))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
        }
    }
}

// MARK: - Setting Row

struct SettingRow<Trailing: View>: View {
    let label: String
    var description: String?
    @ViewBuilder let trailing: Trailing

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                    .font(AppFont.sans(12, weight: .medium))
                    .foregroundStyle(Palette.ink)
                if let description, !description.isEmpty {
                    Text(description)
                        .font(AppFont.sans(10))
                        .foregroundStyle(Palette.muted)
                }
            }
            Spacer(minLength: 16)
            trailing
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }
}

// MARK: - Setting Card

struct SettingCard<Content: View>: View {
    var title: String?
    var badge: String?
    var badgeColor: Color = .blue
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if title != nil || badge != nil {
                HStack(alignment: .center) {
                    if let title {
                        Text(title)
                            .font(AppFont.code(13, weight: .semibold))
                            .foregroundStyle(Palette.ink)
                    }
                    Spacer()
                    if let badge {
                        Text(badge)
                            .font(AppFont.code(10, weight: .bold))
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(RoundedRectangle(cornerRadius: 4).fill(badgeColor.opacity(0.18)))
                            .foregroundStyle(badgeColor)
                    }
                }
            }
            content
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 8).fill(Palette.raised))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
    }
}

// MARK: - Flow Route Step Row

struct FlowRouteStepRow: View {
    let icon: String
    let iconColor: Color
    let title: String
    let targetModel: String?
    var detail: String?

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(iconColor)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(title)
                    .font(AppFont.sans(11, weight: .medium))
                    .foregroundStyle(Palette.ink)
                if let detail {
                    Text(detail)
                        .font(AppFont.sans(10))
                        .foregroundStyle(Palette.muted)
                }
            }
            Spacer()
            if let targetModel, !targetModel.isEmpty {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(Palette.muted)
                    Text(targetModel)
                        .font(AppFont.code(11, weight: .semibold))
                        .foregroundStyle(Palette.ink)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(RoundedRectangle(cornerRadius: 4).fill(Palette.codeBg))
                }
            } else {
                Text("Default / None")
                    .font(AppFont.sans(10))
                    .foregroundStyle(Palette.muted.opacity(0.7))
            }
        }
        .padding(.vertical, 4)
    }
}

// MARK: - Palette & Font Reference Helpers

extension Palette {
    static var cardBorder: Color { ThemeManager.shared.currentColors.rule }
}

// MARK: - Appearance Page View

// MARK: - Appearance Page View

struct AppearancePageView: View {
    @ObservedObject private var theme = ThemeManager.shared
    @State private var isCreatingTheme = false
    @State private var newThemeName = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // 1. Theme Presets & Custom Themes
                SettingSection(title: "Themes", subtitle: "Select a built-in theme or create your own custom palette") {
                    VStack(alignment: .leading, spacing: 12) {
                        // Theme cards grid
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 140, maximum: 180), spacing: 10)], spacing: 10) {
                            ForEach(theme.allThemes) { item in
                                let isSelected = theme.activeThemeID == item.id
                                Button {
                                    theme.selectTheme(item)
                                } label: {
                                    VStack(alignment: .leading, spacing: 8) {
                                        HStack(spacing: 5) {
                                            Circle().fill(Color(hex: item.canvasHex)).frame(width: 14, height: 14)
                                                .overlay(Circle().stroke(Palette.rule, lineWidth: 1))
                                            Circle().fill(Color(hex: item.sidebarHex)).frame(width: 14, height: 14)
                                                .overlay(Circle().stroke(Palette.rule, lineWidth: 1))
                                            Circle().fill(Color(hex: item.accentHex)).frame(width: 14, height: 14)
                                            Spacer()
                                            if isSelected {
                                                Image(systemName: "checkmark.circle.fill")
                                                    .font(.system(size: 12))
                                                    .foregroundStyle(Palette.accent)
                                            }
                                        }

                                        Text(item.name)
                                            .font(AppFont.sans(11.5, weight: isSelected ? .semibold : .medium))
                                            .foregroundStyle(isSelected ? Palette.ink : Palette.muted)
                                            .lineLimit(1)

                                        HStack(spacing: 4) {
                                            Text(item.isDark ? "Dark" : "Light")
                                                .font(AppFont.sans(9.5))
                                                .foregroundStyle(Palette.subtle)
                                            if item.isCustom {
                                                Text("• Custom")
                                                    .font(AppFont.sans(9.5))
                                                    .foregroundStyle(Palette.accent)
                                            }
                                        }
                                    }
                                    .padding(10)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .background(isSelected ? Palette.raised : Palette.card, in: RoundedRectangle(cornerRadius: 8))
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 8)
                                            .stroke(isSelected ? Palette.accent : Palette.rule, lineWidth: isSelected ? 1.5 : 1)
                                    )
                                }
                                .buttonStyle(.plain)
                            }
                        }

                        // Action: Create Custom Theme
                        HStack(spacing: 10) {
                            Button {
                                let created = theme.createCustomTheme(name: "Custom Theme \(theme.customThemes.count + 1)")
                                theme.selectTheme(created)
                            } label: {
                                HStack(spacing: 5) {
                                    Image(systemName: "plus.circle.fill")
                                    Text("New Custom Theme")
                                }
                                .font(AppFont.sans(11.5, weight: .medium))
                                .foregroundStyle(Palette.accent)
                                .padding(.horizontal, 10)
                                .padding(.vertical, 6)
                                .background(Palette.raised.opacity(0.8), in: RoundedRectangle(cornerRadius: 6))
                            }
                            .buttonStyle(.plain)

                            Spacer()

                            Picker("Mode", selection: $theme.mode) {
                                ForEach(ThemeMode.allCases) { mode in
                                    Label(mode.rawValue, systemImage: mode.icon).tag(mode)
                                }
                            }
                            .pickerStyle(.segmented)
                            .frame(width: 200)
                        }
                        .padding(.top, 4)
                    }
                    .padding(14)
                }

                // 2. Custom Theme Editor (Active only when editing a custom theme)
                if theme.activeTheme.isCustom {
                    customThemeEditorSection
                }

                // 3. Tool Icons Style
                SettingSection(title: "Tool Icons", subtitle: "Icon pack and visual rendering for agent tool calls") {
                    SettingRow(label: "Icon Style", description: "Switch between Oli vector icons, Apple SF Symbols, or custom glyph styles") {
                        Picker("Icon Style", selection: $theme.iconStyle) {
                            ForEach(ToolIconStyle.allCases) { style in
                                Text(style.rawValue).tag(style)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(width: 180)
                    }
                }

                // 4. Typography
                SettingSection(title: "Typography", subtitle: "Font family and size scale for chat, chrome, and code") {
                    SettingRow(label: "Font Family", description: "Design style applied to interface and body text") {
                        Picker("Font Family", selection: $theme.fontFamily) {
                            ForEach(AppFontFamily.allCases) { family in
                                Text(family.rawValue).tag(family)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(width: 180)
                    }

                    SettingRow(label: "Font Size Adjust", description: "Fine-tune UI font scale (\(theme.fontSizeDelta >= 0 ? "+\(Int(theme.fontSizeDelta))" : "\(Int(theme.fontSizeDelta))") pt)") {
                        HStack(spacing: 8) {
                            Button {
                                if theme.fontSizeDelta > -3 { theme.fontSizeDelta -= 1 }
                            } label: {
                                Image(systemName: "minus")
                                    .font(.system(size: 11, weight: .semibold))
                            }
                            .buttonStyle(.plain)
                            .frame(width: 24, height: 24)
                            .background(Palette.raised.opacity(0.8), in: RoundedRectangle(cornerRadius: 4))

                            Text("\(theme.fontSizeDelta >= 0 ? "+" : "")\(Int(theme.fontSizeDelta))")
                                .font(AppFont.code(12))
                                .frame(width: 28)

                            Button {
                                if theme.fontSizeDelta < 4 { theme.fontSizeDelta += 1 }
                            } label: {
                                Image(systemName: "plus")
                                    .font(.system(size: 11, weight: .semibold))
                            }
                            .buttonStyle(.plain)
                            .frame(width: 24, height: 24)
                            .background(Palette.raised.opacity(0.8), in: RoundedRectangle(cornerRadius: 4))
                        }
                    }
                }

                // 5. Live Preview Card
                SettingSection(title: "Live Preview", subtitle: "Instant preview of active theme colors, fonts, and tool icons") {
                    VStack(alignment: .leading, spacing: 12) {
                        // User message preview
                        HStack {
                            Spacer()
                            Text("Let's test theme colors and fonts!")
                                .font(AppFont.body(13))
                                .foregroundStyle(Palette.ink)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 8)
                                .background(Palette.userBubble, in: RoundedRectangle(cornerRadius: 12))
                                .overlay(RoundedRectangle(cornerRadius: 12).stroke(Palette.rule, lineWidth: 1))
                        }

                        // Assistant preview
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Arabica assistant responding in **\(theme.activeTheme.name)** with **\(theme.fontFamily.rawValue)** typography.")
                                .font(AppFont.body(13))
                                .foregroundStyle(Palette.ink)

                            // Tool Card preview
                            HStack(spacing: 8) {
                                ToolIconView(kind: "read", title: "read file", size: 13)
                                Text("Read src/theme.rs")
                                    .font(AppFont.code(12))
                                    .foregroundStyle(Palette.ink)
                                Spacer()
                                Image(systemName: "checkmark.circle.fill")
                                    .font(AppFont.sans(11))
                                    .foregroundStyle(Color.green.opacity(0.85))
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 7)
                            .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8))
                            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))

                            // Sidebar sample preview
                            HStack(spacing: 8) {
                                Image(systemName: "folder")
                                    .font(AppFont.sans(11))
                                    .foregroundStyle(Palette.ink.opacity(0.85))
                                Text("Sidebar Sample")
                                    .font(AppFont.sans(11.5, weight: .medium))
                                    .foregroundStyle(Palette.ink)
                                Spacer()
                                Text("Selected")
                                    .font(AppFont.code(10))
                                    .foregroundStyle(Palette.accent)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Palette.selectionBg, in: RoundedRectangle(cornerRadius: 4))
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                            .background(Palette.sidebar, in: RoundedRectangle(cornerRadius: 6))
                            .overlay(RoundedRectangle(cornerRadius: 6).stroke(Palette.rule, lineWidth: 1))
                        }
                        .padding(.top, 4)
                    }
                    .padding(14)
                }
            }
            .padding(20)
        }
    }

    // MARK: - Custom Theme Editor Section

    private var customThemeEditorSection: some View {
        SettingSection(title: "Customize Theme: \(theme.activeTheme.name)", subtitle: "Modify colors, base appearance, and palette values") {
            VStack(spacing: 1) {
                // Name Row
                SettingRow(label: "Theme Name", description: "Display name for this custom theme") {
                    TextField("Theme Name", text: Binding(
                        get: { theme.activeTheme.name },
                        set: {
                            var t = theme.activeTheme
                            t.name = $0
                            theme.updateCustomTheme(t)
                        }
                    ))
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 180)
                }

                // Appearance Base
                SettingRow(label: "Appearance Base", description: "Base styling for traffic lights and menus") {
                    Picker("Base Mode", selection: Binding(
                        get: { theme.activeTheme.isDark },
                        set: {
                            var t = theme.activeTheme
                            t.isDark = $0
                            theme.updateCustomTheme(t)
                        }
                    )) {
                        Text("Dark").tag(true)
                        Text("Light").tag(false)
                    }
                    .pickerStyle(.segmented)
                    .frame(width: 160)
                }

                // Color: Canvas Background
                SettingRow(label: "Canvas Background", description: "Main chat background color") {
                    ColorPicker("", selection: Binding(
                        get: { Color(hex: theme.activeTheme.canvasHex) },
                        set: {
                            var t = theme.activeTheme
                            t.canvasHex = $0.toHex()
                            theme.updateCustomTheme(t)
                        }
                    ), supportsOpacity: false)
                }

                // Color: Sidebar Background
                SettingRow(label: "Sidebar Background", description: "Project list sidebar background") {
                    ColorPicker("", selection: Binding(
                        get: { Color(hex: theme.activeTheme.sidebarHex) },
                        set: {
                            var t = theme.activeTheme
                            t.sidebarHex = $0.toHex()
                            theme.updateCustomTheme(t)
                        }
                    ), supportsOpacity: false)
                }

                // Color: Card Background
                SettingRow(label: "Card / Input Background", description: "Background for cards and composer") {
                    ColorPicker("", selection: Binding(
                        get: { Color(hex: theme.activeTheme.cardHex) },
                        set: {
                            var t = theme.activeTheme
                            t.cardHex = $0.toHex()
                            t.composerBgHex = $0.toHex()
                            theme.updateCustomTheme(t)
                        }
                    ), supportsOpacity: false)
                }

                // Color: Text (Ink)
                SettingRow(label: "Primary Text (Ink)", description: "Main text and headings") {
                    ColorPicker("", selection: Binding(
                        get: { Color(hex: theme.activeTheme.inkHex) },
                        set: {
                            var t = theme.activeTheme
                            t.inkHex = $0.toHex()
                            theme.updateCustomTheme(t)
                        }
                    ), supportsOpacity: false)
                }

                // Color: Accent Color
                SettingRow(label: "Accent Color", description: "Primary brand accent and highlights") {
                    ColorPicker("", selection: Binding(
                        get: { Color(hex: theme.activeTheme.accentHex) },
                        set: {
                            var t = theme.activeTheme
                            t.accentHex = $0.toHex()
                            theme.updateCustomTheme(t)
                        }
                    ), supportsOpacity: false)
                }

                // Color: User Bubble
                SettingRow(label: "User Bubble Color", description: "Background for user prompt bubble") {
                    ColorPicker("", selection: Binding(
                        get: { Color(hex: theme.activeTheme.userBubbleHex) },
                        set: {
                            var t = theme.activeTheme
                            t.userBubbleHex = $0.toHex()
                            theme.updateCustomTheme(t)
                        }
                    ), supportsOpacity: false)
                }

                // Delete Theme Row
                SettingRow(label: "Delete Theme", description: "Permanently delete this custom theme") {
                    Button(role: .destructive) {
                        theme.deleteCustomTheme(id: theme.activeTheme.id)
                    } label: {
                        Label("Delete", systemImage: "trash")
                            .foregroundStyle(.red)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}
