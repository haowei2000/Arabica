import AppKit
import SwiftUI

struct MarkdownBlock: Equatable {
    enum TableAlignment: Equatable { case leading, center, trailing }
    enum Kind: Equatable {
        case paragraph, heading(Int), code, quote, list(String), rule
        case table(headers: [String], rows: [[String]], alignments: [TableAlignment])
    }
    let kind: Kind
    let text: String
    let language: String?
    let taskChecked: Bool?

    init(kind: Kind, text: String, language: String? = nil, taskChecked: Bool? = nil) {
        self.kind = kind
        self.text = text
        self.language = language
        self.taskChecked = taskChecked
    }

    static func parse(_ source: String) -> [MarkdownBlock] {
        var blocks: [MarkdownBlock] = []
        var paragraph: [String] = []
        var code: [String]? = nil
        var fence = ""
        var language: String?
        func flush() {
            if !paragraph.isEmpty {
                blocks.append(Self(kind: .paragraph, text: paragraph.joined(separator: "\n")))
                paragraph.removeAll()
            }
        }
        let lines = source.replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n").components(separatedBy: "\n")
        var index = 0
        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if code != nil {
                if indentation(line) <= 3, isClosingFence(trimmed, fence: fence) {
                    blocks.append(Self(kind: .code, text: code!.joined(separator: "\n"), language: language))
                    code = nil
                } else { code!.append(line) }
                index += 1
                continue
            }
            if indentation(line) <= 3, let opening = openingFence(trimmed) {
                flush()
                fence = opening.fence
                language = opening.language
                code = []
                index += 1
                continue
            }
            if trimmed.isEmpty { flush(); index += 1; continue }
            if paragraph.isEmpty, indentation(line) >= 4 {
                var literal: [String] = []
                while index < lines.count {
                    if indentation(lines[index]) >= 4 {
                        literal.append(removingIndent(4, from: lines[index]))
                    } else if lines[index].trimmingCharacters(in: .whitespaces).isEmpty {
                        literal.append("")
                    } else { break }
                    index += 1
                }
                while literal.last == "" { literal.removeLast() }
                blocks.append(Self(kind: .code, text: literal.joined(separator: "\n")))
                continue
            }
            if !paragraph.isEmpty, indentation(line) <= 3, let level = setextHeading(trimmed) {
                blocks.append(Self(kind: .heading(level), text: paragraph.joined(separator: "\n")))
                paragraph.removeAll()
                index += 1
                continue
            }
            if index + 1 < lines.count,
               let headers = tableCells(line),
               let delimiter = tableDelimiter(lines[index + 1]),
               headers.count == delimiter.count {
                flush()
                var rows: [[String]] = []
                index += 2
                while index < lines.count, !startsBlock(lines[index]), let cells = tableCells(lines[index]) {
                    rows.append(Array((cells + Array(repeating: "", count: headers.count)).prefix(headers.count)))
                    index += 1
                }
                blocks.append(Self(kind: .table(headers: headers, rows: rows, alignments: delimiter), text: ""))
                continue
            }
            if let heading = heading(line) {
                flush()
                blocks.append(Self(kind: .heading(heading.level), text: heading.text))
            } else if isRule(line) {
                flush(); blocks.append(Self(kind: .rule, text: ""))
            } else if quoteBody(line) != nil {
                flush()
                var body: [String] = []
                while index < lines.count {
                    if let quoted = quoteBody(lines[index]) {
                        body.append(quoted)
                    } else if body.last?.trimmingCharacters(in: .whitespaces).isEmpty == false,
                              !startsBlock(lines[index]), !isRule(lines[index]) {
                        // Unmarked paragraph continuations remain inside the quote.
                        body.append(lines[index])
                    } else { break }
                    index += 1
                }
                blocks.append(Self(kind: .quote, text: body.joined(separator: "\n")))
                continue
            } else if let item = listItem(line) {
                flush()
                var body = [item.body]
                index += 1
                while index < lines.count {
                    let next = lines[index]
                    if next.trimmingCharacters(in: .whitespaces).isEmpty {
                        var following = index + 1
                        while following < lines.count,
                              lines[following].trimmingCharacters(in: .whitespaces).isEmpty { following += 1 }
                        guard following < lines.count, indentation(lines[following]) > item.indent else { break }
                        body.append(contentsOf: repeatElement("", count: following - index))
                        index = following
                        continue
                    }
                    let indent = indentation(next)
                    if indent > item.indent {
                        body.append(removingIndent(min(indent, item.contentIndent), from: next))
                    } else if startsBlock(next) || isRule(next) {
                        break
                    } else {
                        body.append(next)
                    }
                    index += 1
                }
                let task = taskBody(body[0])
                if let task { body[0] = task.text }
                blocks.append(Self(kind: .list(item.marker), text: body.joined(separator: "\n"), taskChecked: task?.checked))
                continue
            } else { paragraph.append(line) }
            index += 1
        }
        flush()
        if let code { blocks.append(Self(kind: .code, text: code.joined(separator: "\n"), language: language)) }
        return blocks
    }

    private static func openingFence(_ line: String) -> (fence: String, language: String?)? {
        guard let character = line.first, character == "`" || character == "~" else { return nil }
        let fence = String(line.prefix(while: { $0 == character }))
        guard fence.count >= 3 else { return nil }
        let info = line.dropFirst(fence.count).trimmingCharacters(in: .whitespaces)
        guard character != "`" || !info.contains("`") else { return nil }
        return (fence, info.split(whereSeparator: \.isWhitespace).first.map { String($0).lowercased() })
    }

    private static func isClosingFence(_ line: String, fence: String) -> Bool {
        guard let character = fence.first else { return false }
        let count = line.prefix(while: { $0 == character }).count
        return count >= fence.count && line.dropFirst(count).allSatisfy(\.isWhitespace)
    }

    private static func startsBlock(_ line: String) -> Bool {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty || (indentation(line) <= 3 && openingFence(trimmed) != nil)
            || quoteBody(line) != nil || heading(line) != nil || listItem(line) != nil || isRule(line)
    }

    private static func heading(_ line: String) -> (level: Int, text: String)? {
        guard indentation(line) <= 3 else { return nil }
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        let hashes = trimmed.prefix(while: { $0 == "#" }).count
        guard (1...6).contains(hashes) else { return nil }
        let body = trimmed.dropFirst(hashes)
        guard body.isEmpty || body.first?.isWhitespace == true else { return nil }
        let text = String(body).replacingOccurrences(of: #"[ \t]+#+[ \t]*$"#, with: "", options: .regularExpression)
            .trimmingCharacters(in: .whitespaces)
        return (hashes, text)
    }

    private static func setextHeading(_ line: String) -> Int? {
        guard let first = line.first, first == "=" || first == "-",
              line.allSatisfy({ $0 == first }) else { return nil }
        return first == "=" ? 1 : 2
    }

    private static func isRule(_ line: String) -> Bool {
        guard indentation(line) <= 3 else { return false }
        let markers = line.filter { !$0.isWhitespace }
        guard let first = markers.first, ["-", "*", "_"].contains(first), markers.count >= 3 else { return false }
        return markers.allSatisfy { $0 == first }
    }

    private static func quoteBody(_ line: String) -> String? {
        guard indentation(line) <= 3 else { return nil }
        let trimmed = line.drop(while: { $0 == " " || $0 == "\t" })
        guard trimmed.first == ">" else { return nil }
        var body = trimmed.dropFirst()
        if body.first == " " || body.first == "\t" { body = body.dropFirst() }
        return String(body)
    }

    private static func listItem(_ line: String) -> (indent: Int, contentIndent: Int, marker: String, body: String)? {
        let indent = indentation(line)
        let trimmed = line.drop(while: { $0 == " " || $0 == "\t" })
        guard let range = trimmed.range(of: #"^([-+*]|[0-9]{1,9}[.)])(?:[ \t]+|$)"#, options: .regularExpression) else { return nil }
        let prefix = trimmed[range]
        let marker = String(prefix.prefix(while: { !$0.isWhitespace }))
        let contentIndent = prefix.reduce(indent) { width, character in
            width + (character == "\t" ? 4 - width % 4 : 1)
        }
        return (indent, max(indent + marker.count + 1, contentIndent), marker.count == 1 ? "•" : marker,
                String(trimmed[range.upperBound...]))
    }

    private static func taskBody(_ line: String) -> (checked: Bool, text: String)? {
        guard line.count >= 3 else { return nil }
        let prefix = String(line.prefix(3)).lowercased()
        guard prefix == "[ ]" || prefix == "[x]" else { return nil }
        let remaining = line.dropFirst(3)
        guard remaining.isEmpty || remaining.first?.isWhitespace == true else { return nil }
        return (prefix == "[x]", String(remaining.drop(while: \.isWhitespace)))
    }

    private static func indentation(_ line: String) -> Int {
        var width = 0
        for character in line {
            if character == " " { width += 1 }
            else if character == "\t" { width += 4 - width % 4 }
            else { break }
        }
        return width
    }

    private static func removingIndent(_ count: Int, from line: String) -> String {
        var width = 0
        var index = line.startIndex
        while index < line.endIndex, width < count {
            let character = line[index]
            if character == " " { width += 1 }
            else if character == "\t" { width += 4 - width % 4 }
            else { break }
            index = line.index(after: index)
        }
        return String(repeating: " ", count: max(0, width - count)) + String(line[index...])
    }

    private static func tableCells(_ line: String) -> [String]? {
        let source = Array(line.trimmingCharacters(in: .whitespaces))
        guard source.contains("|") else { return nil }
        var cells: [String] = []
        var cell = ""
        var codeFenceLength = 0
        var separators: [Int] = []
        var index = 0
        while index < source.count {
            let character = source[index]
            if character == "\\", index + 1 < source.count {
                let next = source[index + 1]
                if next == "|" || codeFenceLength == 0 {
                    if next != "|" { cell.append(character) }
                    cell.append(next)
                    index += 2
                    continue
                }
            }
            if character == "`" {
                let count = source[index...].prefix(while: { $0 == "`" }).count
                if codeFenceLength == count {
                    codeFenceLength = 0
                } else if codeFenceLength == 0 {
                    // An unmatched backtick must not swallow later column separators.
                    var cursor = index + count
                    while cursor < source.count {
                        if source[cursor] == "`" {
                            let closingCount = source[cursor...].prefix(while: { $0 == "`" }).count
                            if closingCount == count { codeFenceLength = count; break }
                            cursor += closingCount
                        } else { cursor += 1 }
                    }
                }
                cell.append(contentsOf: source[index..<(index + count)])
                index += count
                continue
            }
            if character == "|", codeFenceLength == 0 {
                cells.append(cell.trimmingCharacters(in: .whitespaces))
                separators.append(index)
                cell = ""
            } else {
                cell.append(character)
            }
            index += 1
        }
        cells.append(cell.trimmingCharacters(in: .whitespaces))
        guard !separators.isEmpty else { return nil }
        if separators.last == source.count - 1 { cells.removeLast() }
        if separators.first == 0 { cells.removeFirst() }
        return cells.isEmpty ? nil : cells
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
            guard !dashes.isEmpty, dashes.allSatisfy({ $0 == "-" }) else { return nil }
            alignments.append(leading && trailing ? .center : trailing ? .trailing : .leading)
        }
        return alignments
    }
}

