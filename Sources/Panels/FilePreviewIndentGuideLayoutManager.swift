import AppKit

final class FilePreviewIndentGuideLayoutManager: NSLayoutManager {
    private(set) var showsIndentationGuides = false
    private var indentationGuideColor = NSColor.separatorColor

    @MainActor
    func configureIndentationGuides(enabled: Bool, color: NSColor) {
        guard enabled != showsIndentationGuides || !color.isEqual(indentationGuideColor) else {
            return
        }
        showsIndentationGuides = enabled
        indentationGuideColor = color
        if let textStorage {
            invalidateDisplay(
                forCharacterRange: NSRange(location: 0, length: textStorage.length)
            )
        }
    }

    override func drawBackground(forGlyphRange glyphsToShow: NSRange, at origin: NSPoint) {
        // TextKit invokes this inherited, nonisolated drawing callback on AppKit's main thread.
        if showsIndentationGuides {
            drawIndentationGuides(forGlyphRange: glyphsToShow, at: origin)
        }
        super.drawBackground(forGlyphRange: glyphsToShow, at: origin)
    }

    private func drawIndentationGuides(forGlyphRange glyphRange: NSRange, at origin: NSPoint) {
        guard glyphRange.location != NSNotFound,
              glyphRange.length > 0,
              let textStorage else { return }

        let text = textStorage.mutableString
        let path = NSBezierPath()
        path.lineWidth = 0.5

        enumerateLineFragments(forGlyphRange: glyphRange) { [self] fragmentRect, _, _, fragmentGlyphRange, _ in
            let characterRange = characterRange(
                forGlyphRange: fragmentGlyphRange,
                actualGlyphRange: nil
            )
            guard characterRange.location != NSNotFound,
                  characterRange.location < text.length else { return }

            var lineStart = 0
            var lineEnd = 0
            var contentsEnd = 0
            text.getLineStart(
                &lineStart,
                end: &lineEnd,
                contentsEnd: &contentsEnd,
                for: NSRange(location: characterRange.location, length: 0)
            )
            let lineRange = NSRange(location: lineStart, length: contentsEnd - lineStart)
            let guideOffsets = FilePreviewIndentGuideCalculator.guideUTF16Offsets(
                in: text,
                lineRange: lineRange
            )

            for characterOffset in guideOffsets {
                let guideGlyphIndex = glyphIndexForCharacter(at: characterOffset)
                let guideFragmentRect = lineFragmentRect(
                    forGlyphAt: guideGlyphIndex,
                    effectiveRange: nil
                )
                let guideLocation = location(forGlyphAt: guideGlyphIndex)
                let x = origin.x + guideFragmentRect.minX + guideLocation.x
                let alignedX = floor(x) + 0.5
                path.move(to: NSPoint(x: alignedX, y: origin.y + fragmentRect.minY))
                path.line(to: NSPoint(x: alignedX, y: origin.y + fragmentRect.maxY))
            }
        }

        indentationGuideColor.setStroke()
        path.stroke()
    }
}

@MainActor
extension NSTextView {
    func configureFilePreviewIndentationGuides(enabled: Bool, color: NSColor) {
        guard let layoutManager = layoutManager as? FilePreviewIndentGuideLayoutManager else {
            return
        }
        layoutManager.configureIndentationGuides(enabled: enabled, color: color)
        needsDisplay = true
    }
}
