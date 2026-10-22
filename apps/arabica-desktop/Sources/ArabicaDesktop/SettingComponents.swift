import AppKit
import SwiftUI

// MARK: - Window Guard View

final class SettingsWindowGuardView: NSView {
    var windowChanged: ((NSWindow?) -> Void)?

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        windowChanged?(window)
    }
}

// MARK: - Appearance Page View

struct AppearancePageView: View {
    @ObservedObject private var theme = ThemeManager.shared
    @State private var themeToDelete: ThemeDefinition?

    var body: some View {
        Form {
            Section("Themes") {
                VStack(alignment: .leading, spacing: 12) {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 140, maximum: 180), spacing: 10)], spacing: 10) {
                        ForEach(theme.allThemes) { item in
                            let isSelected = theme.activeThemeID == item.id
                            Button {
                                theme.selectTheme(item)
                            } label: {
                                VStack(alignment: .leading, spacing: 8) {
                                    HStack(spacing: 5) {
                                        Circle().fill(Color(hex: item.canvasHex)).frame(width: 14, height: 14)
                                            .overlay(Circle().stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                                        Circle().fill(Color(hex: item.sidebarHex)).frame(width: 14, height: 14)
                                            .overlay(Circle().stroke(Color(nsColor: .separatorColor), lineWidth: 1))
                                        Circle().fill(Color(hex: item.accentHex)).frame(width: 14, height: 14)
                                        Spacer()
                                        if isSelected {
                                            Image(systemName: "checkmark.circle.fill")
                                                .font(.system(size: 12))
                                                .foregroundStyle(Color.accentColor)
                                        }
                                    }

                                    Text(item.name)
                                        .font(.system(size: 11.5, weight: isSelected ? .semibold : .medium))
                                        .foregroundStyle(isSelected ? .primary : .secondary)
                                        .lineLimit(1)

                                    HStack(spacing: 4) {
                                        Text(item.isDark ? "Dark" : "Light")
                                            .font(.caption2)
                                            .foregroundStyle(.tertiary)
                                        if item.isCustom {
                                            Text("• Custom")
                                                .font(.caption2)
                                                .foregroundStyle(Color.accentColor)
                                        }
                                    }
                                }
                                .padding(10)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .background(
                                    isSelected
                                        ? Color.accentColor.opacity(0.12)
                                        : Color(nsColor: .controlBackgroundColor),
                                    in: RoundedRectangle(cornerRadius: 8)
                                )
                                .overlay(
                                    RoundedRectangle(cornerRadius: 8)
                                        .stroke(
                                            isSelected ? Color.accentColor : Color(nsColor: .separatorColor),
                                            lineWidth: isSelected ? 1.5 : 1
                                        )
                                )
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel(item.name)
                            .accessibilityAddTraits(isSelected ? .isSelected : [])
                        }
                    }

                    HStack(spacing: 12) {
                        Button {
                            let created = theme.createCustomTheme(name: "Custom Theme \(theme.customThemes.count + 1)")
                            theme.selectTheme(created)
                        } label: {
                            Label("New Custom Theme", systemImage: "plus")
                        }

                        Spacer()

                        Picker("Mode", selection: $theme.mode) {
                            ForEach(ThemeMode.allCases) { mode in
                                Label(mode.rawValue, systemImage: mode.icon).tag(mode)
                            }
                        }
                        .pickerStyle(.segmented)
                        .frame(width: 220)
                    }
                    .padding(.top, 4)
                }
            }

            if theme.activeTheme.isCustom {
                customThemeEditorSection
            }

            Section("Tool Icons") {
                Picker("Icon Style", selection: $theme.iconStyle) {
                    ForEach(ToolIconStyle.allCases) { style in
                        Text(style.rawValue).tag(style)
                    }
                }
            }

            Section("Typography") {
                fontFamilyRow(
                    title: "Normal Font",
                    subtitle: "Sidebar, navigation, buttons, titles & UI chrome",
                    fontName: $theme.fontNormal,
                    presets: ["System", "SF Pro", "Inter", "Helvetica Neue", "System Rounded"]
                )

                fontFamilyRow(
                    title: "Event Font",
                    subtitle: "Tool executions, process events, thought & plan status",
                    fontName: $theme.fontEvent,
                    presets: ["System", "JetBrains Mono", "Fira Code", "SF Pro", "Menlo"]
                )

                fontFamilyRow(
                    title: "Message Font",
                    subtitle: "Composer input draft & conversation markdown dialog",
                    fontName: $theme.fontMessage,
                    presets: ["System", "Inter", "Georgia", "New York (Serif)", "SF Pro"]
                )

                fontFamilyRow(
                    title: "Code Font",
                    subtitle: "Code blocks, file paths, terminal output & diff blocks",
                    fontName: $theme.fontCode,
                    presets: ["System Monospaced", "JetBrains Mono", "Fira Code", "Menlo", "Courier New", "Source Code Pro"]
                )

                LabeledContent("Font Size Adjust") {
                    HStack(spacing: 8) {
                        Button {
                            if theme.fontSizeDelta > -3 { theme.fontSizeDelta -= 1 }
                        } label: {
                            Image(systemName: "minus")
                        }
                        .disabled(theme.fontSizeDelta <= -3)

                        Text("\(theme.fontSizeDelta >= 0 ? "+" : "")\(Int(theme.fontSizeDelta)) pt")
                            .font(.system(.body, design: .monospaced))
                            .frame(width: 50)

                        Button {
                            if theme.fontSizeDelta < 4 { theme.fontSizeDelta += 1 }
                        } label: {
                            Image(systemName: "plus")
                        }
                        .disabled(theme.fontSizeDelta >= 4)
                    }
                }
            }

