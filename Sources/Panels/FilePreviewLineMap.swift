import Foundation

struct FilePreviewLineMap: Sendable {
    let lineStartUTF16Offsets: [Int]
    let utf16Length: Int

    init(source: String) {
        let text = source as NSString
        lineStartUTF16Offsets = Self.makeLineStarts(in: text) { false } ?? [0]
        utf16Length = text.length
    }

    var lineCount: Int {
        lineStartUTF16Offsets.count
    }

    func lineNumber(containingUTF16Offset offset: Int) -> Int {
        lineIndex(containingUTF16Offset: offset) + 1
    }

    func lineIndices(intersectingUTF16Range range: NSRange) -> Range<Int> {
        let firstOffset = min(max(0, range.location), utf16Length)
        let upperOffset = min(max(firstOffset, NSMaxRange(range)), utf16Length)
        let firstIndex = lineIndex(containingUTF16Offset: firstOffset)
        let lastIndex = lineIndex(containingUTF16Offset: upperOffset)
        return firstIndex ..< min(lastIndex + 1, lineStartUTF16Offsets.count)
    }

    static func build(source: String) async -> FilePreviewLineMap? {
        let text = source as NSString
        guard let starts = makeLineStarts(in: text, shouldCancel: { Task.isCancelled }) else {
            return nil
        }
        return FilePreviewLineMap(lineStartUTF16Offsets: starts, utf16Length: text.length)
    }

    private init(lineStartUTF16Offsets: [Int], utf16Length: Int) {
        self.lineStartUTF16Offsets = lineStartUTF16Offsets
        self.utf16Length = utf16Length
    }

    private func lineIndex(containingUTF16Offset offset: Int) -> Int {
        let target = min(max(0, offset), utf16Length)
        var lowerBound = 0
        var upperBound = lineStartUTF16Offsets.count

        while lowerBound < upperBound {
            let candidate = lowerBound + (upperBound - lowerBound) / 2
            if lineStartUTF16Offsets[candidate] <= target {
                lowerBound = candidate + 1
            } else {
                upperBound = candidate
            }
        }

        return max(0, lowerBound - 1)
    }

    private static func makeLineStarts(
        in text: NSString,
        shouldCancel: () -> Bool
    ) -> [Int]? {
        var starts = [0]
        var cursor = 0

        while cursor < text.length {
            if starts.count.isMultiple(of: 2_048), shouldCancel() {
                return nil
            }

            var lineEnd = 0
            var contentsEnd = 0
            text.getLineStart(nil, end: &lineEnd, contentsEnd: &contentsEnd, for: NSRange(location: cursor, length: 0))
            guard lineEnd > cursor else { break }

            cursor = lineEnd
            if cursor < text.length || lineEnd > contentsEnd {
                starts.append(cursor)
            }
        }

        return starts
    }
}