struct MarkdownMessage: View {
    let text: String
    var nestingDepth = 0

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
                    if block.language == "mermaid" {
                        MermaidBlockView(source: block.text)
                    } else {
                        CodeBlockView(code: block.text, language: block.language)
                    }
                case .quote:
                    HStack(alignment: .top, spacing: 10) {
                        Rectangle()
                            .fill(Palette.accent.opacity(0.6))
                            .frame(width: 3)
                            .clipShape(Capsule())
                        container(block.text)
                            .foregroundStyle(Palette.muted)
                    }
                    .fixedSize(horizontal: false, vertical: true)
                case .list(let marker):
                    HStack(alignment: .top, spacing: 8) {
                        Group {
                            if let checked = block.taskChecked {
                                Image(systemName: checked ? "checkmark.square.fill" : "square")
                                    .foregroundStyle(checked ? Palette.accent : Palette.muted)
                                    .accessibilityLabel(checked ? "Completed task" : "Incomplete task")
                            } else {
                                Text(marker).foregroundStyle(Palette.muted)
                            }
                        }
                        .font(AppFont.message(13, weight: .medium))
                        .frame(minWidth: 16, minHeight: 20, alignment: .trailing)
                        container(block.text)
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

    private func container(_ source: String) -> AnyView {
        // Bound recursive layout work for deeply nested, untrusted message content.
        if nestingDepth >= 16 { return AnyView(inline(source)) }
        return AnyView(MarkdownMessage(text: source, nestingDepth: nestingDepth + 1))
    }
}

private struct MarkdownTableView: View {
    let headers: [String]
    let rows: [[String]]
    let alignments: [MarkdownBlock.TableAlignment]
    @State private var availableWidth: CGFloat = 0