            Section("Live Preview") {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Spacer()
                        Text("Let's test theme colors and fonts!")
                            .font(AppFont.message(13))
                            .foregroundStyle(Palette.ink)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(Palette.userBubble, in: RoundedRectangle(cornerRadius: 10))
                    }

                    VStack(alignment: .leading, spacing: 6) {
                        Text("Arabica assistant responding in **\(theme.activeTheme.name)**.")
                            .font(AppFont.message(13))
                            .foregroundStyle(Palette.ink)

                        HStack(spacing: 8) {
                            ToolIconView(kind: "read", title: "read file", size: 13)
                            Text("Read file")
                                .font(AppFont.event(12, weight: .medium))
                                .foregroundStyle(Palette.ink)
                            Text("src/theme.rs")
                                .font(AppFont.code(11.5))
                                .foregroundStyle(Palette.muted)
                            Spacer()
                            Image(systemName: "checkmark.circle.fill")
                                .font(.system(size: 11))
                                .foregroundStyle(Color.green)
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 7)
                        .background(Palette.raised, in: RoundedRectangle(cornerRadius: 8))

                        HStack(spacing: 8) {
                            Image(systemName: "folder")
                                .font(AppFont.normal(11))
                                .foregroundStyle(Palette.ink.opacity(0.85))
                            Text("Sidebar Sample")
                                .font(AppFont.normal(11.5, weight: .medium))
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
                    }
                }
                .padding(.vertical, 4)
            }
        }
        .formStyle(.grouped)
        .confirmationDialog(
            "Delete Custom Theme?",
            isPresented: Binding(
                get: { themeToDelete != nil },
                set: { if !$0 { themeToDelete = nil } }
            ),
            titleVisibility: .visible,
            presenting: themeToDelete
        ) { item in
            Button("Delete \(item.name)", role: .destructive) {
                theme.deleteCustomTheme(id: item.id)
                themeToDelete = nil
            }
            Button("Cancel", role: .cancel) { themeToDelete = nil }
        } message: { item in
            Text("The custom theme \(item.name) will be permanently removed.")
        }
    }

    @ViewBuilder
    private func fontFamilyRow(
        title: String,
        subtitle: String,
        fontName: Binding<String>,
        presets: [String]
    ) -> some View {
        LabeledContent {
            HStack(spacing: 8) {
                TextField("Font Family", text: fontName)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 150)
                Menu {
                    ForEach(presets, id: \.self) { preset in
                        Button(preset) { fontName.wrappedValue = preset }
                    }
                } label: {
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption)
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
            }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                Text(subtitle)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var customThemeEditorSection: some View {
        Section("Customize Theme: \(theme.activeTheme.name)") {
            LabeledContent("Theme Name") {
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

            Picker("Appearance Base", selection: Binding(
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

            ColorPicker("Canvas Background", selection: Binding(
                get: { Color(hex: theme.activeTheme.canvasHex) },
                set: {
                    var t = theme.activeTheme
                    t.canvasHex = $0.toHex()
                    theme.updateCustomTheme(t)
                }
            ), supportsOpacity: false)

            ColorPicker("Sidebar Background", selection: Binding(
                get: { Color(hex: theme.activeTheme.sidebarHex) },
                set: {
                    var t = theme.activeTheme
                    t.sidebarHex = $0.toHex()
                    theme.updateCustomTheme(t)
                }
            ), supportsOpacity: false)

            ColorPicker("Card / Input Background", selection: Binding(
                get: { Color(hex: theme.activeTheme.cardHex) },
                set: {
                    var t = theme.activeTheme
                    t.cardHex = $0.toHex()
                    t.composerBgHex = $0.toHex()
                    theme.updateCustomTheme(t)
                }
            ), supportsOpacity: false)

            ColorPicker("Primary Text (Ink)", selection: Binding(
                get: { Color(hex: theme.activeTheme.inkHex) },
                set: {
                    var t = theme.activeTheme
                    t.inkHex = $0.toHex()
                    theme.updateCustomTheme(t)
                }
            ), supportsOpacity: false)

            ColorPicker("Accent Color", selection: Binding(
                get: { Color(hex: theme.activeTheme.accentHex) },
                set: {
                    var t = theme.activeTheme
                    t.accentHex = $0.toHex()
                    theme.updateCustomTheme(t)
                }
            ), supportsOpacity: false)

            ColorPicker("User Bubble Color", selection: Binding(
                get: { Color(hex: theme.activeTheme.userBubbleHex) },
                set: {
                    var t = theme.activeTheme
                    t.userBubbleHex = $0.toHex()
                    theme.updateCustomTheme(t)
                }
            ), supportsOpacity: false)

            Button("Delete Custom Theme", role: .destructive) {
                themeToDelete = theme.activeTheme
            }
        }
    }
}
