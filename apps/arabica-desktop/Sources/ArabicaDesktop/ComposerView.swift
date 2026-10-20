import AppKit
import SwiftUI

// MARK: - Composer View

struct ComposerView: View {
    @ObservedObject var model: DesktopModel
    @ObservedObject private var themeManager = ThemeManager.shared
    @FocusState private var isInputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 8) {
                // Attached files chips
                if !model.attachedFiles.isEmpty {
                    ScrollView(.horizontal) {
                        HStack(spacing: 6) {
                            ForEach(model.attachedFiles, id: \.self) { file in
                                HStack(spacing: 5) {
                                    Image(systemName: "paperclip")
                                        .font(AppFont.sans(10))
                                    Text(file.lastPathComponent)
                                        .font(AppFont.code(11))
                                        .lineLimit(1)
                                    Button {
                                        model.attachedFiles.removeAll { $0 == file }
                                    } label: {
                                        Image(systemName: "xmark")
                                            .font(AppFont.sans(9, weight: .bold))
                                    }
                                    .buttonStyle(.plain)
                                    .help("Remove \(file.lastPathComponent)")
                                    .accessibilityLabel("Remove attachment \(file.lastPathComponent)")
                                }
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Palette.card, in: Capsule())
                                .overlay(Capsule().stroke(Palette.rule, lineWidth: 1))
                                .help(file.path)
                            }
                        }
                    }
                    .scrollIndicators(.hidden)
                }

                // Input text field
                TextField(
                    model.selectedSessionID == nil ? "Draft a message, then create a conversation…" : "Send a message…",
                    text: $model.draft, axis: .vertical
                )
                .textFieldStyle(.plain)
                .font(AppFont.message(13.5))
                .foregroundStyle(Palette.ink)
                .lineLimit(1...8)
                .padding(.horizontal, 2)
                .padding(.top, 2)
                .focused($isInputFocused)
                .accessibilityLabel("Message")
                .help("Return to send. Shift-Return for a new line.")
                .onSubmit { Task { await model.send() } }

                // Bottom toolbar inside composer card
                HStack(spacing: 8) {
                    Button(action: model.chooseFiles) {
                        Image(systemName: "paperclip")
                            .font(AppFont.sans(13))
                            .foregroundStyle(Palette.muted)
                            .frame(width: 26, height: 26)
                    }
                    .buttonStyle(.plain)
                    .help("Attach files")
                    .accessibilityLabel("Attach files")

                    if !model.availableCommands.isEmpty {
                        Menu {
                            ForEach(Array(model.availableCommands.enumerated()), id: \.offset) { _, cmd in
                                if let name = cmd["name"] as? String {
                                    Button("/\(name)") {
                                        model.draft += model.draft.isEmpty ? "/\(name) " : "\n/\(name) "
                                        isInputFocused = true
                                    }
                                }
                            }
                        } label: {
                            Image(systemName: "slash.circle")
                                .font(AppFont.sans(13))
                                .foregroundStyle(Palette.muted)
                                .frame(width: 26, height: 26)
                        }
                        .menuStyle(.borderlessButton)
                        .help("Slash commands")
                    }

                    policyControl

                    Spacer(minLength: 4)

                    // Send or Stop button
                    if model.isRunning {
                        Button(action: model.cancel) {
                            Label(model.isStopping ? "Stopping…" : "Stop", systemImage: "stop.fill")
                                .font(AppFont.sans(11.5, weight: .semibold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 10)
                                .frame(height: 28)
                                .background(Color.red.opacity(model.isStopping ? 0.5 : 0.85), in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .disabled(model.isStopping)
                        .help(model.isStopping ? model.composerPhase.detail : "Stop run (Cmd+.)")
                        .accessibilityLabel(model.isStopping ? "Stopping current run" : "Stop run")
                    } else {
                        let action = model.composerPhase.primaryAction
                        let isEnabled = canPerformPrimaryAction
                        Button {
                            performPrimaryAction(action)
                        } label: {
                            HStack(spacing: 5) {
                                if model.composerPhase.showsProgress {
                                    ProgressView().controlSize(.mini)
                                } else {
                                    Image(systemName: action.symbol)
                                }
                                Text(action.title)
                            }
                            .font(AppFont.sans(12, weight: .semibold))
                            .foregroundStyle(isEnabled ? Palette.canvas : Palette.subtle)
                            .padding(.horizontal, 10)
                            .frame(height: 28)
                            .background(isEnabled ? Palette.ink : Palette.card, in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .fixedSize()
                        .disabled(!isEnabled)
                        .help(model.canSendMessage ? "Send message (Return)" : model.composerPhase.detail)
                        .accessibilityLabel(action == .send ? "Send message" : action.title)
                    }
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Palette.composerBg, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(Palette.rule, lineWidth: 1))
            .shadow(color: themeManager.isDark ? Color.clear : Color.black.opacity(0.04), radius: 8, x: 0, y: 2)
        }
        .padding(.horizontal, 24)
        .padding(.top, 8)
        .padding(.bottom, 16)
        .frame(maxWidth: 820)
        .frame(maxWidth: .infinity)
        .onChange(of: model.composerFocusRequest) { _, _ in
            isInputFocused = true
        }
    }

    private var policySelection: ComposerPolicySelection {
        ComposerPolicySelection(options: model.composerConfigurationOptions)
    }

    private var policyControl: some View {
        ViewThatFits(in: .horizontal) {
            policyMenu.fixedSize(horizontal: true, vertical: true)
            policyMenu.frame(maxWidth: 280, alignment: .leading)
        }
    }

    private var policyMenu: some View {
        let selection = policySelection
        let feedback = model.composerConfigurationFeedback
        let failureDetail = feedback.flatMap { $0.kind == .failed ? $0.detail : nil }
        return Menu {
            if let policy = selection.policy {
                configurationChoices(policy)
            } else if selection.hasOptions {
                Label("SOE", systemImage: "checkmark")
            }

            if selection.isSOE, let option = selection.model {
                Divider()
                Menu {
                    configurationChoices(option)
                    if let thinking = selection.thinking {
                        Divider()
                        Menu("Thinking Level: \(thinking.currentName)") {
                            configurationChoices(thinking)
                        }
                    }
                } label: {
                    Text("SOE Model: \(option.currentName)")
                }
            }

            if !model.availableSessionModes.isEmpty {
                Divider()
                Menu("Session Mode") {
                    ForEach(Array(model.availableSessionModes.enumerated()), id: \.offset) { _, mode in
                        if let id = mode["id"] as? String {
                            Button {
                                Task { await model.setSessionMode(id) }
                            } label: {
                                if id == model.currentModeID {
                                    Label(mode["name"] as? String ?? id, systemImage: "checkmark")
                                } else {
                                    Text(mode["name"] as? String ?? id)
                                }
                            }
                        }
                    }
                }
            }
        } label: {
            HStack(spacing: 5) {
                if model.isUpdatingSessionConfiguration {
                    ProgressView().controlSize(.mini)
                } else if feedback?.kind == .failed {
                    Image(systemName: "exclamationmark.circle")
                        .foregroundStyle(.orange)
                }
                Text(selection.hasOptions ? "Policy: \(selection.currentName)" : "Policy")
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .foregroundStyle(Palette.ink)
            .font(AppFont.sans(11, weight: .medium))
            .padding(.horizontal, 4)
            .frame(height: 28)
            .frame(maxWidth: 280, alignment: .leading)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.visible)
        .fixedSize(horizontal: false, vertical: true)
        .disabled(!selection.hasOptions || !model.canChangeSessionConfiguration)
        .help(failureDetail ?? selection.help)
        .accessibilityLabel(selection.hasOptions ? "Policy: \(selection.currentName)" : "Policy")
        .accessibilityHint(selection.help)
    }

    @ViewBuilder
    private func configurationChoices(_ option: ComposerConfigurationOption) -> some View {
        ForEach(option.choices) { choice in
            Button {
                Task { await model.setConfigOption(option.id, value: choice.value) }
            } label: {
                let title = choice.group.map { "\($0) · \(choice.name)" } ?? choice.name
                if choice.value == option.currentValue {
                    Label(title, systemImage: "checkmark")
                } else {
                    Text(title)
                }
            }
        }
    }

    private var canPerformPrimaryAction: Bool {
        switch model.composerPhase.primaryAction {
        case .openWorkspace: return true
        case .reconnect: return model.workspace != nil
        case .createConversation: return true
        case .reloadConversation: return model.selectedSessionID != nil
        case .send: return model.canSendMessage
        }
    }

    private func performPrimaryAction(_ action: ComposerPrimaryAction) {
        switch action {
        case .openWorkspace:
            model.chooseWorkspace()
        case .reconnect:
            if let workspace = model.workspace {
                Task { await model.openWorkspace(workspace) }
            }
        case .createConversation:
            Task { await model.newSession() }
        case .reloadConversation:
            if let sessionID = model.selectedSessionID {
                Task { await model.selectSession(sessionID) }
            }
        case .send:
            Task { await model.send() }
        }
    }
}