    private var columnWidths: [CGFloat] {
        MarkdownTableSizing.columnWidths(
            headers: headers, rows: rows, availableWidth: availableWidth,
            fontSize: max(7, 12.5 + ThemeManager.shared.fontSizeDelta)
        )
    }

    var body: some View {
        let widths = columnWidths
        ScrollView(.horizontal) {
            VStack(alignment: .leading, spacing: 0) {
                row(headers, widths: widths, isHeader: true)
                ForEach(Array(rows.enumerated()), id: \.offset) { index, cells in
                    row(cells, widths: widths, isHeader: false)
                        .background(index.isMultiple(of: 2) ? Color.clear : Palette.codeBg.opacity(0.45))
                }
            }
            .frame(width: widths.reduce(0, +))
            .clipShape(RoundedRectangle(cornerRadius: 7))
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(Palette.rule, lineWidth: 1))
        }
        .background(GeometryReader { geometry in
            Color.clear.preference(key: MarkdownTableWidthKey.self, value: geometry.size.width)
        })
        .onPreferenceChange(MarkdownTableWidthKey.self) { availableWidth = $0 }
        .accessibilityLabel("Table, \(headers.count) columns, \(rows.count) rows")
    }

    private func row(_ cells: [String], widths: [CGFloat], isHeader: Bool) -> some View {
        HStack(alignment: .top, spacing: 0) {
            ForEach(Array(cells.enumerated()), id: \.offset) { index, cell in
                markdownCell(cell, index: index, width: widths[index], isHeader: isHeader)
            }
        }
        .background(isHeader ? Palette.codeBg : Color.clear)
        .overlay(alignment: .bottom) { Rectangle().fill(Palette.rule).frame(height: 1) }
    }

