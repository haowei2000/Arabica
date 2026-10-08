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
    static let cardBorder = Color.white.opacity(0.08)
}
