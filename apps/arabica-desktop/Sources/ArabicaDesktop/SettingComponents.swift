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
                        .background(RoundedRectangle(cornerRadius: 4).fill(Color.white.opacity(0.08)))
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

struct AppearancePageView: View {
    @ObservedObject private var theme = ThemeManager.shared

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                // 1. Theme Mode
                SettingSection(title: "Color Theme", subtitle: "Choose between dark, light, or auto system appearance") {
                    SettingRow(label: "Theme Mode", description: "Current: \(theme.mode.rawValue)") {
                        Picker("Theme Mode", selection: $theme.mode) {
                            ForEach(ThemeMode.allCases) { mode in
                                Label(mode.rawValue, systemImage: mode.icon).tag(mode)
                            }
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 260)
                    }
                }

                // 2. Tool Icons Style
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

                // 3. Typography
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

                // 4. Live Preview Card
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
                        }

                        // Assistant preview
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Arabica assistant responding in **\(theme.mode.rawValue)** theme with **\(theme.fontFamily.rawValue)** typography.")
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
                            .background(Palette.raised.opacity(0.65), in: RoundedRectangle(cornerRadius: 8))
                        }
                        .padding(.top, 4)
                    }
                    .padding(14)
                }
            }
            .padding(20)
        }
    }
}