    private func markdownCell(_ cell: String, index: Int, width: CGFloat, isHeader: Bool) -> some View {
        let alignment: Alignment
        let textAlignment: TextAlignment
        switch alignments.indices.contains(index) ? alignments[index] : .leading {
        case .leading: alignment = .leading; textAlignment = .leading
        case .center: alignment = .center; textAlignment = .center
        case .trailing: alignment = .trailing; textAlignment = .trailing
        }
        return inline(cell)
            .font(isHeader ? AppFont.message(12.5, weight: .semibold) : AppFont.message(12.5))
            .multilineTextAlignment(textAlignment)
            .fixedSize(horizontal: false, vertical: true)
            .frame(width: max(0, width - 20), alignment: alignment)
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

private struct MarkdownTableWidthKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}

enum MarkdownTableSizing {
    static func columnWidths(headers: [String], rows: [[String]], availableWidth: CGFloat, fontSize: CGFloat) -> [CGFloat] {
        let font = NSFont.systemFont(ofSize: fontSize)
        let widths = headers.indices.map { index in
            ([headers[index]] + rows.compactMap { $0.indices.contains(index) ? $0[index] : nil })
                .flatMap { $0.components(separatedBy: "\n") }
                .map { ($0 as NSString).size(withAttributes: [.font: font]).width + 20 }
                .max().map { min(340, max(76, ceil($0))) } ?? 76
        }
        let preferred = widths.reduce(0, +)
        let minimum = CGFloat(widths.count) * 76
        guard availableWidth >= minimum, preferred > availableWidth, preferred > minimum else { return widths }
        let ratio = (availableWidth - minimum) / (preferred - minimum)
        return widths.map { 76 + ($0 - 76) * ratio }
    }
}

struct CodeBlockView: View {
    let code: String
    var language: String? = nil
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                if let language, !language.isEmpty {
                    Text(language)
                        .font(AppFont.code(11))
                        .foregroundStyle(Palette.muted)
                }
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
                .help("Copy code")
                .accessibilityLabel(copied ? "Code copied" : "Copy code")
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
