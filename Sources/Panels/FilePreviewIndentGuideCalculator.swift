import Foundation

struct FilePreviewIndentGuideCalculator {
    static let defaultSpacesPerIndentation = 4

    static func guideUTF16Offsets(
        in text: NSString,
        lineRange: NSRange,
        spacesPerIndentation: Int = defaultSpacesPerIndentation
    ) -> [Int] {
        guard spacesPerIndentation > 0 else { return [] }
        let lineStart = min(max(0, lineRange.location), text.length)
        let lineEnd = min(max(lineStart, NSMaxRange(lineRange)), text.length)
        var offsets: [Int] = []
        var cursor = lineStart

        while cursor < lineEnd {
            switch text.character(at: cursor) {
            case 0x09:
                offsets.append(cursor)
                cursor += 1
            case 0x20:
                let runStart = cursor
                while cursor < lineEnd, text.character(at: cursor) == 0x20 {
                    cursor += 1
                }
                let completeIndentations = (cursor - runStart) / spacesPerIndentation
                for indentation in 0 ..< completeIndentations {
                    offsets.append(runStart + indentation * spacesPerIndentation)
                }
            default:
                return offsets
            }
        }

        return offsets
    }
}
