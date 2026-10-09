import SwiftUI

struct MarkdownBlock: Equatable {
    enum Kind: Equatable { case paragraph, heading(Int), code, quote, list(String), rule }
    let kind: Kind
    let text: String

    static func parse(_ source: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var code: [String]? = nil
        var fence = ""
        func flush() {
            if !paragraph.isEmpty {
                blocks.append(Self(kind: .paragraph, text: paragraph.joined(separator: "\n")))
                paragraph.removeAll()
            }
        }
        for line in source.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if code != nil {
                if trimmed.hasPrefix(fence) {
                    blocks.append(Self(kind: .code, text: code!.joined(separator: "\n")))
                    code = nil
                } else { code!.append(line) }
                continue
            }
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flush(); fence = String(trimmed.prefix(3)); code = []; continue
            }
            if trimmed.isEmpty { flush(); continue }
            let hashes = trimmed.prefix(while: { $0 == "#" }).count
            if (1...6).contains(hashes), trimmed.dropFirst(hashes).first == " " {
                flush()
                blocks.append(Self(kind: .heading(hashes), text: String(trimmed.dropFirst(hashes + 1))))
            } else if ["---", "***", "___"].contains(trimmed) {
                flush(); blocks.append(Self(kind: .rule, text: ""))
            } else if trimmed.hasPrefix(">") {
                flush()
                blocks.append(Self(kind: .quote, text: String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces)))
            } else if let range = trimmed.range(of: #"^([-+*]|[0-9]+[.)])\s+"#, options: .regularExpression) {
                flush()
                let marker = String(trimmed[range]).trimmingCharacters(in: .whitespaces)
                blocks.append(Self(kind: .list(marker.count == 1 ? "•" : marker), text: String(trimmed[range.upperBound...])))
            } else { paragraph.append(line) }
        }
        flush()
        if let code { blocks.append(Self(kind: .code, text: code.joined(separator: "\n"))) }
        return blocks
    }
}

import AppKit

struct MarkdownMessage: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(MarkdownBlock.parse(text).enumerated()), id: \.offset) { _, block in
                switch block.kind {
                case .paragraph:
                    inline(block.text)
                case .heading(let level):
                    inline(block.text)
                        .font(AppFont.sans(CGFloat(level == 1 ? 17 : level == 2 ? 15 : 13.5), weight: .semibold))
                        .foregroundStyle(Palette.ink)
                case .code:
                    CodeBlockView(code: block.text)
                case .quote:
                    HStack(alignment: .top, spacing: 10) {
                        Rectangle()
                            .fill(Palette.accent.opacity(0.6))
                            .frame(width: 3)
                            .clipShape(Capsule())
                        inline(block.text)
                            .foregroundStyle(Palette.muted)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                case .list(let marker):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(marker)
                            .font(AppFont.sans(13, weight: .medium))
                            .foregroundStyle(Palette.muted)
                            .frame(minWidth: 16, alignment: .trailing)
                        inline(block.text)
                    }
                case .rule:
                    Divider().overlay(Palette.rule)
                }
            }
        }
        .font(AppFont.body(13.5))
        .lineSpacing(3.5)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func inline(_ source: String) -> some View {
        let content = (try? AttributedString(markdown: source, options: .init(
            interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(source)
        return Text(content)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct CodeBlockView: View {
    let code: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(code, forType: .string)
                    copied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1.8) {
                        copied = false
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: copied ? "checkmark" : "doc.on.doc")
                        Text(copied ? "Copied" : "Copy")
                    }
                    .font(AppFont.sans(11))
                    .foregroundStyle(copied ? Color.green : Palette.muted)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 10)
            .padding(.top, 7)
            .padding(.bottom, 4)

            ScrollView(.horizontal) {
                Text(code)
                    .font(AppFont.code(12))
                    .lineSpacing(2.5)
                    .foregroundStyle(Palette.ink)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12)
                    .padding(.bottom, 10)
            }
        }
        .background(Palette.card, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
    }
}
