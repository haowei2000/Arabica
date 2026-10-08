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

struct MarkdownMessage: View {
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(MarkdownBlock.parse(text).enumerated()), id: \.offset) { _, block in
                switch block.kind {
                case .paragraph:
                    inline(block.text)
                case .heading(let level):
                    inline(block.text).font(AppFont.sans(CGFloat(level == 1 ? 18 : level == 2 ? 16 : 14), weight: .semibold))
                case .code:
                    ScrollView(.horizontal) {
                        Text(block.text).font(AppFont.code(13))
                            .textSelection(.enabled).padding(12)
                    }
                    .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 8))
                case .quote:
                    HStack(alignment: .top, spacing: 10) {
                        Rectangle().fill(Color.secondary.opacity(0.5)).frame(width: 2)
                        inline(block.text).foregroundStyle(.secondary)
                    }.fixedSize(horizontal: false, vertical: true)
                case .list(let marker):
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(marker).frame(minWidth: 16, alignment: .trailing)
                        inline(block.text)
                    }
                case .rule:
                    Divider()
                }
            }
        }
        .font(AppFont.serif(14))
        .lineSpacing(3)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func inline(_ source: String) -> some View {
        let content = (try? AttributedString(markdown: source, options: .init(
            interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(source)
        return Text(content).textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
