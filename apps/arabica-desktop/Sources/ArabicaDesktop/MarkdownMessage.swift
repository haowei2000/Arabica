import SwiftUI

struct MarkdownBlock: Equatable {
    enum TableAlignment: Equatable { case leading, center, trailing }
    enum Kind: Equatable {
        case paragraph, heading(Int), code, quote, list(String), rule
        case table(headers: [String], rows: [[String]], alignments: [TableAlignment])
    }
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
        let lines = source.components(separatedBy: "\n")
        var index = 0
        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if code != nil {
                if trimmed.hasPrefix(fence) {
                    blocks.append(Self(kind: .code, text: code!.joined(separator: "\n")))
                    code = nil
                } else { code!.append(line) }
                index += 1
                continue
            }
            if trimmed.hasPrefix("```") || trimmed.hasPrefix("~~~") {
                flush(); fence = String(trimmed.prefix(3)); code = []; index += 1; continue
            }
            if trimmed.isEmpty { flush(); index += 1; continue }
            if index + 1 < lines.count,
               let headers = tableCells(line),
               let delimiter = tableDelimiter(lines[index + 1]),
               headers.count == delimiter.count {
                flush()
                var rows: [[String]] = []
                index += 2
                while index < lines.count, lines[index].contains("|"), let cells = tableCells(lines[index]) {
                    rows.append(Array((cells + Array(repeating: "", count: headers.count)).prefix(headers.count)))
                    index += 1
                }
                blocks.append(Self(kind: .table(headers: headers, rows: rows, alignments: delimiter), text: ""))
                continue
            }
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
            index += 1
        }
        flush()
        if let code { blocks.append(Self(kind: .code, text: code.joined(separator: "\n"))) }
        return blocks
    }

    private static func tableCells(_ line: String) -> [String]? {
        var source = line.trimmingCharacters(in: .whitespaces)
        guard source.contains("|") else { return nil }
        if source.hasPrefix("|") { source.removeFirst() }
        if source.hasSuffix("|"), !source.hasSuffix("\\|") { source.removeLast() }
        var cells: [String] = []
        var cell = ""
        var escaped = false
        var inCode = false
        for character in source {
            if escaped {
                if character != "|" { cell.append("\\") }
                cell.append(character)
                escaped = false
            } else if character == "\\" {
                escaped = true
            } else if character == "`" {
                inCode.toggle()
                cell.append(character)
            } else if character == "|", !inCode {
                cells.append(cell.trimmingCharacters(in: .whitespaces))
                cell = ""
            } else {
                cell.append(character)
            }
        }
        if escaped { cell.append("\\") }
        cells.append(cell.trimmingCharacters(in: .whitespaces))
        return cells.count > 1 ? cells : nil
    }

    private static func tableDelimiter(_ line: String) -> [TableAlignment]? {
        guard let cells = tableCells(line), !cells.isEmpty else { return nil }
        var alignments: [TableAlignment] = []
        for cell in cells {
            let value = cell.trimmingCharacters(in: .whitespaces)
            let leading = value.hasPrefix(":")
            let trailing = value.hasSuffix(":")
            var dashes = value[...]
            if leading { dashes = dashes.dropFirst() }
            if trailing { dashes = dashes.dropLast() }
            guard dashes.count >= 3, dashes.allSatisfy({ $0 == "-" }) else { return nil }
            alignments.append(leading && trailing ? .center : trailing ? .trailing : .leading)
        }
        return alignments
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
                        .font(AppFont.message(CGFloat(level == 1 ? 17 : level == 2 ? 15 : 13.5), weight: .semibold))
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
                            .font(AppFont.message(13, weight: .medium))
                            .foregroundStyle(Palette.muted)
                            .frame(minWidth: 16, alignment: .trailing)
                        inline(block.text)
                    }
                case .rule:
                    Divider().overlay(Palette.rule)
                case .table(let headers, let rows, let alignments):
                    MarkdownTableView(headers: headers, rows: rows, alignments: alignments)
                }
            }
        }
        .font(AppFont.message(13.5))
        .lineSpacing(3.5)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func inline(_ source: String) -> some View {
        let content = (try? AttributedString(markdown: source, options: .init(
            interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(source)
        return Text(content)
            .foregroundStyle(Palette.ink)
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct MarkdownTableView: View {
    let headers: [String]
    let rows: [[String]]
    let alignments: [MarkdownBlock.TableAlignment]

    var body: some View {
        ScrollView(.horizontal) {
            VStack(alignment: .leading, spacing: 0) {
                row(headers, isHeader: true)
                ForEach(Array(rows.enumerated()), id: \.offset) { _, cells in
                    row(cells, isHeader: false)
                }
            }
            .fixedSize(horizontal: true, vertical: false)
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(Palette.rule, lineWidth: 1))
        }
    }

    private func row(_ cells: [String], isHeader: Bool) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(cells.enumerated()), id: \.offset) { index, cell in
                markdownCell(cell, index: index, isHeader: isHeader)
            }
        }
        .background(isHeader ? Palette.codeBg : Color.clear)
        .overlay(alignment: .bottom) {
            if isHeader { Rectangle().fill(Palette.rule).frame(height: 1) }
        }
    }

    private func markdownCell(_ cell: String, index: Int, isHeader: Bool) -> some View {
        let alignment: Alignment
        switch alignments.indices.contains(index) ? alignments[index] : .leading {
        case .leading: alignment = .leading
        case .center: alignment = .center
        case .trailing: alignment = .trailing
        }
        return inline(cell)
            .font(isHeader ? AppFont.message(12.5, weight: .semibold) : AppFont.message(12.5))
            .frame(width: 150, alignment: alignment)
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .overlay(alignment: .trailing) { Rectangle().fill(Palette.rule).frame(width: 1) }
    }

    private func inline(_ source: String) -> some View {
        let content = (try? AttributedString(markdown: source, options: .init(
            interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(source)
        return Text(content).foregroundStyle(Palette.ink).textSelection(.enabled)
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
        .background(Palette.codeBg, in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Palette.rule, lineWidth: 1))
    }
}
